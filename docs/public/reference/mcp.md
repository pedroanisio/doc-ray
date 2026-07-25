---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "MCP server reference"
doc_type: "reference"
audience: ["integrator", "developer"]
surfaces: ["mcp"]
classification: "public"
applies_to: "1.41.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-25"
status: "current"
---

# MCP server reference

This page is the tool-level contract for the Doc-Ray MCP server: the endpoint, owner-scoped authentication, the read-only default, the write gate, and every registered tool.

## Endpoint

The MCP server is served at `/mcp` and speaks the Model Context Protocol for AI agents.

## Authentication and scope

An agent presents a bearer token. Results are **owner-scoped**: the agent sees exactly the Corpus of the principal whose token it presents, and nothing more. Provision tokens with the Operator CLI; see [Operator CLI](./cli.md) and [Configuration](./config.md).

Behind a public domain, the server accepts calls only for the hosts named in `DOCRAY_MCP_ALLOWED_HOSTS` or `DOCRAY_PUBLIC_BASE_URL`; otherwise every call is rejected. See [MCP calls are rejected](../troubleshooting/mcp-calls-are-rejected.md).

## Read-only by default; the write gate

The default MCP surface is **read-only**. Write tools are registered only when `DOCRAY_MCP_ENABLE_WRITE` is set. When the gate is off, the write tools listed below are absent from the tool list entirely.

## Read tools (always available)

| Tool | Purpose | Key arguments |
|---|---|---|
| `search_sentences` | Keyword-in-context Sentence search across the readable Corpus. | `term`, `document_id`, `limit`, `after`, `response_format` |
| `read_document_sentences` | Read a Document's Sentences in order or as a random-access slice. | `document_id`, `limit`, `after`, `from_ordinal`, `to_ordinal`, `page`, `response_format` |
| `read_document_outline` | Map a Document into addressable ordinal ranges; call before reading. | `document_id`, `max_entries` |
| `full_text_search` | Rank whole Documents by full-text relevance. | `query`, `limit` |
| `list_documents` | List Documents visible to the caller, newest first. | `limit`, `doc_type`, `status`, `after` |
| `get_document` | Fetch one Document's metadata and counts by id. | `document_id` |
| `get_job` | Poll one Job's status by id. | `job_id` |
| `list_collections` | List Collections the caller can see. | `limit`, `kind`, `after` |
| `get_collection` | Fetch one Collection with its member Documents. | `collection_id`, `document_limit` |
| `list_lenses` | List Collections marked as entity-resolution scopes. | `limit`, `after` |
| `corpus_manifest` | Return the shard map for splitting the Corpus into N parts. | `shard_count` |
| `corpus_shard` | Read one disjoint slice of the Corpus for map-reduce. | `shard_index`, `shard_count`, `limit`, `after` |
| `entity_frequency` | Return raw named-entity frequencies. | `document_id`, `entity_type`, `limit` |
| `entity_profiles` | Return frequencies reconciled to one row per entity, with `typeConsistency`. | `document_id`, `entity_type`, `limit` |
| `lexeme_kwic` | Keyword-in-context concordance for a Lexeme. | `uid`, `document_id`, `limit` |
| `entity_mention_corrections` | Return a Document's durable human NER corrections. | `document_id` |
| `document_images` | List detected images and their visual features in a Document. | `document_id`, `limit`, `after` |
| `image_class_summary` | Return the `(imageClass, count)` distribution for a Document's images. | `document_id` |
| `cross_modal_image_search` | Find images by meaning from a text query. | `query`, `limit` |
| `visual_captions` | Return a Document's committed, grounded captions. | `document_id`, `limit` |

Prefer `entity_profiles` over `entity_frequency` for "who/what": a low `typeConsistency` means the type is contested, not that the entity is unimportant. When `search_sentences` reports low `coverage`, a common term flooded one region; retry with rare, specific terms.

## Composition tools (always available)

Composition tools assemble several reads into one cited evidence bundle for an agent to compose from. Each returned passage carries a citation handle.

