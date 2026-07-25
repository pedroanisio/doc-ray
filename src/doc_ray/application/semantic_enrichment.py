"""Deterministic semantic enrichment for schema-backed NLP layers.

Discourse relations, temporal expressions, events, and lexicon sentiment only.
Sentence embeddings (NLP Layer 20) are NOT written here: after the hard cutover
from the hash-BoW placeholder, they are produced by the learned
``application.semantic_embed`` writer under the ``semantic_embed`` job. This
module stays a zero-dependency, deterministic path.
"""

from __future__ import annotations

import re
from typing import Any

from doc_ray.adapters import persistence as db
from doc_ray.adapters.persistence import DictConnection
from doc_ray.domain.progress import report_progress
from doc_ray.domain.ulid import new_ulid_str

DiscourseRow = tuple[str, str, str, str, str, str]
TimexRow = tuple[str, str, str, str, str | None]
EventRow = tuple[str, str, str, str]
SentimentRow = tuple[str, str, str, str, float]


TOOL_NAME = "doc-ray/semantic-heuristic"
TOOL_VERSION = "1.0"
LAYER_NAME = "semantic-heuristic"

_CONNECTIVES = {
    "because": "Contingency.Cause.Reason",
    "therefore": "Contingency.Cause.Result",
    "however": "Comparison.Contrast",
    "but": "Comparison.Contrast",
}
_TIMEX_RE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|\d{4}|"
    r"January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\b",
    re.IGNORECASE,
)
_POSITIVE = {"good", "great", "excellent", "success", "successful", "improved", "safe"}
_NEGATIVE = {"bad", "failed", "failure", "error", "unsafe", "broken", "worse"}


def _full_text(conn: DictConnection, doc_id: str) -> str:
    row = conn.execute(
        "SELECT full_text FROM document_content WHERE document_id=%s", (doc_id,)
    ).fetchone()
    return str(row["full_text"] if row else "")


