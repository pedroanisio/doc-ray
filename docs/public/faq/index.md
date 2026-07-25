---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Frequently asked questions"
doc_type: "faq"
audience: ["end-user", "administrator", "integrator", "evaluator"]
surfaces: ["webapp", "api", "mcp", "cli", "config"]
classification: "public"
applies_to: "1.41.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-25"
status: "current"
---

# Frequently asked questions

Short, direct answers to the questions readers ask most. Each answer links to the
Concept, How-To, or Reference page that covers the topic in full.

## About the product

### What is Doc-Ray?

Doc-Ray is a living library and knowledge base. It ingests documents into a
queryable, linguistically annotated corpus, resolves the people, places, dates,
and organizations inside them into shared canonical identities, and exposes the
result to people, applications, and AI agents. Read the corpus in the Corpus
browser, query it through the [GraphQL API](../reference/api.md), or connect an
agent through the [MCP server](../reference/mcp.md).

See [Concept: Corpus](../concepts/corpus-and-documents.md) for the model the rest of the product
is built on.

### What file types can I ingest?

Doc-Ray ingests PDF, EPUB, plain text (`.txt`), Markdown (`.md`), and Word
(`.docx`) documents. Each ingested document is extracted to text, split into
sentences, annotated, and made searchable.

See [How-To: Ingest a document](../how-to/ingest-a-document.md).

### What is a lens, and does creating one change how entities resolve?

A Lens is a named scope you can attach to your Corpus by promoting a Collection.
Creating a lens does not change how entities resolve today. A lens is metadata
only: it names a scope for future scoped resolution, but every read returns the
same entities and the same results whether or not a lens exists. The reserved
Global lens cannot be renamed, demoted, or deleted.

See [Concept: Lenses](../concepts/lenses.md) and
[How-To: Promote a collection to a lens](../how-to/organize-with-lenses.md).

## Search and results

### Why did my search return so few results?

A search response is a set of cited passages, not the whole document. When a query
term is common, it can flood one region of a document and crowd out passages from
the rest, so the result looks sparse even though the content is present. Every
result carries a `coverage` field and a `note` that say when this happened — a
sparse result is usually a query problem, not missing content.

Prefer rare, specific terms over common ones. Search for a distinctive name,
phrase, or figure rather than a frequent word.

See [Concept: Corpus and documents](../concepts/corpus-and-documents.md),
[How-To: Search the corpus](../how-to/search-the-corpus.md), and
[Troubleshooting: Search returns too few results](../troubleshooting/search-returns-too-few-results.md).

## Documents and lifecycle

### Why does deleting a document not remove it permanently?

Deleting a document hides it from the corpus. It is a reversible action: a deleted
document can be restored until it is purged. Purge is the separate, irreversible
step that removes the document for good. Until a document is purged, delete is
recoverable.

See [Concept: Document lifecycle](../concepts/document-lifecycle.md),
[How-To: Delete a document](../how-to/archive-or-delete-a-document.md),
[How-To: Restore a document](../how-to/archive-or-delete-a-document.md), and
[Troubleshooting: A document link is not found](../troubleshooting/document-not-found.md).

### Who can see my documents?

A document is visible only to its owner, to the principals it is shared with, and
to an Administrator. No access tier grants corpus-wide read of other principals'
documents. Sharing a document extends visibility to the principals you name; it
does not make the document public.

See [Concept: Access and sharing](../concepts/access-and-roles.md) and
[How-To: Share a document](../how-to/share-a-document.md).

## Entities and canonicalization

### Why does canonicalization link almost nothing?

Canonicalization links entity mentions only to entities that are already in the
registry. If the registry is empty, there is nothing to link to, so almost nothing
resolves. Populate the registry first with the seeding workflow: review suggested
seeds, decide which to accept, and approve them. Once the registry holds the
identities you care about, canonicalization links mentions to them.

See [Concept: Canonical entities](../concepts/entities-and-canonicalization.md),
[How-To: Seed and curate entities](../how-to/seed-canonical-entities.md), and
[Troubleshooting: Canonicalization links nothing](../troubleshooting/canonicalization-links-nothing.md).

### An entity's type looks wrong. How do I fix it?

Doc-Ray merges one entity's split annotation types into a single profile and
reports how much they disagree as `typeConsistency`. A low value means the type is
contested across mentions, not that the entity is unimportant. Correct a specific
mention when its type is wrong.

See [How-To: Correct an entity](../how-to/correct-an-entity.md) and
[Troubleshooting: Entity types look wrong](../troubleshooting/entity-types-look-wrong.md).

## AI agents and MCP

### Can an AI agent change my data over MCP?

Not by default. The MCP server is read-only unless the write gate is enabled by an
administrator through configuration. When the gate is off, no write tool is
registered and an agent can only read. Whether reading or writing, an agent is
owner-scoped: it sees and acts on exactly the corpus of the principal whose token
it presents, never anyone else's.

See [Reference: MCP server](../reference/mcp.md),
[Reference: Configuration](../reference/config.md), and
[Troubleshooting: MCP calls are rejected](../troubleshooting/mcp-calls-are-rejected.md).

## Access and administration

### How do I get an API token?

An Administrator provisions a principal and issues its token with the Operator CLI,
then registers the token so the API and MCP server accept it. Present the token as
a bearer token on your requests. If anonymous access is disabled on the deployment,
a token is required for every request.

See [How-To: Obtain an API token](../how-to/obtain-an-api-token.md),
[Reference: Operator CLI](../reference/cli.md), and
[Reference: Configuration](../reference/config.md).

### How do backups work, and how often do they run?

Doc-Ray creates automatic backups on a schedule. By default a backup runs once
every 24 hours. Retention keeps the newest 7 backups at all times, and prunes
older archives beyond that once they pass 30 days. An Administrator can adjust the
cadence and retention through configuration, and can create a backup on demand.

A backup that cannot read the full corpus fails and names what it could not read,
rather than writing an incomplete archive.

See [Concept: Backups](../concepts/backups.md),
[How-To: Create a backup](../how-to/back-up-and-restore.md), and
[Reference: Configuration](../reference/config.md).

### Editing a document's authors used to fail. Is it fixed?

Yes. Editing a document's authors now saves correctly. Earlier versions could fail
an author edit; version 1.40.0 fixed it, and author edits are covered by
regression tests.

See [How-To: Edit document metadata](../how-to/edit-document-metadata.md) and
[Release notes 1.40.0](../release-notes/1.40.0.md).

---

Related: [Troubleshooting](../troubleshooting/index.md) ·
[Release notes](../release-notes/index.md) ·
[Documentation home](../index.md)
