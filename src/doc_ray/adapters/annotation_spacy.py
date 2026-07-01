"""spaCy-backed multi-layer annotator.

For each document it runs a spaCy pipeline over the stored `full_text` and
populates, under one annotation run (layer='spacy'):

  tokens (+ UPOS/XPOS + lexeme link)   -> NLP layers 3, 5, 6
  token_feature (UD FEATS)             -> layer 7
  chunk (noun chunks)                  -> layer 8
  dependency (head/deprel)             -> layer 10
  entity_mention (NER)                 -> layer 11
  coref_chain/coref_mention (string-match over proper-noun entities) -> layer 13

Everything is stand-off: spans are registered in `spans`, tokens/mentions
reference them. Re-running replaces the document's spaCy run.

Usage:  python -m ingest.annotate_spacy --all
"""

from __future__ import annotations

import argparse
import bisect
import sys
from collections.abc import Callable
from typing import Any

import psycopg

from doc_ray.adapters import persistence as db
from doc_ray.adapters.persistence import DictConnection
from doc_ray.config import Config
from doc_ray.domain import lexemes as lex
from doc_ray.domain.ulid import ULIDFactory, new_ulid_str

_MODELS = {"en": "en_core_web_sm", "pt": "pt_core_news_sm"}

#: An untyped spaCy ``Doc`` (spaCy ships no usable stubs for our surface).
SpacyDoc = Any
#: A loaded spaCy ``Language`` pipeline.
SpacyNlp = Any
#: A psycopg cursor over dict rows.
DictCursor = psycopg.Cursor[dict[str, Any]]
#: Maps a character offset to its containing sentence id, if any.
SentenceLookup = Callable[[int], str | None]

#: A token row: (id, span_id, sentence_id, ordinal, lexeme_uid, upos, xpos).
TokenRow = tuple[str, str, str | None, int, str | None, str, str]
#: A dependency row: (dependent_token_id, head_token_id, deprel).
DepRow = tuple[str, str | None, str]


