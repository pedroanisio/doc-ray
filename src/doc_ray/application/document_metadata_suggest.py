"""Verified document-level metadata suggestions (BL-061)."""

from __future__ import annotations

import math
import re
import uuid
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import psycopg

from doc_ray.adapters import document_metadata_suggestion_store as store
from doc_ray.application.verification import verify_model_output
from doc_ray.ports.verification import VerificationIssue, VerificationResult

_Conn = psycopg.Connection[dict[str, Any]]

_STOPWORDS = {
    "about",
    "after",
    "also",
    "from",
    "have",
    "into",
    "make",
    "makes",
    "that",
    "their",
    "this",
    "with",
}


class MetadataSuggestionVerifier:
    """Verify one metadata-suggestion payload before persistence."""

    name = "document-metadata-suggestion"

    def verify(
        self, output: Mapping[str, Any], *, context: Mapping[str, Any] | None = None
    ) -> VerificationResult:
        """Return whether *output* satisfies the BL-061 suggestion contract."""
        issues: list[VerificationIssue] = []
        field = output.get("field")
        if field not in {"title", "tags", "duplicate"}:
            issues.append(VerificationIssue("invalid-field", "field is not suggestible", "field"))
        suggested = output.get("suggested_value")
        if not isinstance(suggested, str) or not suggested.strip():
            issues.append(
                VerificationIssue(
                    "invalid-suggested-value",
                    "suggested_value must be non-empty",
                    "suggested_value",
                )
            )
        if field == "duplicate" and isinstance(suggested, str):
            try:
                uuid.UUID(suggested)
            except ValueError:
                issues.append(
                    VerificationIssue(
                        "invalid-duplicate-id",
                        "duplicate suggestions must point at a document uuid",
                        "suggested_value",
                    )
                )
        confidence = output.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0 <= float(confidence) <= 1
        ):
            issues.append(
                VerificationIssue(
                    "invalid-confidence", "confidence must be numeric in [0, 1]", "confidence"
                )
            )
        reason = output.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            issues.append(VerificationIssue("missing-reason", "reason must be non-empty", "reason"))
        return (
            VerificationResult.failed(self.name, issues)
            if issues
            else VerificationResult.passed(self.name)
        )


def _title_from_filename(filename: str | None) -> str | None:
    """Return a human title candidate from a stored original filename."""
    if not filename:
        return None
    stem = Path(filename).stem
    words = re.findall(r"[A-Za-z0-9]+", stem)
    if not words:
        return None
    return " ".join(word.capitalize() for word in words)


def _tag_candidates(text: str) -> list[str]:
    """Return deterministic tag candidates from document text."""
    words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", text)]
    counts = Counter(w for w in words if w not in _STOPWORDS)
    return [word for word, _n in counts.most_common(5)]


def _emit(conn: _Conn, document_id: str, payload: dict[str, Any]) -> None:
    """Verify and persist one suggestion payload."""
    verified = verify_model_output(payload, MetadataSuggestionVerifier())
    store.upsert_suggestion(
        conn,
        document_id=document_id,
        field=str(verified["field"]),
        current_value=(
            str(verified["current_value"]) if verified.get("current_value") is not None else None
        ),
        suggested_value=str(verified["suggested_value"]),
        confidence=float(verified["confidence"]),
        reason=str(verified["reason"]),
        source=str(verified.get("source") or "verified-deterministic"),
    )


def generate_suggestions(conn: _Conn, document_id: str) -> dict[str, int]:
    """Generate verified title/tag/duplicate suggestions for one document."""
    doc = conn.execute("SELECT * FROM documents WHERE id = %s", (document_id,)).fetchone()
    if doc is None:
        raise ValueError(f"no such document: {document_id}")
    content = conn.execute(
        "SELECT full_text FROM document_content WHERE document_id = %s", (document_id,)
    ).fetchone()
    text = str((content or {}).get("full_text") or "")
    written = 0

    title = str(doc.get("title") or "")
    title_candidate = _title_from_filename(doc.get("original_filename"))
    if title_candidate and title_candidate.casefold() != title.casefold():
        _emit(
            conn,
            document_id,
            {
                "field": "title",
                "current_value": title,
                "suggested_value": title_candidate,
                "confidence": 0.82,
                "reason": "Derived from the original filename.",
                "source": "verified-deterministic",
            },
        )
        written += 1

    existing_tags = {
        r["tag"]
        for r in conn.execute(
            "SELECT t.tag FROM document_tags dt JOIN tags t ON t.id = dt.tag_id "
            "WHERE dt.document_id = %s",
            (document_id,),
        ).fetchall()
    }
    for tag in _tag_candidates(text):
        if tag in existing_tags:
            continue
        _emit(
            conn,
            document_id,
            {
                "field": "tags",
                "current_value": ", ".join(sorted(existing_tags)),
                "suggested_value": tag,
                "confidence": 0.62,
                "reason": "Frequent content term in this document.",
                "source": "verified-deterministic",
            },
        )
        written += 1

    duplicate_title = title_candidate or title
    if duplicate_title:
        row = conn.execute(
            "SELECT id, title FROM documents "
            "WHERE id <> %s AND lower(title) = lower(%s) ORDER BY created_at DESC LIMIT 1",
            (document_id, duplicate_title),
        ).fetchone()
        if row is not None:
            _emit(
                conn,
                document_id,
                {
                    "field": "duplicate",
                    "current_value": title,
                    "suggested_value": str(row["id"]),
                    "confidence": 0.72,
                    "reason": f"Similar title already exists: {row['title']}.",
                    "source": "verified-deterministic",
                },
            )
            written += 1
    return {"suggested": written}
