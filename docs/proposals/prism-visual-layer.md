---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 (1M context) via Claude Code — multi-agent workflow (10 agents: ground/draft/adversarial-verify/synthesize), operator-verified against the live database · P0 correction pass 2026-07-15: PURPOSE.md now exists (alignment added); migration/schema refresh 079/1.24.0 → 088/1.27.0"
  date: "2026-07-15"
---

<!-- This proposal is subject to the methodological caveats in
     [@DISCLAIMER.md](../../DISCLAIMER.md).
     No statement or premise not backed by a real logical definition or
     verifiable reference should be taken for granted. -->

# Prism — A Verifiable Visual & Multimodal Layer for doc-ray

> **Status: PROPOSED, NOT YET BUILT.** Nothing in this document describes shipped capability. Phases 0–3 are proposed work; every capability statement outside the explicitly labelled "design commitment" lists is conditional ("would", "is designed to"). Read it as a build-and-business proposal, not a product description.

> **Confidence convention.** Every quantitative claim carries one of three inline tags: **(verified)** — checked against the repo or an authoritative catalogue this session; **(approximate)** — a planning extrapolation from a verified anchor; **(unverified this session)** — asserted, no live query or authoritative source consulted, must be re-derived before it can carry weight. Under PALS's Law, an untagged number is a defect; if you find one, treat it as unverified.

---

## 1. Executive Summary

doc-ray builds a deep *text* knowledge base — a ~20-layer linguistic annotation graph with per-layer provenance, entity canonicalization, deterministic embeddings, RLS, and a human-review pattern for untrusted model output. That KB is, by construction, **blind to imagery**: figures, diagrams, screenshots, illustrations, and colour carry no representation in the graph.

Direct sampling this session found the corpus to be **visually dominant** (qualitative finding, well-supported by sampling; see §2). The measured text-corpus scale is **verified this session** via live `COUNT(*)` over the corpus tables: **256 documents, 242 fully text-annotated, 14 failed text extraction, 24,967,557 tokens, 828,314 sentences (and 828,314 sentence embeddings), 1,139,122 entity mentions.**

**Prism** proposes to close the visual gap without abandoning the platform's discipline. It adds a three-tier visual layer whose defining property is that *every visual claim is traceable to either a deterministic tool or a grounded, scored, reviewable model output* — never a laundered model guess:

- **Tier 1 — Deterministic visual features** (colour palettes as hex + measured proportions, histograms, saturation/contrast/edge-density, image-type class, dimensions, EXIF-when-present). A pure function of the crop bytes plus a pinned tool version. Reproducible, no model, no hallucination surface. This tier *is* ground truth. Effort: **M**.
- **Tier 2 — Vision embeddings** (CLIP/SigLIP-class dual encoder) for cross-modal retrieval (text→image, image→image). Opaque but reproducible; used only for ranking, never asserts a fact into the graph. Effort: **L**.
- **Tier 3 — Grounded VLM descriptions** (captions + domain analysis) cross-checked against the document's *own* text about the figure, scored, and gated. High-agreement output commits with recorded confidence; low-agreement output routes to a human `visual_review` queue and is never written to the trusted graph. Opt-in, GPU-bearing. Effort: **XL**.

All three tiers carry per-layer provenance, plug into the existing annotation graph and GraphQL API, and inherit per-user RLS. The differentiator is not captioning — it is the verification, grounding, and provenance layer around it.

**What must be resolved before circulating budget or list prices** (all flagged in-line below): (a) re-derive the corpus counts and the image-volume estimate against real queries; (b) confirm the true "image-only" document set with a zero-extracted-character query rather than the annotation gap; (c) re-check third-party vendor pricing (notably the Gemini rate) against dated sources; (d) `PURPOSE.md` now **exists**, and its principles *validate* Prism's verification posture (*"source before synthesis"*, *"AI proposes, verification decides"*, *"low-confidence… route to review"*); but it frames the platform as a **text** KB — its "Becoming" list is semantic retrieval, composition, and relationship discovery, **not** visual/multimodal — so the operator call is now narrower and sharper: *should product scope extend to a visual layer at all*, and if so, may Tier-3 visual facts enter the trusted graph or stay retrieval-only?

---

## 2. The Opportunity

**The measured signal.** Sampling of 16 documents this session (n=16, not the full corpus) found imagery on more than half the pages in **13 of the 16** sampled documents, and design/sketching titles running roughly **50–96% of page area** as imagery. These specific percentages are **sample-derived (n=16)** — the *qualitative* conclusion ("the corpus is pervasively visual") is well-supported by the sampling; the exact percentage band should not be read as a corpus-wide fact, and the 16→256 generalization is directional, not quantified. If a corpus-wide imagery ratio is needed for a business case, it must be computed directly.

**Why the blind spot is structural, not marginal.** The dominant content is diagrams, layouts, screenshots, and illustrations — *not* fine-art photographs. The PDF text extractor (`fitz`) is known to **undercount** visual content because it misses vector graphics: a page that is entirely a vector schematic surfaces as little or no image block, yet carries the document's core meaning. So the visual gap is not a rounding error; on this corpus it appears to be most of the signal.

**The "image-only" documents (verified).** The 14 non-annotated documents are `status='failed'` (verified distinct from the 242 `status='extracted'`), and the ingest extractor diagnosed each as *"no extractable text — scanned/image-only file? OCR required before ingest."* So these 14 have a near-zero text layer and are wholly invisible to the text KB — a **lower** bound on the visual-recall opportunity, because the 242 *extracted* documents also carry dense, unrepresented imagery (para 1). Caveat: "failed text extraction" is the extractor's diagnosis (near-zero characters); a residual few could be corrupt rather than scanned — but all 14 are text-invisible either way.

**The opportunity, stated conservatively.** Add visual understanding that meets the same standards the text corpus already meets: verifiable, provenance-tracked, cross-modal, and grounded against the document's own text. The wedge is the deterministic tier (zero model risk); the expansion is retrieval, then grounded description.

---

## 3. What We Build — the three-tier visual layer

The organizing principle is that the three tiers are **different epistemic objects** and are trusted differently. Verification is not a post-processing step bolted on at the end; per PALS's Law it is a first-class stage, and each tier satisfies it in a distinct way.

### Tier 1 — Deterministic visual features (ground truth)
Per detected image/region: colour palette (hex + measured proportions), per-channel histogram, mean saturation/value, contrast, edge-density, a deterministic image-type classifier (photo / diagram / screenshot / line-art / chart), aspect ratio, and EXIF *when present*.

- **PALS posture: satisfied by construction.** Output is a pure deterministic function of the crop bytes plus a pinned tool version. The only gate is schema/range validation (palette proportions sum ≈ 1.0, hex well-formed, class in the allowed set). No model, no review queue. This tier is the reference against which the others are judged.
- **Determinism caveat (must-fix in implementation).** "Byte-for-byte reproducible" is **not automatic** for the palette feature: k-means colour quantization with seeded-random initialization (e.g. sklearn `KMeans` defaults) is not run-to-run identical. The guarantee holds only with a **deterministic quantizer** — median-cut, or k-means with a pinned seed, fixed init, and fixed iteration count — pinned in `tool_version`. State the guarantee as "deterministic given the pinned quantizer," and make the determinism unit test assert byte-identical palette output on a fixed fixture.
- **EXIF caveat.** EXIF is meaningful mainly for standalone photo files, which the sampling says are *not* the dominant content here. Page-render crops and vector-graphics regions carry no EXIF, and most embedded rasters lack it. EXIF is a "when present" feature, not a headline. For embedded rasters, extract the native image stream (PyMuPDF `extract_image`) rather than cropping the page render, to preserve native resolution and any metadata.

