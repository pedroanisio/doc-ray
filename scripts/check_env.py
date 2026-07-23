"""Validate a real .env / .env.prod against the canonical registry.

Unlike tests/test_env_registry.py (which guards the tracked registry + samples
in CI), this checks an operator's actual, gitignored env file on a host:

* unknown keys      — typos or stale variables no code reads
* missing keys      — required (no-default) variables that are unset
* dev-points-at-prod — a .env whose DB/object-store targets the production
  infrastructure, the footgun where `make truncate-db` (which sources .env)
  would act on the live cluster.

    python scripts/check_env.py .env
    python scripts/check_env.py .env.prod --prod

Exit 0 clean, 1 warnings, 2 errors. Never prints secret values.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "config" / "env.registry.yaml"

# Substrings that mark a value as pointing at production infrastructure.
_PROD_MARKERS = ("ondigitalocean.com", "digitaloceanspaces.com", "doc-ray-prd")


def _parse_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(?:export\s+)?([A-Z][A-Z0-9_]*)=(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("env_file", help="path to the .env file to validate")
    parser.add_argument("--prod", action="store_true", help="this file targets production")
    args = parser.parse_args(argv)

    path = Path(args.env_file)
    if not path.exists():
        print(f"error: {path} does not exist")
        return 2

    registry = {v["key"]: v for v in yaml.safe_load(REGISTRY.read_text())["variables"]}
    present = _parse_env(path)
    warnings: list[str] = []
    errors: list[str] = []

    # Unknown keys (not in the registry, not compose/system scope).
    known = set(registry)
    compose_system = {
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "POSTGRES_PORT",
        "MINIO_PORT",
        "MINIO_CONSOLE_PORT",
        "WEBAPP_PORT",
        "NGINX_HTTP_PORT",
        "NGINX_HTTPS_PORT",
        "ADMIN_CONSOLE_PORT",
        "TZ",
        "COMPOSE_PROJECT_NAME",
        # compose-only ${VAR:-default} substitution vars, never read by app code
        "DOCRAY_WORKERS",
        "OLLAMA_KEEP_ALIVE",
        "OLLAMA_PORT",
        "OLLAMA_CONTEXT_LENGTH",
        "PGADMIN_EMAIL",
        "PGADMIN_PASSWORD",
        "PGADMIN_PORT",
        "CLOUDFLARE_TUNNEL_TOKEN",
        "DOCRAY_DB_HEALTH_INTERVAL_SECONDS",  # compose CLI arg to db-health-monitor
    }
    for key in sorted(present):
        if key not in known and key not in compose_system:
            warnings.append(f"unknown key {key} (no code reads it — typo or stale?)")

    # Required-in-prod essentials that are unset (dev defaults would be wrong or
    # insecure, and no compose overlay supplies them).
    optional_off: list[str] = []
    for key, entry in registry.items():
        if entry["scope"] != "app":
            continue
        is_set = bool(present.get(key))
        if entry.get("required_in_prod") and not is_set:
            (errors if args.prod else warnings).append(
                f"{key} is required in production but unset ({entry['description'][:55]})"
            )
        elif entry["default"] is None and not is_set:
            # No code default and not required: an opt-in feature, simply off.
            optional_off.append(key)

    # Dev file that reaches production infrastructure. A `.env` that is an
    # intentional symlink to `.env.prod` (the per-host prod convention) is not
    # the footgun this guards against — skip it.
    symlinked_to_prod = path.is_symlink() and path.resolve().name == ".env.prod"
    prod_hits = []
    for key in ("DATABASE_URL", "S3_ENDPOINT", "S3_BUCKET"):
        val = present.get(key, "")
        if any(marker in val for marker in _PROD_MARKERS):
            prod_hits.append(key)
    if prod_hits and not args.prod and not symlinked_to_prod:
        errors.append(
            f"{path.name} targets PRODUCTION via {prod_hits} but is not the prod file. "
            "Tooling that sources this file (e.g. `make truncate-db`) would act on the live "
            "cluster. Point these at the local dev stack, or use .env.prod for prod work."
        )

    for kind, items in (("errors", errors), ("warnings", warnings)):
        for item in items:
            print(f"{kind[:-1].upper():7} {item}")
    if optional_off:
        joined = ", ".join(sorted(optional_off))
        print(f"INFO    {len(optional_off)} optional feature(s) unset (off): {joined}")
    if not errors and not warnings:
        print(f"OK  {path.name}: {len(present)} keys, all required-in-prod present, no prod-drift.")
    return 2 if errors else (1 if warnings else 0)


if __name__ == "__main__":
    sys.exit(main())
