---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
doc_type: policy
---

# Doc-Ray Documentation Governance Specification

> **Classification: Internal.** This specification governs the public documentation
> published at `doc-ray.com/docs`. The specification itself is not published.
> **Status: Authoritative.** Where any other document conflicts with this one on a
> documentation question, this document wins.
> **Owner:** Documentation maintainer (see §3.3). **Applies to:** every artifact
> served under a `doc-ray.com/docs/*` path.

This work inherits the epistemic commitments in [`DISCLAIMER.md`](../DISCLAIMER.md):
no published statement may rest on an unverifiable premise. A documented behavior
is a claim about the product, and every claim must be reproducible against a
supported version (§6).

---

## 0. Purpose, scope, and authority

**Purpose.** Establish a single, enforceable policy for what Doc-Ray documents in
public, how that documentation is written and maintained, and how its quality is
governed — so that a user can understand the product, discover its capabilities,
complete a task, resolve a problem, and use each supported surface correctly,
without reading source code or contacting support.

**Scope.** All user-facing documentation published under `doc-ray.com/docs`:
FAQs, How-To guides, Concept documentation, Reference (API / MCP / CLI /
Configuration), Troubleshooting, Release Notes, and Operational Notices.

**Out of scope.** Repository-internal documents (`CLAUDE.md`, `AGENTS.md`,
architecture notes, ADRs, backlog, proposals, this specification) are engineering
artifacts, not product documentation. They may inform public docs but are never
served to users.

**Authority.** No capability is *publicly supported* until it satisfies the
**Minimum Viable Documentation** bar (§1.4). A capability that ships to users
without it is a governance defect, on par with shipping a feature without tests.

---

## 1. What is public — surfaces, capabilities, and document types

### 1.1 The public documentation surfaces

Documentation is organized by *user intent*, not by internal module. Each intent
maps to one document type and one canonical path prefix.

| Document type | Path prefix | Answers the question | Primary audience |
|---|---|---|---|
| **FAQ** | `/docs/faq` | "A short, direct question I have right now." | End user, Evaluator |
| **How-To guide** | `/docs/how-to/<task>` | "How do I accomplish *this specific task*?" | End user, Administrator, Integrator |
| **Concept** (explanation) | `/docs/concepts/<concept>` | "What is *X*, and why does it work this way?" | All |
| **Reference** | `/docs/reference/<surface>` | "What is the exact contract of this field / tool / flag?" | Developer, Integrator, Operator |
| **Troubleshooting** | `/docs/troubleshooting/<symptom>` | "Something went wrong — what does it mean and what do I do?" | End user, Administrator, Operator |
| **Release Notes** | `/docs/release-notes/<version>` | "What changed in this version?" | All |
| **Operational Notice** | `/docs/notices/<id>` | "Is there an incident, deprecation, or required action right now?" | Administrator, Operator |

A single capability is typically documented across several types: a **Concept**
explains what a *lens* is, a **How-To** walks through promoting a collection to a
lens, a **Reference** entry defines `promoteCollectionToLens`, and an **FAQ**
answers "does creating a lens change how my entities resolve?". Do not collapse
these into one page; each serves a different reader in a different moment.

### 1.2 What must be documented

A product capability is publicly documentable when it is reachable by a supported
role (`reader`, `member`, `curator`, `admin`) through a supported surface. The
following classes **must** be documented before the capability is publicly
supported:

- **Capabilities and workflows** — every user-invocable action on a supported
  surface: ingesting a document, browsing the corpus, reading a document,
  searching, curating entities (seeding), correcting entity types, managing
  collections and lenses, editing metadata, sharing, archiving, deleting, and
  creating or restoring a backup.
- **Concepts** — every noun a user must understand to work correctly: Corpus,
  Document, Collection, Lens, Canonical Entity, Entity Mention, Annotation,
  Sentence, Span, Lexeme, Job, Backup, and the document **lifecycle states**
  (`active`, `archived`, `deleted`).
- **Interfaces** — the public contract of every supported surface: the GraphQL
  API, the MCP server (tools, resources, auth), the operator CLIs, and the
  configuration schema. See §1.3 for the interface-reference rule.
- **Configuration options** — every environment variable a user or administrator
  is expected to set, with its purpose, default, and effect.
