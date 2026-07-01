"""engineering_decisions.yml validator — library layer.

Standalone YAML-level validator for the v2 engineering-decisions
schema. Mirrors the rules enforced by the SHACL graphs in
statics/ttl/* but operates directly on the parsed YAML document, so
violations surface without the cost (or the rdflib/pyshacl dependency)
of materialising RDF.

Why a YAML-level validator in addition to SHACL?
  • Fast feedback: parse + validate in milliseconds, run on save.
  • Pre-projection: catches errors before the YAML→RDF step.
  • Plain Python: no rdflib dependency for editor/CI integration.
  • Better messages: rule IDs map 1:1 to the SHACL rule that would
    fire downstream, so a violation here points at exactly one
    shape file/line in the SHACL graph.

What it does NOT replace:
  • SHACL is the authoritative validator. Once the YAML projects to
    RDF, pyshacl runs the full graph (including cross-graph rules
    against results and config). This validator covers only the
    constraints that can be expressed against the YAML document
    itself — about 90 % of the surface, by rule count.
  • Results-graph rules (freshness, latest-fail) are NOT covered:
    they require a separately-loaded results.ttl.

Rule IDs follow `<area>/<rule>` to keep the report scannable:
    record/*          — record-level + per-decision (record_shacl.ttl)
    lifecycle/*       — LifecycleShape interlocks
    consequences/*    — Claim / Check shape + per-kind (consequences_shacl.ttl)
    implementation/*  — Implementation / Step shape + per-kind (implementation_shacl.ttl)
    cross/*           — cross-graph integrity (precondition / postcondition / anchors)
    policy/*          — config policy hints (allowedCheckContexts, strict side-effects)
    category/*        — closed-list category enforcement (config.ttl)

Severity tiers:
    ERROR   — rule violation; emit non-zero exit by default
    WARNING — policy hint that the SHACL graph would flag as a Warning
              (or a project-config hint that depends on a swappable
              policy file)
    INFO    — purely informational
"""
from __future__ import annotations

import dataclasses
import enum
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

__all__ = [
    "Severity", "Finding", "Report",
    "validate", "DEFAULT_CATEGORIES", "DEFAULT_ALLOWED_CHECK_CONTEXTS",
    "VALID_DECISION_STATUS", "VALID_CLAIM_STATUS", "VALID_IMPL_STATUS",
    "VALID_CHECK_KIND", "VALID_STEP_KIND", "VALID_CONTEXT",
    "VALID_SEVERITY", "VALID_SIDE_EFFECTS",
    "CHECK_REQUIRED_FIELDS", "CHECK_EXPECT_OPTIONS", "STEP_REQUIRED_FIELDS",
    "extract_categories_from_config_ttl",
]


# =====================================================================
# Closed enums — kept in sync with the SHACL graphs by hand.
# Sources cited in the comments so a future schema bump knows where to
# look. extract_categories_from_config_ttl() reads the live config.
# =====================================================================

# rec:DecisionStatus — record_shacl.ttl:84-88
VALID_DECISION_STATUS: frozenset[str] = frozenset({
    "proposed", "accepted", "deprecated", "superseded", "withdrawn",
})

# cons:Status — consequences_shacl.ttl:83-86
VALID_CLAIM_STATUS: frozenset[str] = frozenset({
    "populated", "pending", "judgment-only", "subsumed",
})

# impl:Status — implementation_shacl.ttl:88-90
VALID_IMPL_STATUS: frozenset[str] = frozenset({
    "not-applicable", "pending", "populated",
})

# cons:CheckKind — consequences_shacl.ttl:120-130
VALID_CHECK_KIND: frozenset[str] = frozenset({
    "sql", "sql-raises", "grep", "file-exists", "toml-key",
    "file-hash-equals", "python-call", "shell",
    "cross-artifact", "cross-toml",
})

# impl:StepKind — implementation_shacl.ttl:99-105
VALID_STEP_KIND: frozenset[str] = frozenset({
    "shell-write", "python-call", "file-write", "file-patch",
    "toml-set", "sql-exec", "custom",
})

# cons:Context — consequences_shacl.ttl:107-111
VALID_CONTEXT: frozenset[str] = frozenset({
    "workspace", "ci", "runtime", "infra",
})

# cons:Severity — consequences_shacl.ttl:101-104
VALID_SEVERITY: frozenset[str] = frozenset({"error", "warning", "info"})

# cons:SideEffects — consequences_shacl.ttl:114-117
VALID_SIDE_EFFECTS: frozenset[str] = frozenset({"none", "reads", "writes"})

# cfg:policy defaults (config.ttl:182-186). The runtime can override
# these by supplying its own policy dict to validate().
DEFAULT_ALLOWED_CHECK_CONTEXTS: frozenset[str] = frozenset({"workspace", "ci"})

# cfg:CategoryShape — pinned snapshot used when the runtime doesn't
# supply a category list. The live list (read via
# extract_categories_from_config_ttl()) is preferred and authoritative;
# this constant exists so the validator stays usable without the
# config.ttl file.
#
# Extended 2026-05 to add the testing / runtime / performance / security
# / supply-chain axes used by the PGFS quality decisions. See the
# grouped comments in engineering_decisions_config.ttl#cfg:CategoryShape
# for the rationale per group.
DEFAULT_CATEGORIES: frozenset[str] = frozenset({
    # Schema & data modelling
    "constraints", "data-integrity", "data-normalization", "defaults",
    "enums", "identity", "indexing", "naming", "persistence",
    "relationship-semantics", "schema-design", "schema-structure", "typing",
    # Lifecycle & evolution
    "deferred", "evolution", "extensibility", "limitation", "scope", "versioning",
    # Provenance & evidence
    "auditability", "documentation", "provenance",
    # Engineering operations
    "code-quality", "operations", "runtime-target", "tooling",
    # Testing & quality (PGFS, 2026-05)
    "contract-quality", "criticality", "test-automation",
    "test-methodology", "test-technique",
    # Runtime, observability, performance (PGFS, 2026-05)
    "observability", "performance", "runtime-quality",
    # Release, security, supply-chain (PGFS, 2026-05)
    "release-quality", "security", "supply-chain", "technical-debt",
})


# =====================================================================
# Per-kind required-field tables (mirror SHACL SPARQL guards).
# =====================================================================

# consequences_shacl.ttl#CheckShape per-kind SPARQL blocks.
CHECK_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "sql":              ("query",),
    "sql-raises":       ("query",),
    "grep":             ("path", "pattern"),
    "file-exists":      ("path",),
    "toml-key":         ("key",),
    "file-hash-equals": ("path",),
    "python-call":      ("fn", "returns"),
    "shell":            ("command",),
    "cross-artifact":   ("assert_text", "query_db", "grep_file",
                        "grep_pattern", "relation"),
    "cross-toml":       ("left_path", "right_path", "relation"),
}

