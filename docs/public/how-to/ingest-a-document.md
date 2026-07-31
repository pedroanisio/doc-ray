---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Ingest a document"
doc_type: "how-to"
audience: ["end-user", "developer"]
surfaces: ["webapp", "api"]
classification: "public"
applies_to: "1.57.2"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Ingest a document

Add a PDF, EPUB, HTML, Markdown, plain-text, DOCX, PPTX, or SVG file to the Corpus and run it through the ingestion pipeline so its text, sentences, entities, and canonical identities become queryable.

## Prerequisites

- **Corpus browser:** a signed-in account (Member or higher).
- **GraphQL API:** a bearer token with the `write` scope. Ingesting enqueues a Job under your own identity; the document lands in your Corpus.
- **Supported formats:** `.pdf`, `.epub`, `.html`, `.htm`, `.md`, `.markdown`, `.txt`, `.text`, `.docx`, `.pptx`, `.svg`. An unsupported extension is rejected before upload.
- Ingestion returns a **Job**, not a finished document. There is no synchronous result; poll the Job until it reports `succeeded`.

## Ingest through the Corpus browser

1. Open **Upload** (`/upload`).
2. Choose the file.
3. Leave **Run the full pipeline** selected to chain annotation, semantic enrichment, and canonicalization onto the upload. Clear it only to store the document without processing.
4. Optionally set a title. When left blank, the title is derived from the file.
5. Select **Upload**.

   Result: the upload is accepted and the browser follows the new Job's status page. The Job moves from `queued` to `running`.

6. Wait on the Job status page until it reads `succeeded`, then open the document.

   Result: the document appears in the Corpus (`/corpus`) with its sentences, entities, and canonical links populated.

## Ingest through the GraphQL API

`ingestDocument` uploads the file as a multipart GraphQL request, stores it, and enqueues the ingestion Job. With `pipeline: true` (the default) the Job chains annotation → semantic enrichment → canonicalization automatically.

1. Upload the file and enqueue the Job:

   ```bash
   curl -s https://doc-ray.example/graphql \
     -H "Authorization: Bearer REDACTED_TOKEN" \
     -F operations='{"query":"mutation($f:Upload!){ingestDocument(file:$f,pipeline:true){id kind status}}","variables":{"f":null}}' \
     -F map='{"0":["variables.f"]}' \
     -F 0=@quarterly-report.pdf
   ```

   Result:

   ```json
   {"data":{"ingestDocument":{"id":"11111111-1111-1111-1111-111111111111","kind":"ingest","status":"queued"}}}
   ```

2. Poll the Job by its `id` until `status` is `succeeded`. The document's id arrives in `documentId` (and `result`) once the pipeline has created it:

   ```bash
   curl -s https://doc-ray.example/graphql \
     -H "Authorization: Bearer REDACTED_TOKEN" \
     -H "content-type: application/json" \
     -d '{"query":"query($id:ID!){job(id:$id){status progressStage progressCurrent progressTotal documentId error}}","variables":{"id":"11111111-1111-1111-1111-111111111111"}}'
   ```

   Result while running:

   ```json
   {"data":{"job":{"status":"running","progressStage":"annotate","progressCurrent":3,"progressTotal":8,"documentId":null,"error":null}}}
   ```

   Result on completion:

   ```json
   {"data":{"job":{"status":"succeeded","progressStage":null,"progressCurrent":null,"progressTotal":null,"documentId":"22222222-2222-2222-2222-222222222222","error":null}}}
   ```

## Verification

- Fetch the finished document and confirm its counts are populated:

  ```bash
  curl -s https://doc-ray.example/graphql \
    -H "Authorization: Bearer REDACTED_TOKEN" \
    -H "content-type: application/json" \
    -d '{"query":"query($id:ID!){document(id:$id){id title status sentenceCount charCount}}","variables":{"id":"22222222-2222-2222-2222-222222222222"}}'
  ```

  A `status` of `extracted` and a non-zero `sentenceCount` confirm ingestion and annotation completed.

## Common failures

- **Upload rejected as an unsupported type** — the file extension is outside the supported set. See [Unsupported document type](/docs/troubleshooting/unsupported-document-type).
- **Upload rejected as too large** — the file exceeds the deployment's upload ceiling. See [Upload too large](/docs/troubleshooting/upload-too-large).
- **Not permitted** — the token lacks the `write` scope. See [Not permitted](/docs/troubleshooting/not-permitted).
- **Job reports `failed`** — read the Job's `error`. See [A job failed](/docs/troubleshooting/job-failed).

## Next steps

- [Read a document](/docs/how-to/read-a-document)
- [Find who and what a document is about](/docs/how-to/find-who-and-what-a-document-is-about)
- Concept: [Job](/docs/concepts/jobs), [Document](/docs/concepts/corpus-and-documents)
- Reference: [GraphQL API](/docs/reference/api), [MCP server](/docs/reference/mcp)