- **Limitations and known behaviors** — documented, not hidden: pagination caps,
  the fact that a lens is metadata-only and does not change resolution today,
  that canonicalization links only to already-seeded entities, that deletion is
  reversible (soft) until purged, and any capability that requires an opt-in flag
  (for example, MCP write tools require `DOCRAY_MCP_ENABLE_WRITE`).
- **Failure cases** — the observable errors a user can hit and their resolution,
  in Troubleshooting.

### 1.3 The interface-reference rule (API / MCP / CLI / Config)

Interface reference documentation is a **contract**, and the contract is defined
by the running surface, not by prose:

- **GraphQL API** — the published reference is generated from the live schema
  (types, fields, arguments, descriptions). Field and type descriptions in the
  schema *are* the reference text; write them for the audience, not for
  maintainers.
- **MCP** — every registered tool has a reference entry stating its purpose, when
  to prefer it, its arguments, its return shape, and whether it requires the write
  gate. Tool docstrings are the source.
- **CLI** — each supported operator command (`auth_admin`, `backup_scheduler`,
  `migrate`, diagnostics) documents its invocation, arguments, prerequisites,
  expected output, and exit conditions.
- **Configuration** — every publicly settable variable is documented from the
  canonical configuration registry, with default and effect. A variable that
  exists in the registry and is user-settable but absent from the reference is
  documentation drift (§6.4).
- **SDK** — Doc-Ray publishes no client SDK at this time. When one exists, it
  gains a Reference surface under `/docs/reference/sdk` governed by the same
  rules. Until then, no SDK documentation is published, and no page may imply an
  SDK exists.

### 1.4 Minimum Viable Documentation (the public-support bar)

A capability may be marked **publicly supported** only when *all* of the following
exist, are accurate against the current supported version, and are cross-linked:

- [ ] A **Concept** entry for any new noun the capability introduces.
- [ ] At least one **How-To** covering the capability's primary success path on
      each surface that exposes it (webapp, API, MCP).
- [ ] A **Reference** entry for each new field, tool, command, or variable.
- [ ] **Limitations and prerequisites** stated explicitly (roles, flags, order of
      operations, what the capability does *not* do).
- [ ] At least one **Troubleshooting** entry for each user-reachable failure the
      capability can produce.
- [ ] A **Release Note** entry for the version that introduced or changed it.
- [ ] Every procedure is **reproducible** against that version (§6.2).

This bar is the documentation analogue of the engineering Definition of Done: a
capability block is not complete until its public documentation clears it.

---

## 2. What must remain private

### 2.1 Never public

The following never appear in `doc-ray.com/docs`, in any form, including examples,
diagrams, and error transcripts:

- **Internal architecture and implementation** — layering, module names, database
  schema, table and column names, Row-Level Security predicates, role-grant
  mechanics, migration files, the job-queue internals, worker dispatch, and any
  "how it is built" detail. Document the *behavior*, never the mechanism (§2.3).
- **Security-sensitive information** — authentication internals beyond how a user
  obtains and presents a token; any credential, key, token, connection string, or
  bucket name; RLS/authorization logic; and any detail that would help defeat an
  access control. Redact real identifiers from every example.
- **Private infrastructure** — hostnames, internal IPs, container names, the
  managed database and object-store topology, tunnel and proxy configuration, and
  deployment scripts.
- **Unpublished roadmap** — planned but unshipped capabilities, block/issue
  numbers, sequencing, and internal proposals. A capability is documentable only
  once it is shippable to a supported role.
- **Experimental behavior** — anything behind an unsupported flag, an unstable
  interface, or a capability that has not cleared the public-support bar.
- **Internal terminology** — engineering shorthand, internal codenames, gate
  identifiers, and process labels. Public docs use only canonical product
  vocabulary (§5.4).
- **Support and operational procedure** — internal runbooks, escalation paths,
  incident-response detail, and anything that describes how the team operates
  rather than how the product behaves.

### 2.2 Classification taxonomy

Every candidate piece of information carries exactly one classification. The
classification decides publishability.

