"""Heuristic genre classifier (SLP Block C, issue #6).

Reads **deterministic, citable** signals from a document's text and metadata and
emits a :class:`GenreClassification` — a genre, a confidence in ``[0, 1]``, the
method, and a human-readable rationale naming the signals that fired. No LLM, no
network, no model weights: the signals are dialogue density, first-person
narration, chapter headings (narrative structure) versus an academic apparatus
(abstract / methodology / references / citations / DOI), plus any genre hint in
the document metadata.

⚠ ARCHITECTURAL CONTRACT (PALS's LAW) — a machine genre is a GUESS, never a fact.
This classifier **never** emits ``status='confirmed'``: every machine result is
``status='pending'`` (proposal §2 invariant 2 — "AI proposes, verification
decides; it never asserts a guess"). :data:`CONFIRM_THRESHOLD` governs the *label
decision*, not an auto-confirm: at or above it the classifier commits to
``fiction``/``non_fiction``; below it it abstains to ``genre='unknown'``, the
conservative default (an unknown genre routes GLOBAL, a visible/repairable error
rather than a silent local fragmentation). Only a human confirms
(``method='human'``, see the GraphQL/MCP confirm path).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import psycopg

from doc_ray.domain.genre_policy import Genre
from doc_ray.domain.jobs import Job
from doc_ray.domain.progress import report_progress
from doc_ray.ports.object_store import ObjectStore

#: Confidence at or above which the classifier commits to a decided genre rather
#: than abstaining to ``unknown``. Named, not a magic number (issue #6, D-1). A
#: value that let an ambiguous document through as decided would be wrong
#: regardless of the number — the crafted fixtures in test_genre_classification.py
#: pin this.
CONFIRM_THRESHOLD = 0.75

#: The only method string this module emits. Human confirmation uses ``'human'``.
METHOD_HEURISTIC = "heuristic"

_STATUS_PENDING = "pending"

_DIALOGUE_RE = re.compile(r"[\"“”‘’']")
_CHAPTER_RE = re.compile(
    r"(?im)^\s*chapter\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|[ivxlc]+)\b"
)
# Singular first-person only: novels narrate in "I/my/me"; academic prose uses the
# editorial "we/our", which must NOT read as narration (it dragged papers below the
# threshold in testing).
_FIRST_PERSON_RE = re.compile(r"\bI\b|\bI['’](?:m|d|ve|ll)\b|\b[Mm]y\b|\b[Mm]e\b|\b[Mm]yself\b")
_WORD_RE = re.compile(r"\b\w+\b")
_REFERENCES_RE = re.compile(r"(?im)^\s*(?:references|bibliography|works\s+cited)\s*$")
_CITATION_RE = re.compile(r"\[\d+\]|\([A-Z][A-Za-z]+(?:\s+et\s+al\.)?,\s*\d{4}\)|\bet\s+al\.")
_ACADEMIC_SECTION_RE = re.compile(
    r"(?im)^\s*(?:\d+\.\s*)?(?:abstract|introduction|methodology|methods|results|"
    r"discussion|conclusion|acknowledgements)\s*$"
)
_DOI_RE = re.compile(r"(?i)\bdoi:\s*\S+")
_FICTION_META_RE = re.compile(r"(?i)\b(?:fiction|novel|short\s+stor|poetry|drama|fantasy|sci-?fi)")
_NONFICTION_META_RE = re.compile(
    r"(?i)\b(?:non-?fiction|textbook|manual|handbook|history|biography|"
    r"reference|paper|journal|thesis|report)"
)


@dataclass(frozen=True)
class GenreClassification:
    """A machine genre classification — advisory (``pending``) until a human confirms."""

    genre: Genre
    subtype: str | None
    confidence: float
    method: str
    rationale: str
    status: str


def _metadata_text(metadata: Mapping[str, object] | None) -> str:
    if not metadata:
        return ""
    parts = [str(metadata.get(k, "")) for k in ("subject", "keywords", "category", "genre")]
    return " ".join(parts)


def _fiction_signals(text: str, meta: str) -> list[tuple[str, float]]:
    """Return the fired (name, weight) fiction signals — deterministic and citable."""
    signals: list[tuple[str, float]] = []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if lines:
        dialogue_lines = sum(1 for ln in lines if _DIALOGUE_RE.search(ln))
        density = dialogue_lines / len(lines)
        if density >= 0.15:
            signals.append((f"dialogue_density={density:.2f}", 2.0))
    if _CHAPTER_RE.search(text):
        signals.append(("chapter_headings", 1.0))
    words = _WORD_RE.findall(text)
    if words:
        fp = len(_FIRST_PERSON_RE.findall(text)) / len(words)
        if fp >= 0.02:
            signals.append((f"first_person={fp:.3f}", 1.0))
    if _FICTION_META_RE.search(meta):
        signals.append(("metadata:fiction", 3.0))
    return signals


def _non_fiction_signals(text: str, meta: str) -> list[tuple[str, float]]:
    signals: list[tuple[str, float]] = []
    if _REFERENCES_RE.search(text):
        signals.append(("references_section", 2.0))
    if _CITATION_RE.search(text):
        signals.append(("citations", 2.0))
    if _ACADEMIC_SECTION_RE.search(text):
        signals.append(("academic_sections", 1.0))
    if _DOI_RE.search(text):
        signals.append(("doi", 1.0))
    if _NONFICTION_META_RE.search(meta):
        signals.append(("metadata:non_fiction", 3.0))
    return signals


def _subtype(genre: Genre, text: str, meta: str) -> str | None:
    if genre is Genre.FICTION:
        return "novel" if _CHAPTER_RE.search(text) else None
    if genre is Genre.NON_FICTION:
        if re.search(r"(?i)\bbiograph", meta):
            return "biography"
        if re.search(r"(?i)\b(?:manual|handbook)\b", meta):
            return "manual"
        if _REFERENCES_RE.search(text) or _CITATION_RE.search(text):
            return "paper"
        return None
    return None


def classify_genre(
    text: str, *, metadata: Mapping[str, object] | None = None
) -> GenreClassification:
    """Classify a document's genre from deterministic textual + metadata signals.

    Args:
        text: The document's extracted full text (or a representative head slice).
        metadata: Optional document metadata; ``subject``/``keywords``/``category``/
            ``genre`` values are scanned for an explicit genre hint.

    Returns:
        A :class:`GenreClassification` with ``status='pending'`` (a machine guess
        is never asserted). ``confidence`` is the normalized separation between the
        fiction and non-fiction evidence; below :data:`CONFIRM_THRESHOLD` the genre
        abstains to :attr:`Genre.UNKNOWN`.
    """
    meta = _metadata_text(metadata)
    fiction = _fiction_signals(text, meta)
    non_fiction = _non_fiction_signals(text, meta)
    f_score = sum(w for _, w in fiction)
    nf_score = sum(w for _, w in non_fiction)
    total = f_score + nf_score

    confidence = abs(f_score - nf_score) / total if total > 0 else 0.0
    fired = [name for name, _ in (*fiction, *non_fiction)]

    if confidence >= CONFIRM_THRESHOLD and f_score != nf_score:
        genre = Genre.FICTION if f_score > nf_score else Genre.NON_FICTION
    else:
        genre = Genre.UNKNOWN

    rationale = (
        f"signals={fired}; fiction={f_score:g}; non_fiction={nf_score:g}"
        if fired
        else "no genre signals detected"
    )
    return GenreClassification(
        genre=genre,
        subtype=_subtype(genre, text, meta),
        confidence=round(confidence, 4),
        method=METHOD_HEURISTIC,
        rationale=rationale,
        status=_STATUS_PENDING,
    )


def handle_classify_genre(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Job handler: classify a document's genre from its stored full text (Block C, #6).

    Cheap: no model, just :func:`classify_genre` over the document's text. Writes a
    ``pending`` machine guess to ``document_classification``; a human decision
    (``confirmed``/``rejected``) is never overwritten. Enqueued by the API's
    ``classifyDocument`` mutation — docray_api may read the row, but only the batch
    worker's docray_batch grant writes the machine guess. Returns a JSON-able result.
    """
    # Lazy: the store adapter imports GenreClassification from this module, so a
    # top-level import here would be circular.
    from doc_ray.adapters import document_classification_store

    if job.document_id is None:
        raise RuntimeError("classify_genre requires a document_id")
    report_progress(0, 1, "Classifying document genre")
    row = conn.execute(
        "SELECT full_text FROM document_content WHERE document_id = %s", (job.document_id,)
    ).fetchone()
    result = classify_genre((row["full_text"] if row else "") or "")
    written = document_classification_store.upsert_machine_classification(
        conn, job.document_id, result
    )
    return {
        "document_id": job.document_id,
        "genre": result.genre.value,
        "confidence": result.confidence,
        "status": result.status,
        "written": written,
    }
