---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 5 (1M context) via Claude Code"
  date: "2026-09-18"
doc_type: design
status: current
---

# doc·ray brand and visual identity

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

This is the record of the identity the product ships. The colour and spacing
tokens are copied from [`webapp/static/design-system.css`](../../webapp/static/design-system.css),
which remains the source of truth; `scripts/generate_favicons.py` derives the
icon set from §4. Where a value is stated here and in the stylesheet, the
stylesheet wins and this page is updated.

## 1. The brand in one page

| | |
|---|---|
| **Name** | doc·ray — lowercase, one word, joined by a middle dot (interpunct, U+00B7). `doc-ray` (hyphen) is the fallback in plain text, the domain and the package name. |
| **Category** | A living library / knowledge base / document-composition workspace |
| **One-liner** | A living library that turns your documents into a connected corpus — threaded by the entities inside them, with every idea traceable back to its source. |
| **Essence** | *Grounded illumination* — seeing what is inside documents, and never losing the thread back to the source. |
| **Primary device** | The interpunct `·` — an entity node and the origin of the "ray". |
| **Type** | Inter (interface/display) + JetBrains Mono (data, identifiers, labels) |
| **Signature colour** | Brand red `#e5484d` on cool slate — red as *accent*, slate as *structure* |
| **Voice** | Precise, calm, accountable. Never hype; never vague. |

The name: **doc** — documents, the raw material; **ray** — illumination (seeing
*inside* a document) and a line from an origin, the trace back to a source;
**·** — the entity, the shared node where scattered mentions meet and the point
from which the ray emanates.

## 2. Voice

Personality: **precise**, **accountable** (shows its sources; comfortable with
"this cannot be verified"), **calm**, **illuminating**, **living**.

| We are | We are not |
|---|---|
| Grounded | Hypey |
| Structured | Cluttered |
| Illuminating | Mystifying ("magic AI") |
| Accountable | Overconfident |
| Precise | Cold or clinical |

- Lead with the source, not the conclusion. *"Every claim traces to its source."*
- Prefer the product's own vocabulary — corpus, document, ingest, sentence,
  annotation layer, entity, canonicalize, provenance, span, lexeme, collection,
  lens, curate, compose, verification — over marketing abstractions.
- Mark uncertainty rather than hide it.
- Avoid: *magic, AI-powered, unleash, revolutionary, second brain, chatbot,
  answers* (the product cites; it does not "answer"), and anything implying
  autonomous certainty.

Taglines in use: *A living library for grounded knowledge.* (product footer) and
*Every claim traces to its source.* (footer creed).

Boilerplate (≈50 words):
> doc·ray is a living library that turns documents into a connected, queryable
> corpus — recognizing the people, places, dates, and organizations inside them
> and resolving each to one shared identity, so ideas link across the whole
> library and every claim traces back to its source.

## 3. Pillars → design translation

| Pillar ([PURPOSE.md](../../PURPOSE.md)) | Design translation |
|---|---|
| **Source before synthesis** | Provenance is visible: a recurring trace-line / source-link motif; citations styled as first-class, never fine print. |
| **Entities are the connective tissue** | The node-and-thread motif (the `·`); a graph/constellation language for connection. |
| **AI proposes, verification decides** | A visible proposed-vs-confirmed state language (dashed/soft = proposed, solid = verified); model output is never presented as settled. |
| **Living structure over passive storage** | Active, connective imagery — threads, light, growth — not shelves or filing cabinets. |
| **Human direction over autonomous drift** | Calm, deliberate, human-legible UI; governance shown as clarity, not friction. |

## 4. The mark

**Primary lockup.** The wordmark **doc·ray** set in Inter, with the interpunct in
brand red and the letterforms in ink (`.brand b { color: var(--accent) }`). The
red dot on ink letters is the single most recognizable asset.

**Icon / app mark — "the node".** The `·` extracted as a hero glyph: a red dot
with a short emitted ray or trace line on a slate tile. It reads at 16 px as a
confident red dot and at large sizes as node-emits-ray. The constellation of
threaded nodes is an illustrative motif, not the icon.

**Rules.** Clearspace: the width of the letter `o` in the wordmark on all sides.
Minimum sizes: wordmark ~90 px wide on screen; icon 16 px. Monochrome: the mark
must work in single ink and knockout — the dot becomes ink or white, the form
still reads. Never recolour the dot to a non-brand hue, add gradients, bevels
or shadows, stretch, outline or reposition the interpunct, set the wordmark in
another typeface, or place the mark on low-contrast or busy backgrounds.

## 5. Colour system

Red is an accent; slate is the structure. Primary actions are near-black slate;
red marks the brand, links, selection, focus and highlights, used sparingly.

### 5.1 Core tokens

| Role | Light | Dark |
|---|---|---|
| Background | `#f5f7fa` | `#0f1216` |
| Surface / panel | `#ffffff` | `#171b21` |
| Ink (text) | `#171a1f` | `#e6e9ee` |
| Divider / line | `#e3e8ef` | `#262c35` |
| **Brand accent** | **`#e5484d`** | **`#f2555a`** |
| Accent hover | `#f2555a` | `#f79a9e` |
| Primary button fill | `#171a1f` | inverted (light slate on dark ink) |

### 5.2 Ramps

- **Neutral (cool slate), 100→900:** `#f8fafc, #eef1f6, #e0e5ec, #cbd2dc, #97a1b0, #6a7483, #48515d, #2d333b, #171a1f`.
- **Accent (brand red), 100→900:** `#fff1f1, #ffe1e2, #fdc3c5, #f79a9e, #ee666c, #e5484d (600 = base), #cb3a40, #a92e33, #7c2427`.

### 5.3 Semantic status

`ok #1f9d55` · `warn #b7791f` · `info #2b6cb0` · `danger` = accent-700.
Dark-mode variants brighten (`ok #37c26b`, `warn #e0a63a`, `info #4a9eea`).

### 5.4 Annotation colour

The product carries a part-of-speech palette (slate + red family) for
linguistic annotation. It is a distinct sub-palette from brand/UI colour so
that visualization never competes with UI signalling, and meaning is never
encoded by colour alone.

### 5.5 Accessibility

WCAG 2.1 AA: ≥ 4.5:1 for normal text, ≥ 3:1 for large text and UI elements.
The base red `#e5484d` does not meet AA for body text on white and is not used
for body copy — it is reserved for large text, icons, the mark, focus rings and
links (link contrast validated per pairing). Light and dark themes both ship;
the tokens exist for both.

## 6. Typography and spacing

- **Interface and display:** Inter (`--display`, `--sans`; `--serif` is also
  mapped to Inter — the system is deliberately sans-only).
- **Data, identifiers, labels, code, provenance references:** JetBrains Mono
  (`--mono`). The monospace signals "this is data with an identity"; the creed
  line is set in it.
- **Licensing:** Inter and JetBrains Mono are released under the SIL Open Font
  License 1.1 (embedding and web use permitted).
- **Spacing scale:** 4 px base — `4, 8, 12, 16, 24, 32, 48, 64`.

## 7. Iconography and imagery

Geometric line icons with a consistent stroke, aligned to the 4 px grid, built
around node/dot, thread/edge, trace line, source/anchor, lens and layers.
Generic "AI sparkle" and "robot" icons contradict the AI-proposes / humans-decide
stance and are not used.

[← root README](../../README.md)