class SpacyAnnotator:
    """Runs a spaCy pipeline and persists its multi-layer annotation output."""

    def __init__(self) -> None:
        """Initialize the empty per-language pipeline and lexeme caches."""
        self._nlp: dict[str, tuple[SpacyNlp, str]] = {}
        self.lexeme_cache: dict[str, str] = {}

    def nlp_for(self, lang: str) -> tuple[SpacyNlp, str]:
        """Return the loaded spaCy pipeline (and tool tag) for a language.

        Args:
            lang: Language code; unknown codes fall back to the English model.

        Returns:
            A ``(nlp, tool)`` pair where ``tool`` is ``"spacy/<model>@<ver>"``.
        """
        model = _MODELS.get(lang, _MODELS["en"])
        if model not in self._nlp:
            import importlib.metadata

            import spacy

            nlp = spacy.load(model)
            nlp.max_length = 5_000_000
            ver = importlib.metadata.version("spacy")
            self._nlp[model] = (nlp, f"spacy/{model}@{ver}")
        return self._nlp[model]

    def _build_tokens(
        self,
        conn: DictConnection,
        doc: SpacyDoc,
        span_map: dict[tuple[int, int], str],
        sentence_of: SentenceLookup,
        lang: str,
    ) -> tuple[list[TokenRow], list[tuple[str, str, str]], dict[int, str]]:
        """Build token/feature rows and a spaCy-index -> token-id map."""
        fac = ULIDFactory()
        tok_id: dict[int, str] = {}
        token_rows: list[TokenRow] = []
        feat_rows: list[tuple[str, str, str]] = []
        for t in doc:
            if not t.text.strip():
                continue
            tid = str(fac.new())
            tok_id[t.i] = tid
            luid = None
            if t.is_alpha:
                luid, _ = db.get_or_create_lexeme(conn, self.lexeme_cache, t.lemma_, t.pos_, lang)
            token_rows.append(
                (
                    tid,
                    span_map[(t.idx, t.idx + len(t.text))],
                    sentence_of(t.idx),
                    t.i,
                    luid,
                    t.pos_,
                    t.tag_,
                )
            )
            for feat, val in t.morph.to_dict().items():
                feat_rows.append((tid, feat, val))
        return token_rows, feat_rows, tok_id

    def annotate(
        self, conn: DictConnection, doc_id: str, lang_default: str = "en"
    ) -> dict[str, Any]:
        """Run the spaCy pipeline over one document and persist all layers.

        Args:
            conn: Open database connection.
            doc_id: Document to annotate.
            lang_default: Fallback language when detection is weak.

        Returns:
            A summary mapping of per-layer counts plus ``lang`` and ``tool``.
        """
        row = conn.execute(
            "SELECT full_text FROM document_content WHERE document_id=%s", (doc_id,)
        ).fetchone()
        text = (row["full_text"] if row else None) or ""
        if not text.strip():
            return {"tokens": 0}
        lang = lex.detect_language(text, default=lang_default)
        nlp, tool = self.nlp_for(lang)
        doc = nlp(text)

        # sentence-span index for offset containment
        sent_spans = conn.execute(
            "SELECT s.id, sp.char_start, sp.char_end FROM sentences s "
            "JOIN spans sp ON sp.id=s.span_id WHERE s.document_id=%s ORDER BY sp.char_start",
            (doc_id,),
        ).fetchall()
        starts = [r["char_start"] for r in sent_spans]

        def sentence_of(cs: int) -> str | None:
            i = bisect.bisect_right(starts, cs) - 1
            if 0 <= i < len(sent_spans) and sent_spans[i]["char_end"] >= cs:
                return str(sent_spans[i]["id"])
            return None

        # Register every span we need (tokens, entities, chunks) in one bulk call.
        tok_spans = [(t.idx, t.idx + len(t.text)) for t in doc if t.text.strip()]
        ent_spans = [(e.start_char, e.end_char) for e in doc.ents]
        chunk_spans = [(c.start_char, c.end_char) for c in doc.noun_chunks]
        span_map = db.ensure_spans(conn, doc_id, tok_spans + ent_spans + chunk_spans)

        token_rows, feat_rows, tok_id = self._build_tokens(conn, doc, span_map, sentence_of, lang)

        dep_rows: list[DepRow] = []
        for t in doc:
            if t.i not in tok_id:
                continue
            head = None if t.head.i == t.i else tok_id.get(t.head.i)
            dep_rows.append((tok_id[t.i], head, "root" if head is None else t.dep_))

        ent_rows, coref = self._build_entities(doc, span_map)
        chunk_rows = [
            (new_ulid_str(), span_map[(c.start_char, c.end_char)], "NP") for c in doc.noun_chunks
        ]

        # Lexeme layer (real POS) — the projection the webapp reads. One pass,
        # so the whole mapping (spaCy tokens/deps/NER/chunks + lexeme POS) is
        # produced and committed together.
        lex_occ: dict[str, str] = {}
        for _tid, span, _sent, _ordi, luid, _upos, _xpos in token_rows:
            if luid is not None:
                lex_occ[span] = luid
        lex_tool = tool.split("@")[0]
        lex_version = tool.split("@")[1] if "@" in tool else None

        self._write(
            conn,
            doc_id,
            tool,
            token_rows,
            feat_rows,
            dep_rows,
            ent_rows,
            chunk_rows,
            coref,
            lex_occ=list(lex_occ.items()),
            lex_tool=lex_tool,
            lex_version=lex_version,
        )
        return {
            "tokens": len(token_rows),
            "entities": len(ent_rows),
            "chunks": len(chunk_rows),
            "coref_chains": len(coref),
            "occurrences": len(lex_occ),
            "lang": lang,
            "tool": tool,
        }

    def _build_entities(
        self,
        doc: SpacyDoc,
        span_map: dict[tuple[int, int], str],
    ) -> tuple[list[tuple[str, str, str]], dict[str, list[str]]]:
        """Build entity-mention rows and the string-match coref chains."""
        ent_rows: list[tuple[str, str, str]] = []
        ent_by_key: dict[str, list[str]] = {}
        for e in doc.ents:
            mid = new_ulid_str()
            ent_rows.append((mid, span_map[(e.start_char, e.end_char)], e.label_))
            if e.label_ in ("PERSON", "PER", "ORG", "GPE", "LOC"):
                ent_by_key.setdefault(e.text.strip().casefold(), []).append(
                    span_map[(e.start_char, e.end_char)]
                )
        # string-match coref: same proper-noun surface -> one chain
        coref = {k: v for k, v in ent_by_key.items() if len(v) >= 2}
        return ent_rows, coref

    @staticmethod
    def _copy_layers(
        cur: DictCursor,
        ann: str,
        tokens: list[TokenRow],
        feats: list[tuple[str, str, str]],
        deps: list[DepRow],
        ents: list[tuple[str, str, str]],
        chunks: list[tuple[str, str, str]],
    ) -> None:
        """COPY the token/feature/dependency/entity/chunk layers into place."""
        tokens_sql = (
            "COPY tokens (id, annotation_id, span_id, sentence_id, ordinal, "
            "lexeme_uid, upos, xpos) FROM STDIN"
        )
        with cur.copy(tokens_sql) as cp:
            for tid, span, sent, ordi, luid, upos, xpos in tokens:
                cp.write_row((tid, ann, span, sent, ordi, luid, upos, xpos))
        if feats:
            with cur.copy("COPY token_feature (token_id, feature, value) FROM STDIN") as cp:
                for r in feats:
                    cp.write_row(r)
        if deps:
            with cur.copy(
                "COPY dependency (annotation_id, dependent_token_id, "
                "head_token_id, deprel) FROM STDIN"
            ) as cp:
                for dep, head, rel in deps:
                    cp.write_row((ann, dep, head, rel))
        if ents:
            with cur.copy(
                "COPY entity_mention (id, annotation_id, span_id, entity_type) FROM STDIN"
            ) as cp:
                for mid, span, typ in ents:
                    cp.write_row((mid, ann, span, typ))
        if chunks:
            with cur.copy("COPY chunk (id, annotation_id, span_id, chunk_type) FROM STDIN") as cp:
                for cid, span, typ in chunks:
                    cp.write_row((cid, ann, span, typ))

    def _write(
        self,
        conn: DictConnection,
        doc_id: str,
        tool: str,
        tokens: list[TokenRow],
        feats: list[tuple[str, str, str]],
        deps: list[DepRow],
        ents: list[tuple[str, str, str]],
        chunks: list[tuple[str, str, str]],
        coref: dict[str, list[str]],
        lex_occ: list[tuple[str, str]] | None = None,
        lex_tool: str | None = None,
        lex_version: str | None = None,
    ) -> None:
        """Persist all spaCy layers (and the lexeme projection) in one txn."""
        with conn.transaction():
            conn.execute(
                "DELETE FROM annotations WHERE document_id=%s AND layer='spacy' AND tool=%s",
                (doc_id, tool),
            )
            ann = new_ulid_str()
            conn.execute(
                "INSERT INTO annotations (id, document_id, layer, tool) VALUES (%s,%s,'spacy',%s)",
                (ann, doc_id, tool),
            )
            with conn.cursor() as cur:
                self._copy_layers(cur, ann, tokens, feats, deps, ents, chunks)
            for _surface, spans in coref.items():
                chain = new_ulid_str()
                conn.execute(
                    "INSERT INTO coref_chain (id, annotation_id, document_id) VALUES (%s,%s,%s)",
                    (chain, ann, doc_id),
                )
                for span in spans:
                    conn.execute(
                        "INSERT INTO coref_mention (chain_id, span_id) "
                        "VALUES (%s,%s) ON CONFLICT DO NOTHING",
                        (chain, span),
                    )

            # lexeme layer (real spaCy POS) — the webapp reads this projection
            conn.execute(
                "DELETE FROM annotations WHERE document_id=%s AND layer='lexeme' AND tool=%s",
                (doc_id, lex_tool),
            )
            lex_ann = new_ulid_str()
            conn.execute(
                "INSERT INTO annotations (id, document_id, layer, tool, tool_version) "
                "VALUES (%s,%s,'lexeme',%s,%s)",
                (lex_ann, doc_id, lex_tool, lex_version),
            )
            if lex_occ:
                with conn.cursor().copy(
                    "COPY lexeme_occurrences (annotation_id, span_id, lexeme_uid) FROM STDIN"
                ) as cp:
                    for span, luid in lex_occ:
                        cp.write_row((lex_ann, span, luid))


