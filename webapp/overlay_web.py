"""Original-document overlay and markup-highlight builders (BL-082/BL-083/BL-086).

Split out of ``webapp/app.py`` (D-015). Query helpers are resolved through the
``webapp.app`` module object at call time (see :func:`bind`) so the test
suite's wholesale monkeypatching of ``webapp.app.q``/``q1`` keeps reaching the
code that moved here.
"""

from __future__ import annotations

from html import escape as html_escape
from types import ModuleType

from flask import url_for

_core: ModuleType


def bind(core: ModuleType) -> None:
    """Record the ``webapp.app`` module so helpers resolve late-bound ``q``/``q1``."""
    global _core
    _core = core


def overlay_box_style(box: dict, page_width: float, page_height: float) -> str:
    """Return percentage positioning for a source-space bbox."""
    left = 100.0 * float(box["bbox_x0"]) / page_width
    top = 100.0 * float(box["bbox_y0"]) / page_height
    width = 100.0 * (float(box["bbox_x1"]) - float(box["bbox_x0"])) / page_width
    height = 100.0 * (float(box["bbox_y1"]) - float(box["bbox_y0"])) / page_height
    return f"left:{left:.3f}%;top:{top:.3f}%;width:{width:.3f}%;height:{height:.3f}%"


def pos_overlay_boxes(doc_id, page: int, page_width: float, page_height: float) -> list[dict]:
    """Build POS/lexeme overlay boxes for one rendered PDF page.

    Drives from ``spans(document_id, page_number)`` (indexed) so only the page's
    spans are read, then joins their occurrences — not the reverse. The old
    occurrence-first plan materialised EVERY occurrence of the document (measured
    448k rows / 466k buffer hits / ~500 ms for one page of a 1250-page PDF) before
    filtering to the page; this returns the identical rows in ~12 ms.
    """
    boxes = _core.q(
        """
        SELECT l.uid, l.lemma, l.pos,
               sp.bbox_x0, sp.bbox_y0, sp.bbox_x1, sp.bbox_y1
        FROM spans sp
        JOIN lexeme_occurrences o ON o.span_id = sp.id
        JOIN annotations an       ON an.id = o.annotation_id AND an.document_id = %s
        JOIN lexemes l            ON l.uid = o.lexeme_uid
        WHERE sp.document_id = %s
          AND sp.page_number = %s
          AND sp.bbox_x0 IS NOT NULL
          AND sp.bbox_y0 IS NOT NULL
          AND sp.bbox_x1 IS NOT NULL
          AND sp.bbox_y1 IS NOT NULL
        ORDER BY sp.char_start
        """,
        (str(doc_id), str(doc_id), page),
    )
    return [
        {
            **box,
            "href": url_for("lexeme", uid=box["uid"]),
            "class_name": f"p-{box['pos']}",
            "style": overlay_box_style(box, page_width, page_height),
            "tip": f"{box['lemma']} · {box['pos']}",
            "aria_label": f"{box['lemma']}, part of speech {box['pos']}",
        }
        for box in boxes
    ]


def entity_overlay_boxes(doc_id, page: int, page_width: float, page_height: float) -> list[dict]:
    """Build entity/canonical overlay boxes for one rendered PDF page.

    Drives from ``spans(document_id, page_number)`` (indexed) so only the page's
    spans are read before joining mentions — the same fix as
    :func:`pos_overlay_boxes`. Reading the ``canonical`` registry here requires the
    ``docray_client`` SELECT grant added in migration 097 (its absence was a live
    500 on ``?overlay=entities``).
    """
    boxes = _core.q(
        """
        SELECT em.id AS mention_id, em.surface, em.entity_type,
               el.scheme, el.kb_id, c.preferred,
               sp.bbox_x0, sp.bbox_y0, sp.bbox_x1, sp.bbox_y1
        FROM spans sp
        JOIN entity_mention em ON em.span_id = sp.id
        JOIN annotations an    ON an.id = em.annotation_id AND an.document_id = %s
        LEFT JOIN entity_link el ON el.mention_id = em.id
        LEFT JOIN canonical c    ON c.id::text = el.kb_id
        WHERE sp.document_id = %s
          AND sp.page_number = %s
          AND sp.bbox_x0 IS NOT NULL
          AND sp.bbox_y0 IS NOT NULL
          AND sp.bbox_x1 IS NOT NULL
          AND sp.bbox_y1 IS NOT NULL
        ORDER BY sp.char_start
        """,
        (str(doc_id), str(doc_id), page),
    )
    rendered = []
    for box in boxes:
        name = box.get("preferred") or box.get("surface") or "entity"
        entity_type = box.get("entity_type") or "ENTITY"
        canonical = f" · {box['scheme']}:{box['kb_id']}" if box.get("kb_id") else ""
        rendered.append(
            {
                **box,
                "href": f"#entity-{box['mention_id']}",
                "class_name": f"entity-box e-{entity_type}",
                "style": overlay_box_style(box, page_width, page_height),
                "tip": f"{name} · {entity_type}{canonical}",
                "aria_label": f"{name}, entity type {entity_type}",
            }
        )
    return rendered


