"""Re-annotate stored documents with spaCy — real POS + lemmas, per language.

Mirrors ``ingest.annotate`` exactly (same tokenizer, span registration, and DB
calls) but swaps the zero-dependency rule analyzer for a per-language spaCy one,
so the lexeme layer carries real part-of-speech tags instead of 'X'. Writes one
``layer='lexeme'`` run per document with ``tool='spacy/<model>'``; re-running
replaces that run for the document (idempotent).

    python -m ingest.reannotate --all
    python -m ingest.reannotate --id <uuid>
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import psycopg

from doc_ray.adapters import persistence as db
from doc_ray.config import Config
from doc_ray.domain import lexemes as lex
from doc_ray.domain.ulid import new_ulid_str

_MODELS = {"en": "en_core_web_sm", "pt": "pt_core_news_sm", "fr": "fr_core_news_sm"}


class SpacyAnalyzer:
    """spaCy lemma + UPOS over pre-tokenized words, in bounded batches.

    Words come from the same ``lexemes.iter_words`` tokenizer the rule analyzer
    uses, fed to spaCy pre-tokenized so our tokenization stays authoritative.
    Batching keeps a huge document from becoming one giant Doc.

    Attributes:
        name: Analyzer identifier, ``"spacy/<model>"``.
        version: Installed spaCy version.
    """

    def __init__(self, lang: str, batch: int = 256) -> None:
        """Load the spaCy model for *lang* (NER and parser excluded).

        Args:
            lang: Language code selecting the model in ``_MODELS``.
            batch: Maximum number of words fed to spaCy per ``Doc``.
        """
        import spacy
        from spacy.tokens import Doc

        model = _MODELS[lang]
        self._nlp = spacy.load(model, exclude=["ner", "parser"])
        self._Doc = Doc
        self._batch = batch
        self.name = f"spacy/{model}"
        self.version = spacy.__version__

    def analyze(self, surfaces: list[str]) -> list[tuple[str, str]]:
        """Analyze pre-tokenized *surfaces* into ``(lemma, upos)`` pairs.

        Args:
            surfaces: Word surfaces in reading order.

        Returns:
            One ``(lemma, upos)`` pair per surface, in the same order.
        """
        out: list[tuple[str, str]] = []
        for i in range(0, len(surfaces), self._batch):
            chunk = surfaces[i : i + self._batch]
            if not chunk:
                continue
            doc = self._Doc(self._nlp.vocab, words=list(chunk))
            for _, proc in self._nlp.pipeline:
                doc = proc(doc)
            out.extend((t.lemma_.casefold(), t.pos_) for t in doc)
        return out


def reannotate_document(
    conn: psycopg.Connection[Any],
    doc_id: str,
    cache: dict[Any, Any],
    lang_default: str,
) -> dict[str, Any] | None:
    """Re-annotate one document's lexeme layer with spaCy.

    Args:
        conn: Live database connection.
        doc_id: Id of the document to re-annotate.
        cache: Per-run lexeme memo shared across documents.
        lang_default: Fallback language when detection is weak or unsupported.

    Returns:
        A summary dict (``lang``, ``occ``, ``new``, ``tool``), or ``None`` when
        the document has no stored text.
    """
    row = conn.execute(
        "SELECT full_text FROM document_content WHERE document_id = %s", (doc_id,)
    ).fetchone()
    full_text = (row["full_text"] if row else None) or ""
    if not full_text:
        return None

    lang = lex.detect_language(full_text, default=lang_default)
    if lang not in _MODELS:  # no model for detected lang
        lang = lang_default
    analyzer = SpacyAnalyzer(lang)

    words = list(lex.iter_words(full_text))
    analyses = analyzer.analyze([w for w, _, _ in words])
    span_map = db.ensure_spans(conn, doc_id, [(s, e) for _, s, e in words])

    occurrences: list[tuple[str, str]] = []
    new_lexemes = 0
    for (_surface, start, end), (lemma, pos) in zip(words, analyses, strict=False):
        uid, created = db.get_or_create_lexeme(conn, cache, lemma, pos, lang)
        new_lexemes += created
        occurrences.append((span_map[(start, end)], uid))

    provenance = {
        "id": new_ulid_str(),
        "tool": analyzer.name,
        "tool_version": analyzer.version,
        "actor": "reannotate",
        "note": None,
        "params": {"occurrences": len(occurrences), "lexemes_new": new_lexemes, "lang": lang},
    }
    db.replace_annotation(
        conn,
        annotation_id=new_ulid_str(),
        document_id=doc_id,
        layer="lexeme",
        tool=analyzer.name,
        tool_version=analyzer.version,
        occurrences=occurrences,
        provenance=provenance,
    )
    conn.execute(
        "UPDATE documents SET language = %s WHERE id = %s AND language IS DISTINCT FROM %s",
        (lang, doc_id, lang),
    )
    return {"lang": lang, "occ": len(occurrences), "new": new_lexemes, "tool": analyzer.name}


def main(argv: list[str] | None = None) -> int:
    """Run the re-annotation CLI.

    Args:
        argv: Argument vector; defaults to ``sys.argv`` when ``None``.

    Returns:
        Process exit code (``0`` on success, ``1`` when nothing matched).
    """
    ap = argparse.ArgumentParser(description="Re-annotate documents with spaCy.")
    sel = ap.add_mutually_exclusive_group()
    sel.add_argument("--id")
    sel.add_argument("--all", action="store_true")
    ap.add_argument("--lang", default="en", help="Fallback language when detection is weak.")
    args = ap.parse_args(argv)

    cfg = Config.from_env()
    cache: dict[Any, Any] = {}
    with db.connect(cfg) as conn:
        if args.id:
            ids = [args.id]
        else:
            ids = [
                str(r["id"])
                for r in conn.execute(
                    "SELECT id FROM documents WHERE status='extracted' ORDER BY char_count"
                ).fetchall()
            ]
        if not ids:
            print("No matching documents.", file=sys.stderr)
            return 1
        for doc_id in ids:
            title_row = conn.execute(
                "SELECT title FROM documents WHERE id=%s", (doc_id,)
            ).fetchone()
            assert title_row is not None  # id came from this table
            title = title_row["title"]
            r = reannotate_document(conn, doc_id, cache, args.lang)
            if r:
                print(
                    f"  {title[:32]:32} lang={r['lang']} {r['occ']:>7} occ "
                    f"(+{r['new']} lexemes) [{r['tool']}]",
                    flush=True,
                )
        count_row = conn.execute("SELECT count(*) c FROM lexemes").fetchone()
        assert count_row is not None  # count(*) always returns a row
        n = count_row["c"]
        print(f"Done. {n:,} distinct lexemes.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
