#!/usr/bin/env python3
"""Detect drift between this skill and Doc-Ray's core GraphQL structure."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SKILL_NAME = "doc-ray-graphql-maximizer"
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")

REQUIRED_PATHS = [
    "README.md",
    "CHANGELOG.md",
    "skills/doc-ray-graphql-maximizer/scripts/list_documents.py",
    "src/doc_ray/entrypoints/api/schema.py",
    "src/doc_ray/entrypoints/api/query.py",
    "src/doc_ray/entrypoints/api/mutation.py",
    "src/doc_ray/entrypoints/api/security.py",
    "src/doc_ray/entrypoints/api/context.py",
    "src/doc_ray/entrypoints/api/types.py",
    "src/doc_ray/entrypoints/api/layer_types.py",
    "src/doc_ray/entrypoints/api/canonical_types.py",
    "src/doc_ray/entrypoints/api/app.py",
    "src/doc_ray/config.py",
    "webapp/app.py",
    "db/migrations/018_rbac.sql",
    "db/migrations/030_principal_login_roles.sql",
    "docs/architecture.md",
    "docs/webapp_user_manual.md",
]

EXPECTED_DOCUMENT_FIELDS = {
    "id",
    "title",
    "status",
    "cursor",
    "authors",
    "tags",
    "metadata",
    "sentenceCount",
    "tokenCount",
    "sentences",
    "provenance",
    "annotationRuns",
    "tokens",
    "dependencies",
    "chunks",
    "corefChains",
    "entityLinks",
}


def skill_root() -> Path:
    """Return the root directory of this skill package."""
    return Path(__file__).resolve().parents[1]


def repo_root(explicit: Path | None = None) -> Path:
    """Resolve the Doc-Ray repository root."""
    if explicit is not None:
        return explicit.resolve()
    env_root = os.environ.get("DOCRAY_REPO_ROOT")
    if env_root:
        return Path(env_root).resolve()
    for start in (Path.cwd(), Path(__file__).resolve()):
        for candidate in (start, *start.parents):
            if (candidate / "src" / "doc_ray" / "entrypoints" / "api" / "schema.py").exists():
                return candidate
    raise RuntimeError("Could not locate Doc-Ray repo root. Set DOCRAY_REPO_ROOT.")


def read_text(path: Path) -> str:
    """Read UTF-8 text from *path*."""
    return path.read_text(encoding="utf-8")


def check_package_metadata(root: Path, errors: list[str]) -> None:
    """Validate SemVer package metadata and documentation pointers."""
    version_path = root / "VERSION"
    if not version_path.exists():
        errors.append("missing VERSION")
        return
    version = read_text(version_path).strip()
    if not SEMVER_RE.match(version):
        errors.append(f"VERSION is not SemVer MAJOR.MINOR.PATCH: {version!r}")
    readme = root / "README.md"
    changelog = root / "CHANGELOG.md"
    for path in (readme, changelog):
        if not path.exists():
            errors.append(f"missing {path.name}")
    if readme.exists() and f"Version: `{version}`" not in read_text(readme):
        errors.append("README.md does not mention the current VERSION")
    if changelog.exists() and f"## [{version}]" not in read_text(changelog):
        errors.append("CHANGELOG.md has no entry for the current VERSION")
    skill_md = root / "SKILL.md"
    if not skill_md.exists():
        errors.append("missing SKILL.md")
    elif f"Package version: `{version}`." not in read_text(skill_md):
        errors.append("SKILL.md body does not mention the current VERSION")


def schema_digest(root: Path) -> dict[str, object]:
    """Run the bundled schema digest script and return its JSON output."""
    script = skill_root() / "scripts" / "schema_digest.py"
    env = dict(os.environ)
    env["DOCRAY_REPO_ROOT"] = str(root)
    proc = subprocess.run(
        [sys.executable, str(script), "--json"],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )
    return json.loads(proc.stdout)


def check_repo_structure(root: Path, errors: list[str]) -> None:
    """Validate the Doc-Ray files the skill depends on still exist."""
    missing = [rel for rel in REQUIRED_PATHS if not (root / rel).exists()]
    errors.extend(f"missing required Doc-Ray path: {rel}" for rel in missing)
    app_text = read_text(root / "src/doc_ray/entrypoints/api/app.py")
    if 'prefix="/graphql"' not in app_text:
        errors.append("GraphQL router is no longer mounted at /graphql in app.py")
    if "/health" not in app_text or "/readyz" not in app_text:
        errors.append("app.py no longer exposes both /health and /readyz")


def require_text(path: Path, needles: list[str], errors: list[str], label: str) -> None:
    """Require every string in *needles* to appear in *path*."""
    text = read_text(path)
    missing = [needle for needle in needles if needle not in text]
    if missing:
        errors.append(f"{label} missing expected text: {', '.join(missing)}")


def documented_root_operations(path: Path) -> dict[str, set[str]]:
    """Read the exhaustive Query and Mutation inventory from the playbook."""
    inventory = {"Query": set(), "Mutation": set()}
    in_inventory = False
    current_root: str | None = None
    for line in read_text(path).splitlines():
        if line == "## Root Operation Inventory":
            in_inventory = True
            current_root = None
            continue
        if in_inventory and line.startswith("## "):
            break
        if not in_inventory:
            continue
        if line in {"### Query", "### Mutation"}:
            current_root = line.removeprefix("### ")
            continue
        if current_root is not None:
            inventory[current_root].update(re.findall(r"`([a-z][A-Za-z0-9]*)`", line))
    return inventory


def check_auth_visibility_contract(root: Path, errors: list[str]) -> None:
    """Validate source anchors for the API/webapp/RLS visibility model."""
    require_text(
        root / "src/doc_ray/entrypoints/api/security.py",
        [
            '_CLIENT_ROLE = "docray_api"',
            "ANONYMOUS = Principal",
            "SET ROLE",
            "app.principal_id",
        ],
        errors,
        "API security contract",
    )
    require_text(
        root / "src/doc_ray/config.py",
        ["DOCRAY_API_TOKENS", "DOCRAY_API_ALLOW_ANONYMOUS"],
        errors,
        "API auth config",
    )
    require_text(
        root / "webapp/app.py",
        ["DOCRAY_PRINCIPAL_ID", "BYPASSES RLS", "docray_client"],
        errors,
        "webapp RLS contract",
    )
    require_text(
        root / "db/migrations/018_rbac.sql",
        [
            "document_acl",
            "docray_readable_documents",
            "docray_writable_documents",
            "BYPASSES RLS",
        ],
        errors,
        "RBAC migration contract",
    )


def check_schema(root: Path, errors: list[str], warnings: list[str]) -> None:
    """Validate the live Strawberry schema fields used by this skill."""
    digest = schema_digest(root)
    queries = set(digest.get("root_queries", []))
    mutations = set(digest.get("root_mutations", []))
    focus = digest.get("focus_types", {})
    document_fields = set(focus.get("Document", [])) if isinstance(focus, dict) else set()
    missing_doc_fields = sorted(EXPECTED_DOCUMENT_FIELDS - document_fields)
    if missing_doc_fields:
        errors.append(f"schema missing expected Document fields: {', '.join(missing_doc_fields)}")
    playbook = skill_root() / "references" / "graphql-value-playbook.md"
    documented = documented_root_operations(playbook)
    for root_name, live in (("Query", queries), ("Mutation", mutations)):
        missing = sorted(live - documented[root_name])
        stale = sorted(documented[root_name] - live)
        if missing:
            errors.append(f"playbook missing live {root_name} fields: {', '.join(missing)}")
        if stale:
            errors.append(f"playbook lists stale {root_name} fields: {', '.join(stale)}")
    if warnings:
        raise AssertionError("schema drift must fail closed, not emit warnings")


def main(argv: list[str] | None = None) -> int:
    """Run the drift checker."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable result.")
    args = parser.parse_args(argv)

    errors: list[str] = []
    warnings: list[str] = []
    skill = skill_root()
    root = repo_root(args.repo_root)

    check_package_metadata(skill, errors)
    check_repo_structure(root, errors)
    check_auth_visibility_contract(root, errors)
    check_schema(root, errors, warnings)

    result = {
        "skill": SKILL_NAME,
        "skill_root": str(skill),
        "repo_root": str(root),
        "errors": errors,
        "warnings": warnings,
        "ok": not errors,
    }
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"{SKILL_NAME}: {'OK' if result['ok'] else 'DRIFT'}")
        for warning in warnings:
            print(f"WARNING: {warning}")
        for error in errors:
            print(f"ERROR: {error}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
