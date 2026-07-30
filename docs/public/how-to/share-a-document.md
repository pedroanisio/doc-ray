---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Share a document"
doc_type: "how-to"
audience: ["administrator", "end-user", "developer"]
surfaces: ["webapp", "api"]
classification: "restricted"
applies_to: "1.57.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Share a document

Grant a colleague read or annotate access to a Document you own, revoke that access, or publish a Document to the whole organization.

> **Warning — sharing grants access.** Sharing a Document immediately lets the named principal read (or annotate) it, and setting visibility to `org` makes it readable by every principal in the organization. Access takes effect at once; review the recipient and permission before granting, and revoke when access is no longer needed.

## Prerequisites

- You own the Document, or you are an Administrator.
- **GraphQL API:** a bearer token with the `write` scope.
- Grantable permissions are `read` and `annotate` only. `admin` and `redact` are never grantable this way, and sharing never transfers control of the Document.
- Visibility is `private` (owner, granted principals, and Administrator only) or `org` (readable by every principal in the organization).

## Share through the Corpus browser

1. Open the Document's sharing screen at `/doc/<document-id>/sharing`.
2. Enter the colleague's account email and choose `read` or `annotate`, then grant.

   Result: the grant appears in the screen's share list, and the colleague can now access the Document.

3. To remove access, revoke the grant from the same screen.

   Result: the grant disappears and the colleague's access ends.

## Share through the GraphQL API

`shareDocument` grants `read` (default) or `annotate` to a principal identified by login email. It is idempotent: repeating the same grant returns the existing one.

```bash
curl -s https://doc-ray.example/graphql \
  -H "Authorization: Bearer REDACTED_TOKEN" -H "content-type: application/json" \
  -d '{"query":"mutation($id:ID!){shareDocument(documentId:$id,email:\"colleague@example.com\",permission:\"read\"){principalId email permission grantedAt}}","variables":{"id":"22222222-2222-2222-2222-222222222222"}}'
```

Result:

```json
{"data":{"shareDocument":{"principalId":"REDACTED_PRINCIPAL","email":"colleague@example.com","permission":"read","grantedAt":"2026-07-25T15:00:00Z"}}}
```

### Revoke a grant

With `permission` omitted, every grant the principal holds on the Document is removed. `revokeDocumentShare` returns the number of grants removed.

```bash
curl -s https://doc-ray.example/graphql \
  -H "Authorization: Bearer REDACTED_TOKEN" -H "content-type: application/json" \
  -d '{"query":"mutation($id:ID!){revokeDocumentShare(documentId:$id,email:\"colleague@example.com\")}","variables":{"id":"22222222-2222-2222-2222-222222222222"}}'
```

Result: `{"data":{"revokeDocumentShare":1}}`.

### Publish to the organization

```bash
curl -s https://doc-ray.example/graphql \
  -H "Authorization: Bearer REDACTED_TOKEN" -H "content-type: application/json" \
  -d '{"query":"mutation($id:ID!){setDocumentVisibility(documentId:$id,visibility:\"org\")}","variables":{"id":"22222222-2222-2222-2222-222222222222"}}'
```

Result: `{"data":{"setDocumentVisibility":"org"}}`. Set it back to `private` to withdraw organization-wide access.

## Verification

- List who the Document is shared with:

  ```bash
  curl -s https://doc-ray.example/graphql \
    -H "Authorization: Bearer REDACTED_TOKEN" -H "content-type: application/json" \
    -d '{"query":"query($id:ID!){documentShares(id:$id){email permission grantedAt}}","variables":{"id":"22222222-2222-2222-2222-222222222222"}}'
  ```

  Result: one row per active grant. After a revoke, the removed grant is absent.

## Common failures

- **No account found for that email** — the address is unknown or outside your organization. The same message is returned for both, so neither can be distinguished. Confirm the colleague has an account.
- **Permission rejected** — only `read` and `annotate` are grantable; `admin` and `redact` are not.
- **Not permitted** — you neither own the Document nor are an Administrator. See [Not permitted](/docs/troubleshooting/not-permitted).

## Next steps

- [Archive or delete a document](/docs/how-to/archive-or-delete-a-document)
- [Edit document metadata](/docs/how-to/edit-document-metadata)
- Concept: [Document](/docs/concepts/corpus-and-documents)
- Reference: [GraphQL API](/docs/reference/api)