| Class | Definition | May appear in public docs? |
|---|---|---|
| **Public** | Product behavior a supported role can observe on a supported surface. | Yes — this is the substance of the docs. |
| **Restricted** | Public behavior that requires care in presentation (auth flows, destructive actions, rate/size limits). | Yes, with the mandated warnings and redactions (§3.6). |
| **Internal** | How the product is built or operated; team process. | No. |
| **Confidential** | Security-sensitive or private-infrastructure detail; credentials. | No, ever. |
| **Experimental** | Behavior not yet cleared for public support. | No, until promoted to Public. |
| **Deprecated** | Formerly Public behavior scheduled for removal. | Yes, marked as deprecated with a migration path and removal version (§3.8). |

Default classification for anything not yet classified is **Internal** — silence
is safer than an accidental disclosure. Promotion to Public requires the owner's
sign-off (§3.3) against the public-support bar (§1.4).

### 2.3 Documenting behavior without exposing mechanism

The rule is: **describe what the user observes and controls; omit how the product
achieves it.** Concretely:

- Name **user-facing states and actions**, never the tables or columns behind
  them. "A deleted document is hidden from the corpus and can be restored until it
  is purged" is public; the lifecycle column, its CHECK, and the delete job are
  Internal.
- Describe **access outcomes**, never access mechanics. "You see only the
  documents you own or that are shared with you" is public; the RLS predicate is
  Confidential.
- Describe **limits as contracts**, never as implementation. "A single response
  returns at most N results; page with the returned cursor" is public; the query
  plan is Internal.
- Present **errors as user conditions**, never as stack traces. Map each internal
  failure to a stable, user-meaningful symptom and resolution; never paste a raw
  exception, SQL error, or internal identifier.

---

## 3. How content is published

### 3.1 Required page structure

Every page opens with a one-sentence statement of what it is for, then follows the
skeleton for its type:

- **Concept** — definition → why it exists / when it applies → how it relates to
  neighboring concepts → explicit limitations → links to the relevant How-Tos and
  Reference.
- **How-To** — goal (one sentence) → prerequisites (role, flags, prior state) →
  numbered steps → **expected result** after each consequential step → verification
  → common failures (linking Troubleshooting) → next steps.
- **Reference** — the exact contract: name, purpose, inputs (with types, defaults,
  required/optional), output, errors, constraints, and a minimal worked example.
- **Troubleshooting** — symptom (as the user sees it) → likely cause in
  user terms → resolution steps → how to confirm it is resolved → escalation
  boundary (what is a product limit vs. a support matter).
- **Release Note** — version, date, and, per change: what changed, who it affects,
  and any required action.
- **Operational Notice** — status, affected surfaces, user impact, required
  action, and start/estimated-end where applicable.

### 3.2 Required metadata (front matter)

Every published page carries machine-readable front matter:

```yaml
title:            # canonical page title (§5.4 vocabulary)
doc_type:         # faq | how-to | concept | reference | troubleshooting | release-note | notice
audience:         # one or more of: end-user, administrator, developer, integrator, operator, evaluator
surfaces:         # webapp | api | mcp | cli | config  (which the page documents)
classification:   # public | restricted | deprecated
applies_to:       # supported product version(s) the page is verified against
owner:            # accountable maintainer
last_reviewed:    # YYYY-MM-DD
status:           # current | deprecated | archived
```

A page whose `applies_to` does not include the current supported version is
**stale** and is treated as a drift defect (§6.4) until re-verified.

### 3.3 Ownership, review, and approval gates

- **Ownership.** Every page has one accountable owner. The Documentation
  maintainer owns the specification and the information architecture; capability
  owners own the pages for their capability.
- **Review.** No page is published or materially changed without review by a
  second person for **technical correctness** and **classification** (§2.2). The
  classification check is mandatory and independent of the correctness check.
- **Approval gates, in order:** (1) accuracy verified against a supported version;
  (2) classification confirmed — nothing Internal/Confidential/Experimental
  leaked; (3) voice and terminology conform to §5; (4) the quality gates in §6
  pass. A page clears publication only when all four hold.

### 3.4 Versioning and synchronization with the product

Documentation is versioned with the product and must never describe behavior the
supported version does not exhibit.

- **Source of truth is the running surface.** API/MCP/CLI/Config reference is
  derived from the live schema, tool registry, command definitions, and
  configuration registry — not hand-transcribed. Prose that restates a contract
  must match its generated source.
