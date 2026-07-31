---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "OpenAI Codex"
  date: "2026-07-29"
title: "Sentences"
doc_type: "concept"
audience: ["end-user", "developer", "integrator"]
surfaces: ["webapp", "api", "mcp"]
classification: "public"
applies_to: "1.57.2"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Sentences

A **Sentence** is an ordered, addressable unit of a Document's extracted text.
It carries a Document-relative ordinal, its text, and a Span that locates the
same text in the Document. Ordinals make ranged reading resumable; character
offsets make quoted evidence independently checkable.

Sentence search returns the matching Sentence with surrounding context and a
cursor for the same search connection. Literal substring search preserves the
stored text. Phrase search additionally reports verified occurrences and its
matching profile. Semantic search ranks Sentences by learned similarity when
the active embeddings are available.

Sentence boundaries are derived material. Re-extracting or re-annotating a
Document can replace derived rows, so durable citations should keep the
Document id, Sentence ordinal or id, and quoted text together.

## Use Sentences when

- a claim needs a small, citable unit of source evidence;
- an agent must page through a long Document without loading it all; or
- a search result needs enough local context to be interpreted honestly.

Related: [Spans](spans.md) · [Annotations](annotations.md) ·
[Read a document](../how-to/read-a-document.md) ·
[Search the corpus](../how-to/search-the-corpus.md)