### Tier 2 — Vision embeddings (cross-modal retrieval)
A CLIP/SigLIP-class dual encoder produces image vectors for image→image search and, critically, a **text-tower** embedding in the *same* space for text→image search.

- **PALS posture: PALS-safe by use, not by verification.** A temperature-free forward pass is reproducible given fixed weights, preprocessing, and precision, but cannot be "verified correct" — only regenerated. It is safe because it is used **only for retrieval** (ranked candidates) and never asserts a fact into the graph. Provenance pins `model` + version + `preprocessing_sha256`; a re-embed writes a new row, never overwrites.
- **The non-obvious hard requirement.** The corpus's existing sentence embedding is `hash-bow-768` — a **deterministic hash bag-of-words vector, not a semantic one** (verified: `EMBEDDING_MODEL = "hash-bow-768"`, blake2b word-hashing). It is **not comparable** to CLIP/SigLIP space. Text→image search therefore requires a *new* real text-tower embedding from the same dual-encoder model. Skipping this is the single most likely silent failure: image→image search would work while text→image quietly returns garbage.

### Tier 3 — Grounded VLM descriptions (the only untrusted-LLM tier)
A vision-language model produces a caption and structured domain analysis, then Prism **grounds** it against the document's own figure text (nearby caption layout-runs, `"Figure N"` references in the full text, same-page prose), scores the agreement, and gates on the score.

- **PALS posture: full treatment.** (a) constrained/JSON decoding + `verify_model_output` reject malformed output; (b) a **specificity-weighted grounding scorer** — weighting named entities, numerals, and labels so a generic "a diagram" cannot spuriously agree with sparse figure text — produces an explicit, auditable `grounding_score`; (c) threshold policy identical to the existing `resolve_residue` (verified `AUTO_COMMIT_THRESHOLD = 0.8`): commit-with-confidence above threshold, route to `visual_review` below, **never** the trusted graph on the low-confidence path.
- **Grounding is auditable, not vibes.** The exact evidence compared (layout-run ids, `full_text` char-spans) and the overlap/similarity components are stored, so a reviewer sees *why* a caption was trusted or queued.
- **Dependency to state honestly.** The grounding score's *semantic-similarity* component needs a real semantic text embedding — which the corpus does **not** currently have (the only text embedding is non-semantic hash-BoW). So full Tier-3 grounding **depends on Tier-2's text tower**. Either (a) make Tier 3 depend on Tier 2 for the semantic component and state it, or (b) ship Tier 3 first with deterministic token/entity-overlap grounding only and add the embedding-similarity component alongside Tier 2. Do not claim a semantic grounding signal no current embedding can provide.

---

## 4. How We Market It

### Name and positioning
**Prism** (recommended) — *"See the whole document — and prove what you see."* A prism deterministically decomposes light into a measurable spectrum; the name maps onto Tier-1 (colour/spectrum as reproducible measurement) and onto the tiered decomposition of each image. Alternatives: **Lucida** (*"Faithful sight for your corpus"*, after the *camera lucida*) and **VeriSight** (*"Vision your knowledge base can cite"*). This document uses Prism as a placeholder.

### Value proposition (design-level, for an unbuilt system)
Prism would give a governed corpus visual understanding it can defend, split into three tiers with sharply different trust properties so the platform never launders a model guess into a fact. The differentiator is not "we can caption an image" — it is that every visual claim is traceable to a deterministic tool or a grounded, scored, reviewable model output.

### Ideal customer profile
The sharpest fit is an organization with **both**: (a) a genuinely visual-dominant corpus, and (b) a governance/verifiability requirement that makes an ungrounded caption *disqualifying*, not merely imperfect. When only (a) holds, plain multimodal-RAG may suffice; Prism's premium — grounding, per-layer provenance, review routing, RLS — is worth it only when (b) also holds.

- **Primary ICP:** technical documentation/standards and scientific/figure-heavy research, where verification is a hard requirement.
- **Secondary ICP:** regulated/defensible domains — legal exhibits/e-discovery, GLAM/digital archives — where black-box vendor tags cannot be cited and per-matter/per-collection access control is mandatory.
- **Wedge, not beachhead:** design/brand libraries are the strongest *corpus* fit and best demo but often weakest on the governance requirement.
- **Sharpest fit of all:** a team already running a governed text corpus (RLS/tenancy) that has just discovered its documents are visually dominant — doc-ray's own dogfood profile.

### Differentiation (survey-bounded, not absolute)
> **All competitor capability statements below are (unverified — based on public product documentation, undated this session).** Product capabilities change; none was directly tested this session, and the "surveyed offerings" list is referenced but not attached. Attach the survey (vendor, product tier, doc URL, date tested) behind each assertion before publishing, or keep the "(unverified — public docs)" tag. Do not restate these as absolute market claims.

Within the surveyed set, the observation is that **none appears to pair a deterministic visual-feature layer with grounded-against-source verification and per-layer provenance inside a single governed, access-controlled corpus** — most start at model output. Scoped by category:

- **OCR / IDP extractors** (AWS Textract, Google Document AI, Azure AI Document Intelligence): read text/tables/form fields from a page image; per public docs, no semantic model of the imagery itself, no colour/feature layer, no image↔image or text↔image search, no persistent graph, no grounding.
- **RAG-prep parsers** (Unstructured.io, LlamaParse, Reducto): emit image/figure elements, and some emit VLM figure descriptions. Per public docs, the gap is not "can't see figures" — it is that figure descriptions appear to be trusted parse output with no grounding against the document's own caption/prose and no disagreement gate, and the pipeline is stateless.
- **Multimodal retrieval / embeddings** (Morphik, ColPali, Cohere Embed, Voyage, Nomic, Marqo): strong *components*, not competitors — ranked vectors without a deterministic feature layer, grounded descriptions, provenance, or RLS. Prism would treat these as **swappable Tier-2 engines**.
- **DAM taggers** (Cloudinary): model-guess tags not grounded to source text, per public docs not citable, no linguistic-graph integration.
- **Text knowledge graphs** (Docugami): language/structure graph over prose, per public docs no visual layer.

### Defensible marketing claims
Each claim states its warrant; unbacked claims are excluded.

