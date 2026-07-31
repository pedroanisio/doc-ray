---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "OpenAI Codex"
  date: "2026-07-29"
title: "Restore version mismatch"
doc_type: "troubleshooting"
audience: ["administrator", "operator"]
surfaces: ["api", "cli"]
classification: "public"
applies_to: "1.57.2"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Restore version mismatch

## Symptom

A Backup restore refuses to start because the archive schema version and target
database schema version differ.

## Cause

Restore requires an exact schema contract. Loading rows into a different schema
could omit required data, violate constraints, or create a database that only
appears healthy.

## Resolution

1. Record the archive's schema version and the target's
   `docray_schema_version()` result.
2. Provision or select an isolated target running the exact archive version.
3. Restore there, verify the restored corpus, and only then apply forward
   migrations to reach the current version.
4. Never bypass the version check on the active corpus.

Related: [Backups](../concepts/backups.md) ·
[Back up and restore](../how-to/back-up-and-restore.md)
