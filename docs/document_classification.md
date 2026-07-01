# A Universal Classification of Documents

*A proposal — a synthesis of existing frameworks, not an established standard. The
prior-art references in §4 are real and cited in good faith; verify them independently.
Category boundaries are deliberately fuzzy: real documents blend types, so this scheme
positions a document as a **point in a multi-axis space**, not a leaf in a tree.*

---

## 0. The core decision: facets, not a tree

A single taxonomic tree cannot be universal. A **tweet** and an **encyclopedia entry**
can share a genre ("informative") while differing on nearly every other property —
length, internal structure, authority, mutability, audience. A tree forces those
independent properties into one branch and breaks.

The fix is a **faceted (multi-dimensional) classification**. Define orthogonal axes;
every document is a vector of values across them. Two documents can then be *near* on
some axes and *far* on others — which is what actually happens. The extremes prove the
design: **tweet** and **encyclopedia** end up maximally separated on almost every axis,
and everything else lands in between (§3).

---

## 1. The eight axes

Grouped into three clusters for memorability: **Form** (how it's shaped), **Function**
(what it does, for whom), **Source & Time** (who made it, and how it lives).

### Cluster A — Form

**1. Extent** — sheer amount of text.
`1` Micro (<100 words) · `2` Brief (100–1k) · `3` Standard (1k–10k) · `4` Long (10k–100k) · `5` Book (100k–1M) · `6` Corpus (>1M / multi-work)

**2. Structure** — internal organization (this is your `node`-tree depth).
`A` Atomic (no divisions) · `B` Linear (paragraphs only) · `C` Sectioned (headings) · `D` Hierarchical (nested sections + ToC) · `E` Networked (cross-referenced / hyperlinked)

**3. Register** — formality of language.
`v` Vernacular/casual · `c` Conversational · `n` Neutral/standard · `pr` Professional · `f` Formal/academic

### Cluster B — Function

**4. Purpose** — dominant communicative function (after Jakobson / Werlich). A document
may blend several; record the primary.
`Ex` Expressive · `In` Informative · `Di` Directive/instructional · `Pe` Persuasive · `Na` Narrative · `Ph` Phatic/social · `Tr` Transactional · `Ae` Aesthetic/poetic · `Me` Metalingual (about language itself — dictionaries, glossaries)

**5. Reach** — intended audience size.
`p` Private (self) · `i` Interpersonal (1:1/few) · `g` Group (community) · `P` Public (broadcast) · `U` Universal (reference-for-all)

**6. Stance** — the truth-claim the document makes.
`Fic` Fiction · `Op` Opinion/subjective · `Rep` Reportive (factual, journalistic/unverified) · `Ver` Verified/authoritative

### Cluster C — Source & Time

**7. Provenance** — who produced it and how edited (encodes authority).
`I` Individual · `C` Collaborative · `E` Edited/published · `O` Official/institutional · `X` Machine-generated

**8. Lifecycle** — how it changes over time (relates to FRBR Work→Expression→Manifestation→Item).
`eph` Ephemeral (fixed once posted, short-lived) · `fix` Fixed/canonical (immutable, durable) · `ver` Versioned (discrete editions) · `liv` Living (continuously revised)

---

## 2. Two orthogonal facets, deliberately kept separate

These are real and important but *independent* of the eight above, so they are not
folded into the main matrix:

- **Modality (medium composition):** `text-only` · `text + occasional media` ·
  `multimodal` (integral tables/figures/data) · `hypertext`. A tweet may be text or
  text+image; an encyclopedia is multimodal + hypertext. This is a property of the
  *medium*, not the text's linguistic structure.
- **Subject / aboutness:** what the document is *about* (physics, law, cooking…). This
  is the domain of the classic library systems — **Dewey Decimal**, **Library of
  Congress Classification**, **UDC** — and is fully orthogonal to form and function:
  a recipe and a physics paper differ in subject but a *cooking manual* and a *physics
  manual* share every structural axis. Keeping subject separate is what lets the other
  eight axes stay clean.

---

## 3. Placement matrix — tweet → encyclopedia

Each row is a document type as a coordinate vector. Read the top and bottom rows
together: they diverge on almost every column, which is exactly what a *universal*
scheme must let them do.

| Document type | Extent | Struct | Register | Purpose | Reach | Stance | Provenance | Lifecycle |
|---|---|---|---|---|---|---|---|---|
| Tweet / post | 1 | A | v–c | Ph/Pe/Ex | P | Op/Rep | I | eph |
| Text message (DM) | 1 | A | v | Ph | i | Op | I | eph |
| Email | 1–2 | B | c–pr | In/Di/Ph | i | Rep | I | fix |
| Forum / thread post | 1–2 | B | c | Pe/In | g | Op | I | eph |
| Chat log (whole) | 2–4 | B | v | Ph | g | Op | C | eph |
| Blog post | 2–3 | C | c–n | In/Pe/Ex | P | Op/Rep | I | fix |
| News article | 2–3 | C | n–pr | In | P | Rep | E | fix |
| Op-ed / editorial | 2–3 | B–C | n–pr | Pe | P | Op | E | fix |
| Poem | 1–2 | A–B | (any) | Ae | P | Fic | I | fix |
| Short story | 2–3 | B | n | Na | P | Fic | E/I | fix |
| Novel | 4–5 | D | n–f | Na | P | Fic | E | fix/ver |
| Recipe | 1–2 | C | n | Di | P | Rep | I/E | fix |
| Form / invoice | 1 | A–C | pr–f | Tr | i | Ver | O | fix |
| Legal contract | 2–4 | D | f | Di/Tr | i | Ver | O | ver |
| Technical manual | 3–5 | D | pr | Di | U/g | Ver | O/E | ver |
| Scientific paper | 3–4 | D | f | In | g/U | Ver | E/O | fix/ver |
| Corporate/annual report | 4–5 | D | pr–f | In/Pe | P | Rep/Ver | O | ver |
| Textbook | 5 | D | pr–f | In/Di | U | Ver | E | ver |
| Wiki article (e.g. Wikipedia) | 2–4 | E | n–pr | In | U | Ver | C | liv |
| **Encyclopedia (whole)** | **6** | **E** | **f** | **In** | **U** | **Ver** | **E/O** | **ver** |

The tweet vector `{1, A, v, Ph, P, Op, I, eph}` and the encyclopedia vector
`{6, E, f, In, U, Ver, E, ver}` share **no** value. Every intermediate genre occupies
distinct coordinates — the space is genuinely discriminating.

---

## 4. Relation to prior art

This proposal is a synthesis; each existing framework covers a *slice* of the space:

- **Ranganathan's faceted (Colon) classification** — the PMEST facets and the original
  argument that a subject is a *point in facet-space*, not a leaf in a tree → the
  foundation for §0's facets-not-a-tree decision; this whole scheme is faceted in his sense.
- **Jakobson's six functions of language** (referential, emotive, conative, phatic,
  metalingual, poetic) → the backbone of the **Purpose** axis.
