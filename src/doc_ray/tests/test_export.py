"""Tests for the DB export helpers and the export CLI."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from doc_ray.adapters.export import (
    _counts,
    _json_default,
    cmd_list,
    fetch_record,
    main,
    resolve_ids,
)


def test_serializes_datetime_date_uuid_decimal():
    u = uuid.uuid4()
    payload = {
        "when": datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        "day": date(2024, 1, 2),
        "id": u,
        "num": Decimal("3.5"),
    }
    out = json.loads(json.dumps(payload, default=_json_default))
    assert out["when"].startswith("2024-01-02T03:04:05")
    assert out["day"] == "2024-01-02"
    assert out["id"] == str(u)
    assert out["num"] == 3.5


def test_memoryview_decodes_to_text():
    assert _json_default(memoryview(b"hi")) == "hi"


def test_unknown_type_raises():
    with pytest.raises(TypeError):
        _json_default(object())


# --- DB-backed: selectors, record assembly, CLI ----------------------------

_TEXT = "Exports are round trips. They must be lossless."


def _mkdoc(pg, *, title: str | None = None):
    import _itdb

    conn, created = pg
    doc_id = _itdb.make_doc(conn, _TEXT)
    created.append(doc_id)
    if title:
        conn.execute("UPDATE documents SET title=%s WHERE id=%s", (title, doc_id))
    return conn, doc_id


def test_resolve_ids_by_each_selector(pg):
    import _itdb

    title = f"itexp-{_itdb.unique_sha()[:8]}"
    conn, doc_id = _mkdoc(pg, title=title)
    conn.execute("UPDATE documents SET original_filename=%s WHERE id=%s", (f"{title}.txt", doc_id))

    assert resolve_ids(conn, doc_id=doc_id) == [doc_id]
    assert resolve_ids(conn, filename=f"{title}.txt") == [doc_id]
    assert resolve_ids(conn, title=title[2:10]) == [doc_id]
    assert doc_id in resolve_ids(conn)  # --all


def test_fetch_record_shape_and_toggles(pg):
    conn, doc_id = _mkdoc(pg)
    rec = fetch_record(conn, doc_id)
    assert rec is not None
    assert rec["document"]["id"] == uuid.UUID(doc_id)
    assert {"authors", "tags", "identifiers", "metadata"} <= rec["document"].keys()
    assert rec["content"]["full_text"] == _TEXT
    assert [s["ordinal"] for s in rec["sentences"]] == [0, 1]

    slim = fetch_record(conn, doc_id, include_content=False, include_sentences=False)
    assert slim is not None and "content" not in slim and "sentences" not in slim

    capped = fetch_record(conn, doc_id, max_sentences=1)
    assert capped is not None and len(capped["sentences"]) == 1

    assert fetch_record(conn, "00000000-0000-0000-0000-000000000000") is None
    counts = _counts(conn, doc_id)
    assert counts == {"sentences": 2, "provenance": 0}


def test_cmd_list_prints_table(pg, capsys):
    conn, _doc_id = _mkdoc(pg)
    assert cmd_list(conn) == 0
    out = capsys.readouterr().out
    assert "id" in out and "filename" in out


def test_main_exports_by_id_to_file(pg, tmp_path):
    _conn, doc_id = _mkdoc(pg)
    out = tmp_path / "export.json"
    assert main(["--id", doc_id, "-o", str(out), "--max-sentences", "1"]) == 0
    payload = json.loads(out.read_text())
    assert payload["count"] == 1
    assert payload["documents"][0]["document"]["id"] == doc_id
    assert len(payload["documents"][0]["sentences"]) == 1


def test_main_list_and_no_match(pg, capsys):
    _conn, _doc_id = _mkdoc(pg)
    assert main(["--list"]) == 0
    assert main(["--title", "no-such-title-ever-xyzzy"]) == 1
    assert main(["--id", "00000000-0000-0000-0000-000000000000", "--indent", "0"]) == 0
    assert '"count": 0' in capsys.readouterr().out