def _sentences(conn: DictConnection, doc_id: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT s.id, s.ordinal, s.text, sp.char_start, sp.char_end
              FROM sentences s
              JOIN spans sp ON sp.id = s.span_id
             WHERE s.document_id=%s
             ORDER BY s.ordinal
            """,
            (doc_id,),
        ).fetchall()
    ]


def _tokens(conn: DictConnection, doc_id: str) -> list[dict[str, Any]]:
    # Surfaces are sliced in Python from one full_text fetch: substring() in
    # the row query detoasts the entire document text once per token row
    # (measured 76.5 s for a 98k-token document; 0.2 s sliced this way).
    full_text = _full_text(conn, doc_id)
    tokens = []
    for row in conn.execute(
        """
        SELECT t.id, t.upos, sp.id AS span_id, sp.char_start, sp.char_end
          FROM tokens t
          JOIN annotations a ON a.id = t.annotation_id
          JOIN spans sp ON sp.id = t.span_id
         WHERE a.document_id=%s
         ORDER BY t.ordinal, sp.char_start
        """,
        (doc_id,),
    ).fetchall():
        token = dict(row)
        token["surface"] = full_text[token["char_start"] : token["char_end"]]
        tokens.append(token)
    return tokens


def _discourse_rows(
    conn: DictConnection,
    doc_id: str,
    annotation_id: str,
    sentences: list[dict[str, Any]],
) -> list[DiscourseRow]:
    needed: list[tuple[int, int]] = []
    candidates: list[tuple[int, int, int, int, int, int, str]] = []
    for sent in sentences:
        text = str(sent["text"])
        lowered = text.casefold()
        for connective, sense in _CONNECTIVES.items():
            match = re.search(rf"\b{re.escape(connective)}\b", lowered)
            if match is None:
                continue
            base = int(sent["char_start"])
            conn_span = (base + match.start(), base + match.end())
            arg1 = (int(sent["char_start"]), base + match.start())
            arg2 = (base + match.end(), int(sent["char_end"]))
            if arg1[1] > arg1[0] and arg2[1] > arg2[0]:
                needed.extend([conn_span, arg1, arg2])
                candidates.append((*conn_span, *arg1, *arg2, sense))
            break
    span_map = db.ensure_spans(conn, doc_id, needed) if needed else {}
    rows: list[DiscourseRow] = []
    for cs, ce, a1s, a1e, a2s, a2e, sense in candidates:
        rows.append(
            (
                new_ulid_str(),
                annotation_id,
                str(span_map[(cs, ce)]),
                str(span_map[(a1s, a1e)]),
                str(span_map[(a2s, a2e)]),
                sense,
            )
        )
    return rows


def _timex_rows(
    conn: DictConnection, doc_id: str, annotation_id: str, full_text: str
) -> list[TimexRow]:
    spans = [(m.start(), m.end()) for m in _TIMEX_RE.finditer(full_text)]
    span_map = db.ensure_spans(conn, doc_id, spans) if spans else {}
    rows: list[TimexRow] = []
    for start, end in spans:
        value = full_text[start:end]
        rows.append((new_ulid_str(), annotation_id, str(span_map[(start, end)]), "DATE", value))
    return rows


def _event_rows(annotation_id: str, tokens: list[dict[str, Any]]) -> list[EventRow]:
    return [
        (new_ulid_str(), annotation_id, str(token["id"]), "OCCURRENCE")
        for token in tokens
        if token["upos"] == "VERB"
    ]


def _sentiment_rows(annotation_id: str, tokens: list[dict[str, Any]]) -> list[SentimentRow]:
    rows: list[SentimentRow] = []
    for token in tokens:
        surface = str(token["surface"]).casefold()
        if surface in _POSITIVE:
            rows.append((new_ulid_str(), annotation_id, str(token["span_id"]), "POSITIVE", 1.0))
        elif surface in _NEGATIVE:
            rows.append((new_ulid_str(), annotation_id, str(token["span_id"]), "NEGATIVE", -1.0))
    return rows


def annotate_document(conn: DictConnection, doc_id: str) -> dict[str, int]:
    """Populate deterministic discourse, temporal, event, and sentiment rows.

    Sentence embeddings are no longer written here — see the module docstring.
    """
    full_text = _full_text(conn, doc_id)
    sentences = _sentences(conn, doc_id)
    tokens = _tokens(conn, doc_id)
    annotation_id = new_ulid_str()

    with conn.transaction():
        conn.execute(
            "DELETE FROM annotations WHERE document_id=%s AND layer=%s AND tool=%s",
            (doc_id, LAYER_NAME, TOOL_NAME),
        )
        conn.execute(
            "INSERT INTO annotations (id, document_id, layer, tool, tool_version) "
            "VALUES (%s, %s, %s, %s, %s)",
            (annotation_id, doc_id, LAYER_NAME, TOOL_NAME, TOOL_VERSION),
        )
        report_progress(0, 4, "Deriving discourse relations")
        discourse = _discourse_rows(conn, doc_id, annotation_id, sentences)
        report_progress(1, 4, "Extracting temporal expressions")
        timexes = _timex_rows(conn, doc_id, annotation_id, full_text)
        report_progress(2, 4, "Classifying events")
        events = _event_rows(annotation_id, tokens)
        report_progress(3, 4, "Scoring sentiment")
        sentiments = _sentiment_rows(annotation_id, tokens)
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO discourse_relation
                    (id, annotation_id, connective_span_id, arg1_span_id, arg2_span_id, sense)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                discourse,
            )
            cur.executemany(
                "INSERT INTO timex (id, annotation_id, span_id, timex_type, value) "
                "VALUES (%s, %s, %s, %s, %s)",
                timexes,
            )
            cur.executemany(
                "INSERT INTO event (id, annotation_id, token_id, event_class) "
                "VALUES (%s, %s, %s, %s)",
                events,
            )
            cur.executemany(
                "INSERT INTO sentiment (id, annotation_id, span_id, polarity, score) "
                "VALUES (%s, %s, %s, %s, %s)",
                sentiments,
            )
        summary = {
            "discourse_relations": len(discourse),
            "timexes": len(timexes),
            "events": len(events),
            "sentiments": len(sentiments),
        }
        provenance_id = new_ulid_str()
        conn.execute(
            """
            INSERT INTO document_provenance
                (id, document_id, kind, tool, tool_version, actor, note)
            VALUES (%s, %s, 'annotated', %s, %s, %s, %s)
            """,
            (
                provenance_id,
                doc_id,
                TOOL_NAME,
                TOOL_VERSION,
                "doc_ray.application.semantic_enrichment",
                "Deterministic semantic enrichment layers committed with replacement semantics.",
            ),
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO provenance_params (event_id, key, value) VALUES (%s, %s, %s)",
                [(provenance_id, key, str(value)) for key, value in summary.items()],
            )
    return summary