- **Werlich's text typology** (narrative, descriptive, expository, argumentative,
  instructive) → also feeds **Purpose**.
- **Biber's multidimensional register analysis** and **Systemic Functional Linguistics**
  register (*field / tenor / mode*, Halliday) → map onto **Register + Reach** (tenor),
  **Structure + Modality** (mode), and **Subject** (field).
- **FRBR** (Work → Expression → Manifestation → Item, IFLA) → the **Lifecycle** and
  **Provenance** axes: the abstract work vs. its editions vs. physical copies.
- **Dublin Core / DCMI Type Vocabulary** → a coarse *Type* facet (Text, Dataset,
  Image…) subsumed here by **Modality**.
- **Dewey / LC / UDC** → the **Subject** facet, kept orthogonal (§2).

No single one of these is universal on its own; the contribution here is combining the
*form*, *function*, and *source* axes into one coordinate system and holding *subject*
and *modality* separate so the axes stay independent.

---

## 5. Operationalizing it in your pipeline

Each axis becomes a field on the `document` row, and several are **auto-derivable** from
the decomposer you already have — which means classification can drive processing:

> The pipeline today is standard library + an in-memory / JSON tree; the `document` table
> below is the **target** persistence layer. The repo ships a Postgres `db/`, but these
> classifier fields aren't wired to it yet — treat §5 as the design, not the current state.

| Axis | How to assign | Drives… |
|---|---|---|
| Extent | token count (already computed) | batching / chunking strategy |
| Structure | heading depth from your parser — reliably `A`–`D`; `E` (cross-links) is **not** derivable yet, since the readers strip links | whether to run heading/section detection |
| Modality | media/tables in the *source* — but the current text readers discard these, so the signal must be **retained first** | whether to invoke non-text handlers |
| Register | classifier (or lexical features) | tokenizer / model choice |
| Purpose, Stance | text classifier (learned) | which annotation layers are worth running |
| Reach, Provenance, Lifecycle | source metadata (platform, author, edit history) | retention, trust weighting, re-index policy |

```sql
ALTER TABLE document ADD COLUMN extent     INTEGER;  -- 1..6
ALTER TABLE document ADD COLUMN structure  TEXT;     -- 'A'..'E'
ALTER TABLE document ADD COLUMN register   TEXT;     -- 'v','c','n','pr','f'
ALTER TABLE document ADD COLUMN purpose     TEXT;    -- 'Ex','In','Di',...
ALTER TABLE document ADD COLUMN reach      TEXT;     -- 'p','i','g','P','U'
ALTER TABLE document ADD COLUMN stance     TEXT;     -- 'Fic','Op','Rep','Ver'
ALTER TABLE document ADD COLUMN provenance TEXT;     -- 'I','C','E','O','X'
ALTER TABLE document ADD COLUMN lifecycle  TEXT;     -- 'eph','fix','ver','liv'
ALTER TABLE document ADD COLUMN modality   TEXT;     -- optional facet
ALTER TABLE document ADD COLUMN subject    TEXT;     -- optional facet (Dewey/LC code)
```

The structural axis is the direct bridge to the earlier work — but mind the ordering: the
score can't come from the full parse it is meant to gate. Assign it from a **cheap
pre-probe** (a quick scan for heading-like lines / markup), then act on it: a document
probed `A` (atomic) skips full heading/section detection; one probed `C`/`D` triggers the
full `document → heading → paragraph → …` tree you built.

---

## 6. Honest limits

- **Blending is the norm.** Most real documents mix functions (a blog post informs *and*
  persuades). Record a **primary** value per axis, optionally a secondary; don't force a
  single pure category.
- **Axes are not perfectly independent.** Register correlates with Provenance and Reach
  (institutional/public texts trend formal). They're kept separate because the
  correlation is loose — a formal tweet from an official account, an informal internal
  memo — and the exceptions are exactly what you'd want to capture.
- **Ordinal ≠ interval.** The 1–6 Extent scale is ordered, not linear; the gap from a
  novel to an encyclopedia is not "one step" in any real sense.
- **Not exhaustive.** New forms (a thread-of-tweets, a Jupyter notebook, a chat
  transcript) sit *between* rows — which the faceted design handles gracefully by giving
  them a novel coordinate rather than demanding a new tree branch.
