#!/usr/bin/env python3
"""Operator CLI: repair captions already persisted with model scaffolding (BL-196).

``domain/caption_text`` stops new captions carrying reasoning blocks and
chat-template sentinels. Rows written before that fix still carry them, and an
operator has to read them — in one corpus, roughly one caption row in sixteen.

Each affected row is repaired in place, never deleted — a rejected caption is
still the audit trail for a paid-for API call:

* rows that recover into prose keep their status and get the cleaned text;
* rows with nothing left are set to ``rejected`` with a recorded reason, so they
  leave the human queue without a human decision being spent on them.

RLS: ``visual_caption`` is subject to forced row-level security and the
application role does not hold BYPASSRLS, so a plain read returns zero rows —
indistinguishable from "there is nothing to repair". This assumes
``docray_batch`` and asserts the corpus is visible before doing anything.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Any

import psycopg

from doc_ray.application.visual_grounding import grounding_score
from doc_ray.domain.caption_text import is_structurally_invalid, sanitize_caption

#: Role holding BYPASSRLS, created by migration 106 for worker/CLI batch work.
BATCH_ROLE = "docray_batch"

#: Runs of whitespace, collapsed for the "did anything substantive change" test.
_WHITESPACE = re.compile(r"\s+")

#: Recorded on rows rejected by this repair, so the reason is auditable.
REJECTION_REASON = "scaffolding-only caption; no reviewable content (BL-196)"

_SELECT = "SELECT id, caption, status FROM visual_caption WHERE status <> 'rejected'"
_UPDATE_TEXT = "UPDATE visual_caption SET caption = %(caption)s WHERE id = %(id)s"
_UPDATE_DEMOTE = "UPDATE visual_caption SET status = %(status)s WHERE id = %(id)s"
_UPDATE_REJECT = (
    # The ::text cast is required: Postgres cannot infer a parameter's type
    # inside jsonb_build_object, and fails the statement with
    # "could not determine data type of parameter".
    "UPDATE visual_caption SET caption = %(caption)s, status = %(status)s, "
    "analysis = coalesce(analysis, '{}'::jsonb) "
    "|| jsonb_build_object('repair_error', %(error)s::text) "
    "WHERE id = %(id)s"
)


class CorpusInvisible(RuntimeError):
    """Raised when no captions are visible, so a no-op cannot pass as success."""


def repair(conn: Any, *, dry_run: bool = True) -> dict[str, int]:
    """Sanitise every non-rejected caption, rejecting those left with no content.

    Args:
        conn: Live connection able to read and write ``visual_caption``.
        dry_run: When true, count the changes without writing them.

    Returns:
        Counts keyed ``scanned``, ``cleaned`` and ``rejected``.

    Raises:
        CorpusInvisible: No caption rows are visible after assuming the batch role.
    """
    conn.execute(f"SET ROLE {BATCH_ROLE}")
    visible = conn.execute("SELECT count(*) FROM visual_caption").fetchone()[0]
    if int(visible) == 0:
        raise CorpusInvisible(
            f"no caption rows visible after assuming {BATCH_ROLE}; refusing to "
            "report an empty repair as success"
        )

    counts = {"scanned": 0, "cleaned": 0, "rejected": 0, "demoted": 0}
    for row in conn.execute(_SELECT).fetchall():
        if isinstance(row, tuple):
            row_id, raw = row[0], row[1]
            status = row[2] if len(row) > 2 else "review"
        else:
            row_id, raw, status = row["id"], row["caption"], row.get("status", "review")
        counts["scanned"] += 1
        cleaned = sanitize_caption(raw)

        # Structural invalidity is checked FIRST and independently of whether
        # sanitising changed anything: an 80-dash string needs no cleaning and is
        # still not a caption. Gating this on "did the text change" is what let
        # such a row stay committed with grounding 0.89.
        if is_structurally_invalid(cleaned):
            counts["rejected"] += 1
            if not dry_run:
                conn.execute(
                    _UPDATE_REJECT,
                    {
                        "id": row_id,
                        "caption": cleaned,
                        "status": "rejected",
                        "error": REJECTION_REASON,
                    },
                )
            continue

        # Re-apply the corrected specificity gate (BL-195). It is a function of
        # the caption alone, so a stale verdict can be re-decided here without
        # the figure evidence and without re-running the model.
        if status == "committed" and grounding_score(cleaned, cleaned).insufficient_specificity:
            counts["demoted"] += 1
            if not dry_run:
                conn.execute(_UPDATE_DEMOTE, {"id": row_id, "status": "review"})
            continue

        # Compare against a whitespace-normalised original, not the raw string:
        # collapsing newlines is not a repair, and counting it as one would
        # rewrite 3,788 rows to fix 386.
        if cleaned == _WHITESPACE.sub(" ", raw or "").strip():
            continue
        counts["cleaned"] += 1
        if not dry_run:
            conn.execute(_UPDATE_TEXT, {"id": row_id, "caption": cleaned, "status": None})
    return counts


def main(argv: list[str] | None = None) -> int:
    """Run the repair and report what changed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry run).")
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("fatal: DATABASE_URL is not set", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(database_url, autocommit=True) as conn:
            counts = repair(conn, dry_run=not args.apply)
    except CorpusInvisible as exc:
        print(f"fatal: {exc}", file=sys.stderr)
        return 2
    mode = "applied" if args.apply else "dry run"
    print(
        f"caption repair ({mode}): scanned={counts['scanned']} "
        f"cleaned={counts['cleaned']} rejected={counts['rejected']} "
        f"demoted={counts['demoted']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
