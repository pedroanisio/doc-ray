"""Corpus dashboard: gather every per-document artifact count into one report.

WHAT IS AND IS NOT COUNTED HERE, AND WHY
----------------------------------------
Per-document token and sentence counts come from ``document_stats``, not from
``tokens`` / ``sentences``. That is not a shortcut — the fact tables are 75.8M
and 2.5M rows, and D-024 exists because aggregating them per document is
O(table). The rollups are the supported read path.

``entity_mention`` is the one artifact family with no cheap per-document path:
it is keyed by ``span_id`` with no ``document_id`` index, so counting mentions
per document means joining the 89M-row ``spans`` table. Measured on the live
cluster, that query does not finish inside 90s. This report therefore counts
*linked* entities (``entity_link``, 7.1s) and lens projections (0.3s) per
document, and says so in the output rather than quietly presenting a different
number under the word "entities".

RLS: ``documents`` and every document-scoped table carry forced row-level
security (migration 106) and the application role holds no BYPASSRLS, so
counting as that role returns zero for everything — indistinguishable from an
empty corpus. This assumes ``docray_batch`` and refuses to run if the corpus is
invisible, so an unreadable database cannot be rendered as a finished one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import psycopg

#: Role holding BYPASSRLS, created by migration 106 for worker/CLI batch work.
BATCH_ROLE = "docray_batch"

DOCUMENTS_SQL = """
SELECT id::text, title, doc_type, status, language, page_count, char_count,
       byte_size, created_at
