"""The corpus-wide Prism backfill CLI must see the corpus it is backfilling.

The defect these tests pin:
``scripts/enqueue_visual_backfill.py`` is the documented way to run a corpus-wide
Tier-1/Tier-2 backfill, but it connects with ``DATABASE_URL`` — on the managed
cluster that logs in as ``docray``, a role bound by the ``documents`` read policy
with no principal in ``app.principal_id``. Every selection query therefore
returned zero rows and the CLI reported ``enqueued 0 job(s)`` against a corpus of
39 documents holding 2,636 images. A backfill tool that silently reports success
while selecting nothing is worse than one that fails outright.

The fix elevates to the trusted ``docray_batch`` role *in the script* — the
operator boundary, where possession of ``DATABASE_URL`` already implies
corpus-wide authority — and deliberately NOT inside
:mod:`doc_ray.application.visual_backfill`, so an admin API caller can never
inherit a tenant-blind selection by importing it. The first test pins that
division of responsibility so a later "helpful" refactor cannot erase it.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

_itdb = pytest.importorskip("_itdb")

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import enqueue_visual_backfill as cli  # noqa: E402

from doc_ray.application import visual_backfill  # noqa: E402
from doc_ray.domain.ulid import new_ulid_str  # noqa: E402


def _rls_bound_dsn() -> str:
    """The test DSN, but logged in *bound by RLS* like the managed cluster.

    Without this the CLI would run as the test superuser, see the whole corpus,
    and the production failure would be invisible to these tests. ``-c role=…``
    puts the session on ``docray_api`` from the first statement, exactly as the
    pooled production connection sits on an RLS-bound role.
    """
    separator = "&" if "?" in _itdb.DB_URL else "?"
    return f"{_itdb.DB_URL}{separator}options=-c%20role%3Ddocray_api"


def _doc_with_image(conn: Any) -> str:
    """A document in the state the backfill selects: extracted, with an image."""
    doc_id = _itdb.make_doc(conn, "A page with a figure on it.")
    conn.execute("UPDATE documents SET status = 'extracted' WHERE id = %s", (doc_id,))
    conn.execute(
        "INSERT INTO document_image "
        "(id, document_id, page_number, source, object_key, sha256, width_px, height_px, "
        " detector, detector_version) "
        "VALUES (%s, %s, 1, 'embedded-raster', %s, %s, 640, 480, 'test', '0')",
        (new_ulid_str(), doc_id, f"ab/{uuid.uuid4()}.png", _itdb.unique_sha()),
    )
    return doc_id


def test_selection_is_blind_under_an_rls_bound_role(pg) -> None:
    """The regression itself: the raw module selects nothing as ``docray_api``.

    Asserted rather than fixed at the module level on purpose — the module stays
    RLS-honest so an API caller cannot inherit a corpus-wide view from it.
    """
    conn, created = pg
    conn.execute("SET ROLE docray_batch")
    doc_id = _doc_with_image(conn)
    created.append(doc_id)
    conn.commit()
    try:
        conn.execute("SET ROLE docray_api")
        assert visual_backfill.enqueue_visual_embed_jobs(conn) == []
    finally:
        conn.execute("RESET ROLE")
        conn.execute("SET ROLE docray_batch")


def test_cli_elevates_and_enqueues_for_a_document_with_images(pg, monkeypatch) -> None:
    """End to end: the CLI enqueues Tier-2 work for an eligible document."""
    conn, created = pg
    conn.execute("SET ROLE docray_batch")
    doc_id = _doc_with_image(conn)
    created.append(doc_id)
    conn.commit()
    monkeypatch.setenv("DATABASE_URL", _rls_bound_dsn())

    assert cli.main(["--tier", "embed"]) == 0

    row = conn.execute(
        "SELECT count(*) AS n FROM jobs WHERE kind = 'visual_embed' AND document_id = %s",
        (doc_id,),
    ).fetchone()
    assert _itdb._row_value(row, "n") == 1, "the CLI must enqueue Tier-2 work it can see"
    conn.execute("DELETE FROM jobs WHERE document_id = %s", (doc_id,))
    conn.commit()


def test_cli_enqueues_tier3_captions_for_a_document_with_images(pg, monkeypatch) -> None:
    """Tier-3 is reachable from the same CLI as the tiers below it.

    Prism ships three tiers but the backfill CLI only offered two, so the
    grounded-caption tier had no operator entry point at all and stayed at zero
    rows in production while Tier-1 ran corpus-wide.
    """
    conn, created = pg
    conn.execute("SET ROLE docray_batch")
    doc_id = _doc_with_image(conn)
    created.append(doc_id)
    conn.commit()
    monkeypatch.setenv("DATABASE_URL", _rls_bound_dsn())

    assert cli.main(["--tier", "caption"]) == 0

    row = conn.execute(
        "SELECT count(*) AS n FROM jobs WHERE kind = 'visual_caption' AND document_id = %s",
        (doc_id,),
    ).fetchone()
    assert _itdb._row_value(row, "n") == 1, "the CLI must reach Tier-3"
    conn.execute("DELETE FROM jobs WHERE document_id = %s", (doc_id,))
    conn.commit()


def test_caption_only_missing_skips_a_document_already_captioned(pg) -> None:
    """A re-run must not re-drive the VLM over images already captioned.

    Tier-3 is the most expensive kind in the system — one VLM round trip per
    image — so a non-idempotent backfill is not merely wasteful, it is hours of
    GPU time per accidental re-run.
    """
    conn, created = pg
    conn.execute("SET ROLE docray_batch")
    doc_id = _doc_with_image(conn)
    created.append(doc_id)
    image_row = conn.execute(
        "SELECT id FROM document_image WHERE document_id = %s", (doc_id,)
    ).fetchone()
    conn.execute(
        "INSERT INTO visual_caption "
        "(id, document_image_id, document_id, model, model_version, caption, status, "
        " grounding_score) "
        "VALUES (%s, %s, %s, 'test/fake', 'v0', 'Box A: label.', 'committed', 0.9)",
        (new_ulid_str(), _itdb._row_value(image_row, "id"), doc_id),
    )

    assert visual_backfill.enqueue_visual_caption_jobs(conn) == []

    conn.execute("DELETE FROM visual_caption WHERE document_id = %s", (doc_id,))
    conn.commit()


def test_cli_only_missing_skips_an_already_embedded_document(pg, monkeypatch) -> None:
    """Idempotence: a re-run fills gaps, it does not re-enqueue finished work."""
    conn, created = pg
    conn.execute("SET ROLE docray_batch")
    doc_id = _doc_with_image(conn)
    created.append(doc_id)
    image_row = conn.execute(
        "SELECT id FROM document_image WHERE document_id = %s", (doc_id,)
    ).fetchone()
    conn.execute(
        "INSERT INTO image_embedding "
        "(document_image_id, document_id, model, vector, preprocessing_sha256) "
        "VALUES (%s, %s, 'test/fake', %s, %s)",
        (
            _itdb._row_value(image_row, "id"),
            doc_id,
            "[" + ",".join(["0"] * 768) + "]",
            _itdb.unique_sha(),
        ),
    )
    conn.commit()
    monkeypatch.setenv("DATABASE_URL", _rls_bound_dsn())

    assert cli.main(["--tier", "embed"]) == 0

    row = conn.execute(
        "SELECT count(*) AS n FROM jobs WHERE kind = 'visual_embed' AND document_id = %s",
        (doc_id,),
    ).fetchone()
    assert _itdb._row_value(row, "n") == 0, "an already-embedded document must not be re-enqueued"
    conn.execute("DELETE FROM image_embedding WHERE document_id = %s", (doc_id,))
    conn.commit()