- **Change coupling.** A change to any supported surface — a new or altered
  GraphQL field, MCP tool, CLI command, configuration variable, UI screen, state,
  or error — requires the corresponding documentation change in the same release.
  Shipping the change without the doc change is drift (§6.4).
- **Version selector.** Published docs expose the version they describe. Behavior
  that differs across supported versions is stated per version, never as a single
  ambiguous claim.

### 3.5 Update cadence, archival, redirects, deprecation

- **Cadence.** Reference regenerates every release. Every page is re-reviewed on a
  fixed cycle (at minimum each minor release) and `last_reviewed` is updated only
  when the page is actually re-verified.
- **Archival.** Documentation for an unsupported version is moved under an
  archival path, marked `status: archived`, and excluded from the default
  navigation and search — never deleted, so historical links resolve.
- **Redirects.** Every moved or renamed page leaves a permanent redirect from its
  old path. A published URL is a contract; it does not 404 after a reorganization.
- **Deprecation.** A deprecated capability's page is marked at the top with the
  deprecation, the **replacement**, and the **removal version**. It remains until
  the capability is removed, then becomes an archived tombstone that points to the
  replacement.

### 3.6 Examples, warnings, prerequisites, and results

- **Examples must run.** Every code sample, GraphQL document, MCP call, CLI
  invocation, or configuration snippet is executable against the supported version
  and produces the shown result. Non-runnable illustration is labeled as such.
- **Redaction.** Examples use placeholder identifiers, never real tokens,
  document ids, hostnames, or personal data.
- **Expected results.** A How-To shows the observable outcome of each
  consequential step, so a user can confirm they are on track. An action with no
  shown result is incomplete.
- **Prerequisites first.** Required role, opt-in flags, and prior state are stated
  before the first step, not discovered mid-task.
- **Warnings.** A destructive, irreversible, billable, or outward-facing action
  (deletion/purge, restore-over, backup, sharing, anything that leaves the
  system) carries a prominent warning stating the consequence and its
  reversibility *before* the step that triggers it.
- **Failure cases.** Document the common ways a procedure fails and how to
  recover, inline or by link. A How-To that shows only the happy path is not done.

### 3.7 Accessibility, localization, discoverability

- **Accessibility.** Pages meet WCAG 2.1 AA: semantic headings in order,
  meaningful link text (never "click here"), text alternatives for every image,
  sufficient contrast, keyboard-navigable examples, and no meaning carried by
  color alone.
- **Localization.** English (EN-US) is the source of truth; all other languages
  are translations of it and must not diverge in meaning. Where the product's own
  bilingual commitments apply (PT-BR), a translation is a faithful rendering of
  the English, never an independent edit.
- **Discoverability, navigation, search, SEO.** Every page is reachable within
  three clicks of the docs root and appears in search. Titles and descriptions are
  written for both a human scanning results and a search index. Every page carries
  a concise meta description; canonical URLs are stable (§3.5). Cross-linking is
  mandatory: each Concept links its How-Tos and Reference; each How-To links its
  Concept, Reference, and Troubleshooting; orphan pages (reachable by no link) are
  a navigation defect.

### 3.8 Operational notices

Incident, maintenance, and deprecation notices are published under `/docs/notices`
with a status and a required-user-action, dated, and removed or marked resolved
when no longer current. A notice states user impact and action; it never
describes internal cause or response procedure (§2.1).

---

## 4. Who the documentation is for

Each page declares its `audience`. Content is written to that audience's assumed
knowledge and goal; a single page does not try to serve everyone.

| Audience | Assumed knowledge | Primary goal | Depth |
|---|---|---|---|
| **End user** | The product's domain (documents, search, reading); no engineering assumed. | Ingest, find, read, and curate their corpus. | Task-first; no internals. |
| **Administrator** | Operates a Doc-Ray instance for a team; comfortable with configuration and access control. | Configure, manage users and access, run backups. | Configuration and access, framed as outcomes. |
| **Developer** | Programs against the GraphQL API; reads schemas. | Integrate the API correctly. | Full contract precision; exact types and errors. |
| **Integrator** | Connects an agent or tool via MCP. | Use MCP tools and auth correctly and safely. | Tool contracts, the write gate, and safety semantics. |
| **Operator** | Runs the CLIs and diagnostics. | Perform supported operational tasks. | Command contracts, prerequisites, expected output. |
| **Evaluator** | New to the product; deciding whether it fits. | Understand what it does and its boundaries quickly. | Concept-first; honest about limitations. |
| **Support** | Assists users. | Diagnose against the public surface. | Reads the same public docs; no private-only pages. |

