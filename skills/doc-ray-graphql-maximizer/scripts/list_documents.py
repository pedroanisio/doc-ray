#!/usr/bin/env python3
"""List Doc-Ray GraphQL documents with safe auth and pagination."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DOCS_QUERY = """
query Docs($limit: Int!, $after: String) {
  documents(limit: $limit, after: $after) {
    id
    title
    docType
    language
    status
    pageCount
    charCount
    sentenceCount
    tokenCount
    authors
    tags
    cursor
  }
}
""".strip()


def read_env_file(path: Path) -> dict[str, str]:
    """Read simple KEY=VALUE pairs from an env file."""
    values: dict[str, str] = {}
    if not path.exists():
        raise FileNotFoundError(f"env file not found: {path}")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def first_configured_token(raw: str | None) -> str | None:
    """Return the first token key from a DOCRAY_API_TOKENS value."""
    if not raw:
        return None
    for entry in raw.replace(";", ",").split(","):
        item = entry.strip()
        if item and "=" in item:
            token, _principal = item.split("=", 1)
            token = token.strip()
            if token:
                return token
    return None


def request_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    """POST JSON to *url* and decode the JSON response."""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def get_json(url: str) -> dict[str, Any]:
    """GET *url* and decode the JSON response."""
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def endpoint_base(graphql_url: str) -> str:
    """Return the API base URL for a GraphQL endpoint URL."""
    if graphql_url.endswith("/graphql"):
        return graphql_url[: -len("/graphql")]
    return graphql_url.rstrip("/")


def collect_documents(endpoint: str, token: str | None, limit: int) -> list[dict[str, Any]]:
    """Collect all documents visible to the selected GraphQL caller."""
    headers = {"content-type": "application/json"}
    if token:
        headers["X-DocRay-API-Token"] = token

    documents: list[dict[str, Any]] = []
    after: str | None = None
    for _page in range(1000):
        payload = {"query": DOCS_QUERY, "variables": {"limit": limit, "after": after}}
        response = request_json(endpoint, payload, headers)
        if response.get("errors"):
            raise RuntimeError(json.dumps(response["errors"], indent=2))
        page = response["data"]["documents"]
        documents.extend(page)
        if len(page) < limit:
            return documents
        after = page[-1]["cursor"]
    raise RuntimeError("pagination did not terminate after 1000 pages")


def format_table(documents: list[dict[str, Any]]) -> str:
    """Format document rows as a Markdown table."""
    if not documents:
        return "No documents visible to this GraphQL caller."
    lines = [
        "| # | Title | ID | Status | Pages | Chars | Sentences | Tokens |",
        "|---:|---|---|---|---:|---:|---:|---:|",
    ]
    for index, doc in enumerate(documents, start=1):
        title = str(doc["title"]).replace("|", "\\|")
        lines.append(
            f"| {index} | `{title}` | `{doc['id']}` | {doc['status']} | "
            f"{doc.get('pageCount') or 0:,} | {doc.get('charCount') or 0:,} | "
            f"{doc.get('sentenceCount') or 0:,} | {doc.get('tokenCount') or 0:,} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run the document listing command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("DOCRAY_GRAPHQL_URL", "http://127.0.0.1:8000/graphql"),
        help="GraphQL endpoint URL.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Optional .env file to read DOCRAY_API_TOKENS from without printing it.",
    )
    parser.add_argument(
        "--auth",
        choices=("anonymous", "first-configured-token"),
        default="anonymous",
        help="Authentication mode for the GraphQL request.",
    )
    parser.add_argument("--limit", type=int, default=200, help="Page size.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of a Markdown table.",
    )
    parser.add_argument(
        "--skip-health",
        action="store_true",
        help="Skip /health and /readyz checks.",
    )
    args = parser.parse_args(argv)

    env = dict(os.environ)
    if args.env_file is not None:
        env.update(read_env_file(args.env_file))

    token = None
    auth_mode = "anonymous"
    if args.auth == "first-configured-token":
        token = first_configured_token(env.get("DOCRAY_API_TOKENS"))
        if not token:
            raise RuntimeError("DOCRAY_API_TOKENS is not configured")
        auth_mode = "X-DocRay-API-Token:first-configured-token"

    health: dict[str, Any] | None = None
    ready: dict[str, Any] | None = None
    if not args.skip_health:
        base = endpoint_base(args.endpoint)
        health = get_json(f"{base}/health")
        ready = get_json(f"{base}/readyz")

    documents = collect_documents(args.endpoint, token, args.limit)
    result = {
        "endpoint": args.endpoint,
        "auth_mode": auth_mode,
        "health": health,
        "ready": ready,
        "count": len(documents),
        "documents": documents,
    }
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        if health is not None:
            print(f"API: {health.get('status')} version {health.get('version')}")
        if ready is not None:
            print(f"Ready: {ready.get('ready')} ({ready.get('status')})")
        print(f"Auth: {auth_mode}")
        print(format_table(documents))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, urllib.error.URLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
