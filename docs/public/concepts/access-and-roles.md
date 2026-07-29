---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Access and roles"
doc_type: "concept"
audience: ["end-user", "evaluator", "administrator"]
surfaces: ["webapp", "api", "mcp", "config"]
classification: "public"
applies_to: "1.54.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Access and roles

This page defines the access tiers — Reader, Member, Curator, Administrator — and the single rule that decides what a principal can see.

## Definition

Access in Doc-Ray is governed by four tiers:

- **Reader** — reads the Corpus visible to them. No curation or write.
- **Member** — reads and contributes: ingests and works with their own Documents.
- **Curator** — additionally curates shared knowledge, such as seeding and correcting entities.
- **Administrator** — manages the deployment and sees all Documents.

The tiers are cumulative in intent: each higher tier does what the lower ones do and more.

## The visibility rule

Visibility is decided by one rule, independent of tier for everyone below Administrator:

> A Document is visible to its **owner**, to principals it is **shared with**, and to an **Administrator**. No tier grants corpus-wide read.

A Reader is not a "sees everything" tier and an Administrator's broad visibility comes from the Administrator role, not from a general read grant. Your Corpus is the union of what you own and what has been shared with you; an Administrator's is everything.

## Why it exists and when it applies

The tiers separate what a principal is allowed to *do* from what a principal is allowed to *see*. This concept applies whenever a result looks empty or an action is refused: an empty result is usually the visibility rule (the Document is not in your Corpus), and a refused action is usually the tier (your tier does not permit that action).

## How it relates to neighboring concepts

- **Corpus and documents** — the visibility rule is what makes a Corpus owner-scoped; your Corpus is exactly what this rule lets you see.
- **Sharing** — sharing is how an owner extends visibility of a Document to another principal; revoking sharing withdraws it.
- **The MCP server** — an agent connecting over MCP sees exactly the Corpus of the principal whose token it presents; MCP results are owner-scoped by the same rule.

## Access on the MCP server

The MCP server applies the same visibility rule. Two boundaries matter for agents:

- **Owner-scoped results.** An agent sees exactly the Corpus of the principal whose token it presents — no more.
- **Read-only by default.** MCP write tools are registered only when `DOCRAY_MCP_ENABLE_WRITE` is set. Without it, the MCP surface is read-only. Even with writes enabled, destructive Document actions — deleting, archiving, backup, restore, and cancelling a Job — are intentionally not exposed on MCP.

## Limitations

- No tier grants corpus-wide read. Only ownership, sharing, and the Administrator role make a Document visible.
- Tier determines permitted actions; it does not widen visibility.
- The MCP surface is read-only unless write tools are explicitly enabled, and destructive Document verbs are never on MCP.

## Related how-tos and reference

- How-to: [Share a document](../how-to/share-a-document.md)
- Concept: [Corpus and documents](./corpus-and-documents.md)
- Concept: [Document lifecycle](./document-lifecycle.md)
- Concept: [Backups](./backups.md)
- Reference: [MCP server](../reference/mcp.md)
- Reference: [Configuration](../reference/config.md)
- Reference: [Operator CLI](../reference/cli.md)
