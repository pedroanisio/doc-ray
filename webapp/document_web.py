"""Single-document routes: reader page, reprocess controls, metadata, streaming.

Split out of ``webapp/app.py`` (D-015). Query/GraphQL helpers are resolved
through the ``webapp.app`` module object at call time so the test suite's
monkeypatching of ``q``/``q1``/``gql``/``render_template`` keeps reaching the
routes that moved here.
"""

from __future__ import annotations

from types import ModuleType

from flask import abort, current_app, redirect, request, url_for

try:
    from webapp import document_assets_web
    from webapp.collections_web import available_manual_collections
    from webapp.document_metadata import submit_metadata_update
    from webapp.overlay_web import original_markup, original_overlay
    from webapp.previews import document_preview
    from webapp.reader_blocks import attach_page_blocks, latest_annotation_state
    from webapp.reprocess_web import (
        _REPROCESS_CONFIG,
        _step_applies_to_status,
        active_reprocess_jobs,
        reprocess_steps,
    )
    from webapp.visual_web import document_visual_model
except ModuleNotFoundError:  # pragma: no cover - direct ``python webapp/app.py`` path.
    import document_assets_web
    from collections_web import available_manual_collections
    from document_metadata import submit_metadata_update
    from overlay_web import original_markup, original_overlay
    from previews import document_preview
    from reader_blocks import attach_page_blocks, latest_annotation_state
    from reprocess_web import (
        _REPROCESS_CONFIG,
        _step_applies_to_status,
        active_reprocess_jobs,
        reprocess_steps,
    )
    from visual_web import document_visual_model

_core: ModuleType

# Reading options are resolved server-side and travel in the URL: a reading
# setup is then shareable, survives a page turn, and needs no client-side
# restore that would flash the default layout first. Each option falls back to
# its first value, so an unknown or hand-edited parameter degrades instead of
# raising.
READER_LAYOUTS = ("scroll", "spread")
READER_SIZES = ("comfortable", "compact", "large")
READER_FACES = ("sans", "serif")
# Parameters a reader link carries forward. Transient ones (metadata_queued,
# errors) are deliberately absent — they belong to the request that set them.
READER_PARAMS = ("view", "page", "annotate", "overlay", "layout", "size", "face")
# The spread paginates by sentences, not by source page. A PDF page routinely
# holds little or no extracted text — front matter, plates, "intentionally left
# blank" — and a fixed-height spread of such a page renders as a blank screen.
# Sentence-run pagination guarantees every spread has content to lay out.
SPREAD_PER_PAGE = 60


def _reader_option(name: str, allowed: tuple[str, ...]) -> str:
    """Read one reading option from the query string, falling back to its default."""
    value = request.args.get(name, allowed[0])
    return value if value in allowed else allowed[0]


def reader_url(doc_id: str, **overrides: object) -> str:
    """Build a reader URL that carries the current reading options forward.

    Keyword arguments override single parameters; passing ``None`` drops one
    (``view=None`` returns to the paginated text view).
    """
    args = {name: request.args[name] for name in READER_PARAMS if name in request.args}
    for name, value in overrides.items():
        if value is None:
            args.pop(name, None)
        else:
            args[name] = value
    return url_for("document", doc_id=doc_id, **args)


