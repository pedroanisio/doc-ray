"""Read-surface parity guards for GraphQL, Flask, and the async reader."""

from __future__ import annotations

import inspect
import re
import uuid
from collections.abc import Callable
from typing import Any

from doc_ray.adapters import persistence_read as read
from doc_ray.entrypoints.api import query as api_query
from doc_ray.entrypoints.api import types as api_types
from doc_ray.ports.repository import ReadRepository
from webapp import app as webapp

DOC_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
LEX_UID = "lx_en.v_say01"


def _norm(sql: str) -> str:
    """Collapse SQL/Python source whitespace for stable substring checks."""
    return " ".join(sql.split())


def _source(obj: Callable[..., Any]) -> str:
    """Return normalized source for a function or method."""
    return _norm(inspect.getsource(obj))


def _public_async_reader_functions() -> list[str]:
    return sorted(
        name
        for name, obj in vars(read).items()
        if inspect.iscoroutinefunction(obj) and not name.startswith("_")
    )


def _signature_shape(
    fn: Callable[..., Any], *, drop_self: bool = False
) -> tuple[tuple[str, str, str], ...]:
    params = list(inspect.signature(fn).parameters.values())
    if drop_self and params and params[0].name == "self":
        params = params[1:]
    return tuple((p.name, p.kind.name, repr(p.default)) for p in params)


def test_read_repository_protocol_matches_async_reader_signatures():
    """The port must document the full async read adapter, not a stale subset."""
    for name in _public_async_reader_functions():
        assert hasattr(ReadRepository, name), f"ReadRepository is missing {name}()"
        assert _signature_shape(getattr(ReadRepository, name), drop_self=True) == _signature_shape(
            getattr(read, name)
        )


def test_graphql_query_resolvers_do_not_embed_sql():
    """GraphQL reads must delegate to the async read surface instead of inlining SQL."""
    for module in (api_query, api_types):
        tree = inspect.getsource(module)
        sql_literals = [
            literal
            for literal in re.findall(r"""(["']{3}.*?["']{3}|["'][^"']*["'])""", tree, re.DOTALL)
            if re.search(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", literal, re.IGNORECASE)
        ]
        assert sql_literals == []


class _Recorder:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def q(self, sql: str, params: object = ()) -> list[dict[str, object]]:
        self.queries.append(_norm(sql))
        return []

    def q1(self, sql: str, params: object = ()) -> dict[str, object] | None:
        self.queries.append(_norm(sql))
        text = _norm(sql)
        if "SELECT * FROM documents WHERE id" in text:
            return {"id": str(DOC_ID), "title": "itest"}
        if "SELECT * FROM lexemes WHERE uid" in text:
            return {"uid": LEX_UID, "lang": "en", "pos": "VERB", "lemma": "say"}
        if "count(*) n FROM sentences WHERE document_id" in text:
            return {"n": 0}
        if "count(*) n FROM lexeme_occurrences WHERE lexeme_uid" in text:
            return {"n": 0}
        if "FROM documents) docs" in text:
            return {"docs": 0, "sents": 0, "lexemes": 0, "occ": 0}
        return None


def _record_route(monkeypatch, path: str, view: Callable[..., object], *args: object) -> str:
    recorder = _Recorder()
    monkeypatch.setattr(webapp, "q", recorder.q)
    monkeypatch.setattr(webapp, "q1", recorder.q1)
    monkeypatch.setattr(webapp, "render_template", lambda *a, **k: "")
    with webapp.app.test_request_context(path):
        view(*args)
    return " ".join(recorder.queries)


def _assert_fragments_in_both(
    web_sql: str, reader: Callable[..., Any], fragments: tuple[str, ...]
) -> None:
    reader_source = _source(reader)
    for fragment in fragments:
        normalized = _norm(fragment)
        assert normalized in web_sql
        assert normalized in reader_source


def test_webapp_document_reads_share_reader_table_shape(monkeypatch):
    web_sql = _record_route(monkeypatch, f"/doc/{DOC_ID}", webapp.document, DOC_ID)

    _assert_fragments_in_both(
        web_sql,
        read.get_document,
        (
            "SELECT * FROM documents WHERE id = %s",
            "FROM document_authors da JOIN authors a ON a.id = da.author_id",
            "FROM document_tags dt JOIN tags t ON t.id = dt.tag_id",
        ),
    )
    _assert_fragments_in_both(
        web_sql,
        read.list_sentences,
            (
                "FROM sentences",
                "JOIN spans sp ON sp.id =",
                "WHERE",
                "document_id =",
                "ORDER BY",
                "ordinal",
            ),
    )


def test_webapp_lexeme_index_reads_share_reader_table_shape(monkeypatch):
    web_sql = _record_route(monkeypatch, "/lexemes?q=say&pos=VERB&lang=en", webapp.lexemes)

    _assert_fragments_in_both(
        web_sql,
        read.list_lexemes,
            (
                "FROM lexemes l",
                "JOIN lexeme_occurrences o ON o.lexeme_uid = l.uid",
                "l.lemma ILIKE",
                "l.pos =",
                "l.lang =",
                "GROUP BY l.uid, l.lang, l.pos, l.lemma",
                "ORDER BY uses DESC",
            ),
        )


def test_webapp_lexeme_kwic_reads_share_reader_table_shape(monkeypatch):
    web_sql = _record_route(monkeypatch, f"/lexeme/{LEX_UID}", webapp.lexeme, LEX_UID)

    _assert_fragments_in_both(
        web_sql,
        read.lexeme_kwic,
        (
            "FROM lexeme_occurrences o",
            "JOIN annotations a",
            "JOIN spans s",
            "JOIN documents d",
            "JOIN document_content c",
            "WHERE o.lexeme_uid",
            "ORDER BY d.title, s.char_start",
        ),
    )


def test_webapp_sentence_search_reads_share_reader_table_shape(monkeypatch):
    web_sql = _record_route(monkeypatch, "/search?q=startup", webapp.search)

    _assert_fragments_in_both(
        web_sql,
        read.search_sentences,
        (
            "FROM sentences s JOIN documents d ON d.id = s.document_id",
            "WHERE s.text ILIKE",
            "ORDER BY d.title, s.ordinal",
        ),
    )