FROM documents
WHERE deleted_at IS NULL
ORDER BY created_at
"""

STATS_SQL = """
SELECT document_id::text, n_sentences, n_tokens, n_lexeme_occurrences
FROM document_stats
"""

LAYERS_SQL = "SELECT document_id::text, layer FROM annotations GROUP BY 1, 2"

#: Joins spans by primary key (1.1M lookups, ~11s live). The only viable path:
#: `embedding` is keyed by span_id and carries no document_id.
EMBEDDINGS_SQL = """
SELECT sp.document_id::text, e.model, count(*)
FROM embedding e JOIN spans sp ON sp.id = e.span_id
GROUP BY 1, 2
"""

IMAGES_SQL = "SELECT document_id::text, count(*) FROM document_image GROUP BY 1"
IMAGE_EMBEDDINGS_SQL = "SELECT document_id::text, count(*) FROM image_embedding GROUP BY 1"
VISION_SENTENCES_SQL = (
    "SELECT document_id::text, count(*) FROM sentence_vision_embedding GROUP BY 1"
)
CAPTIONS_SQL = "SELECT document_id::text, status, count(*) FROM visual_caption GROUP BY 1, 2"

#: `entity_link` has no document_id; the hop through entity_mention -> spans is
#: bounded by entity_link's 127k rows (~7s live), unlike mentions' 1.7M.
ENTITY_LINKS_SQL = """
SELECT sp.document_id::text, count(*)
FROM entity_link el
JOIN entity_mention em ON em.id = el.mention_id
JOIN spans sp ON sp.id = em.span_id
GROUP BY 1
"""

PROJECTIONS_SQL = """
SELECT document_id::text, count(*), count(DISTINCT lens_id)
FROM entity_projection GROUP BY 1
"""

CLASSES_SQL = "SELECT document_id::text, genre, subtype, status FROM document_classification"

CORPUS_SQL = "SELECT coalesce(max(n_distinct_lexemes), 0) FROM corpus_stats"


@dataclass(frozen=True)
class DocumentRow:
    """Every artifact doc-ray holds for one document, in one place."""

    document_id: str
    title: str
    doc_type: str
    status: str
    language: str | None
    page_count: int
    char_count: int
    byte_size: int
    created_at: str
    n_sentences: int = 0
    n_tokens: int = 0
    n_lexeme_occurrences: int = 0
    layers: frozenset[str] = frozenset()
    embeddings: int = 0
    embedding_models: tuple[str, ...] = ()
    images: int = 0
    image_embeddings: int = 0
    vision_sentence_embeddings: int = 0
    captions_committed: int = 0
    captions_pending: int = 0
    entity_links: int = 0
    projections: int = 0
    lenses: int = 0
    genre: str | None = None
    subtype: str | None = None
    classification_status: str | None = None


@dataclass(frozen=True)
class Coverage:
    """How much of the corpus holds one artifact family."""

    label: str
    n_present: int
    n_total: int
    note: str = ""

    @property
    def n_missing(self) -> int:
        """Documents lacking this artifact."""
        return self.n_total - self.n_present

    @property
    def pct(self) -> float:
        """Share of the corpus holding it, 0.0 for an empty corpus."""
        return (self.n_present / self.n_total * 100) if self.n_total else 0.0


@dataclass(frozen=True)
class Totals:
    """Corpus-wide sums."""

    n_documents: int
    n_sentences: int
    n_tokens: int
    n_lexeme_occurrences: int
    n_embeddings: int
    n_images: int
    n_image_embeddings: int
    n_captions: int
    n_entity_links: int
    n_projections: int
    n_distinct_lexemes: int
    n_bytes: int
    n_chars: int
    n_pages: int


@dataclass(frozen=True)
class Report:
    """Everything the renderer needs; holds no database handle."""

    rows: list[DocumentRow]
    totals: Totals
    coverage: list[Coverage]
    generated_at: str
    schema_version: str
    query_seconds: float
    slow_note: str = ""
    by_type: dict[str, int] = field(default_factory=dict)
    by_status: dict[str, int] = field(default_factory=dict)


#: (label, predicate, note) per artifact family, in pipeline order.
_STAGES: tuple[tuple[str, Any, str], ...] = (
    ("Text extracted", lambda r: r.char_count > 0, ""),
    ("Sentences", lambda r: r.n_sentences > 0, ""),
    ("Tokens", lambda r: r.n_tokens > 0, ""),
    ("NLP annotation layers", lambda r: bool(r.layers), ""),
    ("Semantic embeddings", lambda r: r.embeddings > 0, "sentence vectors for semantic search"),
    ("Linked entities", lambda r: r.entity_links > 0, "entity_link; raw mentions are not counted"),
    ("Lens projections", lambda r: r.projections > 0, ""),
    ("Classified", lambda r: r.genre is not None, ""),
    ("Images", lambda r: r.images > 0, "text-only documents legitimately have none"),
    ("Image embeddings", lambda r: r.image_embeddings > 0, ""),
    ("Committed captions", lambda r: r.captions_committed > 0, ""),
)


def build_rows(
    documents: list[dict[str, Any]],
    *,
    stats: dict[str, tuple[int, int, int]],
    layers: dict[str, set[str]],
    embeddings: dict[str, dict[str, int]],
    images: dict[str, tuple[int, int, int]],
    captions: dict[str, dict[str, int]],
    entity_links: dict[str, int],
    projections: dict[str, tuple[int, int]],
    classes: dict[str, tuple[str, str, str]],
) -> list[DocumentRow]:
    """Join every artifact map onto the document list.

    A document missing from a map is 0, never absent: a never-processed document
    is exactly what this report exists to surface.
    """
    rows: list[DocumentRow] = []
    for doc in documents:
        did = doc["document_id"]
        n_sent, n_tok, n_lex = stats.get(did, (0, 0, 0))
        by_model = embeddings.get(did, {})
        n_img, n_img_emb, n_vis_sent = images.get(did, (0, 0, 0))
        caps = captions.get(did, {})
        n_proj, n_lens = projections.get(did, (0, 0))
        genre, subtype, cls_status = classes.get(did, (None, None, None))
        rows.append(
            DocumentRow(
                document_id=did,
                title=doc["title"] or "(untitled)",
                doc_type=doc["doc_type"] or "",
                status=doc["status"] or "",
                language=doc["language"],
                page_count=doc["page_count"] or 0,
                char_count=doc["char_count"] or 0,
                byte_size=doc["byte_size"] or 0,
                created_at=str(doc["created_at"]),
                n_sentences=n_sent,
                n_tokens=n_tok,
                n_lexeme_occurrences=n_lex,
                layers=frozenset(layers.get(did, set())),
                embeddings=sum(by_model.values()),
                embedding_models=tuple(sorted(by_model)),
                images=n_img,
                image_embeddings=n_img_emb,
                vision_sentence_embeddings=n_vis_sent,
                captions_committed=caps.get("committed", 0),
                captions_pending=caps.get("review", 0),
                entity_links=entity_links.get(did, 0),
                projections=n_proj,
                lenses=n_lens,
                genre=genre,
                subtype=subtype,
                classification_status=cls_status,
            )
        )
    return rows


def coverage(rows: list[DocumentRow]) -> list[Coverage]:
    """Per-artifact corpus coverage, in pipeline order."""
    total = len(rows)
    return [
        Coverage(label=label, n_present=sum(1 for r in rows if pred(r)), n_total=total, note=note)
        for label, pred, note in _STAGES
    ]


def totals(rows: list[DocumentRow], n_distinct_lexemes: int) -> Totals:
    """Sum every countable artifact across the corpus."""

    def s(attr: str) -> int:
        return sum(getattr(r, attr) for r in rows)

    return Totals(
        n_documents=len(rows),
        n_sentences=s("n_sentences"),
        n_tokens=s("n_tokens"),
        n_lexeme_occurrences=s("n_lexeme_occurrences"),
        n_embeddings=s("embeddings"),
        n_images=s("images"),
        n_image_embeddings=s("image_embeddings"),
        n_captions=s("captions_committed"),
        n_entity_links=s("entity_links"),
        n_projections=s("projections"),
        n_distinct_lexemes=n_distinct_lexemes,
        n_bytes=s("byte_size"),
        n_chars=s("char_count"),
        n_pages=s("page_count"),
    )


def connect(database_url: str) -> Any:
    """Open a connection that can see the corpus, or fail loudly.

    Counting as a role that RLS hides everything from yields zeros that render
    as a finished-looking dashboard. Refuse instead.
    """
    conn = psycopg.connect(database_url, autocommit=True)
    conn.execute(f"SET ROLE {BATCH_ROLE}")
    row = conn.execute("SELECT count(*) FROM documents WHERE deleted_at IS NULL").fetchone()
    if not row or row[0] == 0:
        conn.close()
        raise RuntimeError(
            f"no documents visible after assuming {BATCH_ROLE}; refusing to render an "
            "unreadable corpus as an empty one"
        )
    return conn
