"""Export the complete stored record of documents from the database.

The inverse of the ingester: given a document (by id, filename, or title) — or
all of them — it pulls back everything doc-ray persists:

    document (entity + bibliographic + content identity + lineage)
      ├─ provenance   (append-only, ULID-ordered event trail)
      ├─ content      (full extracted text)
      └─ sentences    (ULID-keyed, with char offsets)

Usage:
    python -m ingest.export --list
    python -m ingest.export --filename AirFrance447_BEA.pdf -o out.json
    python -m ingest.export --title AirFrance --no-content
    python -m ingest.export --all -o everything.json
    python -m ingest.export --id 46533017-2989-4909-bc1e-4fc7ed2f26a3
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import date, datetime
from decimal import Decimal

from . import db
from .config import Config


def _json_default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, uuid.UUID):
        return str(o)
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, memoryview):
        return o.tobytes().decode("utf-8", "replace")
    raise TypeError(f"not JSON-serializable: {type(o).__name__}")


# --- fetch helpers ---------------------------------------------------------

def resolve_ids(conn, *, doc_id=None, filename=None, title=None) -> list[str]:
    """Resolve a selector to an ordered list of document ids."""
    if doc_id:
        return [doc_id]
    if filename:
        rows = conn.execute(
            "SELECT id FROM documents WHERE original_filename = %s "
            "ORDER BY created_at",
            (filename,),
        ).fetchall()
    elif title:
        rows = conn.execute(
            "SELECT id FROM documents WHERE title ILIKE %s ORDER BY created_at",
            (f"%{title}%",),
        ).fetchall()
    else:  # all
        rows = conn.execute(
            "SELECT id FROM documents ORDER BY created_at"
        ).fetchall()
    return [str(r["id"]) for r in rows]


def fetch_record(conn, doc_id, *, include_content=True, include_sentences=True,
                 max_sentences=None) -> dict | None:
    """Assemble the full nested record for one document."""
    document = conn.execute(
        "SELECT * FROM documents WHERE id = %s", (doc_id,)
    ).fetchone()
    if document is None:
        return None

    # Reassemble the 4NF satellites back onto the entity.
    document["authors"] = [
        r["name"] for r in conn.execute(
            "SELECT a.name FROM document_authors da JOIN authors a ON a.id = da.author_id "
            "WHERE da.document_id = %s ORDER BY da.position", (doc_id,)
        ).fetchall()
    ]
    document["tags"] = [
        r["tag"] for r in conn.execute(
            "SELECT t.tag FROM document_tags dt JOIN tags t ON t.id = dt.tag_id "
            "WHERE dt.document_id = %s ORDER BY t.tag", (doc_id,)
        ).fetchall()
    ]
    document["identifiers"] = [
        {"scheme": r["scheme"], "value": r["value"]} for r in conn.execute(
            "SELECT scheme, value FROM document_identifiers WHERE document_id = %s "
            "ORDER BY scheme", (doc_id,)
        ).fetchall()
    ]
    document["metadata"] = {
        r["key"]: r["value"] for r in conn.execute(
            "SELECT key, value FROM document_metadata WHERE document_id = %s "
            "ORDER BY key", (doc_id,)
        ).fetchall()
    }

    provenance = conn.execute(
        """
        SELECT id, kind, tool, tool_version, occurred_at, actor,
               input_sha256, output_sha256, note
        FROM document_provenance
        WHERE document_id = %s
        ORDER BY id
        """,
        (doc_id,),
    ).fetchall()
    # Reassemble each event's params from the EAV table.
    params = conn.execute(
        "SELECT event_id, key, value FROM provenance_params pp "
        "JOIN document_provenance dp ON dp.id = pp.event_id "
        "WHERE dp.document_id = %s", (doc_id,)
    ).fetchall()
    by_event: dict = {}
    for r in params:
        by_event.setdefault(str(r["event_id"]), {})[r["key"]] = r["value"]
    for e in provenance:
        e["params"] = by_event.get(str(e["id"]), {})

    record: dict = {"document": document, "provenance": provenance}

    if include_content:
        row = conn.execute(
            "SELECT full_text FROM document_content WHERE document_id = %s",
            (doc_id,),
        ).fetchone()
        record["content"] = {"full_text": row["full_text"] if row else None}

    if include_sentences:
        q = ("SELECT s.id, s.ordinal, s.text, sp.char_start, sp.char_end "
             "FROM sentences s JOIN spans sp ON sp.id = s.span_id "
             "WHERE s.document_id = %s ORDER BY s.ordinal")
        params: tuple = (doc_id,)
        if max_sentences is not None:
            q += " LIMIT %s"
            params = (doc_id, max_sentences)
        record["sentences"] = conn.execute(q, params).fetchall()

    return record


def _counts(conn, doc_id) -> dict:
    s = conn.execute(
        "SELECT count(*) n FROM sentences WHERE document_id = %s", (doc_id,)
    ).fetchone()["n"]
    p = conn.execute(
        "SELECT count(*) n FROM document_provenance WHERE document_id = %s",
        (doc_id,),
    ).fetchone()["n"]
    return {"sentences": s, "provenance": p}


# --- commands --------------------------------------------------------------

def cmd_list(conn) -> int:
    rows = conn.execute(
        """
        SELECT d.id, d.title, d.original_filename AS filename,
               d.page_count, d.char_count,
               (SELECT count(*) FROM sentences s WHERE s.document_id = d.id) AS sentences,
               (SELECT count(*) FROM document_provenance p WHERE p.document_id = d.id) AS provenance
        FROM documents d ORDER BY d.created_at
        """
    ).fetchall()
    if not rows:
        print("(no documents)")
        return 0
    print(f"{'id':36}  {'filename':38}  {'pages':>5}  {'chars':>8}  {'sent':>5}  {'prov':>4}")
    for r in rows:
        print(f"{str(r['id']):36}  {(r['filename'] or '')[:38]:38}  "
              f"{r['page_count'] or 0:>5}  {r['char_count'] or 0:>8}  "
              f"{r['sentences']:>5}  {r['provenance']:>4}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export stored documents from the DB.")
    sel = ap.add_mutually_exclusive_group()
    sel.add_argument("--id", help="Export one document by UUID.")
    sel.add_argument("--filename", help="Export by original source filename.")
    sel.add_argument("--title", help="Export by (fuzzy) title match.")
    sel.add_argument("--all", action="store_true", help="Export every document.")
    ap.add_argument("--list", action="store_true",
                    help="List stored documents with counts and exit.")
    ap.add_argument("--no-content", action="store_true", help="Omit full_text.")
    ap.add_argument("--no-sentences", action="store_true", help="Omit sentences.")
    ap.add_argument("--max-sentences", type=int, default=None,
                    help="Cap sentences per document in the output.")
    ap.add_argument("--indent", type=int, default=2, help="JSON indent (0 = compact).")
    ap.add_argument("-o", "--output", help="Write JSON here instead of stdout.")
    args = ap.parse_args(argv)

    cfg = Config.from_env()
    with db.connect(cfg) as conn:
        if args.list:
            return cmd_list(conn)

        ids = resolve_ids(conn, doc_id=args.id, filename=args.filename,
                          title=args.title)
        if not ids:
            print("No matching documents.", file=sys.stderr)
            return 1

        records = []
        for doc_id in ids:
            rec = fetch_record(
                conn, doc_id,
                include_content=not args.no_content,
                include_sentences=not args.no_sentences,
                max_sentences=args.max_sentences,
            )
            if rec is not None:
                records.append(rec)
                c = _counts(conn, doc_id)
                title = rec["document"].get("title")
                print(f"  exported {title!r}: {c['sentences']} sentences, "
                      f"{c['provenance']} provenance events", file=sys.stderr)

        payload = {"count": len(records), "documents": records}
        indent = args.indent or None
        text = json.dumps(payload, default=_json_default, ensure_ascii=False,
                          indent=indent)

        if args.output:
            with open(args.output, "w", encoding="utf-8") as fh:
                fh.write(text)
            print(f"Wrote {len(records)} document(s) to {args.output} "
                  f"({len(text):,} bytes)", file=sys.stderr)
        else:
            sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
