#!/usr/bin/env python3
"""Print a compact digest of the live Doc-Ray GraphQL schema."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


def _repo_root() -> Path:
    env_root = os.environ.get("DOCRAY_REPO_ROOT")
    if env_root:
        return Path(env_root).resolve()
    for start in (Path.cwd(), Path(__file__).resolve()):
        for candidate in (start, *start.parents):
            if (candidate / "src" / "doc_ray" / "entrypoints" / "api" / "schema.py").exists():
                return candidate
    raise RuntimeError(
        "Could not locate the doc-ray checkout. Run this from the repo root or set "
        "DOCRAY_REPO_ROOT."
    )


def _schema_sdl() -> str:
    root = _repo_root()
    sys.path.insert(0, str(root / "src"))
    from doc_ray.entrypoints.api.schema import build_schema

    return str(build_schema())


def _type_blocks(sdl: str) -> dict[str, list[str]]:
    blocks: dict[str, list[str]] = {}
    for match in re.finditer(r"^type\s+(\w+)\s*\{([^}]*)\}", sdl, flags=re.MULTILINE):
        name = match.group(1)
        fields: list[str] = []
        for raw in match.group(2).splitlines():
            line = raw.strip()
            if not line or line.startswith('"""'):
                continue
            field = re.split(r"[\(:]", line, maxsplit=1)[0].strip()
            if field:
                fields.append(field)
        blocks[name] = fields
    return blocks


def _digest() -> dict[str, object]:
    blocks = _type_blocks(_schema_sdl())
    focus = [
        "Query",
        "Mutation",
        "Document",
        "Lexeme",
        "SentenceHit",
        "EntityMention",
        "CanonicalEntity",
        "ResolutionReview",
        "Job",
        "SeedSuggestion",
    ]
    return {
        "root_queries": blocks.get("Query", []),
        "root_mutations": blocks.get("Mutation", []),
        "focus_types": {name: blocks[name] for name in focus if name in blocks},
        "type_count": len(blocks),
    }


def main(argv: list[str] | None = None) -> int:
    """Run the schema digest command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of Markdown.")
    args = parser.parse_args(argv)
    digest = _digest()
    if args.json:
        print(json.dumps(digest, indent=2, sort_keys=True))
        return 0
    print("# Doc-Ray GraphQL Schema Digest")
    print()
    print(f"Type count: {digest['type_count']}")
    print()
    print("## Root queries")
    for field in digest["root_queries"]:
        print(f"- `{field}`")
    print()
    print("## Root mutations")
    for field in digest["root_mutations"]:
        print(f"- `{field}`")
    print()
    print("## Focus types")
    for name, fields in digest["focus_types"].items():
        joined = ", ".join(f"`{field}`" for field in fields)
        print(f"- `{name}`: {joined}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
