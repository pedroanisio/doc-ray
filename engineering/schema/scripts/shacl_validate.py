#!/usr/bin/env python3
"""Validate a projected engineering-decisions RDF graph against the SHACL shapes.

The bundled ``pyshacl`` CLI cannot combine several ``-s`` shape graphs nor honour
the "config is both shapes and data" rule the schema needs, so this wrapper loads
every shape graph plus the policy config into one shapes graph and the projected
data plus the policy config into one data graph, then runs SHACL-SPARQL
(``advanced=True``) exactly as the upstream test harness does.

Usage::

    python engineering/schema/scripts/yml_to_rdf.py engineering_decisions.yml > /tmp/ed.ttl
    python engineering/schema/scripts/shacl_validate.py /tmp/ed.ttl

Exit codes: 0 = conforms; 1 = violations; 2 = usage/IO.
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TTL_DIR = _HERE.parent / "statics" / "ttl"

_SHAPE_FILES = (
    "engineering_decisions_record_shacl.ttl",
    "engineering_decisions_consequences_shacl.ttl",
    "engineering_decisions_implementation_shacl.ttl",
    "engineering_decisions_config.ttl",
)
_CONFIG_FILE = "engineering_decisions_config.ttl"


def _load(paths: list[Path]) -> "object":
    """Parse and merge a list of Turtle files into one rdflib graph."""
    import rdflib

    graph = rdflib.Graph()
    for path in paths:
        graph.parse(path.as_posix(), format="turtle")
    return graph


def validate_graph(data_ttl: Path) -> tuple[bool, str]:
    """Validate one projected data graph; return (conforms, human report)."""
    from pyshacl import validate as pyshacl_validate

    shapes = _load([_TTL_DIR / name for name in _SHAPE_FILES])
    # The config policy triples must also live in the data graph so the
    # SHACL-SPARQL policy constraints can query them.
    data = _load([data_ttl, _TTL_DIR / _CONFIG_FILE])
    conforms, _report_graph, report_text = pyshacl_validate(
        data_graph=data,
        shacl_graph=shapes,
        inference="none",
        meta_shacl=False,
        advanced=True,
        debug=False,
    )
    return conforms, report_text


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: validate the projected RDF file named on argv."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: shacl_validate.py <projected-data.ttl>", file=sys.stderr)
        return 2
    data_ttl = Path(args[0])
    if not data_ttl.is_file():
        print(f"error: no such file: {data_ttl}", file=sys.stderr)
        return 2
    conforms, report_text = validate_graph(data_ttl)
    print(report_text.rstrip())
    return 0 if conforms else 1


if __name__ == "__main__":
    raise SystemExit(main())
