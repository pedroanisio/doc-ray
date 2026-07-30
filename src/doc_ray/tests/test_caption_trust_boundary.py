"""BL-200: where caption text is allowed to go, and where it is not.

Captions are the one Tier-3 output whose text is shown to a human as a claim
about what an image contains. That is an *assertion* path, not a ranking one —
so the human verification gate (PALS's Law) is load-bearing and cannot be traded
for a use-bounding argument the way ``ports/text_encoder.py`` does for
embeddings.

These tests pin the two halves of that boundary:

* only ``status='committed'`` caption text is ever readable, so an unreviewed
  proposal can never reach the reader; and
* no retrieval or ranking path reads ``visual_caption.caption``, so the gate
  cannot be quietly bypassed by indexing the text instead of displaying it.

If a future change wants captions in search, it must change these tests
deliberately — which is the point.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_VISUAL_READ = _ROOT / "src" / "doc_ray" / "adapters" / "persistence_visual_read.py"

#: Read paths that rank or retrieve. None of them may select caption text.
_RANKING_READERS = ("cross_modal_image_search",)


def _function_body(source: str, name: str) -> str:
    """Return the source of ``async def <name>`` up to the next top-level def."""
    start = source.index(f"async def {name}(")
    rest = source[start + 1 :]
    end = rest.find("\nasync def ")
    return rest if end == -1 else rest[:end]


def test_only_committed_caption_text_is_readable() -> None:
    """The reader's caption query is filtered to committed rows, in SQL."""
    body = _function_body(_VISUAL_READ.read_text(encoding="utf-8"), "list_visual_captions")
    assert "vc.caption" in body, "the reader query must be the one that selects caption text"
    assert re.search(r"vc\.status\s*=\s*'committed'", body), (
        "caption text must be filtered to status='committed' in SQL — a Python-side "
        "filter would expose unreviewed proposals to any caller that skips it"
    )


def test_no_ranking_path_reads_caption_text() -> None:
    """Ranking reads image vectors, never the untrusted caption string.

    Indexing caption text would let unverified model output influence results
    without ever passing the human gate — the same bypass as displaying it.
    """
    source = _VISUAL_READ.read_text(encoding="utf-8")
    for name in _RANKING_READERS:
        body = _function_body(source, name)
        assert "caption" not in body, (
            f"{name} must not read caption text; ranking is served by image_embedding. "
            "Putting captions in a ranking path bypasses the PALS gate."
        )


def test_the_assertion_path_is_the_reader_and_it_labels_provenance() -> None:
    """The single surface that shows caption text names its model and score.

    An assertion presented without its provenance is indistinguishable from a
    fact the system verified itself.
    """
    template = (_ROOT / "webapp" / "templates" / "document.html").read_text(encoding="utf-8")
    lines = template.splitlines()
    hits = [i for i, line in enumerate(lines) if "image.caption.caption" in line]
    assert len(hits) == 1, "caption text should surface on exactly one reader surface"
    # Provenance sits alongside the quote rather than on the same source line, so
    # assert over the surrounding block.
    block = "\n".join(lines[hits[0] : hits[0] + 8])
    assert "Model proposal" in block, (
        "the reader must mark the text as a MODEL proposal, never as doc-ray's "
        "own assertion — captions publish on write and are verified by retraction"
    )
    assert "image.caption.model" in block, "the originating model must be shown"
    assert "groundingScore" in block, "the grounding score must be shown alongside the claim"


def test_the_reader_offers_a_retraction_for_every_published_caption() -> None:
    """Publishing on write is only safe if withdrawing is always one click away."""
    template = (_ROOT / "webapp" / "templates" / "document.html").read_text(encoding="utf-8")
    assert 'data-testid="retract-form"' in template
    assert "retract_caption" in template, "the form must post to the retraction route"
    assert 'name="reason"' in template, "a retraction must capture why"
    assert "required" in template, "the reason must not be optional"