1. **"Every visual fact is traceable to either a deterministic tool or a scored, reviewable model output — nothing in between."** *Warrant:* design-level guarantee — every tier writes `document_provenance` + `provenance_params`; Tier 3 additionally records `grounding_score` and the exact evidence compared. Auditable by construction. (Design commitment of the proposed architecture, not a measured claim.)
2. **"Tier-1 visual features are reproducible from the source image given a pinned quantizer — no hallucination surface."** *Warrant:* Tier-1 output is a deterministic function of crop bytes + pinned tool version (see the quantizer caveat in §3). No LLM is involved.
3. **"Model-generated captions never enter the trusted graph on the low-confidence path."** *Warrant:* Tier 3 mirrors the existing `resolve_residue` / `resolution_review` policy — below-threshold output routes to `visual_review`; only `status='committed'` rows are exposed as trusted. (Design commitment, not a measured claim.)
4. **"A deterministic, provenance-tagged visual-feature layer that none of the surveyed alternatives appears to ship."** *Warrant:* scoped explicitly to the surveyed vendors, based on public docs, undated. **Survey-bounded — not an absolute market claim.**
5. **Cost framing:** deterministic features cost effectively nothing; a whole-corpus GPU backfill is a single-digit-dollar one-time job *on the current corpus-size estimate*. *Warrant:* Tier-1 is CPU-only (~$0.0002/1,000 images, approximate); the ~$4.7 backfill figure (approximate) derives from the verified H200 anchor applied to an **(unverified this session)** ~50,000-image estimate — contingent on that count.

### Go-to-market motion
1. **Dogfood-as-demo.** Run Prism over the already-ingested corpus and lead with the measured blind spot: cross-modal search surfacing documents a text KB cannot retrieve, plus figure↔prose links. Provable on real internal data. (Use the *verified* image-only count once queried, not the "~14" inference.)
2. **Deterministic-tier-first wedge.** Ship Tier 1 (verifiable, no LLM, no per-page SaaS toll) as the zero-risk entry point; upsell Tier 2 then Tier 3.
3. **Economics story vs metered SaaS.** For visual-dominant corpora, per-page OCR/parse tolls compound on every reprocess; position free deterministic Tier 1 + per-second ephemeral-H200 batch compute as the alternative to per-page metering.
4. **Verticalize where grounding is mandatory.** Enter through technical docs/standards and scientific figures; expand to regulated/defensible domains where RLS + review routing are differentiators.
5. **API-led + reviewer webapp.** Cross-modal GraphQL query for pipelines; the `visual_review` queue as the visible human-in-the-loop product.
6. **Compose, don't compete, on embeddings.** Integrate ColPali-class/Cohere/Voyage/Nomic as pluggable Tier-2 engines; differentiate on the verification/provenance/graph layer above them.

---

## 5. How We Build It

### 5.1 Architecture — where the layer sits
Prism is a set of **new annotation layers on the existing hexagonal spine**, not a new subsystem. It reuses the job queue, provenance, tenancy, and RLS machinery. Where the reuse is genuine it is stated as such; where net-new work hides behind a "reuse" framing, that is called out below.

- **Domain** (`domain/jobs.py`): three new job kinds — `KIND_VISUAL_EXTRACT`, `KIND_VISUAL_EMBED`, `KIND_VISUAL_CAPTION`.
- **Application** (`application/`): new handlers in `job_handlers.HANDLERS`; a deterministic feature module and a grounding-scorer module; Tier 3 reuses `application/verification.py:verify_model_output` and mirrors `application/resolution.py:resolve_residue` (verified `AUTO_COMMIT_THRESHOLD = 0.8`).
- **Ports** (`ports/`): Tier-3 VLM output flows through the **existing** `ports/verification.py:ModelOutputVerifier` Protocol (a new `VLMOutputVerifier` implements it). Two **net-new** ports are required: a **vision-encoder port** for Tier 2 (see §5.4 — Ollama cannot serve this) and a page-rendering port (see §5.3).
- **Adapters** (`adapters/`): `object_store.py` stores crops; `persistence_layers_read.py` gains new async `list_*` readers matching `list_tokens`/`list_entity_links`.
- **Entrypoints** (`entrypoints/`): `_PIPELINE_CHAIN` in `api/mutation.py` is extended; the Strawberry schema gains image readers and a **net-new** cross-modal search resolver (grep confirms no vector/semantic search resolver exists today).

**Annotation-graph integration.** Every visual run writes rows to the existing `annotations` table (`UNIQUE(document_id, layer, tool)`), with `layer ∈ {visual-features, visual-embedding, visual-caption}`, using the same delete-by-`(document_id, layer, tool)` then insert replacement semantics as every NLP layer. A figure participates in the same graph as sentences, entity mentions, and spans — that shared graph is the differentiator.

**Pipeline-chain wiring (precise).** Live chain (verified): `_PIPELINE_CHAIN = [KIND_ANNOTATE, KIND_SEMANTIC_ENRICH, KIND_CANONICALIZE]` (mutation.py:58). Deterministic Tiers 1–2 are appended at the tail so entity-linking is not delayed:
```
[ANNOTATE, SEMANTIC_ENRICH, CANONICALIZE, VISUAL_EXTRACT, VISUAL_EMBED]
```
`VISUAL_CAPTION` (Tier 3, GPU/LLM) is **deliberately not chained**, following the in-code marker at mutation.py:55-57: *"The frontier resolver is never chained — LLM cost stays an explicit request (D-018)."* **Status note:** D-018 is currently an **in-code decision marker only** — it does **not** appear in `engineering_decisions.yml`, which CLAUDE.md designates the validated-ADR system of record (D-017 and D-019+ are present there; D-018 is absent — verified). Cite it as a code-level precedent, and — recommended — add it to the ADR system of record. Tier 3 is triggered by an explicit payload flag, exactly like the Stage-3 resolver.

### 5.2 Data model (new migrations, starting at `089_*`)
Highest existing migration is `088_billing_event_retry.sql`; schema is `1.27.0` (verified 2026-07-15). The set closes with a `schema_version_1_28_0` bump (additive tables ⇒ minor SemVer) so `check_version_consistency.py` stays green; each file inserts into `schema_migrations`. New modules must clear the `check_file_size.py` (D-015) gate.

**Type convention (must-fix).** All ULID primary keys and foreign keys use the repo's **`ulid` domain** (`CREATE DOMAIN ulid AS char(26)`, verified in 002), **not** `text`. A `text → ulid`/bpchar FK will fail to create ("incompatible types"). Every table below uses `ulid` for ULID columns; `sentence_vision_embedding.sentence_id` matches the existing sentence-anchor type (the `embedding` table keys on `span_id uuid`).

**Why not reuse `spans` / `embedding`?** Both are structurally incompatible (verified): `spans.char_start`/`char_end` are `NOT NULL` (an image has no char range); `embedding` is keyed `(span_id, model)` with a fixed `vector(768)` and joins through `spans`.