Where a task spans audiences (deleting a document is an End-user action with an
Administrator's purge consequence), the page states each audience's part
explicitly rather than blurring them.

---

## 5. Product voice

### 5.1 Tone

Precise, direct, calm, trustworthy, and product-centered. The reader is treated as
capable and in a hurry. Every sentence either states a fact about the product or
tells the reader what to do.

### 5.2 Rules

- **Address the reader only to instruct.** Use the imperative for actions
  ("Open the corpus", "Set `DOCRAY_MCP_ENABLE_WRITE` to enable write tools").
  State product behavior in the present indicative ("A deleted document is hidden
  from the corpus"). Avoid "we", "you can", and "simply".
- **Prefer concrete actions and observable outcomes** over description of
  intent. Show what happens, not what is "designed to" happen.
- **No marketing.** No superlatives, no "powerful/seamless/robust", no persuasion.
  The documentation informs; it does not sell.
- **No meta-commentary.** Do not describe the documentation, the writing process,
  the tooling, or how content was produced. The reader sees the product, not the
  scaffolding.
- **No conversational filler.** No "Let's", "As you can see", "It's worth noting".
  Delete any sentence that survives removal without changing meaning.
- **Name failure conditions honestly.** State limitations and error conditions
  plainly and early; never bury or soften them.
- **One term, one meaning.** Use the canonical name for every feature, surface,
  entity, action, and state (§5.4), consistently, on every page.

### 5.3 Grammar and formatting conventions

- English (EN-US) spelling. Sentence-case headings. Oxford comma.
- Second-person imperative for steps; present tense for behavior.
- Code, identifiers, field names, states, and literal values in `monospace`.
- Numbered lists for ordered procedures; bullets for unordered sets.
- Prerequisites and warnings as labeled callouts, not buried in prose.
- Numerals for quantities and limits; spell nothing that is a real value.

### 5.4 Canonical vocabulary

These are the only names used in public documentation for the concepts, surfaces,
states, and actions below. Internal synonyms are never published.

**Entities and concepts:** Corpus · Document · Collection · Lens · Global lens ·
Canonical Entity · Entity Mention · Annotation · Sentence · Span · Lexeme · Job ·
Backup.

**Surfaces:** Corpus browser (the web application) · GraphQL API · MCP server ·
Operator CLI · Configuration.

**Access tiers:** Reader · Member · Curator · Administrator.

**Document lifecycle states:** Active · Archived · Deleted. ("Deleted" is
reversible until the document is **purged**.) Ingestion state (whether extraction
succeeded) is distinct from lifecycle state and is named **document status**.

**Canonical actions:** Ingest · Search · Read · Annotate · Correct (an entity) ·
Seed / Curate (entities) · Promote (a collection to a lens) · Share · Archive ·
Delete · Purge · Back up · Restore · Re-annotate.

**Error conditions** are named by user-facing symptom (for example, *not found*,
*not permitted*, *unsupported on this deployment*, *result truncated*), never by
internal exception type.

A new feature must define its canonical names, states, and error conditions in
this glossary as part of clearing the public-support bar (§1.4).

---

## 6. Documentation quality governance

### 6.1 Acceptance criteria

A page is publishable only when it satisfies every criterion:

- **Correct** — every statement is true of the supported version; no fabricated
  field, flag, behavior, or reference (this is the [`DISCLAIMER.md`](../DISCLAIMER.md)
  commitment applied to docs).
- **Complete** — for the page's type, nothing required by §3.1 is missing;
  prerequisites, expected results, limitations, and failure cases are present.
- **Clear** — written to the declared audience's level; one intent per page.
- **Consistent** — canonical vocabulary (§5.4) and structure (§3.1) throughout.
- **Testable** — every procedure and example can be executed and its result
  observed (§6.2).
- **Traceable** — each claim maps to a surface a reviewer can check (a schema
  field, a tool, a command, a configuration variable, an observable UI state).
- **Fresh** — `applies_to` includes the current supported version and
  `last_reviewed` reflects a real verification.
- **Usable** — discoverable, navigable, cross-linked, and accessible (§3.7).

### 6.2 Reproducibility requirement

Every documented procedure and example is reproducible against a supported version
by a reader in the page's audience, producing the stated result. Reproducibility
is verified at authoring time and re-verified whenever the underlying surface
changes. An example that cannot be reproduced is a defect, not a stylistic issue,
and blocks publication.

### 6.3 Quality gates (measurable, enforced before publication)

| Gate | Passes when |
|---|---|
| **Reference-sync** | Every documented API field, MCP tool, CLI command, and configuration variable exists on the live surface with matching name, arguments, and description; and every user-settable surface member has a reference entry. No missing entries, no phantom entries. |
| **Link integrity** | No broken internal or external link; no orphan page; required cross-links (§3.7) present. |
| **Metadata completeness** | Every page has complete, valid front matter (§3.2), including `classification` and `applies_to`. |
| **Freshness** | No page's `applies_to` excludes the current supported version. |
| **Classification** | No page contains Internal/Confidential/Experimental content (§2); examples contain no real credentials or identifiers. |
| **Voice** | No prohibited constructions (§5.2): marketing terms, meta-commentary, filler, non-canonical vocabulary. |
| **Example-runs** | Every runnable example executed successfully against the supported version. |

A release is **documentation-ready** only when all gates pass and the
public-support bar (§1.4) holds for every capability changed in that release.

### 6.4 Drift detection and prevention

Documentation drift — a public surface changing without its documentation, or a
documented claim no longer matching the product — is the primary failure mode this
policy exists to prevent.

- **Detection is automated where the surface is machine-readable.** The
  Reference-sync gate (§6.3) runs against the live schema, tool registry, CLI
  definitions, and configuration registry; a mismatch fails the release, exactly
  as code-level consistency checks already do.
- **No undocumented public capability.** A capability reachable by a supported
  role on a supported surface with no documentation is a release-blocking defect.
  Every release enumerates its user-visible surface changes and confirms each has
  its documentation.
- **Accountability.** The capability owner is responsible for their pages' freshness;
  the Documentation maintainer is responsible for detecting drift across the set
  and for the integrity of the information architecture.
- **Definition of documentation readiness.** A version is documentation-ready when:
  every changed capability clears the public-support bar (§1.4); all quality gates
  (§6.3) pass; no drift is outstanding; and the Release Note for the version is
  published. Absent any of these, the documentation is not ready, and the
  capability is not publicly supported.

---

## Appendix A — Classification decision procedure

For any candidate statement, in order:

1. Does it reveal a credential, private-infrastructure detail, or access-control
   mechanism? → **Confidential.** Do not publish.
2. Does it describe how the product is built or operated (schema, modules, jobs,
   deployment, team process)? → **Internal.** Do not publish; document the
   observable behavior instead (§2.3).
3. Is it behind an unsupported flag or an unstable interface? → **Experimental.**
   Do not publish until promoted.
4. Is it a public behavior that is destructive, security-adjacent, or bounded by a
   limit? → **Restricted.** Publish with the mandated warnings and redactions.
5. Was it public and is now scheduled for removal? → **Deprecated.** Publish with
   replacement and removal version.
6. Otherwise → **Public.** Publish against the public-support bar.

## Appendix B — Per-capability documentation checklist

Use once per new or changed capability, before marking it publicly supported:

- [ ] Canonical names, states, and error conditions added to §5.4.
- [ ] Concept page (if a new noun) — published, cross-linked.
- [ ] How-To for each surface that exposes it — published, with prerequisites,
      expected results, warnings, and failure cases.
- [ ] Reference entries for every new field / tool / command / variable —
      generated from the live surface.
- [ ] Troubleshooting entry for each user-reachable failure.
- [ ] Limitations and prerequisites stated explicitly.
- [ ] Release Note entry for the introducing version.
- [ ] All §6.3 gates pass; every example reproduced against the supported version.
- [ ] Classification confirmed by an independent reviewer.
