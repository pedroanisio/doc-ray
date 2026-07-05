"""Reader-page presentation helpers."""

from __future__ import annotations

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