- **`document_image`** — one row per detected image/region: `id ulid PK`, `document_id uuid FK`, `page_number int`, `source text CHECK in ('embedded-raster','rendered-region','full-page')`, `xref int`, bbox (x0/y0/x1/y1 double precision), width/height px, `object_key text`, `sha256 char(64)`, `detector`/`detector_version text`. RLS via `docray_attach_rls('document_image','document_id IN (%DOCS%)')` + `docray_client` CRUD grant + index on `(document_id, page_number)`.
- **`image_tier1_features`** — `id ulid PK`, `document_image_id ulid FK`, `document_id uuid` (denormalized for direct RLS), `dominant_colors jsonb` (`[{hex, proportion}]`), `histogram jsonb`, mean_saturation/mean_value/contrast/edge_density real, `image_class text CHECK`, aspect_ratio real, exif jsonb, `tool`/`tool_version text`.
- **`image_embedding`** — `document_image_id ulid FK`, `document_id uuid`, `model text`, `vector vector(D)`, `preprocessing_sha256 char(64)`, `PRIMARY KEY (document_image_id, model)`; HNSW index `USING hnsw (vector vector_cosine_ops)`. **`D` is frozen at migration time** to the chosen model (see §5.4); a re-embed under a new model inserts a new `(document_image_id, model)` row, never overwrites.
- **`sentence_vision_embedding`** (net-new, required) — the real text-tower table in the *same* space as `image_embedding`: `sentence_id` (matching the sentence anchor type), `document_id uuid`, `model text`, `vector vector(D)`, `PRIMARY KEY (sentence_id, model)`, HNSW index. Without this, text→image search silently returns garbage.
- **`visual_caption`** — `id ulid PK`, `document_image_id ulid FK`, `document_id uuid`, `annotation_id ulid REFERENCES annotations(id)` (layer `visual-caption`), `caption text`, `analysis jsonb`, `model`/`model_version text`, `grounding_score real`, `grounded_against jsonb`, `status text CHECK in ('committed','review','rejected')`. Only `status='committed'` is exposed as trusted.
- **`visual_review`** — a review queue *structurally similar to* `resolution_review` but **with net-new RLS that must be written and tested, not inherited**: `resolution_review` has **no `document_id` column and no RLS registration** in the 018 RBAC registry (verified — it hangs off `mention_id` as a curator/admin table). So there is no document-scoped RLS pattern to inherit "unchanged." Prism's `visual_review` carries a denormalized `document_id`, its own `docray_attach_rls` call, and a cross-tenant-isolation test. (Confirm whether `resolution_review`'s lack of RLS is intentional before mirroring anything from it.) Fields: `id ulid PK`, `document_image_id ulid FK`, `document_id uuid`, `proposed jsonb`, `grounding_score real`, `grounding_evidence jsonb`, `status text DEFAULT 'pending' CHECK in ('pending','approved','rejected')`, `reviewer uuid REFERENCES principals(id)`, timestamps.
- **Enum + config:** `ALTER TYPE provenance_kind ADD VALUE IF NOT EXISTS 'vision_captioned'` (reuse `embedded`/`indexed` for Tiers 1–2); new config keys `DOCRAY_VISION_MODEL`, `DOCRAY_VISION_EMBED_DIM`, `DOCRAY_VLM_MODEL`, `DOCRAY_VLM_GROUNDING_THRESHOLD`.

**Storage-dimension notes.** Candidate `D`: 768 (CLIP/SigLIP ViT-L) or 1152 (SigLIP-so400m) — model dimensions **(unverified — cite to model cards before freezing)**. The managed cluster runs **pgvector 0.8.1 (verified this session)**, so both dims sit comfortably under the 2000-dim `vector` HNSW-indexed limit, and the **`halfvec` optimization (2 bytes/dim) is available (verified — pgvector ≥ 0.7, `halfvec` type present)**. `D` is still baked into the migration, so freeze it after the model bake-off; default to `vector(D)` and adopt `halfvec` if the storage/recall trade-off measures favorably.

### 5.3 Pipeline (end-to-end)
- **Stage 0 — Pixel source (net-new rendering, corrected).** The earlier "read the cached 144-DPI pixmap, no new rendering code" framing is **wrong**: `reextract.backfill_document_geometry_from_store` reads the *original PDF blob* and re-parses it — it never touches `rendered-pages/`; and the per-page PNGs are produced *lazily* on download cache-miss, so corpus-wide that cache is sparse-to-empty. Stage 0 must therefore **fetch the original PDF and render pages itself** (PyMuPDF), or run a corpus-wide pre-render backfill first. This is real CPU cost on the always-on worker and **weakens the "Tier-1 is effectively free on the always-on worker" claim** — rendering a visual-dominant corpus is not free. **Hexagonal fix:** `_render_pdf_page_png` currently lives in `entrypoints/api/downloads.py` (an entrypoint). Application code must **not** import from entrypoints. Lift the PyMuPDF render logic into an adapter behind a rendering port; both the download entrypoint and the visual job handler depend on the port.
- **Stage 1 — Region detection (`KIND_VISUAL_EXTRACT`, CPU).** Two detectors, because one is provably insufficient: (1) embedded rasters via `page.get_images`/`get_image_rects` — the `type==1` blocks `extraction_pdf.py:_blocks_from_pages` currently drops (verified `if b.get("type",0)!=0: continue`); (2) **vector-graphics/figure regions** by rasterizing page areas with low `text_run_count` (from `document_pages`/`document_layout_runs`) — mandatory, because `fitz` undercounts vector diagrams. Emit `document_image` rows; write crops to Spaces (`document-images/{content_sha256}/pN-rM.png`). DB stores metadata only.
- **Stage 2 — Tier-1 features (CPU).** Per crop: palette (deterministic quantizer), histogram, saturation/value/contrast/edge-density, image-type classifier, aspect ratio, EXIF-when-present. `COPY` into `image_tier1_features`.
- **Stage 3 — Tier-2 embeddings (`KIND_VISUAL_EMBED`, GPU).** Batch crops through the vision encoder on the H200; L2-normalize; write via **`COPY`** into `image_embedding`; build HNSW **after** bulk load. Also embed sentence text through the model's **text tower** into `sentence_vision_embedding`.
- **Stage 4 — Tier-3 grounded VLM (`KIND_VISUAL_CAPTION`, GPU, opt-in).** Assemble the document's own figure text (caption layout-runs, `"Figure N"` matches in `full_text`, same-page prose); pass image + text to the VLM under constrained/JSON decoding.
- **Stage 5 — PALS gate + routing.** `verify_model_output` rejects schema violations; the specificity-weighted grounding scorer computes agreement (token/entity overlap always available; embedding-similarity component requires the Tier-2 text tower — see §3 dependency). `grounding_score ≥ threshold` ⇒ commit; below ⇒ `visual_review(status='pending')`. Threshold is empirically calibrated against a human-labeled sample, not hard-coded.
- **Stage 6/7 — Chain wiring + surface.** Tiers 1–2 appended to `_PIPELINE_CHAIN`; Tier 3 opt-in. New readers + the net-new cross-modal search resolver (`query_vector <=> image_embedding` via HNSW cosine), all principal-bound.

### 5.4 Models & infra
H200 = 141 GB VRAM (verified $3.44/hr, per-second billing). VRAM is not the ceiling — the managed-Postgres backend limit is (see §5.5).

| Role | Candidate class | Approx. VRAM (fp16) | Notes |
|---|---|---|---|
| Vision embedding (Tier 2) | SigLIP-so400m (~1152-d) **or** CLIP/SigLIP ViT-L (~768-d) | single-digit GB (approximate) | **Served by a net-new vision-encoder adapter** (open_clip / transformers / sentence-transformers). **Ollama does not serve CLIP/SigLIP dual-encoder towers** — the "reuse the Ollama transport" claim does *not* apply to Tier 2. Pick one on a corpus sample before freezing `D`. |
| VLM caption (Tier 3) | 7B-class open image-text VLM (Qwen2.5-VL / InternVL / LLaVA-class) | ~14–16 GB fp16, ~8 GB quantized (approximate) | May reuse **only** the Ollama HTTP/host/temperature-0 scaffolding (`resolver_ollama.py`). Net-new work: base64 image submission (the verified payload has **no `images` field**), a caption/analysis response schema, and verification that constrained `format` decoding works on the chosen vision model. vLLM if measured throughput demands it. |

