---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "OpenAI Codex"
  date: "2026-07-29"
title: "Unsupported document type"
doc_type: "troubleshooting"
audience: ["end-user", "integrator"]
surfaces: ["webapp", "api", "cli"]
classification: "public"
applies_to: "1.57.2"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Unsupported document type

## Symptom

An upload or ingest request is rejected before the Document is stored.

## Cause

The filename extension is outside the registered extractor set. Version 1.54.0
supports PDF, EPUB, HTML, Markdown, plain text, DOCX, PPTX, and SVG, including
their documented alternate extensions.

## Resolution

1. Preserve the original source file.
2. Export a faithful copy to one of the supported formats; do not merely rename
   an extension.
3. Ingest the converted file and confirm extraction produced readable text.
4. If the format should be native, add an extractor and update the central
   format registry before advertising it as supported.

Related: [Ingest a document](../how-to/ingest-a-document.md) ·
[Corpus and Documents](../concepts/corpus-and-documents.md)