# `expect:` block: at least one of these keys must be present per kind.
# `None` means the kind doesn't use the `expect:` block (python-call
# encodes the assertion in `returns`; cross-* in `relation`).
CHECK_EXPECT_OPTIONS: dict[str, frozenset[str] | None] = {
    "sql":              frozenset({"rows", "rows_gte", "row_equals",
                                   "row_gte", "row_not_equals"}),
    "sql-raises":       frozenset({"sqlstate"}),
    "grep":             frozenset({"present", "count", "count_gte"}),
    "file-exists":      frozenset({"present"}),
    "toml-key":         frozenset({"present", "equals", "contains"}),
    "file-hash-equals": frozenset({"hash"}),
    "shell":            frozenset({"exit"}),
    "python-call":      None,
    "cross-artifact":   None,
    "cross-toml":       None,
}

# implementation_shacl.ttl#StepShape per-kind SPARQL blocks.
# `custom` is special-cased in the rule: requires XOR(command, fn).
STEP_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "shell-write": ("command",),
    "python-call": ("fn",),
    "file-write":  ("path", "content"),
    "file-patch":  ("path", "patchScript"),
    "toml-set":    ("path", "key", "value"),
    "sql-exec":    ("sql", "target_env"),
    # "custom" handled in code.
}


# =====================================================================
# ID and field patterns
# =====================================================================

