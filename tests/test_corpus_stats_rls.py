"""Guard: the corpus stat cards must be RLS-scoped, never the global registry.

Regression for a multi-tenant confidentiality leak: a user with zero visible
documents saw the *global* lexeme count (18,683) on "The Corpus" page, because
the `lexemes` table has NO row-level security — 018_rbac.sql groups it with the
shared authors/tags registries. The other three cards (documents, sentences,
occurrences) are scoped and correctly read 0.

So the lexeme card must derive its total from the RLS-scoped
`lexeme_occurrences` (matching how the Lexicon page joins), never a bare
`count(*) FROM lexemes`. Verified live: as `docray_client` with a principal that
sees no documents, `count(DISTINCT o.lexeme_uid) FROM lexeme_occurrences` = 0
while `count(*) FROM lexemes` = 18683.
"""

from __future__ import annotations

from pathlib import Path

_CORPUS_WEB = Path(__file__).resolve().parents[1] / "webapp" / "corpus_web.py"


def test_corpus_lexeme_total_is_scoped_through_rls_occurrences():
    text = _CORPUS_WEB.read_text(encoding="utf-8")
    # The lexeme card counts distinct lexemes via the RLS-scoped occurrences.
    assert "count(DISTINCT o.lexeme_uid) FROM lexeme_occurrences" in text, (
        "corpus lexeme total must be scoped through lexeme_occurrences"
    )
    # It must NOT count the un-scoped global `lexemes` registry as a stat total —
    # that leaks every tenant's vocabulary to a user who can see no documents.
    assert "(SELECT count(*) FROM lexemes)" not in text, (
        "bare count(*) FROM lexemes leaks the global registry across tenants"
    )