VRAM footprints are model-class approximations — measure on one real batch before scaling.

### 5.5 DB write strategy (the managed cluster is the bottleneck)
The 4 vCPU / 16 GB managed cluster is the measured constraint; write amplification is the dominant infra concern.
1. **`COPY`, never `executemany`.** `semantic_enrichment._copy_rows` documents a **~2.7× DB-CPU penalty** (verified comment) for concurrent per-row INSERTs on an HNSW-indexed table; `embedding` already writes via `_copy_rows`. Image tables use **`_copy_rows` directly** (not `ensure_spans`, which is span/char-range-specific and inapplicable to images). `COPY` has no `ON CONFLICT`, so callers clear conflicting rows by `(document_id, layer, tool)` first.
2. **Build HNSW after bulk load**; elevate `maintenance_work_mem` or build offline.
3. **Bounded per-worker batches + capped concurrency.** The live 16 GB cluster reports **`max_connections = 400` (verified this session)** — far above the stale `~97` (8 GB-plan) figure in `gpu-batch-processing.md`, so backend headroom is ample. The practical cap is CPU / write-throughput (the `executemany`→`COPY` lesson), not connection count: keep concurrent H200 writers modest and let `COPY` + post-load HNSW do the work.
4. **Keep bytes out of Postgres.** PG storage is ~10× Spaces per GB (~$0.215 vs ~$0.02 /GiB/mo, approximate); only vectors + metadata in PG, crops in Spaces.

### 5.6 Phased rollout (effort XS/S/M/L/XL, no time estimates)
- **Phase 0 — Extraction + render spike on one book. Effort: S.** Prove Stage 0/1 on one visual-dominant document: render pages (net-new render path), run both detectors, write `document_image` rows + crops. Deliverable: measured region counts vs naive `get_images()`, validating the vector-graphics detector. No schema commitment.
- **Phase 1 — Tier-1 deterministic layer, corpus-wide. Effort: M.** Ship `document_image` + `image_tier1_features` migrations (`089_*`), the CPU feature extractor (deterministic quantizer) + classifier, `KIND_VISUAL_EXTRACT`, RLS, provenance, `list_document_images`. Zero LLM, zero GPU (except the render cost noted in §5.3).
- **Phase 2 — Tier-2 embeddings + cross-modal search. Effort: L.** Freeze `D` (after the model bake-off), ship `image_embedding` + `sentence_vision_embedding` + HNSW migrations, the net-new vision-encoder adapter, `KIND_VISUAL_EMBED` (`COPY` writer, post-load HNSW), and the net-new cross-modal search resolver.
- **Phase 3 — Tier-3 grounded VLM, opt-in. Effort: XL.** Ship `visual_caption` + `visual_review` (net-new RLS) migrations, the `VLMOutputVerifier`, the specificity-weighted grounding scorer, `KIND_VISUAL_CAPTION` (net-new image-submission adapter), the review-queue webapp surface, and the `vision_captioned` provenance value. Stays out of `_PIPELINE_CHAIN`. Highest effort: the grounding scorer + threshold require empirical calibration against human review, and Tier 3 is the only tier bearing recurring GPU cost and depends on Tier 2's text tower for the semantic grounding component.

Phases are independently useful: Phase 1 delivers a verifiable index with no model risk; Phase 2 adds retrieval; Phase 3 adds grounded description (with the stated Tier-2 dependency).

### 5.7 Testing (repo TDD + landing gates)
- **Migrations:** golden-file apply test per `089_*`; assert RLS attached (non-owner role cannot read cross-tenant rows), `schema_migrations` inserted, `docray_schema_version()` matches the bump.
- **Tier-1 determinism:** byte-identical palette output across two runs on a fixed fixture (with the pinned quantizer); range/schema checks (`Σ proportion ≈ 1.0`, hex well-formed, class in CHECK set); labeled fixtures for classifier accuracy.
- **Region detection:** fixtures for embedded raster, vector-graphics-only page, and image-only page; assert the vector detector fires where `get_images()` returns nothing (the undercount is a regression test).
- **Tier-2:** L2-norm invariant; `COPY`-path row-count parity; HNSW recall on a seeded fixture; an **explicit test that text→image uses `sentence_vision_embedding`, not `hash-bow` `embedding`** (guards the top silent-failure mode).
- **Tier-3:** `verify_model_output` rejects malformed JSON; grounding scorer unit tests (specific caption scores high, "a diagram" scores low on the same sparse text); routing tests (≥ threshold commits, < threshold lands in `visual_review`, never in committed captions); threshold calibrated against a human-labeled sample.
- **RLS/tenancy:** cross-tenant isolation test on every new table (including `visual_review`, whose RLS is net-new).
- **E2E:** ingest one document; assert Tiers 1–2 auto-produce and Tier 3 does **not** run without the opt-in flag.

---

## 6. How We Price It

### 6.1 Philosophy — price the value/verification premium, not "the compute"
The three tiers have very different cost characters, but the earlier "meter the compute / costs differ by two-to-four orders of magnitude / VLM ≫ embeddings" framing is **false for the default self-host path** and is corrected here. On self-host: Tier 2 ≈ **$0.034/1,000** and Tier 3 ≈ **$0.048/1,000** (approximate) — a **~1.4× ratio**, not orders of magnitude. The four-orders spread only appears if you compare deterministic Tier 1 ($0.0002) to *hosted* Tier 3 — not the default path.

The accurate framing: **Tier 1 is ~free; Tier 2 and Tier 3 self-host compute are within ~1.5× of each other; and Tier 3 is priced as a value/verification premium — the grounding pass + `visual_review` routing — not a compute premium.** The genuine cost gradient is Tier 1 → Tier 2 (~170×, from ~$0.0002 to ~$0.034). Model:
- **Hybrid, not pure usage-based:** a tiered subscription (predictability) with a committed base allowance + metered overage. COGS scales with *images processed and descriptions generated*, not seats, so seat pricing would mis-charge visual-heavy corpora (per-doc image counts vary widely). But pure metering produces unpredictable bills and punishes the exact ICP.
- **Tier 1 bundled free** (it *is* ground truth and near-zero cost). **Tier 2 bundled generously** (cheap, the everyday capability). **Tier 3 metered as a premium opt-in credit** — mirroring the platform's "LLM cost is an explicit request" rule; billed only on the `KIND_VISUAL_CAPTION` opt-in.

### 6.2 Meters
1. **Images processed (Tier 1 + Tier 2)** — counted at region-detection time (one `document_image` row = one billable image), bundled into allowances. **Caveat:** this unit is a direct function of detector tuning, and vector-graphics detection is uncalibrated (§7). **Do not finalize the per-image meter until the detector is calibrated (Phase 0 deliverable);** consider metering embedded-raster count separately from rasterized vector regions, and dedupe/cap rendered-region crops so a tuning change cannot silently multiply billable rows.
2. **Cross-modal search queries** — fair-use rate-limited, metered only past a high threshold; not a primary revenue meter.
3. **VLM descriptions (Tier 3)** — 1 credit = 1 grounded, verification-passed description. The credit funds the grounding pass + review routing, i.e. the verification layer, not the raw forward pass.