def markup_highlights(doc_id, mode: str) -> list[dict]:
    """Return text-range highlights for non-paginated markup originals."""
    if mode == "entities":
        rows = _core.q(
            """
            SELECT em.id AS mention_id, em.surface, em.entity_type,
                   el.scheme, el.kb_id, c.preferred
            FROM entity_mention em
            JOIN annotations an ON an.id = em.annotation_id
            JOIN spans sp ON sp.id = em.span_id
            LEFT JOIN entity_link el ON el.mention_id = em.id
            LEFT JOIN canonical c ON c.id::text = el.kb_id
            WHERE an.document_id = %s
            ORDER BY sp.char_start
            """,
            (str(doc_id),),
        )
        highlights = []
        for row in rows:
            name = row.get("preferred") or row.get("surface") or "entity"
            entity_type = row.get("entity_type") or "ENTITY"
            canonical = f" · {row['scheme']}:{row['kb_id']}" if row.get("kb_id") else ""
            highlights.append(
                {
                    "surface": row.get("surface") or name,
                    "href": f"#entity-{row['mention_id']}",
                    "class_name": f"w entity-box e-{entity_type}",
                    "tip": f"{name} · {entity_type}{canonical}",
                    "aria_label": f"{name}, entity type {entity_type}",
                }
            )
        return highlights
    # Surfaces are sliced in Python from one full_text fetch: substring() in
    # the row query detoasts the entire document text once per occurrence row
    # (measured 44.7 s for the largest live document's 314k occurrences).
    rows = _core.q(
        """
        SELECT sp.char_start, sp.char_end, l.uid, l.lemma, l.pos
        FROM lexeme_occurrences o
        JOIN annotations an      ON an.id = o.annotation_id
        JOIN spans sp            ON sp.id = o.span_id
        JOIN lexemes l           ON l.uid = o.lexeme_uid
        WHERE an.document_id = %s
        ORDER BY sp.char_start
        """,
        (str(doc_id),),
    )
    text_row = _core.q1(
        "SELECT full_text FROM document_content WHERE document_id = %s", (str(doc_id),)
    )
    full_text = str((text_row or {}).get("full_text") or "")
    return [
        {
            "surface": full_text[row["char_start"] : row["char_end"]],
            "href": url_for("lexeme", uid=row["uid"]),
            "class_name": f"w p-{row['pos']}",
            "tip": f"{row['lemma']} · {row['pos']}",
            "aria_label": f"{row['lemma']}, part of speech {row['pos']}",
        }
        for row in rows
    ]


#: How far past the cursor one highlight may search before being skipped.
#: Bounds the cost of a surface that never appears in the rendered html; the
#: forward cursor otherwise finds each occurrence near the previous one.
_MARKUP_SEARCH_WINDOW = 4000


