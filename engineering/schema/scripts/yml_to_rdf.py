"""Project engineering_decisions.yml into RDF/Turtle and (optionally) validate it.

Usage:
    python scripts/yml_to_rdf.py <input.yml> [--ttl OUT.ttl] [--validate] [--shapes DIR]

By default the projected graph is written to stdout in Turtle. With --validate,
pyshacl is run against the project's SHACL graphs (statics/ttl/*) and a non-zero
exit code is returned on any sh:Violation.

Conventions (mirrored from the canonical template):
  - YAML keys are snake_case; RDF predicates are camelCase under one of:
        rec:   https://xaai.ai/lexicon/decisions-record#
        cons:  https://xaai.ai/lexicon/consequences#
        impl:  https://xaai.ai/lexicon/implementation#
  - Decisions are IRIs under d:    https://xaai.ai/lexicon/schema-decisions/decision/
    DecisionRefs under              dref: .../decision-ref/
    Claims under                    claim: .../claim/
    Checks under                    ck:   .../check/
    Implementations under           im:   .../implementation/
    Steps under                     step: .../step/
  - rec:decisionStatus is projected to a typed IRI (rec:accepted, ...).
    cons:status / impl:status / check_kind / step_kind / severity / context /
    side_effects are likewise projected as typed IRIs (camelCased, e.g.
    "judgment-only" -> cons:judgmentOnly, "shell-write" -> impl:shellWrite).
  - rec:decisionDate is xsd:date; declared_*_count and order are xsd:integer;
    auditable / automatable / idempotent / allow_destructive / requires_seed
    are xsd:boolean. Other scalars are xsd:string.
  - The projector REFUSES to emit RDF if total_decisions != len(decisions),
    matching tests/test_template_integrity.py::test_total_decisions_matches.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from pathlib import Path

import yaml
from pyshacl import validate as pyshacl_validate
from rdflib import RDF, Graph, Literal, Namespace, URIRef
from rdflib.namespace import XSD

# --------------------------------------------------------------------------
# Namespaces
# --------------------------------------------------------------------------

REC = Namespace("https://xaai.ai/lexicon/decisions-record#")
CONS = Namespace("https://xaai.ai/lexicon/consequences#")
IMPL = Namespace("https://xaai.ai/lexicon/implementation#")

D = Namespace("https://xaai.ai/lexicon/schema-decisions/decision/")
REC0 = Namespace("https://xaai.ai/lexicon/schema-decisions/record/")
DREF = Namespace("https://xaai.ai/lexicon/schema-decisions/decision-ref/")
CLAIM = Namespace("https://xaai.ai/lexicon/schema-decisions/claim/")
CK = Namespace("https://xaai.ai/lexicon/schema-decisions/check/")
IM = Namespace("https://xaai.ai/lexicon/schema-decisions/implementation/")
STEP = Namespace("https://xaai.ai/lexicon/schema-decisions/step/")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SHAPES_DIR = REPO_ROOT / "statics" / "ttl"
SHAPE_FILENAMES = (
    "engineering_decisions_record_shacl.ttl",
    "engineering_decisions_consequences_shacl.ttl",
    "engineering_decisions_implementation_shacl.ttl",
    "engineering_decisions_results_shacl.ttl",
    "engineering_decisions_config.ttl",
)

# --------------------------------------------------------------------------
# Key/value coercion
# --------------------------------------------------------------------------


def camel(name: str) -> str:
    """snake_case -> camelCase. Idempotent on already-camel keys."""
    parts = name.split("_")
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])


# Each entry maps a YAML enum value to the IRI used in the projected graph.
# Tokens with hyphens (e.g. "judgment-only", "shell-write") become camelCase
# local names ("judgmentOnly", "shellWrite") per the SHACL vocabulary.
_STATUS_REC = {
    "proposed": REC.proposed,
    "accepted": REC.accepted,
    "deprecated": REC.deprecated,
    "superseded": REC.superseded,
    "withdrawn": REC.withdrawn,
}
_STATUS_CLAIM = {
    "populated": CONS.populated,
    "pending": CONS.pending,
    "judgment-only": CONS.judgmentOnly,
    "subsumed": CONS.subsumed,
}
_STATUS_IMPL = {
    "populated": IMPL.populated,
    "pending": IMPL.pending,
    "not-applicable": IMPL.notApplicable,
}
_SEVERITY = {"error": CONS.error, "warning": CONS.warning, "info": CONS.info}
_CONTEXT = {
    "workspace": CONS.workspace,
    "ci": CONS.ci,
    "runtime": CONS.runtime,
    "infra": CONS.infra,
}
_SIDE_EFFECTS = {
    "none": CONS.noEffect,
    "reads": CONS.reads,
    "writes": CONS.writes,
}
_CHECK_KIND = {
    k: CONS[camel(k.replace("-", "_"))]
    for k in (
        "sql",
        "sql-raises",
        "grep",
        "file-exists",
        "toml-key",
        "file-hash-equals",
        "python-call",
        "shell",
        "cross-artifact",
        "cross-toml",
    )
}
_STEP_KIND = {
    k: IMPL[camel(k.replace("-", "_"))]
    for k in (
        "shell-write",
        "python-call",
        "file-write",
        "file-patch",
        "toml-set",
        "sql-exec",
        "custom",
    )
}

# Predicates whose values are Decision IRIs, not strings.
_DECISION_REF_REL = {"enables", "depends_on", "clarifies", "conflicts_with", "superseded_by"}

# Multi-valued narrative fields (each item becomes its own triple).
_MULTI_STR_REC = {"owner", "scope_path", "schema_anchor", "loader_anchor", "pyproject_anchor"}

# Integer- and boolean-valued fields.
_INT_FIELDS = {"declared_checks_count", "declared_steps_count", "order"}
_BOOL_FIELDS = {
    "auditable",
    "automatable",
    "idempotent",
    "allow_destructive",
    "requires_seed",
}


def _decision_iri(decision_id: str) -> URIRef:
    return D[decision_id]


def _date_literal(value: object) -> Literal:
    if isinstance(value, (date, datetime)):
        iso = value.isoformat()
    else:
        iso = str(value)
    return Literal(iso, datatype=XSD.date)


def _scalar_literal(field: str, value: object) -> Literal:
    if field in _INT_FIELDS:
        return Literal(int(value), datatype=XSD.integer)
    if field in _BOOL_FIELDS:
        return Literal(bool(value), datatype=XSD.boolean)
    # Preserve native int/bool typing — pyshacl's sh:datatype xsd:integer
    # constraints reject string-typed numerics even when lexically valid.
    if isinstance(value, bool):
        return Literal(value, datatype=XSD.boolean)
    if isinstance(value, int):
        return Literal(value, datatype=XSD.integer)
    # Plain string: emit as a plain literal (no xsd:string tag) so it
    # matches the shape graphs' sh:in enums, which are written that way.
    return Literal(str(value))


# --------------------------------------------------------------------------
# Projection
# --------------------------------------------------------------------------


def _bind_prefixes(g: Graph) -> None:
    g.bind("rec", REC)
    g.bind("cons", CONS)
    g.bind("impl", IMPL)
    g.bind("d", D)
    g.bind("rec0", REC0)
    g.bind("dref", DREF)
    g.bind("claim", CLAIM)
    g.bind("ck", CK)
    g.bind("im", IM)
    g.bind("step", STEP)
    g.bind("xsd", XSD)


def _add_check(g: Graph, claim_iri: URIRef, check: Mapping[str, object]) -> None:
    check_id = str(check["check_id"])
    iri = CK[check_id]
    g.add((claim_iri, CONS.hasCheck, iri))
    g.add((iri, RDF.type, CONS.Check))
    g.add((iri, CONS.checkId, Literal(check_id)))

    for key, value in check.items():
        if key == "check_id":
            continue
        if key == "check_kind":
            g.add((iri, CONS.checkKind, _CHECK_KIND[str(value)]))
        elif key == "severity":
            g.add((iri, CONS.severity, _SEVERITY[str(value)]))
        elif key == "context":
            g.add((iri, CONS.context, _CONTEXT[str(value)]))
        elif key == "side_effects":
            g.add((iri, CONS.sideEffects, _SIDE_EFFECTS[str(value)]))
        elif key == "expect" and isinstance(value, Mapping):
            for ek, ev in value.items():
                pred = CONS[camel("expect_" + ek)]
                g.add((iri, pred, _scalar_literal(ek, ev)))
        elif key == "args" and isinstance(value, list):
            for item in value:
                g.add((iri, CONS.args, Literal(str(item))))
        else:
            g.add((iri, CONS[camel(key)], _scalar_literal(key, value)))


def _add_claim(g: Graph, dref_iri: URIRef, claim: Mapping[str, object]) -> None:
    claim_id = str(claim["claim_id"])
    iri = CLAIM[claim_id]
    g.add((dref_iri, CONS.hasClaim, iri))
    g.add((iri, RDF.type, CONS.Claim))
    g.add((iri, CONS.claimId, Literal(claim_id)))

    for key, value in claim.items():
        if key in ("claim_id", "checks"):
            continue
        if key == "status":
            g.add((iri, CONS.status, _STATUS_CLAIM[str(value)]))
        else:
            g.add((iri, CONS[camel(key)], _scalar_literal(key, value)))

    for check in claim.get("checks") or ():
        _add_check(g, iri, check)


def _add_step(g: Graph, impl_iri: URIRef, step: Mapping[str, object]) -> None:
    step_id = str(step["step_id"])
    iri = STEP[step_id]
    g.add((impl_iri, IMPL.hasStep, iri))
    g.add((iri, RDF.type, IMPL.Step))
    g.add((iri, IMPL.stepId, Literal(step_id)))

    for key, value in step.items():
        if key == "step_id":
            continue
        if key == "step_kind":
            g.add((iri, IMPL.stepKind, _STEP_KIND[str(value)]))
        elif key == "context":
            g.add((iri, IMPL.context, _CONTEXT[str(value)]))
        elif key == "side_effects":
            g.add((iri, IMPL.sideEffects, _SIDE_EFFECTS[str(value)]))
        elif key in ("precondition", "postcondition"):
            # Must point at a cons:Check IRI, not a literal.
            g.add((iri, IMPL[camel(key)], CK[str(value)]))
        elif key == "args" and isinstance(value, list):
            for item in value:
                g.add((iri, IMPL.args, Literal(str(item))))
        else:
            g.add((iri, IMPL[camel(key)], _scalar_literal(key, value)))


def _add_implementation(
    g: Graph, dref_iri: URIRef, decision_id: str, block: Mapping[str, object]
) -> None:
    iri = IM[decision_id]
    g.add((dref_iri, IMPL.hasImplementation, iri))
    g.add((iri, RDF.type, IMPL.Implementation))
    for key, value in block.items():
        if key == "steps":
            continue
        if key == "status":
            g.add((iri, IMPL.status, _STATUS_IMPL[str(value)]))
        else:
            g.add((iri, IMPL[camel(key)], _scalar_literal(key, value)))
    for step in block.get("steps") or ():
        _add_step(g, iri, step)


def _add_decision(g: Graph, entry: Mapping[str, object]) -> URIRef:
    decision_id = str(entry["id"])
    iri = _decision_iri(decision_id)
    g.add((iri, RDF.type, REC.Decision))
    g.add((iri, REC.id, Literal(decision_id)))

    for key, value in entry.items():
        if key in ("id", "claims", "implementation"):
            continue
        if key == "decision_status":
            g.add((iri, REC.decisionStatus, _STATUS_REC[str(value)]))
        elif key == "decision_date":
            g.add((iri, REC.decisionDate, _date_literal(value)))
        elif key in _DECISION_REF_REL:
            pred = REC[camel(key)]
            targets: Iterable[object] = value if isinstance(value, list) else (value,)
            for target in targets:
                g.add((iri, pred, _decision_iri(str(target))))
        elif key in _MULTI_STR_REC and isinstance(value, list):
            pred = REC[camel(key)]
            for item in value:
                g.add((iri, pred, Literal(str(item))))
        else:
            g.add((iri, REC[camel(key)], _scalar_literal(key, value)))

    return iri


def project(doc: Mapping[str, object]) -> Graph:
    """Build an RDF graph from a parsed YAML document."""
    decisions = doc.get("decisions") or []
    if not isinstance(decisions, list):
        raise ValueError("`decisions` must be a list")

    declared = doc.get("total_decisions")
    if declared is not None and int(declared) != len(decisions):
        raise ValueError(
            f"total_decisions ({declared}) != len(decisions) ({len(decisions)})"
        )

    g = Graph()
    _bind_prefixes(g)

    record = REC0.r1
    g.add((record, RDF.type, REC.DecisionsRecord))
    for top in ("source_schema", "source_schema_version", "target_engine"):
        if top in doc and doc[top] is not None:
            g.add((record, REC[camel(top)], Literal(str(doc[top]))))

    for entry in decisions:
        if not isinstance(entry, Mapping):
            raise ValueError(f"decision entry is not a mapping: {entry!r}")
        decision_iri = _add_decision(g, entry)
        g.add((record, REC.hasDecision, decision_iri))

        # DecisionRef bridge: the Claim/Check/Impl/Step graph hangs off a
        # cons:DecisionRef keyed by the same decision id.
        if entry.get("claims") or entry.get("implementation"):
            dref_iri = DREF[str(entry["id"])]
            g.add((dref_iri, RDF.type, CONS.DecisionRef))
            g.add((dref_iri, CONS.decisionId, Literal(str(entry["id"]))))
            for claim in entry.get("claims") or ():
                _add_claim(g, dref_iri, claim)
            impl_block = entry.get("implementation")
            if isinstance(impl_block, Mapping):
                _add_implementation(g, dref_iri, str(entry["id"]), impl_block)

    return g


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def load_shapes(shapes_dir: Path) -> tuple[Graph, Graph]:
    """Return (shapes_graph, ontology_data_graph).

    engineering_decisions_config.ttl must be parsed into BOTH graphs — the
    SHACL-SPARQL policy constraints query cfg:policy triples from the data
    side (see tests/shacl_harness.py).
    """
    shapes = Graph()
    extra_data = Graph()
    for name in SHAPE_FILENAMES:
        path = shapes_dir / name
        shapes.parse(path, format="turtle")
        if name == "engineering_decisions_config.ttl":
            extra_data.parse(path, format="turtle")
    return shapes, extra_data


_SH = Namespace("http://www.w3.org/ns/shacl#")


def validate_graph(data: Graph, shapes_dir: Path) -> tuple[bool, int, int, str]:
    """Run pyshacl. Returns (no_violations, n_violations, n_warnings, report).

    `no_violations` is True iff there are no sh:Violation results — warnings
    and info do not flip the bit. Callers decide whether warnings count.
    """
    shapes, extra_data = load_shapes(shapes_dir)
    for triple in extra_data:
        data.add(triple)
    _conforms, report_graph, report_text = pyshacl_validate(
        data_graph=data,
        shacl_graph=shapes,
        inference="none",
        meta_shacl=False,
        advanced=True,
        debug=False,
    )
    n_violations = n_warnings = 0
    for result in report_graph.subjects(RDF.type, _SH.ValidationResult):
        sev = report_graph.value(result, _SH.resultSeverity)
        if sev == _SH.Violation:
            n_violations += 1
        elif sev == _SH.Warning:
            n_warnings += 1
    return n_violations == 0, n_violations, n_warnings, report_text


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", type=Path, help="Path to engineering_decisions.yml")
    parser.add_argument(
        "--ttl", type=Path, default=None, help="Write Turtle output to this path (default: stdout)"
    )
    parser.add_argument(
        "--validate", action="store_true", help="Validate the projection with pyshacl"
    )
    parser.add_argument(
        "--shapes",
        type=Path,
        default=DEFAULT_SHAPES_DIR,
        help=f"Directory holding the SHACL graphs (default: {DEFAULT_SHAPES_DIR})",
    )
    args = parser.parse_args(argv)

    with args.input.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)

    graph = project(doc)
    turtle = graph.serialize(format="turtle")

    if args.ttl is not None:
        args.ttl.write_text(turtle, encoding="utf-8")
    else:
        sys.stdout.write(turtle)

    if not args.validate:
        return 0

    ok, n_violations, n_warnings, report = validate_graph(graph, args.shapes)
    sys.stderr.write(report)
    sys.stderr.write(f"\nSummary: {n_violations} violation(s), {n_warnings} warning(s).\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
