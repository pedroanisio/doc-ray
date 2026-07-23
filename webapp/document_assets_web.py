"""Original-asset streaming routes: download, page PNG, cover WebP.

Split out of ``webapp/document_web.py`` (D-015). These routes proxy binary
assets through the API's HTTP boundary and touch no database, so they carry no
``_core``/GraphQL-helper dependency — ``document_web.register`` wires them in
alongside the reader routes, keeping the webapp's route wiring in one place.
"""

from __future__ import annotations

from flask import Response, abort, request, stream_with_context

try:
    from webapp.graphql_client import API_URL, api_auth_headers
except ModuleNotFoundError:  # pragma: no cover - direct ``python webapp/app.py`` path.
    from graphql_client import API_URL, api_auth_headers


def _stream_api_response(url: str, expected_statuses: tuple[int, ...]) -> tuple:
    """Open a streaming GET against the API; abort on the expected error statuses."""
    import httpx

    client = httpx.Client(timeout=None)
    upstream = client.send(
        client.build_request("GET", url, headers=api_auth_headers()), stream=True
    )
    if upstream.status_code in expected_statuses:
        status = upstream.status_code
        upstream.close()
        client.close()
        abort(status)
    upstream.raise_for_status()

    def _pump():
        try:
            yield from upstream.iter_bytes(chunk_size=1 << 20)
        finally:
            upstream.close()
            client.close()

    return upstream, _pump


def document_download(doc_id):
    """Stream an original document download through the API boundary."""
    base = API_URL.rsplit("/graphql", 1)[0]
    upstream, pump = _stream_api_response(
        f"{base}/documents/{doc_id}/download", (401, 403, 404, 409)
    )
    headers = {}
    if upstream.headers.get("content-disposition"):
        headers["Content-Disposition"] = upstream.headers["content-disposition"]
    return Response(
        stream_with_context(pump()),
        mimetype=upstream.headers.get("content-type", "application/octet-stream"),
        headers=headers,
    )


def document_page_image(doc_id, page_number):
    """Stream a rendered original-PDF page PNG through the API boundary."""
    base = API_URL.rsplit("/graphql", 1)[0]
    upstream, pump = _stream_api_response(
        f"{base}/documents/{doc_id}/pages/{page_number}.png", (401, 403, 404, 409, 415)
    )
    headers = {}
    if upstream.headers.get("x-docray-cache"):
        headers["X-DocRay-Cache"] = upstream.headers["x-docray-cache"]
    return Response(
        stream_with_context(pump()),
        mimetype=upstream.headers.get("content-type", "image/png"),
        headers=headers,
    )


def document_cover(doc_id):
    """Stream a small WebP first-page cover through the API boundary.

    Lets list surfaces render a ~15 KB thumbnail instead of proxying the full
    144-DPI page PNG. ``w`` is forwarded and the API clamps it to its allowlist.
    """
    base = API_URL.rsplit("/graphql", 1)[0]
    width = request.args.get("w", type=int)
    query = f"?w={width}" if width else ""
    upstream, pump = _stream_api_response(
        f"{base}/documents/{doc_id}/cover.webp{query}", (401, 403, 404, 409, 415)
    )
    headers = {}
    for header in ("cache-control", "x-docray-cache"):
        if upstream.headers.get(header):
            headers[header.title()] = upstream.headers[header]
    return Response(
        stream_with_context(pump()),
        mimetype=upstream.headers.get("content-type", "image/webp"),
        headers=headers,
    )


def register(app) -> None:
    """Attach the original-asset streaming routes to *app* (D-015 split).

    Takes only *app*: these routes reach the corpus through the API's HTTP
    boundary, never the database, so they need no late-bound ``core`` module.
    """
    app.add_url_rule("/doc/<uuid:doc_id>/download", "document_download", document_download)
    app.add_url_rule(
        "/doc/<uuid:doc_id>/page/<int:page_number>.png",
        "document_page_image",
        document_page_image,
    )
    app.add_url_rule("/doc/<uuid:doc_id>/cover.webp", "document_cover", document_cover)