| Tool | Purpose | Key arguments |
|---|---|---|
| `briefing_pack` | Assemble a cited evidence pack on a topic. | `topic`, `max_documents`, `passages_per_document`, `max_entities` |
| `entity_dossier` | Gather everything the Corpus says about an entity, as cited mentions. | `name`, `max_mentions` |
| `document_evidence` | Pull cited passages on a focus from one Document. | `document_id`, `focus`, `max_passages` |

## Write tools (only when `DOCRAY_MCP_ENABLE_WRITE` is set)

Each of these enqueues a background Job or applies a curation decision; poll returned Jobs with `get_job`.

| Tool | Purpose | Key arguments |
|---|---|---|
| `reannotate` | Re-run the full linguistic annotation for a Document. | `document_id`, `lang` |
| `reextract_document` | Re-run text extraction for one Document. | `document_id` |
| `annotate_document` | Run first-stage annotation for a Document. | `document_id`, `lang` |
| `semantic_enrich_document` | Run semantic enrichment for a Document. | `document_id` |
| `canonicalize_document` | Canonicalize a Document; `resolve` enables auto-linking. | `document_id`, `resolve` |
| `suggest_seeds` | Mine seed candidates for a Document. | `document_id`, `limit` |
| `suggest_document_metadata` | Generate metadata suggestions for a Document. | `document_id` |
| `update_document_metadata` | Edit a Document's title, description, tags, or authors. | `document_id`, `title`, `description`, `tags`, `authors` |
| `bulk_tag_documents` | Merge tags into several Documents. | `document_ids`, `tags` |
| `correct_entity_mention` | Record a durable human fix to an NER mention. | `document_id`, `action`, `char_start`, `char_end`, `entity_type`, `surface`, `new_char_start`, `new_char_end`, `original_type`, `rationale` |
| `decide_seeds` | Approve or reject seed suggestions. | `approve_ids`, `reject_ids` |
| `decide_document_metadata_suggestions` | Approve or reject metadata suggestions. | `approve_ids`, `reject_ids` |
| `seed_approved` | Seed a Document's approved suggestions into the registry. | `document_id` |
| `create_collection` | Create a Collection. | `name`, `description`, `kind`, `rule`, `doc_type`, `status`, `tags` |
| `add_document_to_collection` | Add a Document to a manual Collection. | `collection_id`, `document_id` |
| `remove_document_from_collection` | Remove a Document from a manual Collection. | `collection_id`, `document_id` |
| `update_collection` | Rename a Collection or edit its description. | `collection_id`, `name`, `description` |
| `delete_collection` | Delete a Collection container. | `collection_id` |
| `promote_collection_to_lens` | Promote a Collection to a named entity-resolution scope. | `collection_id`, `parent_lens_id` |
| `demote_lens` | Demote a Lens back to an ordinary Collection. | `collection_id` |
| `set_document_default_lens` | Set or clear a Document's default Lens. | `document_id`, `lens_id` |

Canonicalization links mentions only to entities already in the registry; seed first (`suggest_seeds` → `decide_seeds` → `seed_approved`). A Lens is metadata only today and does not change how entities resolve; the Global lens cannot be renamed, demoted, or deleted.

## Entity correction actions

`correct_entity_mention` takes exactly one `action`:

- `reclassify` — the mention has the wrong type; give the correct type.
- `reject` — the span is not an entity.
- `add` — a missed entity; record it.
- `adjust_span` — the boundaries are wrong; give the corrected span.

See [Correct an entity](../how-to/correct-an-entity.md).

## Destructive verbs intentionally absent

The MCP surface deliberately omits destructive Document verbs. There is no tool to delete or archive a Document, create or restore a Backup, or cancel a Job. Those actions exist only on the GraphQL API, where they enqueue Jobs under a principal's own credentials. See [GraphQL API](./api.md).

## Related concepts, how-tos, and reference

- Concept: [Corpus and documents](../concepts/corpus-and-documents.md)
- Concept: [Entities and canonicalization](../concepts/entities-and-canonicalization.md)
- Concept: [Jobs](../concepts/jobs.md)
- Concept: [Access and roles](../concepts/access-and-roles.md)
- How-to: [Correct an entity](../how-to/correct-an-entity.md)
- Reference: [GraphQL API](./api.md)
- Reference: [Configuration](./config.md)
- Troubleshooting: [MCP calls are rejected](../troubleshooting/mcp-calls-are-rejected.md)
</content>