Storage (PG vectors, Spaces crops) is a GB-month guardrail, near-negligible (§6.4), not a profit center.

### 6.3 Illustrative tiers
> **Price points are proposed/illustrative**, anchored only to competitor subscription figures in evidence — Cloudinary Plus **~$89/mo** and Advanced **~$249/mo** (approximate), Docugami **~$300/mo** (unverified). No first-party willingness-to-pay data exists; **final prices require market validation.** Allowances stated in images (the cost-true unit); document-equivalents use the **(unverified this session)** ~200-images/doc assumption.

- **Free / Pilot ($0):** Tier 1 up to 10,000 images/mo; Tier 2 one-time 2,500-image pilot + rate-limited queries; Tier 3 one-time 100-credit trial. Cost to serve (self-host): T1 10k ≈ $0.002, T2 2,500 ≈ $0.085 one-time, storage negligible → **≪ $1/mo** (genuinely free to offer).
- **Team — illustrative $99/mo:** Tier 1 unlimited (fair-use); Tier 2 100,000 images/mo (~500 docs); Tier 3 2,000 descriptions/mo, overage $3/1,000; 25 GB storage; GraphQL API, RLS/tenancy, reviewer webapp.
- **Business — illustrative $499/mo:** Tier 2 1,000,000 images/mo (~5,000 docs); Tier 3 25,000 descriptions/mo, overage $2/1,000; 250 GB; SSO, priority review queue.
- **Enterprise — custom / open-core self-host:** committed VLM credit pools, dedicated H200 batch windows or BYO-GPU, on-prem/self-host license, SLA, audit exports. Quote-based.

**Overage-safety rule (must-hold).** The flat $2/$3 credits are **guaranteed only if metered overage is fulfilled self-host or via Gemini-Flash-batch** (COGS $0.048–$0.38/1,000, approximate). Against a *premium hosted model* the flat credit can go margin-negative: on the consistent token basis below, Sonnet-5-batch ≈ $1.86/1,000 (intro) rising to ≈ $2.79/1,000 post-intro — the latter exceeds the $2 Business credit (negative margin) and nearly exhausts the $3 Team credit. **Either enforce self-host/Gemini-Flash fulfillment for metered overage (recommended), or price premium-model captions as a separate higher credit.** Do not offer a flat credit that a premium model can exceed.

### 6.4 Unit economics (arithmetic shown)
Verified H200 anchor: **$3.44/hr = $0.000956/GPU-second**. Throughputs are **(approximate)** planning extrapolations (embedding ~100,000 img/hr, band 55k–180k; VLM 7B ~20 img/s, band 10–40) — measure one real batch before scaling. All hosted-VLM rows use **one consistent per-image token basis: 858 input + 200 output tokens (ASSUMED, not measured)**; Anthropic/Google tokenize images differently (Claude ≈ (w·h)/750 image tokens), so the real input count depends on crop dimensions and must be re-measured.

**Per 1,000 images:**

| Path | Arithmetic | Cost / 1,000 | Confidence |
|---|---|---|---|
| Tier 1 (CPU) | ~free | **~$0.0002** | approximate |
| Tier 2 embed (self-host) | (1,000 / 100,000) × $3.44 | **$0.034** (band **$0.019–$0.063**) | approximate; band re-derived from 55k–180k img/hr |
| Tier 3 VLM (self-host, 7B) | (1,000 / 20) × $0.000956 = 50 s × $0.000956 | **$0.048** (band $0.024–$0.096) | approximate |
| Tier 3 — Gemini Flash batch | (858k × $0.15/M) + (200k × $1.25/M) = $0.129 + $0.25 | **$0.38** | **rates unverified this session — confirm against Google's live pricing page (date it); token counts assumed** |
| Tier 3 — Claude Haiku 4.5 batch | [(858k × $1/M) + (200k × $5/M)] × 0.5 = [$0.858 + $1.00] × 0.5 | **$0.93** | Haiku rate $1/$5 verified; token counts assumed |
| Tier 3 — Claude Sonnet 5 batch (intro $2/$10) | [(858k × $2/M) + (200k × $10/M)] × 0.5 = [$1.716 + $2.00] × 0.5 | **$1.86** (post-intro $3/$15 → **$2.79**) | Sonnet rate verified (intro exp 2026-08-31); token counts assumed |
| GPT-4o-mini | image-token multiplier unverified | **excluded from all decisions** | do not use until verified |
| pgvector storage (D=1152, heap-only) | 1152×4 B = 4.6 KB/row; 1,000 rows = 4.6 MB = 0.0045 GiB × $0.215/GiB/mo | **~$0.001/mo** (heap); **~$0.002/mo** with HNSW index (~2×) | approximate; basis stated |
| Spaces crops (~50 KB) | 50 MB = 0.049 GiB × $0.02/GiB/mo, only above base | **~$0.001/mo** (else $0) | approximate |

> **Correction applied:** the earlier Haiku ($1.55) and Sonnet ($3.10) figures used a hidden image-token basis ~2.45× the Gemini row's 858/200 and are not reproducible. On the single consistent basis above, Haiku-batch ≈ **$0.93** and Sonnet-5-batch ≈ **$1.86** (intro). The two storage bases are now stated explicitly (heap-only ~4.6 KB/row at D=1152 vs +HNSW ~2×), reconciling the earlier build (4.6 KB) vs pricing (8.7 KB) gap.

**Per document (~200 images — unverified density):**
- Self-host T1+T2+VLM: ($0.034 + $0.048) × 0.2 ≈ **$0.016/doc** + amortized spin-up; recurring storage < $0.001/doc/mo.
- Hosted VLM (batch) + self-host T1/T2 (adds ~$0.007/doc for T2): Gemini Flash ≈ **$0.083/doc**, Haiku ≈ **$0.19/doc**, Sonnet-5 intro ≈ **$0.38/doc** (per-doc figures now include the T2 self-host component, which the earlier draft omitted).

**Margin math (self-host, direct compute + storage only):**
- **Team @ $99:** T2 100k × $0.034/1,000 = **$3.44**; VLM 2,000 self-host × $0.048/1,000 = **$0.10**; storage ~$0.20; boot amortized ~$0.01–0.30 → direct COGS ≈ **$4–5** → margin ≈ **95%**.
- **Business @ $499:** T2 1M = **$34.40**; VLM 25k self-host = **$1.20**; storage ~$2 → direct COGS ≈ **$38** → margin ≈ **92%**.
- **Overage:** $3/1,000 vs self-host $0.048 → 98%; vs Gemini Flash $0.38 → 87%; vs Haiku batch $0.93 → 69%. **Against Sonnet-5-batch $1.86 (intro), the $2 Business credit is only ~7% and the post-intro $2.79 is negative — hence the §6.3 fulfillment rule.**

