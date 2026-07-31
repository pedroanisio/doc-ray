---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "A document link is not found"
doc_type: "troubleshooting"
audience: ["end-user", "administrator"]
surfaces: ["webapp", "api", "mcp"]
classification: "public"
applies_to: "1.57.2"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# A document link is not found

A link to a document that used to open now returns *not found*, and the document no
longer appears in the corpus.

## Likely cause

The document was deleted. Deleting a document hides it from the corpus, so any link
to it resolves to *not found*. A delete is reversible: the document remains
recoverable until it is purged. It may also be a document you no longer have access
to, or one whose identifier is mistyped.

## Resolution

1. Confirm the identifier is correct and complete. A truncated or altered document
   identifier resolves to *not found* even for a document that exists.
2. Check whether the document was deleted. A deleted document is hidden from normal
   listings but can be restored while it is still recoverable.
3. Restore the document if it was deleted and has not been purged. After restore,
   the document returns to the corpus and its link resolves again.
   See [How-To: Restore a document](../how-to/archive-or-delete-a-document.md).
4. If the document was purged, it cannot be restored. Recover it from a Backup if
   one covers a time before the purge.
   See [How-To: Restore a backup](../how-to/back-up-and-restore.md).

## Confirm it is resolved

Open the document link again, or list the corpus. The document appears and its
content is readable.

## Escalation boundary

Restoring a deleted document and recovering from a backup are supported actions you
or an Administrator can perform. A purge is irreversible by design: once a document
is purged and no backup covers it, the content is gone, and this is a product
limit, not a recoverable error. If a document you own is missing and was never
deleted, that is a support matter.

---

Related: [Concept: Document lifecycle](../concepts/document-lifecycle.md) ·
[How-To: Delete a document](../how-to/archive-or-delete-a-document.md) ·
[FAQ](../faq/index.md#why-does-deleting-a-document-not-remove-it-permanently) ·
[Troubleshooting](index.md)
