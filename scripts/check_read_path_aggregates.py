#!/usr/bin/env python3
"""Fail if a read path aggregates a large fact table live (D-024).

doc-ray's read surfaces (the Flask webapp, the GraphQL resolvers, the MCP server,
and the read-only persistence adapters they delegate to) must never compute an
aggregate — ``count`` / ``count(distinct)`` / ``GROUP BY`` / ``string_agg`` /
``sum`` — over one of the large append-mostly *fact* tables on the request path.
Those aggregates are O(table) and blow the latency budget (measured live: the
corpus band 18.7 s, the Lexicon 37 s, the reader sidebar 1.2 s, all over the
17.5M-row ``lexeme_occurrences``). The corpus writes those facts in batch, so the
aggregates belong in read-model rollups refreshed at the pipeline boundary
(``document_stats`` / ``corpus_stats`` / ``lexeme_stats`` / ``entity_frequency_stats``),
read in O(result).

This gate makes that rule enforceable instead of aspirational — the same class of
static check as ``check_file_size.py`` / ``check_version_consistency.py``. It is a
heuristic (it reads SQL string literals, not a query planner), so a small,
reasoned allowlist covers the cases that are genuinely bounded to a single key
(and therefore O(result), not O(table)). A NEW aggregate over a fact table fails
the build; add a rollup, don't add to the allowlist.

Exit 0 when clean, 1 on any un-allowlisted finding.
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import token as token_mod
import tokenize
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

#: Read-surface roots scanned by this gate (request-path code + the read adapters
#: it delegates to). The pipeline/write adapters are exempt — they MAY aggregate.
_READ_PATHS: tuple[str, ...] = (
    "webapp",
    "src/doc_ray/entrypoints/api",
    "src/doc_ray/entrypoints/mcp",
    "src/doc_ray/adapters/persistence_read.py",
    "src/doc_ray/adapters/persistence_read_collections.py",
    "src/doc_ray/adapters/persistence_layers_read.py",
    "src/doc_ray/adapters/persistence_canonical_read.py",
    "src/doc_ray/adapters/persistence_projection_read.py",
    "src/doc_ray/adapters/persistence_read_structure.py",
    "src/doc_ray/adapters/persistence_read_terms.py",
    "src/doc_ray/adapters/persistence_visual_read.py",
)

#: Large append-mostly fact tables that must not be aggregated on a read path.
_FACT_TABLES: frozenset[str] = frozenset(
    {"lexeme_occurrences", "spans", "tokens", "entity_mention", "chunk", "embedding", "sentences"}
)

#: Aggregate signals in a SQL literal (lowercased, whitespace-collapsed).
_AGG_PATTERNS: tuple[str, ...] = (
    "count(",
    "count (",
    "group by",
    "string_agg(",
    "string_agg (",
    "sum(",
    "sum (",
    "select distinct",
    "count(distinct",
    "count(distinct ",
)

#: Reasoned exceptions: a finding is suppressed when its file matches and the
#: normalized SQL contains the fragment. These are genuinely bounded to a single
#: key (O(result)), or tracked debt handled by a different layer. Keep it short.
_ALLOWLIST: tuple[dict[str, str], ...] = (
    {
        "file": "webapp/corpus_web.py",
        "contains": "count(*) n from lexeme_occurrences where lexeme_uid = %s",
        "reason": "lexeme detail page: bounded to one lexeme via idx_occ_lexeme (O(result)).",
    },
    {
        "file": "webapp/corpus_web.py",
        "contains": "where o.lexeme_uid = %s limit 400",
        "reason": "lexeme detail 'forms': bounded to one lexeme's first 400 occurrences.",
    },
    {
        "file": "webapp/document_web.py",
        "contains": "count(*) n from sentences where document_id = %s",
        "reason": "single-document sentence count on the reader page (bounded to one doc).",
    },
    {
        "file": "src/doc_ray/adapters/persistence_read.py",
        "contains": "and ann.document_id = %(doc)s::uuid",
        "reason": (
            "list_lexemes document-scoped branch: uses counts one document's occurrences "
            "(bounded to that doc); the corpus-wide branch reads lexeme_stats."
        ),
    },
    {
        "file": "src/doc_ray/adapters/persistence_layers_read.py",
        "contains": "jsonb_agg",
        "reason": "per-document layer readers: bounded to one document_id (O(result)).",
    },
    {
        "file": "webapp/document_web.py",
        "contains": "select distinct sp.page_number",
        "reason": "reader page list: bounded to one document_id (O(that doc's pages)).",
    },
    {
        "file": "webapp/operator_health.py",
        "contains": "from tokens",
        "reason": (
            "operator-only health dashboard (token-gated, off the user request path / SLA); "
            "exact corpus totals are the point of the page. Tracked as operator-perf debt."
        ),
    },
    {
        "file": "webapp/operator_health.py",
        "contains": "from entity_mention em",
        "reason": "operator-only health dashboard (see above): per-document diagnostic counts.",
    },
)


def _normalize(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip().lower()


def _string_literals(path: Path) -> list[tuple[int, str]]:
    """Return (line, merged-text) for each run of adjacent Python string tokens.

    Adjacent string literals (Python implicit concatenation, how the SQL is built)
    are merged so a statement split across many quoted lines is checked as one.
    """
    fstart = getattr(token_mod, "FSTRING_START", -1)
    fmid = getattr(token_mod, "FSTRING_MIDDLE", -1)
    fend = getattr(token_mod, "FSTRING_END", -1)
    skip = (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT)
    out: list[tuple[int, str]] = []
    try:
        raw = path.read_bytes()
        run_line = 0
        run_parts: list[str] = []
        fdepth = 0
        for tok in tokenize.tokenize(io.BytesIO(raw).readline):
            t = tok.type
            if t == fstart:
                if not run_parts:
                    run_line = tok.start[0]
                fdepth += 1
            elif t == fend:
                fdepth = max(0, fdepth - 1)
            elif t in (token_mod.STRING, fmid):
                if not run_parts:
                    run_line = tok.start[0]
                run_parts.append(tok.string)
            elif fdepth > 0 or t in skip:
                # Inside an f-string interpolation (keep the run open across it) or
                # an insignificant token — do not break the merged literal.
                continue
            elif run_parts:
                out.append((run_line, " ".join(run_parts)))
                run_parts = []
        if run_parts:
            out.append((run_line, " ".join(run_parts)))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        pass
    return out


def _is_allowlisted(relpath: str, normalized: str) -> bool:
    return any(entry["file"] == relpath and entry["contains"] in normalized for entry in _ALLOWLIST)


def _iter_files(roots: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        p = _ROOT / root
        if p.is_file() and p.suffix == ".py":
            files.append(p)
        elif p.is_dir():
            files.extend(sorted(f for f in p.rglob("*.py") if "__pycache__" not in f.parts))
    return files


def find_violations(roots: tuple[str, ...] = _READ_PATHS) -> list[tuple[str, int, str]]:
    """Return (relpath, line, fact_table) for each un-allowlisted read-path aggregate."""
    findings: list[tuple[str, int, str]] = []
    for path in _iter_files(roots):
        try:
            relpath = str(path.relative_to(_ROOT))
        except ValueError:  # a root outside the repo (e.g. a test fixture dir)
            relpath = str(path)
        for line, literal in _string_literals(path):
            norm = _normalize(literal)
            if not any(sig in norm for sig in _AGG_PATTERNS):
                continue
            # The fact table must appear in a FROM/JOIN clause — this keeps the
            # heuristic on real SQL and off prose docstrings that merely mention a
            # table name next to an English word like "count".
            hit = next((t for t in _FACT_TABLES if re.search(rf"(from|join)\s+{t}\b", norm)), None)
            if hit is None:
                continue
            if _is_allowlisted(relpath, norm):
                continue
            findings.append((relpath, line, hit))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="*", help="Override the scanned read-surface roots.")
    args = parser.parse_args()
    roots = tuple(args.roots) if args.roots else _READ_PATHS

    violations = find_violations(roots)
    if not violations:
        print("D-024 OK: no live fact-table aggregates on read paths.")
        return 0

    print("D-024 VIOLATION: read paths must not aggregate large fact tables live.")
    print("Derive a read-model rollup (refreshed at the pipeline boundary) instead.\n")
    for relpath, line, table in violations:
        print(f"  {relpath}:{line}  aggregates fact table '{table}'")
    print(f"\n{len(violations)} finding(s). See D-024 in engineering_decisions.yml.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