> **Unbiased caveat — these are direct-COGS margins, not fully-loaded.** They exclude: (a) vLLM/H200 ops engineering, (b) the mandatory grounding-verification pass (a second, cheaper LLM/embedding pass required on *both* build and buy paths), (c) `visual_review` human-reviewer labor, and (d) the always-on Postgres node + Spaces base (sunk but real). Fully-loaded margin is **lower** and cannot be stated precisely from available facts. The 90%+ figures describe marginal compute economics, not company margin.

**Whole-corpus backfill (~50,000 images — unverified count):**
- **Retrieval demo only (Tier 1+2):** embed 50k/100k = 0.5 hr + boot ~0.17 hr = 0.67 hr × $3.44 ≈ **$2.3**.
- **Full T1+T2+T3:** + caption 50k/20 = 2,500 s = 0.69 hr → 1.36 hr × $3.44 ≈ **$4.7** (band ~$2–$15). (The two are different jobs; the marketing demo of *cross-modal search* needs only the $2.3 retrieval backfill.)
- **Recurring storage:** PG vectors 50k × ~8.7 KB (incl. index) ≈ 0.42 GiB × $0.215 ≈ **~$0.09/mo**; Spaces crops **50,000 × 50 KB = 2.5 GB ≈ 2.4 GiB ≈ ~$0.05/mo** (or $0 within base) — *correcting the earlier 18 GiB figure, which conflated pre-existing full-page renders (not new to this layer) with new crops.*
- **Increment vs the existing sentence index:** by row count 50k/828k ≈ **+6%**; by *storage* (D=1152 vs 768) ≈ **+8–9%**. Either way, no cluster upsize expected at base volume. **Both the ~828k baseline and the ~50k count are (unverified this session)** — re-derive before any budget depends on them. Sub-region embedding (e.g. 5× → ~250k vectors, +~30%) plus corpus growth is what would eventually force the next Postgres tier.

### 6.5 Build-vs-buy
Self-host adds a **fixed ~$0.57/batch** spin-up (10 min boot × $0.000956/s) on top of ~$0.05/1,000 marginal. Against Gemini-Flash-batch at $0.38/1,000 **(rate unverified — see table)**:
> N = $0.57 / ($0.38 − $0.05 per 1,000) ≈ **1,700 images** — **contingent on the unverified Gemini rate; treat as provisional.**
- **Above ~1,700 images/batch → self-host wins**, widening fast (50k backfill ≈ $4.7 self-host vs ~$19 Gemini / ~$47 Haiku / ~$93 Sonnet-5 intro batch, all approximate on assumed tokens).
- **Below ~1,700 → hosted batch wins** (avoids boot cost + the 60-second GPU billing floor).
- **Routing policy (effort S–M):** default backfills/scheduled windows to self-host; route small on-demand requests to hosted batch. **Two hard caveats:** the Sonnet-5 intro rate **expires 2026-08-31** (reverts $3/$15, ~doubling that path), and **GPT-4o-mini must not enter any build-vs-buy decision** until its image-token multiplier is verified.
- Not captured on either side: the PALS grounding-verification pass (fixed cost of doing this correctly, both paths) and vLLM ops overhead.

### 6.6 Competitor-anchored comparison
> **The honest comparison is on *toll structure*, not identical capability** — OCR/IDP vendors extract text/tables, not visual features/embeddings/grounded descriptions. **Every per-page toll below is (approximate/unverified) and the underlying market facts are undated** — attach a dated source (vendor pricing page or internal cost-analysis doc + date) before publishing. The **1.3 images/page** conversion is itself an **assumption**.

At ~1.3 images/page (~1,300 images per 1,000 pages), our T1+T2 self-host COGS ≈ $0.04/1,000 pages (approximate):

| Vendor | Per-page toll (unverified/approximate, undated) | Our comparable self-host COGS |
|---|---|---|
| AWS Textract OCR | ~$1.50/1,000 pp (Tables ~$15, Forms ~$50) | ~$0.04/1,000 pp |
| Google Document AI OCR | ~$1.50/1,000 pp | ~$0.04/1,000 pp |
| Azure AI Doc Intelligence Read | ~$1.50/1,000 pp | ~$0.04/1,000 pp |
| Unstructured.io Hi-Res | ~$10/1,000 pp | ~$0.04/1,000 pp |
| Reducto | ~$0.015/page ≈ $15/1,000 | ~$0.04/1,000 pp |
| LlamaParse | Fast ~$1/1,000, Agentic ~$10/1,000 | ~$0.04/1,000 pp |

For the VLM-description tier (closest to LlamaParse/Reducto figure captions), hosted-batch COGS ≈ Gemini Flash ~$0.49/1,000 pages / Haiku ~$1.2/1,000 pages (approximate, assumed tokens) — same band as parse APIs, but with grounding + verification + review those APIs do not perform.

### 6.7 Pilot motion
Land-with-deterministic-value, expand-into-metered-compute: (1) dogfood-as-demo (one ~$2.3 retrieval backfill turns the internal corpus into a live cross-modal demo; use the *queried* image-only count, not "~14"); (2) deterministic-tier-first wedge (Free Tier 1, ≪ $1/mo to serve even at the 10k cap); (3) expansion path = the cost gradient (Free → Team/Business → Enterprise); (4) verticalize where verification is mandatory (technical docs/standards, scientific figures, then legal/GLAM).

Instrumenting the three meters + overage is an **M** effort.

---

## 7. Risks & Open Questions