DECISION_ID_RE = re.compile(r"^D-\d{3}$")
CLAIM_ID_RE = re.compile(r"^D-\d{3}\.k\d+$")
CHECK_ID_RE = re.compile(r"^D-\d{3}\.k\d+\.c\d+$")
STEP_ID_RE = re.compile(r"^D-\d{3}\.i\d+$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
OWNER_RE = re.compile(r"^\S+$")

# Anchor patterns from config.ttl (Schema/Loader/PyprojectAnchorShape).
ANCHOR_PATTERNS: dict[str, re.Pattern[str]] = {
    "schema_anchor":    re.compile(r"^schema\.sql:[0-9]+"),
    "loader_anchor":    re.compile(r"^load_yaml_to_sql\.py:[0-9]+"),
    "pyproject_anchor": re.compile(r"^pyproject\.toml:[0-9]+"),
}

PROSE_FIELDS_FOR_DREF_SCAN = (
    "decision", "rationale", "note", "withdrawn_reason", "open_issue",
    "future", "caveat", "check", "mitigation", "linkage", "indexing",
    "supersedes",
)

# Toml-set steps must target a .toml file (the kind name is not just
# a label; the writer parses TOML).
TOML_LIKE_EXTENSIONS = frozenset({".toml"})


# =====================================================================
# Finding model
# =====================================================================

class Severity(str, enum.Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclasses.dataclass(frozen=True)
class Finding:
    """One validation finding.

    Attributes
    ----------
    rule:
        Stable rule id of the form ``<area>/<rule>`` — points at the
        SHACL graph that would fire the equivalent constraint.
    severity:
        ERROR by default; WARNING for policy hints that depend on a
        project-specific config (e.g. category closed-list).
    target:
        Dotted path of the offending node within the document — e.g.
        ``D-007.k2.c1`` for a check, ``D-009.i4`` for a step,
        ``D-002.scope_path`` for a field on a decision.
    message:
        Human-readable description of what's wrong and (where useful)
        why.
    """
    rule: str
    severity: Severity
    target: str
    message: str

    def format(self) -> str:
        return f"  [{self.severity.value.upper():<7}] {self.rule:<48} {self.target}: {self.message}"


@dataclasses.dataclass
class Report:
    findings: list[Finding] = dataclasses.field(default_factory=list)

    # --- adders (used by validate()) -------------------------------
    def error(self, rule: str, target: str, message: str) -> None:
        self.findings.append(Finding(rule, Severity.ERROR, target, message))

    def warn(self, rule: str, target: str, message: str) -> None:
        self.findings.append(Finding(rule, Severity.WARNING, target, message))

    def info(self, rule: str, target: str, message: str) -> None:
        self.findings.append(Finding(rule, Severity.INFO, target, message))

    # --- queries ----------------------------------------------------
    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.WARNING]

    @property
    def infos(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.INFO]

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def by_rule(self, rule_prefix: str) -> list[Finding]:
        return [f for f in self.findings if f.rule.startswith(rule_prefix)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "counts": {
                "error":   len(self.errors),
                "warning": len(self.warnings),
                "info":    len(self.infos),
            },
            "findings": [dataclasses.asdict(f) | {"severity": f.severity.value}
                         for f in self.findings],
        }


# =====================================================================
# Helper: extract live category list from config.ttl
# =====================================================================

# Match `cfg:CategoryShape` ... up to the closing `)` of its `sh:in` list.
# The body can contain Turtle line comments (`# ...`) freely, so we DON'T
# try to constrain its inner shape — we just slurp from `sh:in (` to the
# next `)` and then extract quoted strings, ignoring whatever else is in
# between (comments, whitespace, newlines).
_CATEGORY_SH_IN_RE = re.compile(
    r"cfg:CategoryShape\b.*?sh:in\s*\(\s*(.*?)\)\s*;",
    re.DOTALL,
)
# Strip Turtle comments (`# ... end-of-line`) before pulling the quotes,
# so an in-list comment can't accidentally start with `"`.
_TTL_COMMENT_RE = re.compile(r"#[^\n]*")
_QUOTED_RE = re.compile(r'"([^"]+)"')


def extract_categories_from_config_ttl(config_ttl_path: Path) -> frozenset[str] | None:
    """Read the live category enum from engineering_decisions_config.ttl.

    Returns ``None`` if the file is missing or the pattern doesn't
    match — the caller falls back to ``DEFAULT_CATEGORIES``.
    Tolerates Turtle line comments inside the ``sh:in`` list (added in
    the 2026-05 extension to group categories by axis). Comments are
    stripped BEFORE the regex match so a stray ``)`` inside a comment
    cannot prematurely terminate the ``sh:in ( ... )`` body.
    """
    try:
        text = config_ttl_path.read_text(encoding="utf-8")
    except OSError:
        return None
    text_no_comments = _TTL_COMMENT_RE.sub("", text)
    m = _CATEGORY_SH_IN_RE.search(text_no_comments)
    if not m:
        return None
    cats = _QUOTED_RE.findall(m.group(1))
    return frozenset(cats) if cats else None


# =====================================================================
# Validator entrypoint
# =====================================================================

def validate(
    doc: dict[str, Any],
    *,
    categories: frozenset[str] | None = None,
    allowed_check_contexts: frozenset[str] | None = None,
    strict_side_effects_on_shell: bool = True,
) -> Report:
    """Validate a parsed engineering_decisions YAML document.

    Parameters
    ----------
    doc:
        Result of ``yaml.safe_load(...)`` on the file.
    categories:
        Closed list of allowed ``category`` values. Defaults to the
        snapshot in :data:`DEFAULT_CATEGORIES`. Use
        :func:`extract_categories_from_config_ttl` to read the live
        project list.
    allowed_check_contexts:
        Closed list of contexts the runner is willing to dispatch
        checks to. Defaults to ``{workspace, ci}`` (config.ttl:186).
    strict_side_effects_on_shell:
        If True (default), shell + python-call checks MUST declare
        ``side_effects:``. Matches ``cfg:strictSideEffectsOnShell=true``.
    """
    report = Report()
    if not isinstance(doc, dict):
        report.error(
            "record/document-shape", "<root>",
            "top-level document must be a YAML mapping",
        )
        return report

    cats = categories if categories is not None else DEFAULT_CATEGORIES
    allowed_ctx = (allowed_check_contexts
                   if allowed_check_contexts is not None
                   else DEFAULT_ALLOWED_CHECK_CONTEXTS)

    _check_top_level(doc, report)

    decisions = doc.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        report.error(
            "record/has-decision", "<root>",
            "document must contain a non-empty `decisions:` array",
        )
        return report

    by_id: dict[str, dict] = {}
    for idx, d in enumerate(decisions):
        if not isinstance(d, dict):
            report.error(
                "record/decision-shape", f"decisions[{idx}]",
                "decision entry must be a YAML mapping",
            )
            continue
        did = d.get("id")
        if isinstance(did, str):
            if did in by_id:
                report.error(
                    "record/unique-id", did,
                    "duplicate decision id within the record",
                )
            else:
                by_id[did] = d

    # ---- self-reported count -------------------------------------
    declared_total = doc.get("total_decisions")
    if isinstance(declared_total, int) and declared_total != len(decisions):
        report.error(
            "record/total-decisions-count", "<root>",
            f"total_decisions={declared_total} but the decisions array "
            f"has {len(decisions)} entries",
        )

    all_check_ids: set[str] = set()
    all_step_ids: set[str] = set()

    for d in decisions:
        if not isinstance(d, dict):
            continue
        _check_decision(d, report, by_id, cats)
        _check_claims(d, report, all_check_ids)
        _check_implementation(d, report, all_step_ids)

    # ---- cross-graph: precondition/postcondition resolution ------
    for d in decisions:
        if not isinstance(d, dict):
            continue
        impl = d.get("implementation")
        if not isinstance(impl, dict):
            continue
        for step in impl.get("steps") or []:
            if not isinstance(step, dict):
                continue
            sid = step.get("step_id", "<no-step-id>")
            for fld in ("precondition", "postcondition"):
                ref = step.get(fld)
                if ref is None:
                    continue
                if ref not in all_check_ids:
                    report.error(
                        "cross/step-pre-post-resolution", sid,
                        f"{fld} -> {ref!r}: check does not exist in this record",
                    )

    # ---- policy hints --------------------------------------------
    _check_policy(doc, report, allowed_ctx, strict_side_effects_on_shell)

    return report


# =====================================================================
# Section: top-level
# =====================================================================

def _check_top_level(doc: dict, report: Report) -> None:
    expected = ("disclaimer", "source_schema", "source_schema_version",
                "target_engine", "total_decisions", "decisions")
    for key in expected:
        if key not in doc:
            report.warn(
                "record/top-level-fields", "<root>",
                f"recommended top-level key {key!r} is missing",
            )
    v = doc.get("source_schema_version")
    if isinstance(v, str) and not SEMVER_RE.match(v):
        report.error(
            "record/source-schema-semver", "<root>",
            f"source_schema_version must be SemVer (M.m.p); got {v!r}",
        )
    if "total_decisions" in doc and not isinstance(doc["total_decisions"], int):
        report.error(
            "record/total-decisions-type", "<root>",
            f"total_decisions must be an integer; got {type(doc['total_decisions']).__name__}",
        )


# =====================================================================
# Section: per-decision
# =====================================================================

_REQUIRED_DECISION_FIELDS = (
    "id", "title", "version_introduced", "category", "decision",
    "decision_status", "decision_date", "owner",
)


def _check_decision(
    d: dict,
    report: Report,
    by_id: dict[str, dict],
    cats: frozenset[str],
) -> None:
    did = d.get("id", "<no-id>")

    # Required scalars / lists.
    for fld in _REQUIRED_DECISION_FIELDS:
        if fld not in d:
            report.error(
                "record/required-field", did,
                f"missing required field {fld!r}",
            )

    # id pattern.
    if isinstance(d.get("id"), str) and not DECISION_ID_RE.match(d["id"]):
        report.error(
            "record/id-pattern", did,
            f"decision id must match ^D-NNN$; got {d['id']!r}",
        )

    # title non-empty.
    if isinstance(d.get("title"), str) and not d["title"].strip():
        report.error("record/title-nonempty", did, "title must be non-empty")

    # version_introduced SemVer.
    v = d.get("version_introduced")
    if isinstance(v, str) and not SEMVER_RE.match(v):
        report.error(
            "record/version-introduced-semver", did,
            f"version_introduced must be SemVer; got {v!r}",
        )

    # category closed-list (warning if not in pinned list — config-dependent).
    cat = d.get("category")
    if isinstance(cat, str) and cat not in cats:
        report.warn(
            "category/closed-list", did,
            f"category {cat!r} is not in the project's closed list; "
            f"extend cfg:CategoryShape in engineering_decisions_config.ttl "
            f"or change to one of the known values",
        )

    # decision body non-empty.
    body = d.get("decision")
    if isinstance(body, str) and not body.strip():
        report.error("record/decision-body-nonempty", did,
                     "`decision:` body must be non-empty")

    # decisionStatus enum.
    st = d.get("decision_status")
    if st is not None and st not in VALID_DECISION_STATUS:
        report.error(
            "record/decision-status-enum", did,
            f"decision_status must be one of {sorted(VALID_DECISION_STATUS)}; "
            f"got {st!r}",
        )

    # decisionDate ISO.
    dt = d.get("decision_date")
    if dt is not None:
        if isinstance(dt, str) and not ISO_DATE_RE.match(dt):
            report.error(
                "record/decision-date-iso", did,
                f"decision_date must be ISO YYYY-MM-DD; got {dt!r}",
            )

    # owner: non-empty list of well-shaped handles.
    owners = d.get("owner")
    if owners is not None:
        if not isinstance(owners, list) or not owners:
            report.error(
                "record/owner-non-empty", did,
                "owner must be a non-empty list",
            )
        else:
            for o in owners:
                if not isinstance(o, str) or len(o) < 3 or not OWNER_RE.match(o):
                    report.error(
                        "record/owner-shape", did,
                        f"owner handle {o!r} must be a string >=3 chars, no whitespace",
                    )

    # scope_path entries are non-empty strings if present.
    for sp in d.get("scope_path", []) or []:
        if not isinstance(sp, str) or not sp.strip():
            report.error(
                "record/scope-path-shape", did,
                f"scope_path entries must be non-empty glob strings; got {sp!r}",
            )

    # withdrawn_reason length when present.
    wr = d.get("withdrawn_reason")
    if wr is not None and len(str(wr).strip()) < 8:
        report.error(
            "record/withdrawn-reason-length", did,
            "withdrawn_reason, if present, must be >=8 chars",
        )

    # Anchors: patterns.
    for fld, regex in ANCHOR_PATTERNS.items():
        for anchor in d.get(fld, []) or []:
            if not regex.match(str(anchor)):
                report.error(
                    f"record/{fld.replace('_', '-')}-pattern", did,
                    f"{fld} entry {anchor!r} fails pattern {regex.pattern!r}",
                )

    # Legacy prose relations: must mention a D-NNN.
    for fld in ("relation_to", "companion_to"):
        text = d.get(fld)
        if text is None:
            continue
        if not re.search(r"D-\d{3}", str(text)):
            report.error(
                f"record/{fld.replace('_', '-')}-must-mention-d-id", did,
                f"{fld!r} prose must mention at least one D-NNN; "
                f"prefer typed relations (clarifies / conflicts_with / ...) for new entries",
            )

    # supersedes prose length.
    sup = d.get("supersedes")
    if sup is not None and len(str(sup).strip()) < 8:
        report.error(
            "record/supersedes-prose-length", did,
            "`supersedes:` prose must be >=8 chars",
        )

    # Typed relations: target must resolve, no self-reference.
    for rel in ("enables", "depends_on", "clarifies", "conflicts_with"):
        for tgt in d.get(rel, []) or []:
            if tgt == d.get("id"):
                report.error(
                    "record/no-self-reference", f"{did}.{rel}",
                    f"a decision cannot reference itself via {rel!r}",
                )
            elif tgt not in by_id:
                report.error(
                    "record/relation-target-resolves", f"{did}.{rel}",
                    f"-> {tgt!r}: unknown decision (every typed relation "
                    f"target must be present in the record)",
                )

    # No dangling D-NNN in prose; no D-??? placeholders.
    for fld in PROSE_FIELDS_FOR_DREF_SCAN:
        text = d.get(fld)
        if not text:
            continue
        text = str(text)
        if re.search(r"D-\?\?\?", text):
            report.error(
                "record/no-d-question-placeholder", f"{did}.{fld}",
                "prose contains D-??? placeholder; "
                "use D-PENDING:<slug> (or similar non-D-NNN form) for "
                "unassigned future work",
            )
        for ref in re.findall(r"\bD-\d{3}\b", text):
            if ref not in by_id:
                report.error(
                    "record/no-dangling-decision-ref", f"{did}.{fld}",
                    f"dangling reference to {ref}: decision is not in this record",
                )

    # Lifecycle interlocks.
    _check_lifecycle_interlocks(d, report, by_id)


def _check_lifecycle_interlocks(
    d: dict,
    report: Report,
    by_id: dict[str, dict],
) -> None:
    did = d.get("id", "<no-id>")
    status = d.get("decision_status")

    if status == "superseded":
        tgt_id = d.get("superseded_by")
        if not tgt_id:
            report.error(
                "lifecycle/superseded-requires-superseded-by", did,
                "status=superseded requires superseded_by pointing at a Decision",
            )
        elif tgt_id == d.get("id"):
            report.error(
                "lifecycle/superseded-by-not-self", did,
                "superseded_by cannot point at the same decision",
            )
        elif tgt_id not in by_id:
            report.error(
                "lifecycle/superseded-by-target-resolves", did,
                f"superseded_by -> {tgt_id!r}: target not in this record",
            )
        else:
            tgt_status = by_id[tgt_id].get("decision_status")
            if tgt_status not in {"accepted", "deprecated"}:
                report.error(
                    "lifecycle/superseded-by-target-active", did,
                    f"superseded_by target {tgt_id} has status "
                    f"{tgt_status!r}; must be accepted or deprecated "
                    f"(zombie supersession otherwise)",
                )

    if status == "withdrawn":
        if not str(d.get("withdrawn_reason", "")).strip():
            report.error(
                "lifecycle/withdrawn-requires-reason", did,
                "status=withdrawn requires a non-empty withdrawn_reason",
            )

    if status in {"withdrawn", "superseded"}:
        for rel in ("enables", "depends_on"):
            if d.get(rel):
                report.error(
                    "lifecycle/no-zombie-outbound", did,
                    f"status={status} but still carries active {rel}="
                    f"{d[rel]} (a tombstoned decision must not actively "
                    f"enable / depend_on other decisions)",
                )

    if status == "accepted":
        for rel in ("depends_on", "enables"):
            for tgt in d.get(rel, []) or []:
                tgt_status = by_id.get(tgt, {}).get("decision_status")
                if tgt_status == "proposed":
                    report.error(
                        "lifecycle/no-accepted-to-proposed-ref", f"{did}.{rel}",
                        f"-> {tgt}: an accepted decision cannot "
                        f"reference a still-proposed decision "
                        f"(unstable forward reference)",
                    )


# =====================================================================
# Section: claims + checks
# =====================================================================

def _check_claims(
    d: dict,
    report: Report,
    all_check_ids: set[str],
) -> None:
    did = d.get("id", "<no-id>")
    claims = d.get("claims")

    if not isinstance(claims, list) or not claims:
        report.error(
            "consequences/claim/at-least-one", did,
            "every decision must carry >=1 claim (use auditable=false / "
            "status=judgment-only for non-auditable decisions)",
        )
        return

    seen_claim_ids: set[str] = set()
    for cl in claims:
        if not isinstance(cl, dict):
            report.error(
                "consequences/claim/shape", did,
                "claim entries must be YAML mappings",
            )
            continue
        cid = cl.get("claim_id", "<no-claim-id>")
        if cid in seen_claim_ids:
            report.error(
                "consequences/claim/unique-id", cid,
                "duplicate claim_id within this decision",
            )
        seen_claim_ids.add(cid)
        _check_one_claim(d, cl, report, all_check_ids)


def _check_one_claim(
    d: dict, cl: dict,
    report: Report,
    all_check_ids: set[str],
) -> None:
    did = d.get("id", "<no-id>")
    cid = cl.get("claim_id", "<no-claim-id>")

    if not isinstance(cid, str) or not CLAIM_ID_RE.match(cid):
        report.error(
            "consequences/claim/id-pattern", cid,
            f"claim_id must match ^D-NNN.kN$; got {cid!r}",
        )
    elif isinstance(d.get("id"), str) and not cid.startswith(d["id"] + "."):
        report.error(
            "consequences/claim/id-belongs-to-parent", cid,
            f"claim_id does not start with parent decision id {d['id']!r}",
        )

    # claimText length.
    text = cl.get("claim_text")
    if not isinstance(text, str) or len(text) < 8:
        report.error(
            "consequences/claim/claim-text-length", cid,
            "claim_text must be a string of >=8 chars",
        )

    # auditable type.
    auditable = cl.get("auditable")
    if not isinstance(auditable, bool):
        report.error(
            "consequences/claim/auditable-type", cid,
            f"auditable must be a boolean; got {type(auditable).__name__}",
        )

    # status enum.
    status = cl.get("status")
    if status not in VALID_CLAIM_STATUS:
        report.error(
            "consequences/claim/status-enum", cid,
            f"status must be one of {sorted(VALID_CLAIM_STATUS)}; got {status!r}",
        )

    checks = cl.get("checks") or []
    dcc = cl.get("declared_checks_count")
    if not isinstance(dcc, int):
        report.error(
            "consequences/claim/declared-checks-count-type", cid,
            "declared_checks_count is required and must be an integer",
        )

    # State interlocks.
    if auditable is False:
        if checks:
            report.error(
                "consequences/claim/auditable-false-no-checks", cid,
                "auditable=false forbids checks (use status=judgment-only / "
                "subsumed and supply a reason)",
            )
        if status not in {"judgment-only", "subsumed"}:
            report.error(
                "consequences/claim/auditable-false-status", cid,
                f"auditable=false requires status in "
                f"{{judgment-only, subsumed}}; got {status!r}",
            )
        if not str(cl.get("reason", "")).strip():
            report.error(
                "consequences/claim/auditable-false-reason", cid,
                "auditable=false requires a non-empty reason",
            )

    if status == "pending":
        if "checks" not in cl:
            report.error(
                "consequences/claim/pending-explicit-empty-checks", cid,
                "status=pending requires an explicit `checks: []` "
                "(the 'we owe ourselves a check' marker)",
            )
        if checks:
            report.error(
                "consequences/claim/pending-no-checks", cid,
                "status=pending forbids actual checks (use [])",
            )
        if dcc != 0:
            report.error(
                "consequences/claim/pending-zero-count", cid,
                f"status=pending requires declared_checks_count=0; got {dcc!r}",
            )

    if status == "populated" and not checks:
        report.error(
            "consequences/claim/populated-requires-check", cid,
            "status=populated requires >=1 check",
        )

    if isinstance(dcc, int) and dcc != len(checks):
        report.error(
            "consequences/claim/declared-count-matches-actual", cid,
            f"declared_checks_count={dcc} but checks array has {len(checks)}",
        )

    # allow_destructive type.
    ad = cl.get("allow_destructive")
    if ad is not None and not isinstance(ad, bool):
        report.error(
            "consequences/claim/allow-destructive-type", cid,
            f"allow_destructive, if present, must be a boolean; "
            f"got {type(ad).__name__}",
        )

    seen_check_ids: set[str] = set()
    for chk in checks:
        if not isinstance(chk, dict):
            report.error(
                "consequences/check/shape", cid,
                "check entries must be YAML mappings",
            )
            continue
        ckid = chk.get("check_id", "<no-check-id>")
        if ckid in seen_check_ids:
            report.error(
                "consequences/check/unique-id", ckid,
                "duplicate check_id within this claim",
            )
        seen_check_ids.add(ckid)
        all_check_ids.add(ckid)
        _check_one_check(cl, chk, report)


def _check_one_check(cl: dict, chk: dict, report: Report) -> None:
    cid = cl.get("claim_id", "<no-claim-id>")
    ckid = chk.get("check_id", "<no-check-id>")

    if not isinstance(ckid, str) or not CHECK_ID_RE.match(ckid):
        report.error(
            "consequences/check/id-pattern", ckid,
            f"check_id must match ^D-NNN.kN.cN$; got {ckid!r}",
        )
    elif isinstance(cid, str) and not ckid.startswith(cid + "."):
        report.error(
            "consequences/check/id-belongs-to-parent", ckid,
            f"check_id does not start with parent claim_id {cid!r}",
        )

    # Required base fields.
    for fld in ("description", "check_kind", "severity", "context"):
        if fld not in chk:
            report.error(
                "consequences/check/base-required-field", ckid,
                f"missing base field {fld!r}",
            )

    # description length.
    desc = chk.get("description")
    if isinstance(desc, str) and len(desc) < 8:
        report.error(
            "consequences/check/description-length", ckid,
            "description must be >=8 chars",
        )

    # check_kind enum + per-kind required fields.
    kind = chk.get("check_kind")
    if kind not in VALID_CHECK_KIND:
        report.error(
            "consequences/check/kind-enum", ckid,
            f"check_kind must be one of {sorted(VALID_CHECK_KIND)}; got {kind!r}",
        )
    else:
        for fld in CHECK_REQUIRED_FIELDS[kind]:
            if fld not in chk:
                report.error(
                    f"consequences/check/{kind}-required-field", ckid,
                    f"check_kind={kind!r} requires field {fld!r}",
                )
        # expect block (when applicable).
        expect_keys = CHECK_EXPECT_OPTIONS[kind]
        if expect_keys is not None:
            expect = chk.get("expect")
            if not isinstance(expect, dict) or not expect:
                report.error(
                    f"consequences/check/{kind}-expect-block", ckid,
                    f"check_kind={kind!r} requires a non-empty `expect:` "
                    f"block with at least one of {sorted(expect_keys)}",
                )
            else:
                if not (set(expect.keys()) & expect_keys):
                    report.error(
                        f"consequences/check/{kind}-expect-keys", ckid,
                        f"`expect:` keys {sorted(expect.keys())} share none "
                        f"with allowed {sorted(expect_keys)}",
                    )

    # severity / context enums.
    sev = chk.get("severity")
    if sev is not None and sev not in VALID_SEVERITY:
        report.error(
            "consequences/check/severity-enum", ckid,
            f"severity must be one of {sorted(VALID_SEVERITY)}; got {sev!r}",
        )
    ctx = chk.get("context")
    if ctx is not None and ctx not in VALID_CONTEXT:
        report.error(
            "consequences/check/context-enum", ckid,
            f"context must be one of {sorted(VALID_CONTEXT)}; got {ctx!r}",
        )

    # timeout_ms / retries tuneables.
    if "timeout_ms" in chk:
        t = chk["timeout_ms"]
        if not isinstance(t, int) or t < 1:
            report.error(
                "consequences/check/timeout-ms-shape", ckid,
                f"timeout_ms must be a positive integer; got {t!r}",
            )
    if "retries" in chk:
        r = chk["retries"]
        if not isinstance(r, int) or r < 0:
            report.error(
                "consequences/check/retries-shape", ckid,
                f"retries must be a non-negative integer; got {r!r}",
            )

    # side_effects enum.
    se = chk.get("side_effects")
    if se is not None and se not in VALID_SIDE_EFFECTS:
        report.error(
            "consequences/check/side-effects-enum", ckid,
            f"side_effects must be one of {sorted(VALID_SIDE_EFFECTS)}; "
            f"got {se!r}",
        )

    # Drift-purity interlock: writes in runtime|infra require allow_destructive.
    if se == "writes" and ctx in {"runtime", "infra"}:
        if cl.get("allow_destructive") is not True:
            report.error(
                "cross/destructive-runtime-needs-allow", ckid,
                f"side_effects=writes in {ctx!r} requires parent claim "
                f"{cl.get('claim_id')!r} to set allow_destructive=true",
            )


# =====================================================================
# Section: implementation + steps
# =====================================================================

def _check_implementation(
    d: dict,
    report: Report,
    all_step_ids: set[str],
) -> None:
    did = d.get("id", "<no-id>")
    impl = d.get("implementation")
    if impl is None:
        return  # implementation block is optional
    if not isinstance(impl, dict):
        report.error(
            "implementation/shape", did,
            "`implementation:` must be a YAML mapping",
        )
        return

    automatable = impl.get("automatable")
    if not isinstance(automatable, bool):
        report.error(
            "implementation/automatable-type", did,
            f"automatable must be a boolean; got {type(automatable).__name__}",
        )

    status = impl.get("status")
    if status not in VALID_IMPL_STATUS:
        report.error(
            "implementation/status-enum", did,
            f"implementation.status must be one of "
            f"{sorted(VALID_IMPL_STATUS)}; got {status!r}",
        )

    steps = impl.get("steps") or []
    dsc = impl.get("declared_steps_count")
    if not isinstance(dsc, int):
        report.error(
            "implementation/declared-steps-count-type", did,
            "declared_steps_count is required and must be an integer",
        )

    if automatable is False:
        if not str(impl.get("reason", "")).strip():
            report.error(
                "implementation/automatable-false-reason", did,
                "implementation.automatable=false requires a reason",
            )
        if status != "not-applicable":
            report.error(
                "implementation/automatable-false-status", did,
                f"automatable=false requires status=not-applicable; got {status!r}",
            )
        if steps:
            report.error(
                "implementation/automatable-false-no-steps", did,
                "automatable=false forbids steps",
            )

    if status == "populated" and not steps:
        report.error(
            "implementation/populated-requires-step", did,
            "implementation.status=populated requires >=1 step",
        )

    if status == "pending":
        if "steps" not in impl:
            report.error(
                "implementation/pending-explicit-empty-steps", did,
                "implementation.status=pending requires an explicit `steps: []`",
            )
        if steps:
            report.error(
                "implementation/pending-no-steps", did,
                "implementation.status=pending forbids actual steps",
            )
        if dsc != 0:
            report.error(
                "implementation/pending-zero-count", did,
                f"implementation.status=pending requires declared_steps_count=0; "
                f"got {dsc!r}",
            )

    if isinstance(dsc, int) and dsc != len(steps):
        report.error(
            "implementation/declared-count-matches-actual", did,
            f"declared_steps_count={dsc} but steps array has {len(steps)}",
        )

    # Steps.
    seen_orders: set[int] = set()
    seen_step_ids: set[str] = set()
    for step in steps:
        if not isinstance(step, dict):
            report.error("implementation/step/shape", did,
                         "step entries must be YAML mappings")
            continue
        sid = step.get("step_id", "<no-step-id>")
        if sid in seen_step_ids:
            report.error("implementation/step/unique-id", sid,
                         "duplicate step_id within this implementation")
        seen_step_ids.add(sid)
        all_step_ids.add(sid)
        _check_one_step(d, step, report)

        # order uniqueness within the implementation.
        order = step.get("order")
        if isinstance(order, int):
            if order in seen_orders:
                report.error(
                    "implementation/step/order-unique", sid,
                    f"step order={order} is duplicated within this implementation",
                )
            seen_orders.add(order)

    # Contiguity: orders are {1..N}.
    if steps and seen_orders and all(isinstance(s.get("order"), int) for s in steps):
        expected = set(range(1, len(steps) + 1))
        if seen_orders != expected:
            report.error(
                "implementation/step/order-contiguous", did,
                f"step orders must be 1..{len(steps)} contiguous; "
                f"got {sorted(seen_orders)}",
            )


def _check_one_step(d: dict, step: dict, report: Report) -> None:
    did = d.get("id", "<no-id>")
    sid = step.get("step_id", "<no-step-id>")

    if not isinstance(sid, str) or not STEP_ID_RE.match(sid):
        report.error(
            "implementation/step/id-pattern", sid,
            f"step_id must match ^D-NNN.iN$; got {sid!r}",
        )
    elif isinstance(d.get("id"), str) and not sid.startswith(d["id"] + "."):
        report.error(
            "implementation/step/id-belongs-to-parent", sid,
            f"step_id does not start with parent decision id {d['id']!r}",
        )

    # Required base fields.
    for fld in ("description", "step_kind", "order", "context", "idempotent"):
        if fld not in step:
            report.error(
                "implementation/step/base-required-field", sid,
                f"missing base field {fld!r}",
            )

    desc = step.get("description")
    if isinstance(desc, str) and len(desc) < 8:
        report.error(
            "implementation/step/description-length", sid,
            "step description must be >=8 chars",
        )

    # order positive.
    order = step.get("order")
    if order is not None and (not isinstance(order, int) or order < 1):
        report.error(
            "implementation/step/order-positive", sid,
            f"order must be a positive integer; got {order!r}",
        )

    # context enum.
    ctx = step.get("context")
    if ctx is not None and ctx not in VALID_CONTEXT:
        report.error(
            "implementation/step/context-enum", sid,
            f"context must be one of {sorted(VALID_CONTEXT)}; got {ctx!r}",
        )

    # idempotent type + non-idempotent contract.
    idem = step.get("idempotent")
    if not isinstance(idem, bool):
        report.error(
            "implementation/step/idempotent-type", sid,
            f"idempotent must be a boolean; got {type(idem).__name__}",
        )
    elif idem is False:
        j = str(step.get("idempotency_justification", "")).strip()
        if len(j) < 16:
            report.error(
                "implementation/step/idempotency-justification", sid,
                "idempotent=false requires idempotency_justification (>=16 chars)",
            )
        # rollback_hint required if idempotent=false AND context in {runtime, infra}.
        if ctx in {"runtime", "infra"}:
            if not str(step.get("rollback_hint", "")).strip():
                report.error(
                    "implementation/step/rollback-hint", sid,
                    f"idempotent=false in context={ctx!r} requires a rollback_hint",
                )

    # side_effects enum.
    se = step.get("side_effects")
    if se is not None and se not in VALID_SIDE_EFFECTS:
        report.error(
            "implementation/step/side-effects-enum", sid,
            f"side_effects must be one of {sorted(VALID_SIDE_EFFECTS)}; got {se!r}",
        )

    # step_kind enum + per-kind required fields.
    kind = step.get("step_kind")
    if kind not in VALID_STEP_KIND:
        if kind is not None:
            report.error(
                "implementation/step/kind-enum", sid,
                f"step_kind must be one of {sorted(VALID_STEP_KIND)}; got {kind!r}",
            )
    elif kind == "custom":
        has_command = "command" in step
        has_fn = "fn" in step
        if has_command == has_fn:
            report.error(
                "implementation/step/custom-xor-command-fn", sid,
                f"step_kind=custom requires exactly ONE of {{command, fn}}; "
                f"got command={has_command}, fn={has_fn}",
            )
    else:
        for fld in STEP_REQUIRED_FIELDS[kind]:
            if fld not in step:
                report.error(
                    f"implementation/step/{kind}-required-field", sid,
                    f"step_kind={kind!r} requires field {fld!r}",
                )
        # toml-set must target a .toml file (the kind name is not a label).
        if kind == "toml-set":
            path = step.get("path", "")
            suffix = Path(str(path)).suffix.lower()
            if suffix not in TOML_LIKE_EXTENSIONS:
                report.error(
                    "implementation/step/toml-set-target-extension", sid,
                    f"step_kind=toml-set targets {path!r} (suffix={suffix!r}); "
                    f"toml-set writes TOML and must target a .toml file. "
                    f"For JSON / YAML targets use file-patch or python-call.",
                )

    # Cross-claim opt-in for runtime/infra writes.
    if se == "writes" and ctx in {"runtime", "infra"}:
        has_destructive_claim = any(
            isinstance(cl, dict) and cl.get("allow_destructive") is True
            for cl in d.get("claims", []) or []
        )
        if not has_destructive_claim:
            report.error(
                "cross/destructive-step-needs-allow", sid,
                f"side_effects=writes in {ctx!r} requires parent decision "
                f"{did} to carry a Claim with allow_destructive=true",
            )


# =====================================================================
# Section: policy hints
# =====================================================================

def _check_policy(
    doc: dict,
    report: Report,
    allowed_check_contexts: frozenset[str],
    strict_side_effects_on_shell: bool,
) -> None:
    """Project-policy-dependent rules.

    These are emitted as ERRORS (the policy is the source of truth in
    the project) but are isolated under policy/* so they can be
    relaxed via CLI flag if validating against a project with a
    different config.
    """
    for d in doc.get("decisions", []) or []:
        if not isinstance(d, dict):
            continue
        for cl in d.get("claims", []) or []:
            if not isinstance(cl, dict):
                continue
            for chk in cl.get("checks") or []:
                if not isinstance(chk, dict):
                    continue
                ckid = chk.get("check_id", "<no-check-id>")
                ctx = chk.get("context")
                kind = chk.get("check_kind")

                # cfg:allowedCheckContexts
                if ctx in VALID_CONTEXT and ctx not in allowed_check_contexts:
                    report.error(
                        "policy/check-context-allowed", ckid,
                        f"context={ctx!r} is not in cfg:allowedCheckContexts="
                        f"{sorted(allowed_check_contexts)}; the runner will "
                        f"refuse to dispatch this check (widen the policy "
                        f"in engineering_decisions_config.ttl to allow it)",
                    )

                # cfg:strictSideEffectsOnShell
                if (
                    strict_side_effects_on_shell
                    and kind in {"shell", "python-call"}
                    and "side_effects" not in chk
                ):
                    report.error(
                        "policy/strict-side-effects-on-shell", ckid,
                        f"check_kind={kind!r} requires explicit side_effects "
                        f"under cfg:strictSideEffectsOnShell=true",
                    )


# =====================================================================
# Rule catalogue (used by `--rules` listing)
# =====================================================================

RULES: tuple[tuple[str, str], ...] = (
    # record/*
    ("record/document-shape",             "top-level document must be a YAML mapping"),
    ("record/top-level-fields",           "recommended top-level keys: disclaimer, source_schema, source_schema_version, target_engine, total_decisions, decisions"),
    ("record/source-schema-semver",       "source_schema_version must match ^M.m.p$"),
    ("record/has-decision",               "document must contain a non-empty decisions: array"),
    ("record/total-decisions-type",       "total_decisions must be an integer"),
    ("record/total-decisions-count",      "total_decisions must equal len(decisions)"),
    ("record/decision-shape",             "decision entry must be a YAML mapping"),
    ("record/required-field",             "id, title, version_introduced, category, decision, decision_status, decision_date, owner"),
    ("record/id-pattern",                 "id must match ^D-NNN$"),
    ("record/unique-id",                  "id must be unique within the record (tombstones occupy their slot)"),
    ("record/title-nonempty",             "title must be non-empty"),
    ("record/version-introduced-semver",  "version_introduced must be SemVer"),
    ("record/decision-body-nonempty",     "decision body must be non-empty"),
    ("record/decision-status-enum",       "decision_status ∈ {proposed, accepted, deprecated, superseded, withdrawn}"),
    ("record/decision-date-iso",          "decision_date must be ISO YYYY-MM-DD"),
    ("record/owner-non-empty",            "owner must be a non-empty list"),
    ("record/owner-shape",                "owner handles must be >=3 chars, no whitespace"),
    ("record/scope-path-shape",           "scope_path entries must be non-empty glob strings"),
    ("record/withdrawn-reason-length",    "withdrawn_reason, if present, must be >=8 chars"),
    ("record/schema-anchor-pattern",      "schema_anchor must match ^schema.sql:<line>"),
    ("record/loader-anchor-pattern",      "loader_anchor must match ^load_yaml_to_sql.py:<line>"),
    ("record/pyproject-anchor-pattern",   "pyproject_anchor must match ^pyproject.toml:<line>"),
    ("record/relation-to-must-mention-d-id", "legacy relation_to prose must mention a D-NNN"),
    ("record/companion-to-must-mention-d-id", "legacy companion_to prose must mention a D-NNN"),
    ("record/supersedes-prose-length",    "supersedes (legacy prose), if present, must be >=8 chars"),
    ("record/no-self-reference",          "typed relations cannot reference the decision itself"),
    ("record/relation-target-resolves",   "every typed relation target must be a Decision in this record"),
    ("record/no-d-question-placeholder",  "no D-??? placeholders in prose; use D-PENDING:<slug> form"),
    ("record/no-dangling-decision-ref",   "D-NNN references in prose must resolve to a Decision in this record"),
    # lifecycle/*
    ("lifecycle/superseded-requires-superseded-by", "status=superseded requires superseded_by"),
    ("lifecycle/superseded-by-not-self",  "superseded_by cannot point at self"),
    ("lifecycle/superseded-by-target-resolves", "superseded_by target must be in this record"),
    ("lifecycle/superseded-by-target-active", "superseded_by target status ∈ {accepted, deprecated}"),
    ("lifecycle/withdrawn-requires-reason", "status=withdrawn requires withdrawn_reason"),
    ("lifecycle/no-zombie-outbound",      "withdrawn / superseded decisions cannot actively enable / depend_on"),
    ("lifecycle/no-accepted-to-proposed-ref", "accepted decisions cannot depend_on / enable proposed decisions"),
    # consequences/claim/*
    ("consequences/claim/at-least-one",   "every decision must carry >=1 claim"),
    ("consequences/claim/shape",          "claim entries must be YAML mappings"),
    ("consequences/claim/unique-id",      "claim_id must be unique within its decision"),
    ("consequences/claim/id-pattern",     "claim_id must match ^D-NNN.kN$"),
    ("consequences/claim/id-belongs-to-parent", "claim_id must start with parent decision id"),
    ("consequences/claim/claim-text-length", "claim_text must be >=8 chars"),
    ("consequences/claim/auditable-type", "auditable must be a boolean"),
    ("consequences/claim/status-enum",    "status ∈ {populated, pending, judgment-only, subsumed}"),
    ("consequences/claim/declared-checks-count-type", "declared_checks_count must be an integer"),
    ("consequences/claim/auditable-false-no-checks", "auditable=false forbids checks"),
    ("consequences/claim/auditable-false-status", "auditable=false requires status ∈ {judgment-only, subsumed}"),
    ("consequences/claim/auditable-false-reason", "auditable=false requires a reason"),
    ("consequences/claim/pending-explicit-empty-checks", "status=pending requires explicit checks: []"),
    ("consequences/claim/pending-no-checks", "status=pending forbids actual checks"),
    ("consequences/claim/pending-zero-count", "status=pending requires declared_checks_count=0"),
    ("consequences/claim/populated-requires-check", "status=populated requires >=1 check"),
    ("consequences/claim/declared-count-matches-actual", "declared_checks_count must equal len(checks)"),
    ("consequences/claim/allow-destructive-type", "allow_destructive must be a boolean if present"),
    # consequences/check/*
    ("consequences/check/shape",          "check entries must be YAML mappings"),
    ("consequences/check/unique-id",      "check_id must be unique within its claim"),
    ("consequences/check/id-pattern",     "check_id must match ^D-NNN.kN.cN$"),
    ("consequences/check/id-belongs-to-parent", "check_id must start with parent claim_id"),
    ("consequences/check/base-required-field", "checks require: description, check_kind, severity, context"),
    ("consequences/check/description-length", "check description must be >=8 chars"),
    ("consequences/check/kind-enum",      "check_kind ∈ {sql, sql-raises, grep, file-exists, toml-key, file-hash-equals, python-call, shell, cross-artifact, cross-toml}"),
    ("consequences/check/severity-enum",  "severity ∈ {error, warning, info}"),
    ("consequences/check/context-enum",   "context ∈ {workspace, ci, runtime, infra}"),
    ("consequences/check/timeout-ms-shape", "timeout_ms must be a positive integer"),
    ("consequences/check/retries-shape",  "retries must be a non-negative integer"),
    ("consequences/check/side-effects-enum", "side_effects ∈ {none, reads, writes}"),
    # Per-kind families (expanded so each rule id is greppable):
    ("consequences/check/sql-required-field",             "sql checks require: query"),
    ("consequences/check/sql-expect-block",               "sql checks require an `expect:` block"),
    ("consequences/check/sql-expect-keys",                "sql `expect:` must include one of {rows, rows_gte, row_equals, row_gte, row_not_equals}"),
    ("consequences/check/sql-raises-required-field",      "sql-raises checks require: query"),
    ("consequences/check/sql-raises-expect-block",        "sql-raises checks require an `expect:` block"),
    ("consequences/check/sql-raises-expect-keys",         "sql-raises `expect:` must include sqlstate"),
    ("consequences/check/grep-required-field",            "grep checks require: path, pattern"),
    ("consequences/check/grep-expect-block",              "grep checks require an `expect:` block"),
    ("consequences/check/grep-expect-keys",               "grep `expect:` must include one of {present, count, count_gte}"),
    ("consequences/check/file-exists-required-field",     "file-exists checks require: path"),
    ("consequences/check/file-exists-expect-block",       "file-exists checks require an `expect:` block"),
    ("consequences/check/file-exists-expect-keys",        "file-exists `expect:` must include present"),
    ("consequences/check/toml-key-required-field",        "toml-key checks require: key"),
    ("consequences/check/toml-key-expect-block",          "toml-key checks require an `expect:` block"),
    ("consequences/check/toml-key-expect-keys",           "toml-key `expect:` must include one of {present, equals, contains}"),
    ("consequences/check/file-hash-equals-required-field", "file-hash-equals checks require: path"),
    ("consequences/check/file-hash-equals-expect-block",  "file-hash-equals checks require an `expect:` block"),
    ("consequences/check/file-hash-equals-expect-keys",   "file-hash-equals `expect:` must include hash"),
    ("consequences/check/python-call-required-field",     "python-call checks require: fn, returns"),
    ("consequences/check/shell-required-field",           "shell checks require: command"),
    ("consequences/check/shell-expect-block",             "shell checks require an `expect:` block"),
    ("consequences/check/shell-expect-keys",              "shell `expect:` must include exit"),
    ("consequences/check/cross-artifact-required-field",  "cross-artifact requires: assert_text, query_db, grep_file, grep_pattern, relation"),
    ("consequences/check/cross-toml-required-field",      "cross-toml requires: left_path, right_path, relation"),
    # implementation/*
    ("implementation/shape",              "implementation: must be a YAML mapping"),
    ("implementation/automatable-type",   "automatable must be a boolean"),
    ("implementation/status-enum",        "implementation.status ∈ {not-applicable, pending, populated}"),
    ("implementation/declared-steps-count-type", "declared_steps_count must be an integer"),
    ("implementation/automatable-false-reason", "automatable=false requires a reason"),
    ("implementation/automatable-false-status", "automatable=false requires status=not-applicable"),
    ("implementation/automatable-false-no-steps", "automatable=false forbids steps"),
    ("implementation/populated-requires-step", "status=populated requires >=1 step"),
    ("implementation/pending-explicit-empty-steps", "status=pending requires explicit steps: []"),
    ("implementation/pending-no-steps",   "status=pending forbids actual steps"),
    ("implementation/pending-zero-count", "status=pending requires declared_steps_count=0"),
    ("implementation/declared-count-matches-actual", "declared_steps_count must equal len(steps)"),
    ("implementation/step/shape",         "step entries must be YAML mappings"),
    ("implementation/step/unique-id",     "step_id must be unique within an implementation"),
    ("implementation/step/id-pattern",    "step_id must match ^D-NNN.iN$"),
    ("implementation/step/id-belongs-to-parent", "step_id must start with parent decision id"),
    ("implementation/step/base-required-field", "steps require: description, step_kind, order, context, idempotent"),
    ("implementation/step/description-length", "step description must be >=8 chars"),
    ("implementation/step/order-positive", "step order must be a positive integer"),
    ("implementation/step/order-unique",  "step orders must be unique within an implementation"),
    ("implementation/step/order-contiguous", "step orders must be contiguous 1..N"),
    ("implementation/step/context-enum",  "context ∈ {workspace, ci, runtime, infra}"),
    ("implementation/step/idempotent-type", "idempotent must be a boolean"),
    ("implementation/step/idempotency-justification", "idempotent=false requires idempotency_justification (>=16 chars)"),
    ("implementation/step/rollback-hint", "idempotent=false in runtime|infra requires rollback_hint"),
    ("implementation/step/side-effects-enum", "side_effects ∈ {none, reads, writes}"),
    ("implementation/step/kind-enum",     "step_kind ∈ {shell-write, python-call, file-write, file-patch, toml-set, sql-exec, custom}"),
    ("implementation/step/shell-write-required-field", "shell-write steps require: command"),
    ("implementation/step/python-call-required-field", "python-call steps require: fn"),
    ("implementation/step/file-write-required-field",  "file-write steps require: path, content"),
    ("implementation/step/file-patch-required-field",  "file-patch steps require: path, patchScript"),
    ("implementation/step/toml-set-required-field",    "toml-set steps require: path, key, value"),
    ("implementation/step/sql-exec-required-field",    "sql-exec steps require: sql, target_env"),
    ("implementation/step/custom-xor-command-fn", "step_kind=custom requires exactly ONE of {command, fn}"),
    ("implementation/step/toml-set-target-extension", "toml-set must target a .toml file"),
    # cross/*
    ("cross/step-pre-post-resolution",    "step precondition / postcondition must reference an existing check_id"),
    ("cross/destructive-runtime-needs-allow", "writes in runtime|infra need parent claim allow_destructive=true (check)"),
    ("cross/destructive-step-needs-allow", "writes in runtime|infra need a claim with allow_destructive=true on parent decision (step)"),
    # category/*
    ("category/closed-list",              "category must be in cfg:CategoryShape closed list (warning)"),
    # policy/*
    ("policy/check-context-allowed",      "check context must be in cfg:allowedCheckContexts"),
    ("policy/strict-side-effects-on-shell", "shell / python-call checks must declare side_effects under cfg:strictSideEffectsOnShell=true"),
)


def list_rules() -> Iterable[tuple[str, str]]:
    """Iterator over (rule_id, description) for `--rules` output."""
    return RULES
