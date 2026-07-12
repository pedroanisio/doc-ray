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

import bisect
import os
from collections.abc import Callable
from typing import Any

import psycopg

from doc_ray.adapters import persistence as db
from doc_ray.adapters.annotation_capabilities import (
    CAPABILITY_MODELS,
    AnnotationLanguageResolution,
    UnsupportedAnnotationLanguage,
    normalize_language,
    spacy_capabilities,
)
from doc_ray.adapters.annotation_validation import validate_spacy_annotation_output
from doc_ray.adapters.persistence import DictConnection
from doc_ray.domain import lexemes as lex
from doc_ray.domain.progress import report_progress
from doc_ray.domain.ulid import ULIDFactory, new_ulid_str

__all__ = [
    "SpacyAnnotator",
    "UnsupportedAnnotationLanguage",
    "lexeme_projection_from_tokens",
    "spacy_capabilities",
]

_MODELS = CAPABILITY_MODELS

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
_ANNOTATE_PROGRESS_TOTAL = 12


def lexeme_projection_from_tokens(tokens: list[TokenRow]) -> list[tuple[str, str]]:
    """Project token lexeme links into ``lexeme_occurrences`` rows."""
    return [
        (span, luid) for _tid, span, _sent, _ordi, luid, _upos, _xpos in tokens if luid is not None
    ]


def _split_tool_tag(tool: str) -> tuple[str, str | None]:
    """Split ``spacy/<model>@<version>`` into provenance columns."""
    if "@" not in tool:
        return tool, None
    name, version = tool.rsplit("@", 1)
    return name, version


