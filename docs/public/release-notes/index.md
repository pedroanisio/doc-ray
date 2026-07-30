---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Fable 5 (1M context) via Claude Code"
  date: "2026-07-26"
title: "Release notes"
doc_type: "release-note"
audience: ["end-user", "administrator", "developer", "integrator"]
surfaces: ["webapp", "api", "mcp"]
classification: "public"
applies_to: "1.55.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-28"
status: "current"
---

# Release notes

What changed in each version, newest first. Each entry states who a change affects
and any action required.

- **[1.55.0](1.55.0.md)** — image descriptions now appear on documents as soon
  as they are generated, labelled as machine proposals, and anyone reading a
  document can withdraw one that is wrong
  (**migration required**, no configuration change).
- **[1.54.0](1.54.0.md)** — searching sentence text is much faster; searches
  that took seconds now use the text index that was already there
  (**migration required**, no configuration change).
- **[1.53.1](1.53.1.md)** — add citation-bearing whole-phrase sentence search, repair phrase-frequency sentence denominators, and
  return an explicit null when that maintained rollup is unavailable
  (**migration required**, no configuration change).
- **[1.53.0](1.53.0.md)** — curation tools moved to their own address so
  read-only assistants stop loading them (**action required** for editing clients).
- **[1.52.0](1.52.0.md)** — asking how common a phrase is now returns a measured
  count instead of an estimate that could not prove the words appear together.
- **[1.51.0](1.51.0.md)** — fix a repeated NER surface/type error once, choose
  all-term or ordered-phrase Document search, pre-flight phrase frequency, and
  keep MCP calls restart-safe without sticky sessions.
- **[1.50.0](1.50.0.md)** — review uncertain named-entity mentions in priority
  order with an explanation beside every heuristic score.
- **[1.49.0](1.49.0.md)** — add a document by naming it (arXiv reference) from
  the browser, the API, or an agent; a failed document now shows why it failed.
- **[1.48.2](1.48.2.md)** — ingest a paper by arXiv reference instead of a
  downloaded file; bulk loading stops re-fetching documents it can never read
  and reports them honestly as failed.
- **[1.48.0](1.48.0.md)** — relationship discovery over the entity graph:
  significant figures per lens, related-entity exploration with bounded hops,
  and honest co-occurrence strength with freshness stamps.
- **[1.47.0](1.47.0.md)** — allusion edges: typed, reviewed links between a
  work's entity and its real counterpart, without ever merging identities.
- **[1.46.0](1.46.0.md)** — lens-scoped entity identity: fiction and
  non-fiction share one corpus without corrupting each other's entities;
  explicit *unresolved* instead of guesses; reviewable resolutions.
- **[1.45.0](1.45.0.md)** — document genre classification (fiction /
  non-fiction / mixed) with human confirmation; defines the per-type
  resolution policy that 1.46.0 enforces.
- **[1.44.0](1.44.0.md)** — briefing packs rank passages by meaning with a
  stated (never silent) keyword fallback; pipeline cost telemetry; Admin
  Console operator-health panel fixed.
- **[1.43.0](1.43.0.md)** — semantic and visual discovery, a unified human
  review inbox, web Lens controls, cited briefing packs, parity enforcement, and
  the operator backfill/migration sequence.
- **[1.42.0](1.42.0.md)** — learned sentence encoder for semantic search,
  MCP production hardening (health, rate limits, prompts, term-frequency
  pre-flight, evaluation harness); in-app backup became an explicit toggle
  (action required if you relied on it).
- **[1.41.0](1.41.0.md)** — Lenses: name a scope by promoting a collection (metadata
  only; entity resolution is unchanged). Entity dossiers over the MCP server report
  honest totals and flag truncation.
- **[1.40.0](1.40.0.md)** — Editing a document's authors now saves. Automatic
  backups capture the full corpus and refuse to write an incomplete archive.
