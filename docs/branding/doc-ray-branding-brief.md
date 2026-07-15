---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-15"
status: draft
---

# doc·ray — Branding Brief

**Purpose of this document.** A production brief for the design partners who
will create doc·ray's visual assets: logo, color, typography, iconography, the
`doc-ray.com` site, and a documented design system. It defines *what the brand
means and how it must look and sound* so that independent design work stays
coherent. It is grounded in [`PURPOSE.md`](../../PURPOSE.md) (the product's
reason to exist) and in the design tokens **already shipping** in
[`webapp/static/design-system.css`](../../webapp/static/design-system.css),
which this brief treats as the current source of truth to be formalized and
extended — not replaced.

> This brief is subject to the caveats in [`../../DISCLAIMER.md`](../../DISCLAIMER.md).
> Where it proposes new visual direction (logo concepts, applications), that is
> exploration for the design team to resolve — flagged as such — not settled
> fact. Where it documents existing tokens or licensing, those are verifiable
> against the cited files.

**How to read the confidence markers.**
- **[SHIPPING]** — already true in the codebase; the source of truth.
- **[DIRECTION]** — a recommended path; strong but open to design iteration.
- **[EXPLORE]** — a divergent option offered for the design team to test.
- **[OPEN]** — a decision the operator must make (see §14).

---

## 1. The brand in one page

| | |
|---|---|
| **Name** | doc·ray (always lowercase; the middle dot is integral — see §6) |
| **Domain** | doc-ray.com **[OPEN]** — confirm final domain + any trademark clearance |
| **Category** | A living library / knowledge base / document-composition workspace |
| **One-liner** | A living library that turns your documents into a connected corpus — threaded by the entities inside them, with every idea traceable back to its source. |
| **Essence** | *Grounded illumination* — seeing what is inside documents, and never losing the thread back to the source. |
| **Primary device** | The interpunct `·` — an entity node and the origin of the "ray". |
| **Type** | Inter (interface/display) + JetBrains Mono (data, IDs, labels) |
| **Signature color** | Brand red `#e5484d` on cool slate — red as *accent*, slate as *structure* |
| **Voice** | Precise, calm, accountable. Never hype; never vague. |

---

## 2. Why the brand exists (grounding, from PURPOSE.md)

doc·ray rejects a false choice that PURPOSE.md names directly: knowledge systems
either **preserve documents as static files** that are hard to recombine, or
**generate fluent answers** that are hard to verify. doc·ray closes that gap. Its
connective mechanism is **entities** — the same person, place, date, or
organization, resolved to one shared identity, threading scattered mentions into
a single connected fabric of concepts on which new work can be built *without
losing the trace back to source*.

The brand must carry three ideas that follow from this:

1. **Connection** — scattered mentions become one fabric (entities as tissue).
2. **Provenance** — every claim traces to its source; nothing is taken for granted.
3. **Governed intelligence** — AI proposes; verification and humans decide.

A brand that reads as "another AI chat tool" is **off-strategy**. doc·ray is the
opposite of untraceable fluency.

---

## 3. Positioning

