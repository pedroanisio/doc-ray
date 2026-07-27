"""The read/curation endpoint split (#36 Phase 2, OD-1 Option A).

Every agent session pays for the advertised tool surface before its first useful
call. `DOCRAY_MCP_ENABLE_WRITE` used to *widen* `/mcp` to all 65 tools, so a
read-only caller on a write-enabled deployment paid for 32 curation tools it
would never invoke. The curation registry now lives at its own path, and the
default endpoint is read-only whatever the flag says.

The reduction is a property of consumer segmentation, not of the split: a client
that configures both endpoints loads both lists. What these tests pin is the half
the server controls — that `/mcp` cannot be widened, and that the second endpoint
is not a lower-scrutiny path.
"""

from __future__ import annotations

import dataclasses

import anyio

from doc_ray.config import Config
from doc_ray.entrypoints.mcp import server
from doc_ray.tests.test_mcp_server import _READ_TOOL_NAMES, _WRITE_TOOL_NAMES


def _cfg(*, enable_write: bool) -> Config:
    return dataclasses.replace(Config.from_env(), mcp_enable_write=enable_write)


def _paths(app) -> set[str]:
    return {getattr(route, "path", "") for route in app.routes}


# --- A-TEST-1: the default endpoint is read-only, unconditionally -------------


def test_the_read_endpoint_registers_only_read_tools_even_when_write_is_enabled():
    """A-FR-1: `enable_write` must no longer widen the default endpoint.

    This is the whole point of the split. If the flag can still widen `/mcp`, a
    read-only caller on a write-enabled deployment pays for the curation surface
    it will never call — exactly the cost #36 measured.
    """
    names = {tool.name for tool in anyio.run(server.build_read_server().list_tools)}

    assert names == _READ_TOOL_NAMES
    assert not (names & _WRITE_TOOL_NAMES)


def test_the_curation_endpoint_registers_the_write_tools():
    names = {tool.name for tool in anyio.run(server.build_curation_server().list_tools)}

    assert names >= _WRITE_TOOL_NAMES


def test_the_curation_endpoint_keeps_the_read_tools_so_a_curator_can_verify():
    """A curation agent must be able to read what it is about to change.

    Splitting reads away from the write surface would force every curator to
    configure both endpoints, which is the configuration burden this option is
    already asking consumers to accept once.
    """
    names = {tool.name for tool in anyio.run(server.build_curation_server().list_tools)}

    assert names >= _READ_TOOL_NAMES


def test_the_read_endpoint_costs_materially_less_than_the_curation_one():
    """A-TEST-3: the split must actually reduce what a read-only caller loads.

    Measured from the live registries rather than asserted against a constant
    (NFR-3: never report a surface number the harness did not measure). Before the
    split a write-enabled deployment served the larger figure at ``/mcp``; now that
    path serves the smaller one, and that difference is the whole return on this
    change.
    """
    import json

    def _chars(srv) -> int:
        tools = anyio.run(srv.list_tools)
        return len(json.dumps([t.model_dump(exclude_none=True) for t in tools]))

    read_chars = _chars(server.build_read_server())
    curation_chars = _chars(server.build_curation_server())

    assert read_chars < curation_chars
    # A saving too small to notice would not justify a second endpoint and a
    # breaking migration; hold the change to the order of reduction it promised.
    assert read_chars < curation_chars * 0.75


# --- routing: both registries reachable, at distinct absolute paths -----------


def test_the_curation_registry_is_mounted_at_its_own_path():
    app = server.create_app(_cfg(enable_write=True))

    assert server.CURATION_PATH in _paths(app)
    assert server.READ_PATH in _paths(app)
    assert server.CURATION_PATH != server.READ_PATH


def test_no_curation_path_exists_when_writes_are_disabled():
    """The default surface stays provably read-only: no path to widen at all.

    `DOCRAY_MCP_ENABLE_WRITE` keeps its security meaning — it just moves from
    "widen /mcp" to "additionally expose /mcp/curate".
    """
    app = server.create_app(_cfg(enable_write=False))

    assert server.READ_PATH in _paths(app)
    assert server.CURATION_PATH not in _paths(app)


# --- A-TEST-2: the curation path is not lower-scrutiny ------------------------


def test_both_endpoints_share_one_middleware_chain():
    """A-FR-2: auth, rate limiting and compression apply identically.

    Asserted as stack equality rather than a smoke request: a smoke test proves
    one request was authenticated, not that the chain cannot drift apart later.
    Both registries are served by routes on ONE app, so the chain is shared by
    construction — this pins that construction.
    """
    app = server.create_app(_cfg(enable_write=True))
    classes = [mw.cls for mw in app.user_middleware]

    from doc_ray.entrypoints.mcp.auth import BearerAuthMiddleware
    from doc_ray.entrypoints.mcp.ratelimit import RateLimitMiddleware

    assert BearerAuthMiddleware in classes
    assert RateLimitMiddleware in classes
    # Auth must resolve the principal before the limiter keys on it.
    assert classes.index(BearerAuthMiddleware) < classes.index(RateLimitMiddleware)


def test_enabling_curation_adds_no_middleware_and_removes_none():
    """The curation endpoint must not be reachable through a thinner chain."""
    read_only = [mw.cls for mw in server.create_app(_cfg(enable_write=False)).user_middleware]
    with_curation = [mw.cls for mw in server.create_app(_cfg(enable_write=True)).user_middleware]

    assert read_only == with_curation


def test_health_probes_stay_on_the_app_when_curation_is_enabled():
    app = server.create_app(_cfg(enable_write=True))

    from doc_ray.entrypoints.mcp import health

    for path, _handler in health.ROUTES:
        assert path in _paths(app)


# --- end to end: both registries actually answer over HTTP -------------------


def _tools_at(client, path: str) -> set[str]:
    """POST tools/list at *path* through an already-running client."""
    import json

    response = client.post(
        path,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200, f"{path} -> {response.status_code} {response.text[:200]}"
    return {t["name"] for t in json.loads(response.text.split("data: ")[-1])["result"]["tools"]}


def test_both_endpoints_answer_tools_list_over_http(monkeypatch):
    """Routes existing is not the same as a registry serving.

    FastMCP puts its session manager in the sub-app's lifespan, which Starlette
    never runs for a mounted app — a registry whose manager was not entered has a
    route and 404s every request to it. Only a real request catches that, and one
    client context serves both endpoints because a session manager may be run
    exactly once per instance.
    """
    from starlette.testclient import TestClient

    monkeypatch.setenv("DOCRAY_MCP_ALLOWED_HOSTS", "localhost")
    app = server.create_app(_cfg(enable_write=True))

    with TestClient(app, base_url="http://localhost:8080") as client:
        read_names = _tools_at(client, server.READ_PATH)
        curation_names = _tools_at(client, server.CURATION_PATH)

    assert read_names == _READ_TOOL_NAMES
    assert curation_names >= _WRITE_TOOL_NAMES
    # The saving this split exists to deliver, proven at the wire.
    assert len(read_names) < len(curation_names)


def test_the_curation_path_is_absent_over_http_when_writes_are_disabled(monkeypatch):
    monkeypatch.setenv("DOCRAY_MCP_ALLOWED_HOSTS", "localhost")
    from starlette.testclient import TestClient

    app = server.create_app(_cfg(enable_write=False))

    with TestClient(app, base_url="http://localhost:8080") as client:
        response = client.post(
            server.CURATION_PATH,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={"Accept": "application/json, text/event-stream"},
        )

    assert response.status_code == 404
