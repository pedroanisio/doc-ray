#!/usr/bin/env python3
"""Report GraphQL root fields without a webapp binding or reasoned exception.

A capability is shipped only when a human can reach it in ``webapp/`` or the
API-only ledger states why it deliberately has no browser binding. This static,
offline gate complements ``tests/test_read_surface_parity.py``: that test checks
read-model shape/signatures, while this checker inventories human reachability.

Usage::

    python scripts/check_surface_parity.py
    python scripts/check_surface_parity.py --strict

Exit codes: report mode always exits 0; ``--strict`` exits 1 on any finding.
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_API_ROOT = _ROOT / "src/doc_ray/entrypoints/api"
DEFAULT_WEBAPP_ROOT = _ROOT / "webapp"

# Explicit exception ledger. Every entry names the intentional human or
# operational alternative; an empty/stale entry fails the gate.
_API_ONLY_ALLOWLIST: dict[str, str] = {
    # The collection webapp keeps this relation in its existing direct-SQL route.
    "addDocumentToCollection": "The Collections page adds members through its RLS-scoped SQL form.",
    # The human lifecycle workflow is the selection-based bulk archive action.
    "archiveDocument": "The Corpus page exposes archive through bulkArchiveDocuments.",
    # Security audit inspection belongs to the separate operator console.
    "auditEvents": "The admin-console Audit page is the dedicated human surface.",
    # Both UIs resolve the deployment toggle locally; the admin console also queries it.
    "backupEnabled": "Webapp nav uses the same env capability; admin-console queries this field.",
    # Cross-lens allusion edges (SLP Block E) are an agent/API + MCP curation primitive;
    # the read webapp does not yet surface them.
    "allusions": "Cross-lens allusion edges are an agent/MCP retrieval primitive (Block E).",
    # Billing is intentionally isolated in the privileged operator console.
    "billingEvents": "The admin-console Billing page is the dedicated human surface.",
    "billingStatus": "The admin-console Billing page is the dedicated human surface.",
    # Job cancellation is a privileged operational action in the admin console.
    "cancelJob": "The admin-console Jobs page exposes this admin-only action.",
    # Review uses the bounded canonical resolver; fuzzy registry discovery stays agent/API-only.
    "canonicalSearch": "The Review page uses canonical; fuzzy registry search is agent/API-only.",
    # Genre classification (SLP Block C) is an agent/API + MCP curation action; the read
    # webapp does not yet surface genre (this block defines the policy, Block D applies it).
    "classifyDocument": "Genre classification is an agent/API + MCP curation action (Block C).",
    "confirmDocumentClassification": (
        "Human genre confirmation is an agent/API + MCP verification action (Block C)."
    ),
    # Lens-scoped identity review (SLP Block D) is an agent/API + MCP verification action;
    # the read webapp does not yet surface projections.
    "confirmProjection": "Projection confirmation is an agent/API + MCP review action (Block D).",
    "correctEntitySurface": (
        "Systematic exact-surface correction is an agent/API + MCP curation action (#41)."
    ),
    "confirmAllusion": "Allusion confirmation is an agent/API + MCP review action (Block E).",
    # Shards are orchestration primitives rather than an end-user corpus interaction.
    "corpusManifest": "Deterministic fan-out metadata is an agent/operations primitive.",
    "corpusShard": "Deterministic shard traversal is an agent/operations primitive.",
    # Hosted billing redirects are privileged controls in the admin console.
    "createBillingCheckoutSession": "The admin-console Billing page owns hosted checkout.",
    "createBillingPortalSession": "The admin-console Billing page owns hosted portal access.",
    # Collection CRUD predates GraphQL and remains one RLS-scoped direct-SQL web flow.
    "createCollection": "The Collections page creates collections through its direct-SQL form.",
    "deleteCollection": "Collection administration remains in the direct-SQL web route.",
    # Hard deletion is deliberately absent from the general corpus browser.
    "deleteDocument": "Permanent deletion is API/operator-only; the browser offers archive.",
    # The genre axis is an agent/MCP retrieval primitive; the read webapp does not yet
    # surface it (Block C defines the resolution policy, Block D applies it).
    "documentClassification": "The genre axis is an agent/MCP retrieval primitive (Block C).",
    # Reader/operator pages render this audit trail from the RLS-scoped table directly.
    "documentMetadataHistory": "Reader and operator pages load metadata history through SQL.",
    # Outline is a compact agent retrieval primitive; people read the anchored document view.
    "documentOutline": "Agents consume the compact outline; people use the document reader.",
    # Corpus-scale embedding commands are expensive administrator operations.
    "embedCorpusSentences": "Corpus re-embedding is an audited operator script/admin action.",
    # Per-document embedding is exposed as the reader's semantic reprocessing action.
    "embedDocumentSentences": "Reader reprocessing exposes this as Refresh semantic search.",
    # The document sidebar computes the same visible counts through bounded SQL.
    "entityFrequency": "The document entity panel reads per-document frequencies via SQL.",
    # Correction history is an API audit surface; the browser exposes the actionable inbox.
    "entityMentionCorrections": (
        "The Review inbox exposes pending corrections; history is API-only."
    ),
    "entitySurfaceOccurrences": (
        "Exact-surface impact preview is an agent/API + MCP curation primitive (#41)."
    ),
    # Structured profiles are optimized for agent traversal, while the reader shows entities.
    "entityProfiles": "Entity profiles are an agent/MCP structural-retrieval primitive.",
    # Lens-scoped identity (SLP Block D) is an agent/MCP retrieval primitive; the read
    # webapp does not yet surface projections.
    "entityProjection": "Lens-scoped identity is an agent/MCP retrieval primitive (Block D).",
    # Browser keyword search uses its established direct-SQL sentence query.
    "fullTextSearch": "Search and Briefing provide human retrieval through their own bindings.",
    # Per-lens entity survey (SLP Block D/F) is an agent/MCP retrieval primitive.
    "lensEntities": "The per-lens entity rollup is an agent/MCP retrieval primitive (Block D/F).",
    # Hyper-context relationship discovery (SLP Block F) is an agent/MCP retrieval primitive.
    "entityRelations": "Co-occurrence relations are an agent/MCP retrieval primitive (Block F).",
    "relatedEntities": "Bounded N-hop discovery is an agent/MCP retrieval primitive (Block F).",
    # Fleet-wide re-extraction is privileged and lives in the admin console.
    "reextractDocuments": "The admin-console Jobs page exposes this admin-only batch action.",
    # The collection member form owns the same relation through RLS-scoped SQL.
    "removeDocumentFromCollection": "The Collections page removes members through SQL.",
    # Restore is intentionally an API/operator recovery verb, not a general-user action.
    "restoreDocument": "Deleted-document recovery is API/operator-only; browser lists active docs.",
    # Lens-scoped identity review (SLP Block D) is an agent/API + MCP verification action.
    "rejectProjection": "Projection rejection is an agent/API + MCP review action (Block D).",
    "proposeAllusion": "Proposing a cross-lens allusion is an agent/API curation action (Block E).",
    "rejectAllusion": "Allusion rejection is an agent/API + MCP review action (Block E).",
    # Billing-event replay is a privileged control in the admin console.
    "retryBillingEvent": "The admin-console Billing page exposes this admin-only retry.",
    # Browser keyword search performs the same RLS-scoped sentence read through SQL.
    "searchSentences": "The Search page binds sentence search through its indexed SQL read.",
    # The seeding and unified review pages read the proposal table directly.
    "seedSuggestions": "Seeding and Review pages render suggestions through RLS-scoped SQL.",
    # Lens projection designation (SLP Block D) is an agent/API curation action.
    "setLensProjects": "Designating a projecting lens is an agent/API curation action (Block D).",
    # The reader's word-frequency panel uses bounded direct SQL over the same document.
    "termFrequency": "The document reader renders per-document word frequency through SQL.",
    # Block G deliberately ships agent/API review access first; its issue excludes
    # a dedicated browser queue while retaining correctEntityMention as the action.
    "uncertainMentions": (
        "Mention-confidence triage is an agent/API + MCP review primitive (Block G)."
    ),
    # Collection editing remains within the existing direct-SQL web flow.
    "updateCollection": "The Collections page edits collection state through its SQL route.",
}

Status = Literal["surfaced", "allowlisted", "unsurfaced"]


@dataclass(frozen=True)
class SurfaceField:
    """One GraphQL root field and its human-surface classification."""

    name: str
    status: Status
    reason: str = ""


@dataclass(frozen=True)
class SurfaceReport:
    """Deterministic field inventory plus gate violations."""

    fields: tuple[SurfaceField, ...]
    violations: tuple[str, ...]


def snake_to_camel(name: str) -> str:
    """Convert Strawberry's default Python snake_case field name to camelCase."""
    head, *tail = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail if part)


