"""Keyword and semantic corpus-search routes.

The keyword path preserves the webapp's indexed, RLS-scoped SQL read. Semantic
search crosses the GraphQL boundary so it uses the production encoder and the
same RLS-scoped API read as agents. Both return one presentation shape and link
every hit to a stable sentence-ordinal anchor in the reader.
"""

from __future__ import annotations

from types import ModuleType

from flask import current_app, request

try:
    from webapp.visual_web import image_search
except ModuleNotFoundError:  # pragma: no cover - direct ``python webapp/app.py`` path.
    from visual_web import image_search

_core: ModuleType

KEYWORD_PAGE_SIZE = 100
SEMANTIC_PAGE_SIZE = 25
SEMANTIC_RESULT_CAP = 100
IMAGE_PAGE_SIZE = 25
IMAGE_RESULT_CAP = 100

_SEMANTIC_QUERY = """
query SemanticSearch($query: String!, $limit: Int!) {
  semanticSearchSentences(query: $query, limit: $limit) {
    sentenceId documentId ordinal text similarity
  }
}
"""


def _keyword_results(term: str, page: int) -> tuple[list[dict], bool]:
    """Return the existing literal-substring results and pager state."""
    rows = _core.q(
        """
        SELECT d.id AS doc_id, d.title, s.ordinal, s.text
        FROM sentences s JOIN documents d ON d.id = s.document_id
        WHERE s.text ILIKE %(term)s
        ORDER BY d.title, s.ordinal LIMIT %(limit)s OFFSET %(offset)s
        """,
        {
            "term": f"%{term}%",
            "limit": KEYWORD_PAGE_SIZE,
            "offset": (page - 1) * KEYWORD_PAGE_SIZE,
        },
    )
    for row in rows:
        pre, found, post = row["text"].partition(term)
        row["pre"], row["hit"], row["post"] = pre, (term if found else ""), post
    return rows, len(rows) == KEYWORD_PAGE_SIZE


def _semantic_results(term: str, page: int) -> tuple[list[dict], bool]:
    """Return one bounded semantic page, enriched with readable document titles."""
    requested = min(page * SEMANTIC_PAGE_SIZE + 1, SEMANTIC_RESULT_CAP)
    data = _core.gql(_SEMANTIC_QUERY, {"query": term, "limit": requested})
    hits = list(data.get("semanticSearchSentences") or [])
    document_ids = sorted({str(hit["documentId"]) for hit in hits})
    titles = {}
    if document_ids:
        title_rows = _core.q(
            "SELECT id, title FROM documents WHERE id = ANY(%s::uuid[])",
            (document_ids,),
        )
        titles = {str(row["id"]): str(row["title"]) for row in title_rows}
    normalized = [
        {
            "doc_id": str(hit["documentId"]),
            "title": titles.get(str(hit["documentId"]), str(hit["documentId"])),
            "ordinal": int(hit["ordinal"]),
            "text": str(hit["text"]),
            "similarity": float(hit["similarity"]),
        }
        for hit in hits
    ]
    offset = (page - 1) * SEMANTIC_PAGE_SIZE
    page_rows = normalized[offset : offset + SEMANTIC_PAGE_SIZE]
    has_next = len(normalized) > offset + SEMANTIC_PAGE_SIZE
    return page_rows, has_next


def _image_results(term: str, page: int) -> tuple[list[dict], bool]:
    """Return one bounded page of cross-modal image results."""
    requested = min(page * IMAGE_PAGE_SIZE + 1, IMAGE_RESULT_CAP)
    hits = image_search(_core, term, limit=requested)
    offset = (page - 1) * IMAGE_PAGE_SIZE
    return hits[offset : offset + IMAGE_PAGE_SIZE], len(hits) > offset + IMAGE_PAGE_SIZE


def search():
    """Render keyword or learned semantic sentence search with honest fallback."""
    term = request.args.get("q", "", type=str).strip()
    mode = request.args.get("mode", "keyword", type=str)
    if mode not in {"keyword", "semantic", "image"}:
        mode = "keyword"
    page = max(1, request.args.get("page", 1, type=int))
    rows: list[dict] = []
    has_next = False
    notice = None
    result_mode = mode
    if term and mode == "image":
        try:
            rows, has_next = _image_results(term, page)
        except Exception as exc:  # noqa: BLE001 - visual stack failure is a rendered state
            current_app.logger.warning("visual search unavailable: %s", exc)
            notice = "Visual search unavailable — visual coverage is unknown."
    elif term and mode == "semantic":
        try:
            rows, has_next = _semantic_results(term, page)
            if not rows and page == 1:
                notice = "No semantic vectors matched — showing keyword matches."
                result_mode = "keyword"
                rows, has_next = _keyword_results(term, page)
        except Exception as exc:  # noqa: BLE001 - API failure is a user-visible fallback boundary
            current_app.logger.warning("semantic search unavailable: %s", exc)
            notice = "Semantic search unavailable — showing keyword matches."
            result_mode = "keyword"
            rows, has_next = _keyword_results(term, page)
    elif term:
        rows, has_next = _keyword_results(term, page)
    return _core.render_template(
        "search.html",
        term=term,
        mode=mode,
        result_mode=result_mode,
        rows=rows,
        page=page,
        has_next=has_next,
        notice=notice,
        semantic_cap=SEMANTIC_RESULT_CAP,
        image_cap=IMAGE_RESULT_CAP,
    )


def register(app, core: ModuleType) -> None:
    """Attach the search route to *app* with late-bound webapp helpers."""
    global _core
    _core = core
    app.add_url_rule("/search", "search", search)