def _paginate_sentences(
    doc,
    doc_id,
    page,
    page_numbers,
    n_sent,
    full_view,
    spread,
    per_page,
    sentence_sql,
    sentence_base_sql,
):
    """Select the sentences for one reader page and report (rows, page, pages).

    Three pagination modes: the whole document (full_view), fixed runs of
    sentences (the spread — see SPREAD_PER_PAGE for why it does not page by
    source page), and per-source-page for the default text reader (falling back
    to sentence chunks when the document carries no page geometry).
    """
    if full_view:
        return _core.q(sentence_sql, (str(doc_id),)), 1, 1
    if spread:
        pages = max(1, -(-n_sent // SPREAD_PER_PAGE))
        page = min(page, pages)
        rows = _core.q(
            f"{sentence_sql} LIMIT %s OFFSET %s",
            (str(doc_id), SPREAD_PER_PAGE, (page - 1) * SPREAD_PER_PAGE),
        )
        return rows, page, pages
    if page_numbers:
        pages = max(page_numbers + [int(doc.get("page_count") or 0)])
        page = min(page, pages)
        rows = _core.q(
            f"{sentence_base_sql} AND sp.page_number = %s ORDER BY sen.ordinal",
            (str(doc_id), page),
        )
        return rows, page, pages
    rows = _core.q(
        f"{sentence_sql} LIMIT %s OFFSET %s",
        (str(doc_id), per_page, (page - 1) * per_page),
    )
    return rows, page, max(1, -(-n_sent // per_page))


def document(doc_id):
    """Render a single document with metadata, POS stats, and annotated sentences."""
    doc = _core.q1("SELECT * FROM documents WHERE id = %s", (str(doc_id),))
    if not doc:
        abort(404, "Document not found.")

    authors = _core.q(
        "SELECT a.name FROM document_authors da JOIN authors a ON a.id = da.author_id "
        "WHERE da.document_id = %s ORDER BY da.position",
        (str(doc_id),),
    )
    tags = _core.q(
        "SELECT t.tag FROM document_tags dt JOIN tags t ON t.id = dt.tag_id "
        "WHERE dt.document_id = %s ORDER BY t.tag",
        (str(doc_id),),
    )
    # POS distribution + top verbs are document-level stats shown in the reader
    # sidebar on every page. They are read from the precomputed document_stats
    # rollup (migration 098) — as JSONB arrays of {pos,n} / {uid,lemma,n} — instead
    # of aggregating the whole document's lexeme_occurrences live on every page
    # navigation (measured ~740 ms + ~464 ms for a 1250-page document). The row is
    # RLS-scoped by document_id, so it is present iff the caller can read the doc.
    stats = _core.q1(
        "SELECT pos_counts, top_verbs FROM document_stats WHERE document_id = %s",
        (str(doc_id),),
    )
    pos = (stats or {}).get("pos_counts") or []
    top_verbs = (stats or {}).get("top_verbs") or []
    # Prism Tier-1: image-class distribution (RLS-scoped via the docray_api role).
    visual = _core.q(
        """
        SELECT f.image_class, count(*) n
        FROM image_tier1_features f
        WHERE f.document_id = %s
        GROUP BY f.image_class ORDER BY n DESC, f.image_class
        """,
        (str(doc_id),),
    )
    per_page = 40
    page = max(1, request.args.get("page", 1, type=int))
    view = request.args.get("view")
    original_view = view == "original"
    images_view = view == "images"
    full_view = view == "all"
    overlay_mode = request.args.get("overlay", "grammar")
    if overlay_mode not in {"grammar", "entities"}:
        overlay_mode = "grammar"
    # Default annotated/plain from the reader's saved preference; the explicit
    # ?annotate= query param always wins when present.
    default_annotate = "1" if _core.current_preferences().annotated_by_default else "0"
    annotate = request.args.get("annotate", default_annotate) != "0"
    layout = _reader_option("layout", READER_LAYOUTS)
    size = _reader_option("size", READER_SIZES)
    face = _reader_option("face", READER_FACES)
    # "spread" is the shared reading intent: two facing pages, full-viewport,
    # turned not scrolled. Text builds it from CSS columns over a run of
    # sentences; the original view builds it from two rendered page images.
    spread = layout == "spread" and not original_view and not images_view
    original_spread = layout == "spread" and original_view
    n_sent = _core.q1("SELECT count(*) n FROM sentences WHERE document_id = %s", (str(doc_id),))[
        "n"
    ]
    sentence_base_sql = (
        "SELECT sen.ordinal, sen.text, sp.char_start AS ss, sp.char_end AS se, "
        "sp.page_number "
        "FROM sentences sen JOIN spans sp ON sp.id = sen.span_id "
        "WHERE sen.document_id = %s"
    )
    sentence_sql = f"{sentence_base_sql} ORDER BY sen.ordinal"
    page_rows = []
    page_numbers: list[int] = []
    if not full_view and not spread and not images_view:
        page_rows = _core.q(
            """
            SELECT page_number
            FROM (
                SELECT DISTINCT sp.page_number
                FROM sentences sen JOIN spans sp ON sp.id = sen.span_id
                WHERE sen.document_id = %s AND sp.page_number IS NOT NULL
                UNION
                SELECT dp.page_number
                FROM document_pages dp
                WHERE dp.document_id = %s
            ) pages_with_geometry
            ORDER BY page_number
            """,
            (str(doc_id), str(doc_id)),
        )
        page_numbers = [int(row["page_number"]) for row in page_rows]

    if images_view:
        sentences, page, pages = [], 1, 1
    else:
        sentences, page, pages = _paginate_sentences(
            doc,
            doc_id,
            page,
            page_numbers,
            n_sent,
            full_view,
            spread,
            per_page,
            sentence_sql,
            sentence_base_sql,
        )

    # Sentence blocks are rendered only in the reading views (annotated/plain),
    # never in the original-image overlay view — so skip the per-page block
    # annotation there. It re-reads the page's occurrences (measured ~1.7 s for a
    # 1250-page PDF) purely to build markup the original view never shows.
    if not original_view and not images_view:
        attach_page_blocks(_core.q, _core.q1, sentences, doc_id, annotate=annotate)
    overlay = None
    overlay_facing = None
    if original_view:
        overlay = (
            original_overlay(doc, doc_id, page, page_numbers, overlay_mode)
            if doc.get("doc_type") == "pdf"
            else original_markup(doc, doc_id, overlay_mode)
        )
        # Facing page: the right leaf of an original-view spread. Only PDFs page
        # image-by-image, and only when there is a next page to show.
        if original_spread and overlay.get("kind") == "pdf" and page < (pages or 1):
            overlay_facing = original_overlay(doc, doc_id, page + 1, page_numbers, overlay_mode)

    visual_gallery = document_visual_model(_core, str(doc_id)) if images_view else None

    # The two text layouts count pages differently (a source page vs a run of
    # sentences), so each layout switch carries the other's equivalent of where
    # the reader is now instead of dropping them at page 1. The original view
    # counts source pages in both layouts, so its position is unchanged.
    first_ordinal = int((sentences[0] if sentences else {}).get("ordinal") or 0)
    first_source_page = next((int(s["page_number"]) for s in sentences if s.get("page_number")), 0)
    if original_view:
        scroll_page = spread_page = page
    else:
        scroll_page = (first_source_page or 1) if spread else page
        spread_page = page if spread else first_ordinal // SPREAD_PER_PAGE + 1

    metadata_history = _core.q(
        "SELECT field, old_value, new_value, changed_at, actor "
        "FROM document_metadata_history WHERE document_id = %s ORDER BY id DESC",
        (str(doc_id),),
    )
    metadata_suggestions = _core.q(
        "SELECT id, field, current_value, suggested_value, confidence, reason, source, status "
        "FROM document_metadata_suggestion WHERE document_id = %s "
        "ORDER BY status, confidence DESC, field, suggested_value",
        (str(doc_id),),
    )
    member_lenses = _core.q(
        """
        SELECT c.id, c.name
        FROM document_collections c
        JOIN document_collection_members m ON m.collection_id = c.id
        WHERE m.document_id = %s AND c.is_scope
        ORDER BY c.name
        """,
        (str(doc_id),),
    )
    for suggestion in metadata_suggestions:
        confidence = float(suggestion.get("confidence") or 0)
        if confidence >= 0.85:
            suggestion["confidence_label"] = "High confidence"
        elif confidence >= 0.75:
            suggestion["confidence_label"] = "Medium confidence"
        else:
            suggestion["confidence_label"] = "Low confidence"
    return _core.render_template(
        "document.html",
        doc=doc,
        authors=[a["name"] for a in authors],
        tags=[t["tag"] for t in tags],
        pos=pos,
        top_verbs=top_verbs,
        visual=visual,
        preview=document_preview(doc),
        manual_collections=available_manual_collections(doc_id),
        sentences=sentences,
        page=page,
        pages=pages,
        full_view=full_view,
        original_view=original_view,
        images_view=images_view,
        visual_gallery=visual_gallery,
        overlay=overlay,
        overlay_facing=overlay_facing,
        original_spread=original_spread,
        overlay_mode=overlay_mode,
        n_sent=n_sent,
        annotate=annotate,
        layout=layout,
        size=size,
        face=face,
        scroll_page=scroll_page,
        spread_page=spread_page,
        reader_url=lambda **overrides: reader_url(str(doc_id), **overrides),
        annotation_state=latest_annotation_state(_core.q, doc_id),
        metadata_history=metadata_history,
        metadata_suggestions=metadata_suggestions,
        member_lenses=member_lenses,
        lens_notice=request.args.get("lens_notice"),
        lens_error=request.args.get("lens_error"),
        metadata_error=request.args.get("metadata_error"),
        metadata_queued=request.args.get("metadata_queued"),
        suggestions_error=request.args.get("suggestions_error"),
        suggestions_queued=request.args.get("suggestions_queued"),
        reprocess_steps=reprocess_steps(_core.q, doc, str(doc_id)),
        reprocess_queued=request.args.get("reprocess_queued"),
        reprocess_error=request.args.get("reprocess_error"),
    )


def document_reprocess(doc_id):
    """Queue one document pipeline step again through the GraphQL API."""
    doc = _core.q1("SELECT * FROM documents WHERE id = %s", (str(doc_id),))
    if doc is None:
        abort(404, "Document not found.")
    step = request.form.get("step", "")
    cfg = _REPROCESS_CONFIG.get(step)
    if cfg is None:
        return redirect(url_for("document", doc_id=doc_id, reprocess_error="unknown step"))
    if cfg["pdf_only"] and str(doc.get("doc_type") or "").lower() != "pdf":
        return redirect(
            url_for("document", doc_id=doc_id, reprocess_error="re-extract is PDF-only")
        )
    if not _step_applies_to_status(cfg, doc):
        return redirect(
            url_for(
                "document",
                doc_id=doc_id,
                reprocess_error=(
                    f"{cfg['label']} does not apply to a {doc.get('status') or 'unknown'} document"
                ),
            )
        )
    active = active_reprocess_jobs(_core.q, str(doc_id)).get(step)
    if active is not None:
        return redirect(
            url_for(
                "document",
                doc_id=doc_id,
                reprocess_error=f"{cfg['label']} is already {active['status']}",
            )
        )
    try:
        _core.gql(cfg["mutation"], {"id": str(doc_id)})
    except Exception as exc:
        current_app.logger.exception("document reprocess action failed")
        return redirect(url_for("document", doc_id=doc_id, reprocess_error=str(exc)))
    return redirect(url_for("document", doc_id=doc_id, reprocess_queued=step))


def document_metadata_suggestions_request(doc_id):
    """Queue verified document-metadata suggestions for human review."""
    try:
        _core.gql(
            """
            mutation SuggestDocumentMetadata($id: ID!) {
              suggestDocumentMetadata(documentId: $id) { id }
            }
            """,
            {"id": str(doc_id)},
        )
    except Exception as exc:
        return redirect(url_for("document", doc_id=doc_id, suggestions_error=str(exc)))
    return redirect(url_for("document", doc_id=doc_id, suggestions_queued=1))


def document_metadata_update(doc_id):
    """Submit a title/description/tags edit via the GraphQL mutation (BL-058).

    The edit is enqueued, not applied synchronously (D-004/D-005: writes never
    happen in the request) — the worker typically catches up within
    WORKER_POLL_SECONDS (5s by default); flag that on the redirect rather than
    let the reloaded page look like the edit silently did nothing.
    """
    error = submit_metadata_update(_core.gql, doc_id, request.form)
    if error is not None:
        return redirect(url_for("document", doc_id=doc_id, metadata_error=error))
    return redirect(url_for("document", doc_id=doc_id, metadata_queued=1))


def register(app, core: ModuleType) -> None:
    """Attach the single-document routes to *app*, late-binding *core* (D-015 split)."""
    global _core
    _core = core
    app.add_url_rule("/doc/<uuid:doc_id>", "document", document)
    app.add_url_rule(
        "/doc/<uuid:doc_id>/reprocess", "document_reprocess", document_reprocess, methods=["POST"]
    )
    app.add_url_rule(
        "/doc/<uuid:doc_id>/metadata-suggestions",
        "document_metadata_suggestions_request",
        document_metadata_suggestions_request,
        methods=["POST"],
    )
    app.add_url_rule(
        "/doc/<uuid:doc_id>/metadata",
        "document_metadata_update",
        document_metadata_update,
        methods=["POST"],
    )
    # Original-asset streaming routes live in a sibling module (D-015); they proxy
    # through the API's HTTP boundary and need no ``core`` late-binding.
    document_assets_web.register(app)