def _python_files(root: Path) -> list[Path]:
    """Return sorted Python files below *root*, excluding bytecode caches."""
    if root.is_file():
        return [root] if root.suffix == ".py" else []
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _is_root_container(node: ast.ClassDef) -> bool:
    return node.name in {"Query", "Mutation"} or node.name.endswith(("Queries", "Mutations"))


def _decorator_name(decorator: ast.expr) -> str | None:
    if isinstance(decorator, ast.Call):
        decorator = decorator.func
    if (
        isinstance(decorator, ast.Attribute)
        and isinstance(decorator.value, ast.Name)
        and decorator.value.id == "strawberry"
    ):
        return decorator.attr
    return None


def _api_fields(api_root: Path) -> tuple[set[str], list[str]]:
    fields: set[str] = set()
    errors: list[str] = []
    for path in _python_files(api_root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            errors.append(f"PARSE-ERROR {path}: {exc}")
            continue
        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or not _is_root_container(node):
                continue
            for member in node.body:
                if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                decorators = {_decorator_name(item) for item in member.decorator_list}
                if decorators & {"field", "mutation"}:
                    fields.add(snake_to_camel(member.name))
    return fields, errors


def _webapp_source(webapp_root: Path) -> tuple[str, list[str]]:
    chunks: list[str] = []
    errors: list[str] = []
    for path in _python_files(webapp_root):
        try:
            chunks.append(path.read_text(encoding="utf-8"))
        except OSError as exc:
            errors.append(f"READ-ERROR {path}: {exc}")
    return "\n".join(chunks), errors


def analyze(
    api_root: Path = DEFAULT_API_ROOT,
    webapp_root: Path = DEFAULT_WEBAPP_ROOT,
    *,
    allowlist: Mapping[str, str] | None = None,
) -> SurfaceReport:
    """Classify root fields and return all unsurfaced/malformed-ledger findings."""
    ledger = dict(_API_ONLY_ALLOWLIST if allowlist is None else allowlist)
    api_fields, violations = _api_fields(api_root)
    web_source, read_errors = _webapp_source(webapp_root)
    violations.extend(read_errors)
    violations.extend(
        f"EMPTY-REASON {field}" for field, reason in sorted(ledger.items()) if not reason.strip()
    )

    classified: list[SurfaceField] = []
    for field in sorted(api_fields):
        reason = ledger.get(field)
        if field in web_source:
            classified.append(SurfaceField(field, "surfaced"))
        elif reason is not None and reason.strip():
            classified.append(SurfaceField(field, "allowlisted", reason.strip()))
        else:
            classified.append(SurfaceField(field, "unsurfaced"))
            if reason is None:
                violations.append(f"UNSURFACED {field}")

    for field in sorted(ledger.keys() - api_fields):
        violations.append(f"STALE-ALLOWLIST {field}")
    return SurfaceReport(tuple(classified), tuple(sorted(violations)))


def find_violations(
    api_root: Path = DEFAULT_API_ROOT,
    webapp_root: Path = DEFAULT_WEBAPP_ROOT,
    *,
    allowlist: Mapping[str, str] | None = None,
) -> list[str]:
    """Return the pure violation list used by CI and unit tests."""
    return list(analyze(api_root, webapp_root, allowlist=allowlist).violations)


def main(
    argv: Sequence[str] | None = None,
    *,
    allowlist: Mapping[str, str] | None = None,
) -> int:
    """Run the report, enforcing findings only when ``--strict`` is selected."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="Exit 1 on any finding.")
    parser.add_argument("--api-root", type=Path, default=DEFAULT_API_ROOT)
    parser.add_argument("--webapp-root", type=Path, default=DEFAULT_WEBAPP_ROOT)
    args = parser.parse_args(argv)

    report = analyze(args.api_root, args.webapp_root, allowlist=allowlist)
    for field in report.fields:
        suffix = f" — {field.reason}" if field.reason else ""
        print(f"  {field.status.upper():11} {field.name}{suffix}")
    if report.violations:
        for violation in report.violations:
            print(f"  {violation}")
        tag = "VIOLATION" if args.strict else "REPORT"
        print(
            f"SURFACE-PARITY {tag}: {len(report.violations)} finding(s) across "
            f"{len(report.fields)} GraphQL root fields."
        )
        return 1 if args.strict else 0
    print(f"SURFACE-PARITY OK: {len(report.fields)} GraphQL root fields classified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
