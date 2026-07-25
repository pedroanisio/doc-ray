"""Render .env.example and .env.prod.example from config/env.registry.yaml.

The registry is the single source of truth for every runtime environment
variable: key, one-line description, and code-bound default. This generator
writes the two tracked sample files from it, so their descriptions and defaults
can never drift apart (they used to be maintained by hand in two places).

Run after editing the registry; tests/test_env_registry.py fails if the
committed samples are not exactly this output.

    python scripts/gen_env_samples.py            # write both files
    python scripts/gen_env_samples.py --check     # exit 1 if they are stale
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "config" / "env.registry.yaml"

_GENERATED_BANNER = (
    "# GENERATED FILE — do not edit by hand.\n"
    "# Source of truth: config/env.registry.yaml\n"
    "# Regenerate: python scripts/gen_env_samples.py\n"
)

# Human-ordered groups; anything else sorts after, alphabetically.
_GROUP_ORDER = [
    "database",
    "object-store",
    "email",
    "auth",
    "mcp",
    "webapp",
    "worker",
    "backup",
    "extraction",
    "resolver",
    "vision",
    "billing",
    "observability",
    "ops",
    "other",
]
_GROUP_TITLES = {
    "database": "Database",
    "object-store": "Object store (S3 / Spaces / MinIO)",
    "email": "Outbound email (SMTP)",
    "auth": "Authentication & sessions",
    "mcp": "MCP server",
    "webapp": "Webapp",
    "worker": "Workers & job queue",
    "backup": "Backups",
    "extraction": "Document extraction",
    "resolver": "Entity resolver (LLM)",
    "vision": "Visual pipeline (Prism)",
    "billing": "Billing (Stripe)",
    "observability": "Logging & observability",
    "ops": "Operations & tooling",
    "other": "Other",
}


def load_registry() -> list[dict]:
    """Registry entries that belong in the tracked env samples.

    ``app``-scoped keys are read by application code; ``compose``-scoped keys
    (POSTGRES_* and friends) are consumed by docker-compose from the same .env
    file, so both must appear in the samples. ``system``-scoped keys stay out.
    """
    data = yaml.safe_load(REGISTRY.read_text())
    return [v for v in data["variables"] if v.get("scope", "app") in ("app", "compose")]


def _wrap_comment(text: str) -> list[str]:
    return ["# " + line for line in textwrap.wrap(text, width=78)] or ["#"]


def _render(variables: list[dict], *, prod: bool) -> str:
    by_group: dict[str, list[dict]] = {}
    for v in variables:
        by_group.setdefault(v["group"], []).append(v)
    order = _GROUP_ORDER + sorted(set(by_group) - set(_GROUP_ORDER))

    out: list[str] = [_GENERATED_BANNER.rstrip("\n")]
    header = (
        "# doc-ray PRODUCTION environment sample. Copy to .env.prod and fill in "
        "real values.\n# .env.prod is gitignored and holds every production "
        "credential."
        if prod
        else "# doc-ray DEVELOPMENT environment sample. Copy to .env for local work.\n"
        "# Defaults below target the local compose stack (Postgres + MinIO)."
    )
    out.append("")
    out.extend(header.splitlines())

    for group in order:
        entries = sorted(by_group.get(group, []), key=lambda e: e["key"])
        if not entries:
            continue
        out.append("")
        out.append(f"# ===== {_GROUP_TITLES.get(group, group.title())} " + "=" * 10)
        for v in entries:
            out.append("")
            out.extend(_wrap_comment(v["description"]))
            secret = v.get("secret", False)
            default = v.get("default")
            if prod:
                # Never bake a real secret into a tracked sample; leave a placeholder.
                if secret:
                    out.append(f"{v['key']}=   # SECRET — set in .env.prod")
                    continue
                # A prod-specific value (managed cluster, in-network host, etc.)
                # wins over the dev default in the production sample.
                value = v.get("prod", default)
                out.append(f"{v['key']}={'' if value is None else value}")
            else:
                if default is None:
                    # No code default: opt-in / required. Comment it out so an
                    # empty dev env still runs.
                    out.append(f"# {v['key']}=")
                else:
                    out.append(f"{v['key']}={default}")
    return "\n".join(out) + "\n"


def render_all() -> dict[Path, str]:
    variables = load_registry()
    return {
        ROOT / ".env.example": _render(variables, prod=False),
        ROOT / ".env.prod.example": _render(variables, prod=True),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 if samples are stale")
    args = parser.parse_args(argv)
    rendered = render_all()
    if args.check:
        stale = [p.name for p, text in rendered.items() if not p.exists() or p.read_text() != text]
        if stale:
            print(f"stale sample(s): {stale} — run: python scripts/gen_env_samples.py")
            return 1
        print("env samples in sync with config/env.registry.yaml")
        return 0
    for path, text in rendered.items():
        path.write_text(text)
        print(f"wrote {path.name} ({text.count(chr(10))} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
