"""Reader-page presentation helpers."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from markupsafe import Markup, escape

_POS_SIGIL_SPEC = (
    "NOUN:N PROPN:PN VERB:V ADJ:J ADV:R PRON:PR NUM:# DET:D "
    "ADP:P CCONJ:C SCONJ:S PART:T AUX:A PUNCT:. SYM:* X:X INTJ:I"
)
_POS_SIGILS = dict(item.split(":") for item in _POS_SIGIL_SPEC.split())


def _pos_sigil(pos: str) -> str:
    """Return the compact visible POS marker used as a non-color cue."""
    return _POS_SIGILS.get(pos, (pos[:2] or "?").upper())


def annotate_html(text: str, occs: list[dict]) -> Markup:
    """Overlay POS-tagged, clickable word spans onto a raw text slice."""
    out: list = []
    cur = 0
    for o in sorted(occs, key=lambda x: x["rel_start"]):
        a, b = o["rel_start"], o["rel_end"]
        if a < cur or a >= b or b > len(text):
            continue
        out.append(escape(text[cur:a]))
        word = text[a:b]
        pos = str(o["pos"])
        lemma = str(o["lemma"])
        out.append(
            Markup(
                '<a class="w p-{pos}" href="/lexeme/{uid}" data-t="{tip}" '
                'data-pos="{pos}" aria-label="{aria}">'
                '<span class="pos-sigil" aria-hidden="true">{cue}</span>{w}</a>'
            ).format(
                pos=pos,
                uid=o["uid"],
                tip=f"{lemma} · {pos}",
                aria=f"{word}, lemma {lemma}, part of speech {pos}",
                cue=_pos_sigil(pos),
                w=word,
            )
        )
        cur = b
    out.append(escape(text[cur:]))
    return Markup("").join(out)


# --- structural block splitting --------------------------------------------
#
# Extraction (``doc_ray.domain.pdf_layout.to_markdown``) assembles each
# document as Markdown blocks — headings (``# ``/``## ``/``### ``), bullet
# lists (``- `` lines joined by single newlines), and paragraphs — joined
# by blank lines (``\n\n``). Sentence segmentation only splits on terminal
# punctuation, so a run of blocks with no periods (heading, caption, bullet
# list, next heading, ...) can end up folded into a single stored sentence.
#
# The stored sentence *offsets* still point at the untouched raw text, so
# the original blank-line and single-newline boundaries survive there even
# though ``sentence.text`` itself has collapsed them to spaces. Recovering
# structure at render time means preferring those real boundaries whenever
# they're available (``body`` in annotated mode is a raw slice), and
# falling back to a marker-counting heuristic only when they aren't (plain
# mode's already-collapsed ``sentence.text``, or any block that turns out
# to be a single line).

_BLANK_LINE_RE = re.compile(r"\n{2,}")
_HEADING_PREFIX_RE = re.compile(r"^(#{1,3})\s+")
_LIST_LINE_PREFIX_RE = re.compile(r"^-\s+")
_LINE_RE = re.compile(r"[^\n]+")

_HEADING_RE = re.compile(r"(?<!\S)(#{1,3})\s+")
_LIST_MARKER_RE = re.compile(r"(?<!\S)-\s+(?=\S)")


def split_structural_blocks(text: str) -> list[dict[str, Any]]:
    """Split one sentence's text into heading/list/paragraph sub-blocks.

    Returns an ordered list of ``{"type", "start", "end"}`` dicts — offsets
    into ``text`` with any marker prefix already stripped. ``type`` is one
    of ``"h1"``/``"h2"``/``"h3"``/``"list"``/``"para"``. Plain sentences
    (the common case) come back as a single ``para`` block spanning the
    whole text, so callers don't need a separate no-op path.
    """
    if _BLANK_LINE_RE.search(text):
        blocks = _split_by_blank_lines(text)
    else:
        blocks = _split_by_inline_markers(text)
    return blocks or [{"type": "para", "start": 0, "end": len(text)}]


def _split_by_blank_lines(text: str) -> list[dict[str, Any]]:
    """Split on real blank-line block boundaries, the actual assembly seams."""
    bounds: list[tuple[int, int]] = []
    cursor = 0
    for m in _BLANK_LINE_RE.finditer(text):
        bounds.append((cursor, m.start()))
        cursor = m.end()
    bounds.append((cursor, len(text)))

    blocks: list[dict[str, Any]] = []
    for start, end in bounds:
        if text[start:end].strip():
            blocks.extend(_classify_chunk(text, start, end))
    return blocks


def _classify_chunk(text: str, start: int, end: int) -> list[dict[str, Any]]:
    """Classify one blank-line-delimited chunk as a heading, list, or paragraph."""
    chunk = text[start:end]
    lead = len(chunk) - len(chunk.lstrip())
    heading = _HEADING_PREFIX_RE.match(chunk[lead:])
    if heading:
        content_start = start + lead + heading.end()
        return [{"type": f"h{len(heading.group(1))}", "start": content_start, "end": end}]

    lines = [(m.start(), m.end()) for m in _LINE_RE.finditer(chunk)]
    if lines and all(_LIST_LINE_PREFIX_RE.match(chunk[a:b].lstrip()) for a, b in lines):
        items: list[dict[str, Any]] = []
        for a, b in lines:
            line = chunk[a:b]
            line_lead = len(line) - len(line.lstrip())
            marker = _LIST_LINE_PREFIX_RE.match(line[line_lead:])
            items.append(
                {"type": "list", "start": start + a + line_lead + marker.end(), "end": start + b}
            )
        return items

    return [{"type": "para", "start": start, "end": end}]


def _split_by_inline_markers(text: str) -> list[dict[str, Any]]:
    """Fall back to marker-counting when no blank-line boundaries survive.

    Used for plain-mode text (whitespace already collapsed to spaces) and
    for single-line chunks, where inline ``# ``/``- `` markers are the only
    remaining signal that several logical lines were joined together.
    """
    hits = list(_HEADING_RE.finditer(text))
    raw: list[tuple[str, int, int]] = []
    if not hits:
        raw.append(("para", 0, len(text)))
    else:
        if hits[0].start() > 0:
            raw.append(("para", 0, hits[0].start()))
        for i, m in enumerate(hits):
            seg_start = m.end()
            seg_end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
            raw.append((f"h{len(m.group(1))}", seg_start, seg_end))

    blocks: list[dict[str, Any]] = []
    for kind, start, end in raw:
        if not text[start:end].strip():
            continue
        if kind == "para":
            blocks.extend(_split_list_run(text, start, end))
        else:
            blocks.append({"type": kind, "start": start, "end": end})
    return blocks


def _split_list_run(text: str, start: int, end: int) -> list[dict[str, Any]]:
    """Split a plain segment into bullet items when it reads as a run of them.

    Requires either a marker right at the start of the segment, or at least
    three markers in it. A *pair* of " - " markers reads just as often as an
    em-dash aside bracketing a clause ("the result - as expected - was
    correct") as it does a two-item list with a lead-in label, so that case
    is left as prose rather than risk mangling the far more common aside.
    """
    segment = text[start:end]
    matches = list(_LIST_MARKER_RE.finditer(segment))
    leading_ws = len(segment) - len(segment.lstrip())
    starts_with_marker = bool(matches) and matches[0].start() == leading_ws
    if not matches or not (starts_with_marker or len(matches) >= 3):
        return [{"type": "para", "start": start, "end": end}]

    blocks: list[dict[str, Any]] = []
    lead_end = start + matches[0].start()
    if text[start:lead_end].strip():
        blocks.append({"type": "para", "start": start, "end": lead_end})
    for i, m in enumerate(matches):
        item_start = start + m.end()
        item_end = start + matches[i + 1].start() if i + 1 < len(matches) else end
        if text[item_start:item_end].strip():
            blocks.append({"type": "list", "start": item_start, "end": item_end})
    return blocks


def render_annotated_blocks(
    body: str, occs: list[dict], blocks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Render each structural block to POS-annotated HTML.

    ``occs`` offsets are relative to ``body`` (as produced for
    ``annotate_html``); each block gets only the occurrences that fall
    inside it, re-based to that block's own start.
    """
    rendered: list[dict[str, Any]] = []
    for b in blocks:
        start, end = b["start"], b["end"]
        inner = [
            {**o, "rel_start": o["rel_start"] - start, "rel_end": o["rel_end"] - start}
            for o in occs
            if o["rel_start"] >= start and o["rel_end"] <= end
        ]
        rendered.append({"type": b["type"], "html": annotate_html(body[start:end], inner)})
    return rendered


def render_plain_blocks(text: str, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Render each structural block to its plain-text slice (no annotation)."""
    return [{"type": b["type"], "text": text[b["start"] : b["end"]]} for b in blocks]


def attach_page_blocks(
    query: Callable[..., Sequence[Mapping[str, Any]]],
    query_one: Callable[..., Mapping[str, Any] | None],
    sentences: list[dict[str, Any]],
    doc_id: Any,
    *,
    annotate: bool,
) -> None:
    """Set ``sentence["blocks"]`` in place for every sentence in *sentences*.

    Sentence segmentation only splits on terminal punctuation, so a run of
    un-punctuated Markdown lines (heading, caption, bullet, next heading, ...)
    can be folded into a single stored sentence. The raw ``full_text`` slice
    spanning *sentences* still has the original blank-line/newline block
    boundaries that ``sentence.text`` collapsed away, so block-splitting
    always works from that raw slice rather than the lossy collapsed text.
    No-op for an empty *sentences*.
    """
    if not sentences:
        return
    lo = min(s["ss"] for s in sentences)
    hi = max(s["se"] for s in sentences)
    slice_row = query_one(
        "SELECT substring(full_text FROM %s FOR %s) AS t "
        "FROM document_content WHERE document_id = %s",
        (lo + 1, hi - lo, str(doc_id)),
    )
    page_text = (slice_row or {}).get("t") or ""
    occ = (
        query(
            """
            SELECT sp.char_start AS a, sp.char_end AS b, l.uid, l.lemma, l.pos
            FROM lexeme_occurrences o
            JOIN annotations an ON an.id = o.annotation_id
            JOIN spans sp       ON sp.id = o.span_id
            JOIN lexemes l      ON l.uid = o.lexeme_uid
            WHERE an.document_id = %s AND sp.char_start >= %s AND sp.char_end <= %s
            ORDER BY sp.char_start
            """,
            (str(doc_id), lo, hi),
        )
        if annotate
        else []
    )
    for s in sentences:
        body = page_text[s["ss"] - lo : s["se"] - lo]
        blocks = split_structural_blocks(body)
        if annotate:
            inside = [
                {
                    "rel_start": o["a"] - s["ss"],
                    "rel_end": o["b"] - s["ss"],
                    "uid": o["uid"],
                    "lemma": o["lemma"],
                    "pos": o["pos"],
                }
                for o in occ
                if o["a"] >= s["ss"] and o["b"] <= s["se"]
            ]
            s["blocks"] = render_annotated_blocks(body, inside, blocks)
        else:
            s["blocks"] = render_plain_blocks(body, blocks)


def latest_annotation_state(query: Callable[..., Sequence[Mapping[str, Any]]], doc_id: Any) -> str:
    """Return the newest annotation job status for a document, or ``absent``."""
    rows = query(
        """
        SELECT status
        FROM jobs
        WHERE document_id = %s AND kind = 'annotate'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (str(doc_id),),
    )
    return str(rows[0]["status"]) if rows else "absent"
