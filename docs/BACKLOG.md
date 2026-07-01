---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-01"
---

# Backlog

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

Deferred work — features, tech-debt, and known bugs — captured so it isn't lost.
Nothing here blocks current use.

**System of record:** [`docs/backlog.yml`](backlog.yml), structured by
[`docs/schema/backlog.schema.json`](schema/backlog.schema.json) (v1.0.0). The YAML
is canonical; this table is a rendered view of it. Effort is expressed as
**complexity** (XS–XL), never time (CLAUDE.md). `Owner` records the provenance
boundary so concurrent-session work stays attributable.

Validate the backlog against its schema:

```bash
pip install jsonschema pyyaml
python -c "import json,yaml,jsonschema; \
  jsonschema.validate(yaml.safe_load(open('docs/backlog.yml')), \
  json.load(open('docs/schema/backlog.schema.json')))"
```

## Items

| ID | Item | Category | Type | Cx | Priority | Owner |
|----|------|----------|------|----|----------|-------|
| BL-001 | OCR pass for image-only PDFs | extraction | feature | M | high | shared |
| BL-002 | Math fidelity: recover equations as LaTeX via opt-in ML backend | extraction | feature | L | medium | shared |
| BL-003 | Calibrate heading-detection thresholds per corpus | extraction | tech-debt | S | low | mine |
| BL-004 | Render Markdown in the reading pane | webapp | feature | S | medium | mine |
| BL-005 | Full-document scroll view + solo-POS focus mode | webapp | feature | M | low | mine |
| BL-006 | Materialize the remaining NLP annotation layers | nlp | feature | XL | medium | concurrent |
| BL-007 | Verification layer for ML/LLM annotator output (PALS's Law) | nlp | infra | M | high | shared |
| BL-008 | Complete the GraphQL (Strawberry) driving adapter | api | feature | L | medium | concurrent |
| BL-009 | GraphQL safety: depth/complexity limits + keyset pagination | security | feature | M | high | concurrent |
| BL-010 | Background jobs via a Postgres queue (FOR UPDATE SKIP LOCKED) | ops | feature | L | medium | concurrent |
| BL-011 | jobs table + sync worker runtime | persistence | feature | M | medium | concurrent |
| BL-012 | Per-principal LOGIN roles for hostile-direct-SQL isolation | security | feature | L | medium | shared |
| BL-013 | Raise package coverage from 57% to the 90% gate | testing | tech-debt | L | high | shared |
| BL-014 | Fix 14 E501 line-length errors in gen_data_dictionary.py | tooling | tech-debt | XS | low | concurrent |
| BL-015 | Fix test_migrate row_factory KeyError against the live DB | persistence | bug | S | high | concurrent |

See [`backlog.yml`](backlog.yml) for each item's full description, rationale,
acceptance criteria, dependencies, related decisions (`D-###`), and references.

---

[← Back to root README](../README.md)
