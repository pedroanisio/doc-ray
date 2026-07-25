"""Streamable-HTTP MCP server exposing the doc-ray corpus read tools to agentic AI.

The server is a thin, stateless front door: it registers the read tools from
:mod:`~doc_ray.entrypoints.mcp.corpus` on a :class:`~mcp.server.fastmcp.FastMCP`
instance, wraps the resulting Streamable-HTTP ASGI app with
:class:`~doc_ray.entrypoints.mcp.auth.BearerAuthMiddleware`, and forwards each
caller's token to the GraphQL API. It owns no database connection — every tool
result is exactly what the caller's principal may read under Postgres RLS.

Run it with any ASGI server, e.g.::

    uvicorn doc_ray.entrypoints.mcp.server:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from doc_ray.config import Config
from doc_ray.entrypoints.mcp import (
    audit,
    compose,
    corpus,
    mutations,
    prompts,
    resources,
    semantic,
    structure,
)
from doc_ray.entrypoints.mcp.auth import BearerAuthMiddleware

#: Loopback patterns the SDK trusts by default — kept so local dev still works.
_LOCAL_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
_LOCAL_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]


def _public_hosts() -> list[str]:
    """Public ``Host`` values to accept (``DOCRAY_MCP_ALLOWED_HOSTS`` + ``DOCRAY_PUBLIC_BASE_URL``).

    Behind the Cloudflare tunnel the request arrives with the public host (e.g.
    ``doc-ray.com``), which the SDK's localhost-only DNS-rebind default rejects
    with HTTP 421 ("Invalid Host header"). This surfaces the deployed host(s) so
    they can be allow-listed without disabling the protection.
    """
    hosts = [
        h.strip() for h in os.environ.get("DOCRAY_MCP_ALLOWED_HOSTS", "").split(",") if h.strip()
    ]
    base = os.environ.get("DOCRAY_PUBLIC_BASE_URL", "").strip()
    if base and (netloc := urlparse(base).netloc):
        hosts.append(netloc)
    return hosts


def _transport_security() -> TransportSecuritySettings | None:
    """DNS-rebind settings allowing the public host(s); ``None`` keeps the SDK localhost default."""
    hosts = _public_hosts()
    if not hosts:
        return None
    allowed_hosts = list(_LOCAL_HOSTS)
    allowed_origins = list(_LOCAL_ORIGINS)
    for host in hosts:
        allowed_hosts += [host, f"{host}:*"]
        allowed_origins += [f"https://{host}", f"https://{host}:*"]
    return TransportSecuritySettings(allowed_hosts=allowed_hosts, allowed_origins=allowed_origins)


#: Advertised to the model as "how to use this server" (BL-145). The friction in
#: practice was not a missing capability — it was the agent re-learning the
#: retrieval model mid-task. Front-load it here, once, per Anthropic's finding
#: that tool/description guidance is load-bearing.
SERVER_INSTRUCTIONS = (
    "doc-ray is RETRIEVAL, not reading: it returns cited slices of documents, "
    "never a whole document, and every result carries a citation handle "
    "(ordinal + character offsets) back to an exact sentence.\n\n"
    "To cover a document completely and cheaply, prefer this path:\n"
    "1. list_documents to find the document id.\n"
    "2. read_document_outline to get its addressable ranges — each entry's "
    "ordinalStart/ordinalEnd goes straight into the reader, so you never guess an "
    "ordinal. Check its `source`: 'headings' is real structure, 'uniform' is an "
    "even grid (equally good for aiming a read, but its titles are NOT chapter "
    "names — do not quote them as such).\n"
    "3. read_document_sentences with from_ordinal/to_ordinal for random access to "
    "any section verbatim — it also pages sequentially (start after=None, follow "
    "the returned cursor, stop on []) for exhaustive 100% coverage. Pass "
    "response_format='concise' when reading in bulk: same text and citation "
    "offsets, roughly a third fewer characters.\n"
    "4. search_sentences with RARE, scene-specific terms to locate particular "
    "beats. Common, high-frequency words flood one region of the text and starve "
    "coverage of the rest; its `coverage` field and `note` tell you when that has "
    "happened, so a sparse-looking result is a query problem, not missing content. "
    "To avoid the wasted call entirely, run term_frequency FIRST on your candidate "
    "terms (batch them in one call): a `high` floodRisk means pick a rarer one, and "
    "an unattested term (attested=false) means the search will likely return "
    "nothing.\n\n"
    "For who and what a document is about, prefer entity_profiles over "
    "entity_frequency: raw NER splits one entity across types and spellings, and "
    "profiles merge them and report the disagreement as typeConsistency — a low "
    "value means the type is contested, not that the entity is unimportant.\n\n"
    "Reach for the outline and the ranged reader early rather than grinding "
    "keyword searches. "
    "Cursors are per-tool and NOT interchangeable — a search_sentences cursor is "
    "not valid for read_document_sentences (use ordinals there). Entity results "
    "are canonicalized with a confidence; treat low-confidence rows as untrusted."
)


def build_server(enable_write: bool = False) -> FastMCP:
    """Create a FastMCP server with the corpus read + composition-grounding tools.

    Every tool is wrapped by :func:`~doc_ray.entrypoints.mcp.audit.audited`, so
    each call emits one audit log line (caller fingerprint, outcome, duration).

    Args:
        enable_write: When true, additionally register the reprocessing + curation
            write tools (:data:`doc_ray.entrypoints.mcp.mutations.TOOLS`). Off by
            default so the advertised surface is read-only unless an operator opts
            in via ``DOCRAY_MCP_ENABLE_WRITE``; even when on, every write is still
            gated by the API's ``write`` scope and per-document ACL guards.
    """
    mcp = FastMCP("doc-ray", instructions=SERVER_INSTRUCTIONS)
    if (security := _transport_security()) is not None:
        # Accept the public Host behind the tunnel — the localhost-only default 421s.
        mcp.settings.transport_security = security
    tools = (
        *corpus.TOOLS,
        *semantic.TOOLS,
        *structure.TOOLS,
        *compose.TOOLS,
        *(mutations.TOOLS if enable_write else ()),
    )
    for tool in tools:
        mcp.tool()(audit.audited(tool))
    # `docray://` resources/templates — the addressing layer over the read tools.
    # Always registered: they are reads, RLS-scoped by the same forwarded token,
    # and grant nothing the tools don't (see resources.py).
    for spec in resources.RESOURCES:
        mcp.resource(spec.uri, mime_type=spec.mime_type, description=spec.description)(
            audit.audited(spec.fn)
        )
    # Prompts — MCP's user-controlled primitive: the corpus methodology as
    # selectable workflows (prompts.py). Audited like every other caller action,
    # and read-only: they call the read tools, holding no pool of their own.
    for prompt in prompts.PROMPTS:
        mcp.prompt()(audit.audited(prompt))
    return mcp


def create_app(cfg: Config | None = None) -> Starlette:
    """Build the bearer-authenticated Streamable-HTTP ASGI app.

    Args:
        cfg: Runtime config; loaded from the environment when omitted.

    Returns:
        A Starlette app exposing the MCP endpoint behind :class:`BearerAuthMiddleware`.
    """
    cfg = cfg or Config.from_env()
    app = build_server(enable_write=cfg.mcp_enable_write).streamable_http_app()
    app.add_middleware(BearerAuthMiddleware, cfg=cfg)
    return app


#: Module-level ASGI app for ``uvicorn doc_ray.entrypoints.mcp.server:app``.
app = create_app()