class SpacyAnnotator:
    """Runs a spaCy pipeline and persists its multi-layer annotation output."""

    def __init__(self, fallback_policy: str | None = None) -> None:
        """Initialize the empty per-language pipeline and lexeme caches."""
        self._nlp: dict[str, tuple[SpacyNlp, str]] = {}
        self.lexeme_cache: dict[str, str] = {}
        self.fallback_policy = self._normalize_policy(
            fallback_policy or os.environ.get("DOCRAY_SPACY_FALLBACK_POLICY", "fail")
        )

    @staticmethod
    def _normalize_policy(policy: str) -> str:
        """Validate and normalize the configured fallback policy."""
        normalized = policy.strip().lower()
        if normalized not in {"fail", "fallback"}:
            raise ValueError("fallback_policy must be 'fail' or 'fallback'")
        return normalized

    def resolve_language(
        self, lang: str | None, *, lang_default: str = "en"
    ) -> AnnotationLanguageResolution:
        """Resolve a detected language to a configured spaCy model.

        Args:
            lang: Detected language code.
            lang_default: Fallback language when fallback policy is enabled.

        Returns:
            The resolved annotation-language/model pair.

        Raises:
            UnsupportedAnnotationLanguage: If no model supports ``lang`` and the
                policy is strict, or if the configured fallback language is also
                unsupported.
        """
        requested = normalize_language(lang, default=lang_default)
        model = CAPABILITY_MODELS.get(requested)
        if model is not None:
            return AnnotationLanguageResolution(
                requested_language=requested,
                annotation_language=requested,
                model=model,
                fallback_policy=self.fallback_policy,
                fallback_used=False,
            )

        supported = ", ".join(sorted(CAPABILITY_MODELS))
        if self.fallback_policy == "fail":
            raise UnsupportedAnnotationLanguage(
                f"unsupported annotation language {requested!r}; supported languages: {supported}"
            )

        fallback = normalize_language(lang_default)
        fallback_model = CAPABILITY_MODELS.get(fallback)
        if fallback_model is None:
            raise UnsupportedAnnotationLanguage(
                f"unsupported annotation language {requested!r} and unsupported fallback "
                f"language {fallback!r}; supported languages: {supported}"
            )
        return AnnotationLanguageResolution(
            requested_language=requested,
            annotation_language=fallback,
            model=fallback_model,
            fallback_policy=self.fallback_policy,
            fallback_used=True,
        )

    def nlp_for(self, lang: str, *, lang_default: str = "en") -> tuple[SpacyNlp, str]:
        """Return the loaded spaCy pipeline (and tool tag) for a language.

        Args:
            lang: Language code to annotate.
            lang_default: Fallback language when fallback policy is enabled.

        Returns:
            A ``(nlp, tool)`` pair where ``tool`` is ``"spacy/<model>@<ver>"``.
        """
        model = self.resolve_language(lang, lang_default=lang_default).model
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
        report_progress(0, _ANNOTATE_PROGRESS_TOTAL, "Loading text and language model")
        row = conn.execute(
            "SELECT full_text FROM document_content WHERE document_id=%s", (doc_id,)
        ).fetchone()
        text = (row["full_text"] if row else None) or ""
        if not text.strip():
            return {"tokens": 0}
        detected_lang = lex.detect_language(text, default=lang_default)
        language_resolution = self.resolve_language(detected_lang, lang_default=lang_default)
        lang = language_resolution.annotation_language
        nlp, tool = self.nlp_for(lang, lang_default=lang_default)
        report_progress(1, _ANNOTATE_PROGRESS_TOTAL, "Running spaCy pipeline")
        doc = nlp(text)

        report_progress(2, _ANNOTATE_PROGRESS_TOTAL, "Indexing sentences")
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
        report_progress(3, _ANNOTATE_PROGRESS_TOTAL, "Collecting token spans")
        tok_spans = [(t.idx, t.idx + len(t.text)) for t in doc if t.text.strip()]
        report_progress(4, _ANNOTATE_PROGRESS_TOTAL, "Collecting entity and chunk spans")
        ents = list(doc.ents)
        chunks = list(doc.noun_chunks)
        ent_spans = [(e.start_char, e.end_char) for e in ents]
        chunk_spans = [(c.start_char, c.end_char) for c in chunks]
        span_count = len(tok_spans) + len(ent_spans) + len(chunk_spans)
        report_progress(5, _ANNOTATE_PROGRESS_TOTAL, f"Registering {span_count} annotation spans")
        span_map = db.ensure_spans(conn, doc_id, tok_spans + ent_spans + chunk_spans)

        report_progress(6, _ANNOTATE_PROGRESS_TOTAL, "Building token and morphology rows")
        token_rows, feat_rows, tok_id = self._build_tokens(conn, doc, span_map, sentence_of, lang)

        report_progress(
            7,
            _ANNOTATE_PROGRESS_TOTAL,
            "Building dependency, entity, chunk, and coreference rows",
        )
        dep_rows: list[DepRow] = []
        for t in doc:
            if t.i not in tok_id:
                continue
            head = None if t.head.i == t.i else tok_id.get(t.head.i)
            dep_rows.append((tok_id[t.i], head, "root" if head is None else t.dep_))

        ent_rows, coref = self._build_entities(ents, span_map)
        chunk_rows = [
            (new_ulid_str(), span_map[(c.start_char, c.end_char)], "NP", c.text)
            for c in chunks
        ]

        # Lexeme layer (real POS) — the projection the webapp reads. One pass,
        # so the whole mapping (spaCy tokens/deps/NER/chunks + lexeme POS) is
        # produced and committed together.
        report_progress(8, _ANNOTATE_PROGRESS_TOTAL, "Projecting lexeme occurrences")
        lex_occ = lexeme_projection_from_tokens(token_rows)
        lex_tool, lex_version = _split_tool_tag(tool)
        report_progress(9, _ANNOTATE_PROGRESS_TOTAL, "Validating annotation output")
        validation_summary = validate_spacy_annotation_output(
            text_length=len(text),
            span_map=span_map,
            sentence_ids={str(r["id"]) for r in sent_spans},
            lang=lang,
            tokens=token_rows,
            feats=feat_rows,
            deps=dep_rows,
            ents=ent_rows,
            chunks=chunk_rows,
            coref=coref,
            lex_occ=lex_occ,
            language_resolution=language_resolution,
        )

        report_progress(10, _ANNOTATE_PROGRESS_TOTAL, "Persisting annotation layers")
        self._write(
            conn,
            doc_id,
            tool,
            lang,
            token_rows,
            feat_rows,
            dep_rows,
            ent_rows,
            chunk_rows,
            coref,
            lex_occ=lex_occ,
            lex_tool=lex_tool,
            lex_version=lex_version,
            language_resolution=language_resolution,
            validation_summary=validation_summary,
        )
        report_progress(11, _ANNOTATE_PROGRESS_TOTAL, "Annotation layers persisted")
        return {
            "tokens": len(token_rows),
            "entities": len(ent_rows),
            "chunks": len(chunk_rows),
            "coref_chains": len(coref),
            "occurrences": len(lex_occ),
            "lang": lang,
            "detected_lang": detected_lang,
            "model": language_resolution.model,
            "fallback_policy": language_resolution.fallback_policy,
            "fallback_used": language_resolution.fallback_used,
            "tool": tool,
            **validation_summary,
        }

    def _build_entities(
        self,
        ents: list[Any],
        span_map: dict[tuple[int, int], str],
    ) -> tuple[list[tuple[str, str, str, str]], dict[str, list[str]]]:
        """Build entity-mention rows and the string-match coref chains."""
        ent_rows: list[tuple[str, str, str, str]] = []
        ent_by_key: dict[str, list[str]] = {}
        for e in ents:
            mid = new_ulid_str()
            ent_rows.append((mid, span_map[(e.start_char, e.end_char)], e.label_, e.text))
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
        ents: list[tuple[str, str, str, str]],
        chunks: list[tuple[str, str, str, str]],
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
                "COPY entity_mention (id, annotation_id, span_id, entity_type, surface) FROM STDIN"
            ) as cp:
                for mid, span, typ, surface in ents:
                    cp.write_row((mid, ann, span, typ, surface))
        if chunks:
            with cur.copy(
                "COPY chunk (id, annotation_id, span_id, chunk_type, surface) FROM STDIN"
            ) as cp:
                for cid, span, typ, surface in chunks:
                    cp.write_row((cid, ann, span, typ, surface))

    def _write(
        self,
        conn: DictConnection,
        doc_id: str,
        tool: str,
        lang: str,
        tokens: list[TokenRow],
        feats: list[tuple[str, str, str]],
        deps: list[DepRow],
        ents: list[tuple[str, str, str, str]],
        chunks: list[tuple[str, str, str, str]],
        coref: dict[str, list[str]],
        lex_occ: list[tuple[str, str]] | None = None,
        lex_tool: str | None = None,
        lex_version: str | None = None,
        language_resolution: AnnotationLanguageResolution | None = None,
        validation_summary: dict[str, str | int] | None = None,
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

            provenance_id = new_ulid_str()
            provenance_tool, provenance_version = _split_tool_tag(tool)
            conn.execute(
                """
                INSERT INTO document_provenance
                    (id, document_id, kind, tool, tool_version, actor, note)
                VALUES (%s, %s, 'annotated', %s, %s, %s, %s)
                """,
                (
                    provenance_id,
                    doc_id,
                    provenance_tool,
                    provenance_version,
                    "doc_ray.adapters.annotation_spacy.SpacyAnnotator",
                    "spaCy multi-layer annotation run committed with replacement semantics.",
                ),
            )
            provenance_params = {
                "annotation_id": ann,
                "lexeme_annotation_id": lex_ann,
                "language": lang,
                **(language_resolution.provenance_params() if language_resolution else {}),
                "replacement": "replace document spacy and lexeme layers for this tool",
                "tokens": len(tokens),
                "token_features": len(feats),
                "dependencies": len(deps),
                "entities": len(ents),
                "chunks": len(chunks),
                "coref_chains": len(coref),
                "lexeme_occurrences": len(lex_occ or []),
                **(validation_summary or {}),
            }
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO provenance_params (event_id, key, value) VALUES (%s, %s, %s)",
                    [(provenance_id, key, str(value)) for key, value in provenance_params.items()],
                )