**For** individual researchers, authors, and builders — and organizations with
serious document collections — **who** need their documents to become
connected, searchable, verifiable knowledge rather than a pile of files or
un-auditable AI output, **doc·ray is** a living library that threads a corpus
together through the entities inside it and keeps every idea traceable to its
source. **Unlike** static document stores (which don't recombine) **or**
chat-style assistants (which don't cite), doc·ray makes structure and provenance
first-class.

**Category line [SHIPPING copy]:** *Living library · knowledge base.*

**Frame to own:** the space *between* the filing cabinet and the chatbot —
connected, accountable knowledge.

---

## 4. Audiences

Design and messaging must serve four audiences without fracturing the identity:

| Audience | What they need the brand to signal |
|---|---|
| **Individual researchers / authors / builders** | A personal, trustworthy "living library"; calm, capable, not enterprise-cold. |
| **Organizations with document collections** | Governance, access control, auditability, continuity — credibility and rigor. |
| **Knowledge / legal / research / compliance teams** | Reusable institutional knowledge; checkable AI-assisted output. |
| **AI agents under governance** | First-class but *non-sovereign* actors — the brand frames AI as a proposer, not an authority. |

**Implication:** one identity, two registers. A warmer, personal register for
individuals; a more governed, precise register for organizations. Same wordmark,
same color, same type — modulated by tone and imagery, not by a second brand.

---

## 5. Brand personality, voice & tone

**Personality attributes** (design and copy should be legible as these):
- **Precise** — exact, structured, defined. No hand-waving.
- **Accountable** — shows its sources; comfortable with "we can't verify this."
- **Calm** — quiet confidence; restraint over spectacle.
- **Illuminating** — makes the hidden structure of documents visible.
- **Living** — active, connective, growing — not a static archive.

**We are / We are not:**

| We are | We are not |
|---|---|
| Grounded | Hypey |
| Structured | Cluttered |
| Illuminating | Mystifying ("magic AI") |
| Accountable | Overconfident |
| Precise | Cold or clinical |

**Voice principles** (see the codebase's own writing: PURPOSE.md, DISCLAIMER.md):
- Lead with the source, not the conclusion. *"Every claim traces to its source."*
- Prefer concrete nouns from the domain (corpus, entity, provenance) over marketing abstractions (synergy, unleash, revolutionize).
- Mark uncertainty rather than hide it — uncertainty is a feature of the brand, not a weakness.
- Short, load-bearing sentences. Delete anything that exists only to sound impressive.

**Tone shifts by context:** confident and inviting on the marketing site; exact
and neutral in the product; sober and explicit in governance/enterprise
material.

---

## 6. Verbal identity & the name

**Wordmark spelling.** `doc·ray` — lowercase, one word, joined by a middle dot
(interpunct, U+00B7). The dot is **not** decorative punctuation; it is the brand
device (see §8). In running plain text where the interpunct is impractical,
`doc-ray` (hyphen, as in the domain and package name) is the accepted fallback,
but the interpunct wordmark is preferred wherever type is controlled.

**Meaning of the name (rationale, [DIRECTION]):**
- **doc** — documents, the raw material.
- **ray** — illumination (seeing *inside* a document, X-ray-like) **and** a ray as a line from an origin: the trace back to a source. The "ray" is both *insight* and *provenance line*.
- **·** — the entity: the shared node where scattered mentions meet; also the origin point from which the ray emanates.

**Domain-language lexicon** (use these words; they are the product's real
vocabulary): corpus, document, ingest, extract, sentence, annotation layer,
**entity**, canonicalize, **provenance / source**, span, occurrence, lexeme,
collection, curate, compose, governance, verification.

**Words to avoid:** *magic, AI-powered miracle, unleash, revolutionary,
brain, second brain* (overused), *chatbot, answers* (we cite, we don't
"answer"), and anything implying autonomous certainty.

**Taglines** (candidate set — pick one primary, [OPEN]):
- **Primary [DIRECTION]:** *A living library for grounded knowledge.* [SHIPPING — already in product footer]
- *Every claim traces to its source.* [SHIPPING — footer creed]
- *Documents, connected by what's inside them.*
- *See inside your documents. Keep the thread to the source.*
- *Nothing taken for granted.* (ties to the disclaimer ethos)

**Boilerplate (≈50 words, for footers/OG/press):**
> doc·ray is a living library that turns documents into a connected, queryable
> corpus — recognizing the people, places, dates, and organizations inside them
> and resolving each to one shared identity, so ideas link across the whole
> library and every claim traces back to its source.

---

## 7. Brand pillars → design translation

The five pillars come straight from PURPOSE.md. Each must have a visual/verbal
consequence, so the identity *argues the strategy* rather than decorating it.

| Pillar (PURPOSE.md) | Design translation |
|---|---|
| **Source before synthesis** | Provenance is visible: a recurring "trace line" / source-link motif; citations styled as first-class, never fine print. |
| **Entities are the connective tissue** | The node-and-thread motif (the `·`); a graph/constellation visual language for connection. |
| **AI proposes, verification decides** | A visible "proposed vs. confirmed" state language (e.g. dashed/soft = proposed, solid = verified); never present AI output as settled. |
| **Living structure over passive storage** | Active, connective imagery — threads, light, growth — not shelves, folders, or filing cabinets. |
| **Human direction over autonomous drift** | Calm, deliberate, human-legible UI; governance shown as clarity, not friction. |

---

## 8. Logo & the mark (design direction)

The wordmark leads; the interpunct is the extractable icon. Deliver a **system**,
not a single lockup.

### 8.1 Primary lockup [DIRECTION]
The wordmark **doc·ray** set in Inter, with the interpunct `·` in brand red and
the letterforms in ink (slate/near-black). This is already how the product
renders its brand (`.brand b { color: var(--accent) }` — the dot is red). Keep
that equity. The red dot on ink letters is the single most recognizable asset;
protect it.

### 8.2 The icon / app mark [DIRECTION]
Extract the **`·` as a hero glyph** — an entity node that can also read as the
origin of a ray. Recommended primary icon directions to design and test:

- **[DIRECTION] The Node** — a single red dot with a subtle emitted ray or a
  short "trace" line, on a slate tile. Reads at 16px (favicon) as a confident
  red dot; reads at large sizes as node-emits-ray.
- **[EXPLORE] The Aperture / Ray** — "ray" as illumination: a fine beam or lens
  aperture passing through/into a document edge. Evokes seeing-inside.
- **[EXPLORE] The d·r monogram** — `d` and `r` connected by the node, forming a
  minimal two-node graph (documents ↔ entity ↔ retrieval).
- **[EXPLORE] The Constellation** — a few nodes threaded into a fabric that
  resolves into the mark; the most literal "entities connect the corpus" idea,
  but hardest to keep legible small.

**Recommendation:** lead with **The Node** for the app icon/favicon (it survives
16px and monochrome); reserve the constellation as an illustrative/marketing
motif, not the icon.

### 8.3 Construction & rules (to be finalized by the designer)
- **Clearspace:** minimum = the width of the letter `o` in the wordmark on all sides.
- **Minimum sizes:** wordmark legible to ~90px wide on screen; icon to 16px.
- **Monochrome:** must work in single-ink (all-ink and all-white/knockout). The red dot becomes ink or white when color is unavailable — the *form* must still read.
- **Do not:** recolor the dot to a non-brand hue; add gradients/bevels/shadows to the mark; stretch, outline, or reposition the interpunct; set the wordmark in a different typeface; place the mark on low-contrast or busy backgrounds.

### 8.4 Favicon / OS icons [SHIPPING favicon convention]
Product artifacts in this ecosystem use emoji favicons where a full icon isn't
warranted; for the site and app, produce a proper icon set. Deliverables in §12.

---

## 9. Color system [SHIPPING — source of truth]

doc·ray already ships a complete, theme-aware token system. **Document and
extend it; do not restyle it.** Values below are copied from
`design-system.css`. The governing rule: **red is an accent, slate is the
structure.** Primary actions (buttons) are near-black slate; red marks the
brand, links, selection, focus, and highlights — used sparingly so it stays
meaningful.

### 9.1 Core tokens

| Role | Light | Dark |
|---|---|---|
| Background | `#f5f7fa` | `#0f1216` |
| Surface / panel | `#ffffff` | `#171b21` |
| Ink (text) | `#171a1f` | `#e6e9ee` |
| Divider / line | `#e3e8ef` | `#262c35` |
| **Brand accent** | **`#e5484d`** | **`#f2555a`** (brightens in dark) |
| Accent hover/2 | `#f2555a` | `#f79a9e` |
| Primary button fill | `#171a1f` (near-black slate) | inverts (light slate on dark ink) |

### 9.2 Ramps
- **Neutral (cool slate), 100→900:** `#f8fafc, #eef1f6, #e0e5ec, #cbd2dc, #97a1b0, #6a7483, #48515d, #2d333b, #171a1f`.
- **Accent (brand red), 100→900:** `#fff1f1, #ffe1e2, #fdc3c5, #f79a9e, #ee666c, #e5484d(600 = base), #cb3a40, #a92e33, #7c2427`.

### 9.3 Semantic status
`ok #1f9d55` · `warn #b7791f` · `info #2b6cb0` · `danger` = accent-700. Dark-mode
variants brighten (`ok #37c26b`, `warn #e0a63a`, `info #4a9eea`).

### 9.4 Domain color language [SHIPPING]
The product carries a **part-of-speech palette** (slate + red family) for
annotation. The design system should treat "data/annotation color" as a distinct
sub-palette from "brand/UI color," and document both, so linguistic visualization
never competes with UI signaling.

### 9.5 Accessibility rules (mandatory)
- Target **WCAG 2.1 AA**: ≥ 4.5:1 for normal text, ≥ 3:1 for large text and UI/graphical elements.
- **The base red `#e5484d` does not meet AA for normal body text on white** and must **not** be used for body copy — reserve it for large text, icons, the mark, focus rings, links (validate link contrast), and accents. This matches its current usage. **[Verify every pairing with a contrast checker; do not assume.]**
- Never encode meaning by color alone (the product already pairs POS color with non-color cues — keep that principle brand-wide).
- Design and ship both light and dark; the tokens already exist for both.

---

## 10. Typography [SHIPPING]

- **Interface & display:** **Inter** (`--display`, `--sans`). Also currently mapped to `--serif` — i.e. the system is intentionally sans-only today. **[OPEN]** whether to introduce an editorial serif for long-form/marketing (see below).
- **Data, IDs, labels, code, entity/lexeme identifiers:** **JetBrains Mono** (`--mono`). The monospace is a *brand carrier*: it signals precision and "this is data with an identity." Use it deliberately for IDs, provenance/source references, counts, and technical labels.
- **Licensing (verifiable):** Inter and JetBrains Mono are both released under the **SIL Open Font License 1.1**, which permits embedding and web use — safe for the site, product, and distributed assets. *(Confirm the exact version/source at production time.)*
- **Spacing scale [SHIPPING]:** 4px base — `4, 8, 12, 16, 24, 32, 48, 64`. The type scale should be documented against this rhythm.

**Type direction [DIRECTION]:**
- Keep Inter/JetBrains Mono as the core pair — it is real, licensable, and already the product's voice.
- **[EXPLORE]** a single editorial display/serif *only* for marketing hero moments (the "living library" feels literary). If adopted, constrain it to marketing headers; do not introduce it into the product UI. Decide before logo finalization since it affects the wordmark's typographic world.
- Use **JetBrains Mono for the "creed/provenance" line** as a recurring signature (e.g. *"Every claim traces to its source."*) to make provenance feel systemic.

---

## 11. Iconography, imagery, motion & data language

**Iconography [DIRECTION].** Geometric line icons, consistent stroke, aligned to
the 4px grid. Motifs to build the set around: node/dot, thread/edge, trace line,
source/anchor, lens, layers. Avoid generic "AI sparkle" and "robot" icons —
they contradict the "AI proposes, humans decide" stance.

**Imagery [DIRECTION].**
- **Do:** abstract node-and-thread fabrics; light/ray tracing a path back to a
  point; document structure made visible (spans, layers, links); restrained,
  precise diagrams.
- **Don't:** filing cabinets, folders, shelves (static-storage cliché); glowing
  brains; humanoid robots; chat bubbles; stock "AI" gradients and neon meshes.
- **Provenance as a visual habit:** wherever a claim, summary, or entity appears
  in marketing, show its source link/trace. The brand *demonstrates* provenance.

**Motion [DIRECTION / SHIPPING seed].** The product already has a top
**progress "ray"** (`#nav-progress`, a thin accent bar) and honors
`prefers-reduced-motion`. Extend this restraint: motion illuminates or traces
(a ray drawing a path, a node connecting), it never bounces or decorates. Always
provide reduced-motion equivalents.

**Data / graph visual language [DIRECTION].** Because the product *is* a
connected fabric of entities, define a first-class visualization style: nodes
(entities) = filled dots in the accent/neutral families; edges (co-occurrence /
relationships) = thin slate lines; "proposed" connections dashed/soft, "verified"
solid — encoding the *AI-proposes / verification-decides* pillar directly in the
graph aesthetic. This is likely doc·ray's most ownable visual territory.

---

## 12. Applications & assets to produce

**doc-ray.com (marketing site)** — hero (wordmark + primary tagline + category
line), the "false choice → doc·ray closes the gap" narrative, entity-fabric
visual, audience sections (individual vs. organization registers), provenance
demonstration, footer with creed line. Light/dark.

**Product surface** — the existing webapp is the live design system; align the
site to it (same tokens, type, mark) so marketing→product feels continuous.

**Icon & favicon set** — SVG master + PNG (16/32/180/192/512) + `.ico` +
maskable/adaptive + Apple touch + OG/social preview (1200×630, light & dark).

**Social / OG cards** — templated: wordmark, one message, entity-fabric motif;
per-audience variants.

**Logo package** — wordmark (primary, stacked, monochrome, knockout), icon
(full-color, mono, knockout), clearspace/min-size sheet — in SVG, PNG, and
print-safe formats.

**Docs / repo** — a README brand header/badge consistent with the mark; the
disclaimer/creed treatment.

**Presentation & enterprise** — a deck template and one-pager in the governed,
precise register for organizational buyers.

**Agent/MCP context** — a text/attribution convention for how doc·ray presents
itself to AI agents (non-sovereign framing).

---

## 13. Design-system deliverables & handoff

The engineering source of truth is
[`webapp/static/design-system.css`](../../webapp/static/design-system.css)
(CSS custom properties: color ramps, semantic aliases, spacing, type roles,
light/dark, POS palette). Design deliverables must **map 1:1 to these tokens** so
design and code do not drift.

Deliver:
1. **Token reference** — colors (light/dark), type roles + scale, spacing, radii, elevation, motion — named to match the CSS variables.
2. **Component kit** — buttons (slate primary, accent secondary/links), inputs, cards, tables, pills/tags, nav, pager, status/notice, empty/pending/failed states, the preview/cover thumbnail, the annotation/POS treatment, the entity/graph components.
3. **Logo package** (§12) with usage do/don'ts.
4. **Figma library** mirroring tokens + components, ready for both marketing and product.
5. **Accessibility annotations** — contrast results per pairing, focus states, reduced-motion specs.
6. **Brand guidelines PDF/site** — condensing §§5–11 for external partners.

---

## 14. Open decisions for the operator [OPEN]

1. **Domain / name lockup:** confirm `doc-ray.com`; confirm the interpunct wordmark `doc·ray` as canonical vs. the hyphen fallback; trademark clearance.
2. **Editorial serif:** introduce one for marketing headers, or stay sans-only (affects the wordmark's world; decide before logo finalization).
3. **Primary tagline:** choose one from §6.
4. **Icon direction:** approve **The Node** as primary (recommended) or select from the explore set.
5. **Register balance:** how much to lean personal ("living library") vs. governed ("enterprise control") in the *default* first impression on the homepage.
6. **Motion appetite:** confirm the "illuminate/trace, never decorate" restraint as the ceiling.

---

## 15. Acceptance criteria (definition of done for the asset production)

- Wordmark + icon system delivered in all formats (§12), passing 16px and monochrome tests.
- Full token set documented and reconciled 1:1 with `design-system.css`, light + dark.
- Every color pairing used for text/UI validated against WCAG AA; the base red is confirmed *not* used for body text.
- `doc-ray.com` hero and core sections designed in both themes, continuous with the product UI.
- No off-strategy imagery (no folders/brains/robots/chat clichés); provenance shown at least once in marketing.
- A brand-guidelines artifact exists that a new designer can follow without this brief.

---

## 16. References

- [`PURPOSE.md`](../../PURPOSE.md) — product purpose, pillars, audiences (primary grounding).
- [`README.md`](../../README.md) — what the product does.
- [`DISCLAIMER.md`](../../DISCLAIMER.md) — epistemic commitments; the "nothing taken for granted" ethos.
- [`webapp/static/design-system.css`](../../webapp/static/design-system.css) — the shipping token system (color, type, spacing, light/dark, POS palette).
- [`docs/product-capability-map.md`](../product-capability-map.md) — capability grounding.
- Font licensing: Inter and JetBrains Mono — SIL Open Font License 1.1 *(confirm version/source at production)*.