1. **Managed-cluster write amplification (top risk).** A visual-dominant corpus can generate more `image_embedding` + HNSW inserts than the existing sentence-embedding index, on the 4 vCPU/16 GB bottleneck. *Mitigation (mandatory):* `_copy_rows` (not `executemany`), post-load HNSW build, bounded batches, capped concurrency (backend ceiling **verified at `max_connections=400`**, §5.5; the real cap is write throughput, not connections). Getting this wrong repeats the `executemany` incident that motivated `_copy_rows`.
2. **Cross-modal search silently broken.** The existing `embedding` is hash-BoW-768, not semantic. *Mitigation:* `sentence_vision_embedding` (real text tower) is a required Phase-2 deliverable, with a test asserting it is the table queried.
3. **Vision dimension baked into the migration.** Switching SigLIP↔CLIP later = full re-embed + index rebuild. *Mitigation:* bake off on a corpus sample and freeze `D` before writing the Phase-2 migration; also verify the installed pgvector version and the indexed-dimension ceiling (both unverified this session).
4. **Vector-graphics region detection is genuinely hard.** Under-detection ⇒ KB stays blind; over-detection ⇒ floods Tiers 2/3 with junk and **inflates the per-image bill** (the meter depends on detector tuning, §6.2). *Mitigation:* calibrated detector using `document_pages`/`document_layout_runs` geometry, validated in Phase 0; do not finalize the per-image meter until calibrated.
5. **Stage-0 rendering is real, not free.** The corpus-wide render cache is sparse; Tier 1 must render pages itself on the always-on worker. *Mitigation:* account for CPU cost; consider a pre-render backfill; do not market Tier 1 as "zero compute."
6. **Tier-1 palette determinism.** Requires a pinned deterministic quantizer or the byte-for-byte guarantee (and its test) fails. *Mitigation:* median-cut or pinned-seed k-means, pinned in `tool_version`.
7. **Tier-3 grounding can be gamed by generic captions.** *Mitigation:* specificity-weighted scorer; threshold calibrated against human review, not a guessed value.
8. **Tier-3 cost/opacity + Tier-2 dependency.** VLM is the only non-reproducible, cost-bearing tier, and its semantic grounding component depends on Tier 2's text tower. *Mitigation:* keep it opt-in (D-018 precedent); state the Tier-2 dependency; hosted-vs-self-host break-even (~1,700 images) is provisional on the unverified Gemini rate.
9. **~~`halfvec` / pgvector version unconfirmed~~ (resolved, verified this session).** Managed cluster runs pgvector **0.8.1**; `halfvec` is available and both candidate dims are under the 2000-dim indexed ceiling. `D` is still baked into the migration — freeze it after the model bake-off; default to `vector(D)`.
10. **Corpus statistics — text counts verified, image volume not.** The text-corpus counts (256 docs / 242 extracted / 14 failed / 24,967,557 tokens / 828,314 sentences+embeddings / 1,139,122 mentions) and the 14-doc image-only set are **verified this session** via live `COUNT(*)`. The image-volume estimate (~200 images/doc, ~50k corpus-wide) remains **(unverified)** and is load-bearing for GTM allowances and backfill math. *Mitigation:* re-derive it from a real region-detection pass before any budget or list price depends on it.
11. **Third-party vendor pricing unverified.** The Gemini rate anchors the build-vs-buy break-even; competitor per-page tolls are undated. *Mitigation:* confirm against dated vendor sources before publishing any decision that depends on them; per-image token counts (858/200) are assumed and must be measured.
12. **`PURPOSE.md` scope (operator call).** `PURPOSE.md` now **exists** (89 lines, committed `f69fcde`), so the CLAUDE.md align-with-vision mandate **is** checkable — and Prism is *compatible*: its verification-first design is a direct expression of PURPOSE.md's *"source before synthesis"*, *"AI proposes, verification decides"*, and *"low-confidence… route to review"* principles (Tier-3's grounding gate + `visual_review` is exactly that pattern; *"human direction over autonomous drift"* sanctions the review path). The decisions that genuinely remain **operator, not agent, calls:** (a) **product scope** — PURPOSE.md frames a *text* KB, and its "Becoming" list (semantic retrieval, Document Composer, relationship discovery) does **not** include visual/multimodal, so extending to a visual layer at all is a vision decision; (b) given (a), whether Tier-3 visual facts may enter the *trusted graph* (PURPOSE.md's verify-decide principle implies the gated path is the sanctioned mechanism, but asserting visual facts at all is the operator's call); (c) *open-core vs closed* — the GTM leans on a self-host narrative, and the licensing boundary sets the Enterprise price floor. Phases 1–2 (retrieval-only, no graph assertions) align with PURPOSE.md regardless; Phase 3's trusted-graph write and the premium meter are the scope decisions that need the operator. **Flag; do not assume.**

---

## 8. Appendix — Assumptions & confidence markers

| # | Assumption / figure | Value | Confidence | Notes |
|---|---|---|---|---|
| A1 | Corpus text scale | 256 docs, 242 extracted, 14 failed, 24,967,557 tokens, 828,314 sentences+embeddings, 1,139,122 mentions | **verified this session** | Live `COUNT(*)` over the corpus tables. |
| A2 | Corpus is visually dominant (qualitative) | 13/16 sampled docs >50% pages imagery; 50–96% page-area (design titles) | sample-derived (n=16); qualitative direction supported | Exact percentages not corpus-wide facts. |
| A3 | Image-only document count | 14 (`status='failed'`) | **verified this session** | Extractor-diagnosed "no extractable text — scanned/image-only"; text-invisible. Lower bound (extracted docs also carry unrepresented imagery). |
| A4 | Image volume | ~200 images/doc; ~50,000 corpus-wide (band 50–500/doc) | **unverified this session** | Drives all corpus-wide dollar/allowance figures. |
| A5 | H200 anchor | $3.44/hr = $0.000956/GPU-s, 141 GB VRAM, per-second billing | **verified** | |
| A6 | Repo facts | chain `[ANNOTATE, SEMANTIC_ENRICH, CANONICALIZE]` (mutation.py:58); D-018 comment mutation.py:55-57 (absent from engineering_decisions.yml; D-017/D-019+ present); migration 088 / schema 1.27.0; `AUTO_COMMIT_THRESHOLD=0.8`; `ulid`=char(26); `EMBEDDING_MODEL="hash-bow-768"`; resolution_review has no RLS | **verified 2026-07-15** | |
| A7 | Vision embedding dims | CLIP/SigLIP ViT-L ~768; SigLIP-so400m ~1152 | **unverified — cite to model cards** | Under pgvector 2000-dim indexed limit — ceiling also unverified vs managed cluster. |
| A8 | Vision-encoder serving | Ollama cannot serve CLIP/SigLIP; net-new adapter required | **verified (architectural)** | Ollama reuse applies only to Tier-3 HTTP scaffolding. |
| A9 | Embedding throughput | ~100,000 img/hr (band 55k–180k) | **approximate** | Measure one real batch. |
| A10 | VLM throughput (7B) | ~20 img/s (band 10–40) | **approximate** | |
| A11 | Per-image VLM tokens | 858 input / 200 output | **assumed, not measured** | Vendor image tokenization differs (Claude ≈ (w·h)/750). |
| A12 | Gemini Flash batch rate | $0.15/M in, $1.25/M out → $0.38/1,000 | **unverified this session** | Confirm against Google's dated pricing page; anchors the break-even. |
| A13 | Anthropic rates | Haiku 4.5 $1/$5; Sonnet 5 intro $2/$10 (exp 2026-08-31), std $3/$15; batch 0.5× | **verified** | |
| A14 | Postgres backend ceiling | `max_connections=400` | **verified this session** | Live `SHOW max_connections` on the resized 16 GB cluster (the ~97 figure was the 8 GB-plan number). |
| A15 | Competitor per-page tolls & subscriptions | Textract ~$1.50/1,000 pp; Cloudinary ~$89/$249; Docugami ~$300; etc. | **approximate / unverified, undated** | Attach dated sources before publishing. |
| A16 | Competitor capabilities | (see §4) | **unverified — public docs, undated, not tested** | Survey-bounded, not absolute. |
| A17 | Images/page conversion | 1.3 | **assumption** | |
| A18 | `_copy_rows` DB-CPU penalty | ~2.7× vs `COPY` for per-row INSERTs | **verified (in-repo comment)** | |
| A19 | pgvector version / halfvec / 2000-dim ceiling | pgvector **0.8.1**; halfvec available; dims < 2000 ceiling | **verified this session** | Both candidate dims (768, 1152) index-safe; halfvec optional. |

---

*Prepared as a build-and-business proposal. Every quantitative claim is tagged; untagged numbers are defects. Per PALS's Law, all model-derived output in the proposed system is untrusted by default and verified explicitly — the absence of that verification layer would be an architectural defect, not a runtime bug. `PURPOSE.md` exists and Prism aligns with its verification-first principles; the remaining product-scope questions in §7.12 are operator calls and are flagged, not assumed.*