def _target_ids(conn: DictConnection, args: argparse.Namespace) -> list[str]:
    """Resolve CLI selectors to an ordered list of document ids."""
    if args.id:
        return [args.id]
    if args.filename:
        rows = conn.execute(
            "SELECT id FROM documents WHERE original_filename=%s", (args.filename,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id FROM documents WHERE status='extracted' ORDER BY created_at"
        ).fetchall()
    return [str(r["id"]) for r in rows]


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: annotate the selected documents with spaCy.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv``).

    Returns:
        Process exit code (0 on success, 1 when no documents match).
    """
    ap = argparse.ArgumentParser(description="Annotate documents with spaCy (tokens/deps/NER/…).")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--id")
    g.add_argument("--filename")
    g.add_argument("--all", action="store_true")
    args = ap.parse_args(argv)

    ann = SpacyAnnotator()
    with db.connect(Config.from_env()) as conn:
        ids = _target_ids(conn, args)
        if not ids:
            print("No documents.", file=sys.stderr)
            return 1
        for doc_id in ids:
            title_row = conn.execute(
                "SELECT left(coalesce(original_filename,title),36) t FROM documents WHERE id=%s",
                (doc_id,),
            ).fetchone()
            assert title_row is not None  # doc_id came from a documents query
            title = title_row["t"]
            try:
                r = ann.annotate(conn, doc_id)
                print(
                    f"  {title:36}  {r.get('tokens', 0):>6} tok  "
                    f"{r.get('entities', 0):>4} ent  {r.get('chunks', 0):>4} chunk  "
                    f"{r.get('coref_chains', 0):>3} coref  [{r.get('tool', '-')}]"
                )
            except Exception as e:
                print(f"  {title:36}  SKIPPED: {type(e).__name__}: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