def apply_markup_highlights(render_html: str, highlights: list[dict]) -> str:
    """Wrap each highlight's escaped surface in a sanitized anchor, in order.

    Highlights arrive in document (char_start) order and the rendered markup
    preserves the document's text order, so a single forward cursor visits the
    html exactly once. The old replace-from-the-top loop rescanned its own
    output per highlight — quadratic over hundreds of thousands of highlights,
    and a repeated surface matched inside an earlier anchor's attributes and
    corrupted it.
    """
    parts: list[str] = []
    cursor = 0
    for item in highlights:
        surface = str(item.get("surface") or "")
        if not surface:
            continue
        needle = html_escape(surface, quote=False)
        idx = render_html.find(needle, cursor, cursor + _MARKUP_SEARCH_WINDOW + len(needle))
        if idx < 0:
            continue
        parts.append(render_html[cursor:idx])
        parts.append(
            f'<a href="{html_escape(item["href"], quote=True)}" '
            f'class="{html_escape(item["class_name"], quote=True)}" '
            f'data-t="{html_escape(item["tip"], quote=True)}" '
            f'aria-label="{html_escape(item["aria_label"], quote=True)}">{needle}</a>'
        )
        cursor = idx + len(needle)
    parts.append(render_html[cursor:])
    return "".join(parts)


def original_markup(doc: dict, doc_id, mode: str = "grammar") -> dict:
    """Build the non-paginated original-markup overlay model."""
    mode = mode if mode in {"grammar", "entities"} else "grammar"
    if doc.get("doc_type") not in {"docx", "html", "epub"}:
        return {
            "kind": "markup",
            "available": False,
            "message": f"Original markup is not available for {doc.get('doc_type')} documents yet.",
        }
    row = _core.q1(
        "SELECT value FROM document_metadata WHERE document_id = %s AND key = 'render_html'",
        (str(doc_id),),
    )
    if not row or not row.get("value"):
        return {
            "kind": "markup",
            "available": False,
            "message": "Original markup is not available until this document is re-extracted.",
        }
    html = apply_markup_highlights(str(row["value"]), _core._markup_highlights(doc_id, mode))
    return {"kind": "markup", "available": True, "mode": mode, "html": html}


def original_overlay(
    doc: dict, doc_id, page: int, page_numbers: list[int], mode: str = "grammar"
) -> dict:
    """Build the PDF original-page overlay model for the document template."""
    mode = mode if mode in {"grammar", "entities"} else "grammar"
    if doc.get("doc_type") != "pdf":
        return {
            "available": False,
            "message": (
                f"Original overlay is not available for {doc.get('doc_type')} documents yet."
            ),
        }
    if not page_numbers:
        return {
            "available": False,
            "message": "Original overlay is not available until this PDF is re-extracted.",
        }
    meta_rows = _core.q(
        """
        SELECT page_width, page_height, has_text, text_run_count
        FROM document_pages
        WHERE document_id = %s AND page_number = %s
        """,
        (str(doc_id), page),
    )
    meta = meta_rows[0] if meta_rows else {}
    from_document_pages = bool(meta)
    if not meta:
        meta_rows = _core.q(
            """
            SELECT max(page_width) AS page_width, max(page_height) AS page_height
            FROM document_layout_runs
            WHERE document_id = %s AND page_number = %s
            """,
            (str(doc_id), page),
        )
        meta = meta_rows[0] if meta_rows else {}
    page_width = float(meta.get("page_width") or 0)
    page_height = float(meta.get("page_height") or 0)
    has_text = bool(meta.get("has_text", True))
    text_run_count = int(meta.get("text_run_count") or 0)
    text_free_page = from_document_pages and (not has_text or text_run_count == 0)
    if page_width <= 0 or page_height <= 0 or text_free_page:
        return {
            "kind": "pdf",
            "available": True,
            "image_src": url_for("document_page_image", doc_id=doc_id, page_number=page),
            "page_width": page_width if page_width > 0 else None,
            "page_height": page_height if page_height > 0 else None,
            "mode": mode,
            "boxes": [],
            "message": "No text detected on this page — nothing to overlay.",
        }
    boxes = (
        entity_overlay_boxes(doc_id, page, page_width, page_height)
        if mode == "entities"
        else pos_overlay_boxes(doc_id, page, page_width, page_height)
    )
    return {
        "kind": "pdf",
        "available": True,
        "image_src": url_for("document_page_image", doc_id=doc_id, page_number=page),
        "page_width": page_width,
        "page_height": page_height,
        "mode": mode,
        "boxes": boxes,
    }
