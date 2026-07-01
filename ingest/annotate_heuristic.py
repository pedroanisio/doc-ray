"""Dependency-light heuristic annotators for layers without a bundled model.

These are explicitly heuristic (regex/lexicon), tagged with a `heuristic/*`
tool so they never masquerade as model output. They populate:

  discourse_relation  <- PDTB-style connective regex          (layer 17)
  timex               <- date/year regex                      (layer 18, TIMEX3)
  sentiment           <- polarity-lexicon per sentence        (layer 19)

Usage:  python -m ingest.annotate_heuristic --all
"""

from __future__ import annotations

import argparse
import re
import sys

from . import db
from .config import Config
from .ulid import new_ulid_str

# --- discourse connectives -> PDTB sense -----------------------------------
_CONNECTIVES = {
    "because": "Contingency.Cause.Reason", "therefore": "Contingency.Cause.Result",
    "however": "Comparison.Contrast", "but": "Comparison.Contrast",
    "although": "Comparison.Concession", "though": "Comparison.Concession",
    "while": "Comparison.Contrast", "since": "Contingency.Cause.Reason",
    "thus": "Contingency.Cause.Result", "meanwhile": "Temporal.Synchrony",
    "nevertheless": "Comparison.Concession", "moreover": "Expansion.Conjunction",
}
_CONN_RE = re.compile(r"\b(" + "|".join(_CONNECTIVES) + r")\b", re.IGNORECASE)

# --- temporal expressions ---------------------------------------------------
_TIMEX_RE = re.compile(
    r"\b((?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+\d{4}|\b(?:19|20)\d{2}\b|\d{4}-\d{2}-\d{2})", re.IGNORECASE)

# --- sentiment lexicon ------------------------------------------------------
_POS = set("good great excellent optimistic success successful improve improved "
           "advantage benefit robust reliable clear strong confidence confident "
           "efficient effective progress gain positive best better".split())
_NEG = set("bad poor fail failed failure difficult hard risk risky loss lose "
           "problem problems error errors weak wrong negative worse worst "
           "expensive costly limitation degrade broken".split())
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def _annotation(conn, doc_id, layer, tool):
    conn.execute("DELETE FROM annotations WHERE document_id=%s AND layer=%s AND tool=%s",
                 (doc_id, layer, tool))
    aid = new_ulid_str()
    conn.execute("INSERT INTO annotations (id, document_id, layer, tool) VALUES (%s,%s,%s,%s)",
                 (aid, doc_id, layer, tool))
    return aid


def annotate_document(conn, doc_id: str) -> dict:
    row = conn.execute("SELECT full_text FROM document_content WHERE document_id=%s", (doc_id,)).fetchone()
    text = (row["full_text"] if row else None) or ""
    if not text.strip():
        return {}
    # sentence spans for arg/sentence attachment
    sents = conn.execute(
        "SELECT sp.id AS span_id, sp.char_start, sp.char_end FROM sentences s "
        "JOIN spans sp ON sp.id=s.span_id WHERE s.document_id=%s ORDER BY sp.char_start", (doc_id,)).fetchall()

    def sent_at(pos):
        for i, s in enumerate(sents):
            if s["char_start"] <= pos < s["char_end"]:
                return i
        return None

    counts = {"discourse": 0, "timex": 0, "sentiment": 0}

    # Collect all matches first, then register their spans in ONE bulk call.
    disc = [(m.start(), m.end(), _CONNECTIVES[m.group(1).lower()], sent_at(m.start()))
            for m in _CONN_RE.finditer(text)]
    disc = [(s, e, sense, i) for (s, e, sense, i) in disc if i not in (None, 0)]
    tx = {(m.start(), m.end()): m.group(1) for m in _TIMEX_RE.finditer(text)}
    span_map = db.ensure_spans(conn, doc_id,
                               [(s, e) for s, e, _, _ in disc] + list(tx))

    with conn.transaction():
        ann = _annotation(conn, doc_id, "discourse", "heuristic/connectives")
        for s, e, sense, i in disc:
            conn.execute(
                "INSERT INTO discourse_relation (id, annotation_id, connective_span_id, arg1_span_id, arg2_span_id, sense) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (new_ulid_str(), ann, span_map[(s, e)], sents[i - 1]["span_id"],
                 sents[i]["span_id"], sense))
            counts["discourse"] += 1

        ann = _annotation(conn, doc_id, "timex", "heuristic/dates")
        for (s, e), value in tx.items():
            conn.execute("INSERT INTO timex (id, annotation_id, span_id, timex_type, value) VALUES (%s,%s,%s,'DATE',%s)",
                         (new_ulid_str(), ann, span_map[(s, e)], value))
            counts["timex"] += 1

        # sentiment (per sentence)
        ann = _annotation(conn, doc_id, "sentiment", "heuristic/lexicon")
        for s in sents:
            seg = text[s["char_start"]:s["char_end"]].casefold()
            toks = _WORD.findall(seg)
            pos = sum(t in _POS for t in toks); neg = sum(t in _NEG for t in toks)
            if pos == neg:
                continue
            polarity = "POSITIVE" if pos > neg else "NEGATIVE"
            score = round((pos - neg) / max(1, pos + neg), 3)
            conn.execute("INSERT INTO sentiment (id, annotation_id, span_id, polarity, score) VALUES (%s,%s,%s,%s,%s)",
                         (new_ulid_str(), ann, s["span_id"], polarity, score))
            counts["sentiment"] += 1

    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Heuristic discourse/timex/sentiment annotation.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--id"); g.add_argument("--all", action="store_true")
    args = ap.parse_args(argv)
    with db.connect(Config.from_env()) as conn:
        ids = ([args.id] if args.id else
               [str(r["id"]) for r in conn.execute(
                   "SELECT id FROM documents WHERE status='extracted' ORDER BY created_at").fetchall()])
        tot = {"discourse": 0, "timex": 0, "sentiment": 0}
        for d in ids:
            c = annotate_document(conn, d)
            for k in tot:
                tot[k] += c.get(k, 0)
        print(f"Heuristics: {tot['discourse']} discourse, {tot['timex']} timex, {tot['sentiment']} sentiment")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
