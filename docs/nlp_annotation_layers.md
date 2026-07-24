# The Complete Set of NLP Structural Notations — Mapped onto a Worked Example

This document lists the full stack of structural-notation layers used in NLP and
then walks a single synthetic 3‑paragraph text through **every layer**, showing the
exact notation each one produces. The layers you already built (document → section →
heading/paragraph → sentence → phrase → word) sit at the base; everything else layers on top.

> A few annotations are **illustrative** and depend on the specific tool/model or
> guideline version: subword splits vary by vocabulary (BPE vs. WordPiece vs.
> SentencePiece), knowledge‑base IDs depend on the target KB, and tagset details
> (UPOS vs. language‑specific XPOS, PropBank vs. FrameNet) differ across corpora.
> Treat the shapes as canonical and the exact strings as tool‑dependent.

This document defines the **target**: the complete 20-layer set doc-ray aims
to support. For what is actually schema-backed, writer-backed, API-exposed,
browser-exposed, health-checked, and tested *today*, see the companion
[`docs/nlp_capability_matrix.md`](nlp_capability_matrix.md) — a documented
layer here does not imply production support there.

---

## 1. The complete set

Two families sit side by side. Layers 1–19 are **symbolic structure** (discrete tags,
spans, trees, graphs) — the classical NLP annotation stack. Layer 20 is the
**non‑symbolic** substrate that modern transformers actually consume.

| # | Layer | What it captures | Standard notation / format | Scope |
|---|---|---|---|---|
| 1 | Document / physical structure | headings, sections, paragraphs | TEI‑XML, your `node` tree | document |
| 2 | Sentence segmentation | sentence boundaries | offsets / span list | document |
| 3 | Tokenization | word/punctuation tokens | token list, offsets | sentence |
| 4 | Subword tokenization | model input units | BPE / WordPiece / SentencePiece | token |
| 5 | Lemmatization & stemming | dictionary base form / crude root | `lemma` field | token |
| 6 | Part‑of‑speech tagging | word class | UPOS + XPOS (Universal Dependencies) | token |
| 7 | Morphological features | gender, number, case, tense, mood… | UD **FEATS** (`Key=Value`) | token |
| 8 | Chunking (shallow parsing) | flat NP/VP/PP spans | **BIO / IOB / BIOES** | span |
| 9 | Constituency parsing | phrase‑structure tree | Penn Treebank brackets | sentence |
| 10 | Dependency parsing | head→dependent grammatical arcs | **CoNLL‑U** + UD relations | sentence |
| 11 | Named entity recognition | entity spans + types | BIO / BIOES | span |
| 12 | Entity linking (wikification) | mention → knowledge‑base ID | Wikidata/DBpedia QID, `NIL` | span |
| 13 | Coreference resolution | mentions of the same entity | mention clusters / chains | document |
| 14 | Semantic role labeling | who did what to whom | PropBank ARG0…ARGM‑* | sentence |
| 15 | Word sense disambiguation | which sense of a word | WordNet sense keys | token |
| 16 | Semantic parsing | full meaning representation | **AMR** graph, logical form | sentence |
| 17 | Discourse parsing | relations between spans/clauses | **RST** trees, **PDTB** connectives | document |
| 18 | Temporal & event annotation | events, times, their ordering | **TimeML** (EVENT, TIMEX3, TLINK) | document |
| 19 | Sentiment & pragmatics | polarity, speech acts | polarity labels, dialogue acts | span / sentence |
| 20 | Distributed representations | learned meaning as vectors | embeddings (e.g. 768‑dim) | token / span / doc |

**Where your pipeline already reaches:** layers 1–3 fully (the `docmodel` tree), and
layers 5–6 via `lexeme.py` (spaCy lemma + UPOS, with a stable lexeme‑id registry on top of
the lemmas) — plus per‑segment **language identification**, which isn't even in the list
above. Note the `phrase` level is **not** layer‑8 chunking: it segments clauses on
punctuation, not linguistic NP/VP spans. The remaining gaps are shallow/deep syntax
(8–10), reference (11–13), meaning (14–16), and discourse/temporal/sentiment (17–19).

Two layers this project uses sit *outside* the classic 20: **language identification**
(a span label — which language each segment is in, needed for multilingual routing) and
**lexeme / type identity** (a dictionary mapping each lemma to a stable id that per‑token
annotations reference — a type‑level index, not a per‑occurrence annotation).

---

## 2. The synthetic text

> **Paragraph 1.** In March 2023, Dr. Elena Vasquez founded a startup called Lumière
> in Berlin. She had studied astrophysics at MIT, but she wanted to build tools for
> climate research. Her first product measured atmospheric carbon with cheap sensors.
>
> **Paragraph 2.** The company grew quickly because investors trusted her vision.
> However, Elena soon realized that the hardware was expensive. Therefore, she pivoted
> to software, and her team released an open‑source library that scientists could use
> freely.
>
> **Paragraph 3.** Today, Lumière employs forty engineers, and its library powers
> hundreds of experiments worldwide. Analysts predict that the firm will double its
> revenue next year. Elena remains optimistic, though she knows the road ahead is
> difficult.

For the token‑ and sentence‑level layers below, the running example is the core clause
of sentence 1: **“Elena founded a startup in Berlin.”** Cross‑sentence layers
(coreference, discourse, temporal) are shown over the whole text.

---

## 3. Layer‑by‑layer mapping

### 1 · Document / physical structure
The text has no explicit headings, so the tree is: `document → paragraph×3 →
sentence×9 → …`. This is exactly your `node` tree with `kind` and parent/child links.

```
document
├── paragraph[0]
│   ├── sentence[0]  "In March 2023, Dr. Elena Vasquez founded a startup called Lumière in Berlin."
│   ├── sentence[1]  "She had studied astrophysics at MIT, but she wanted to build tools…"
│   └── sentence[2]  "Her first product measured atmospheric carbon with cheap sensors."
├── paragraph[1] → sentence[3..5]
└── paragraph[2] → sentence[6..8]
```

### 2 · Sentence segmentation
Boundaries as character spans into the paragraph (stand‑off). Note the hazard your
own segmenter has: `Dr.` must **not** end sentence 1, and `MIT,` must not either.

```
sentence[0] = [0, 76)
sentence[1] = [77, 165)
sentence[2] = [166, 235)
```

### 3 · Tokenization
```
["Elena", "founded", "a", "startup", "in", "Berlin", "."]
```

### 4 · Subword tokenization *(illustrative WordPiece)*
Rare/compound words fragment; `##` marks a continuation piece. Exact splits depend on
the model's vocabulary.
```
Elena    -> ["El", "##ena"]
founded  -> ["founded"]
startup  -> ["start", "##up"]
Berlin   -> ["Berlin"]
```

### 5 · Lemmatization & stemming
```
token     lemma      stem
Elena     Elena      elena
founded   found      found     (verb "to found" = establish; NOT "find")
startup   startup    startup
Berlin    Berlin     berlin
```

### 6–7 · POS + morphological features (UD)
English is morphologically sparse; the same slots carry gender/case in the
Portuguese/German/French/Spanish versions from the earlier tables.
```
token     UPOS   XPOS   FEATS
Elena     PROPN  NNP    Number=Sing
founded   VERB   VBD    Mood=Ind|Tense=Past|VerbForm=Fin
a         DET    DT     Definite=Ind|PronType=Art
startup   NOUN   NN     Number=Sing
in        ADP    IN     _
Berlin    PROPN  NNP    Number=Sing
.         PUNCT  .      _
```

### 8 · Chunking (BIO)
Flat, non‑recursive spans. `B-` opens a chunk, `I-` continues it, `O` is outside.
```
Elena    B-NP
founded  B-VP
a        B-NP
startup  I-NP
in       B-PP
Berlin   B-NP
.        O
```

### 9 · Constituency parsing (Penn Treebank brackets)
The recursive phrase structure your chunker only approximates — here the PP genuinely
contains its NP.
```
(S
  (NP (NNP Elena))
  (VP (VBD founded)
    (NP (DT a) (NN startup))
    (PP (IN in)
      (NP (NNP Berlin))))
  (. .))
```

### 10 · Dependency parsing (CoNLL‑U)
Heads and grammatical relations. `HEAD` 0 marks the root; `founded` is the root and
everything hangs off it.
```
# text = Elena founded a startup in Berlin.
# ID  FORM     LEMMA    UPOS   XPOS  FEATS                             HEAD  DEPREL
1     Elena    Elena    PROPN  NNP   Number=Sing                       2     nsubj
2     founded  found    VERB   VBD   Mood=Ind|Tense=Past|VerbForm=Fin  0     root
3     a        a        DET    DT    Definite=Ind|PronType=Art         4     det
4     startup  startup  NOUN   NN    Number=Sing                       2     obj
5     in       in       ADP    IN    _                                 6     case
6     Berlin   Berlin   PROPN  NNP   Number=Sing                       2     obl
7     .        .        PUNCT  .     _                                 2     punct
```

### 11 · Named entity recognition (BIO, over full sentence 1)
```
In       O          founded  O
March    B-DATE     a        O
2023     I-DATE     startup  O
,        O          called   O
Dr.      O          Lumière  B-ORG
Elena    B-PER      in       O
Vasquez  I-PER      Berlin   B-GPE
                    .        O
```
Entities: `Elena Vasquez` (PER), `March 2023` (DATE), `Lumière` (ORG), `Berlin`
(GPE), plus `MIT` (ORG) in sentence 2.

This layer is machine output and therefore wrong some of the time, so it carries
a human verification layer on top: `entity_mention_correction` (migration 115).
A reviewer can `reclassify` a type, `reject` a spurious mention, `add` a missed
entity, or `adjust_span` a boundary. The correction is keyed by a stable span
identity — `(document_id, char_start, char_end)` — *not* by `entity_mention.id`,
because the mention rows are `ON DELETE CASCADE` from `annotations` and are
rebuilt from scratch by every annotation run. The pipeline re-applies the
correction log at the tail of each run
(`doc_ray.domain.ner_corrections.reconcile()` — pure, deterministic, idempotent),
so a fix made once persists across re-annotation instead of being overwritten by
it. Corrections store the original ML value and the reviewer, keeping every
override attributable and reversible.

### 12 · Entity linking
Mentions resolve to knowledge‑base IDs; invented entities correctly get `NIL`.
```
Berlin        -> wikidata:Q64        (the city)
MIT           -> wikidata:Q49108
Elena Vasquez -> NIL                 (fictional — no KB entry)
Lumière       -> NIL                 (fictional company)
```

### 13 · Coreference resolution
Two identity chains span the whole text (mentions in reading order):
```
CHAIN A (person):  Dr. Elena Vasquez → She → she → Her → her → Elena → she → Elena → she
CHAIN B (company): Lumière → The company → Lumière → its → the firm → its
```
`her team` in P2 is an **associative/bridging** reference to CHAIN B, not strict
identity — a good example of the distinction coref systems must make.

### 14 · Semantic role labeling (PropBank)
Predicate‑argument structure — independent of surface syntax.
```
found.01:   [ARG0 Elena] [PRED founded] [ARG1 a startup] [ARGM-LOC in Berlin]
trust.01:   [ARG0 investors] [PRED trusted] [ARG1 her vision]      (P2, sentence 1)
```
Reading: ARG0 = agent/founder, ARG1 = thing founded, ARGM‑LOC = location modifier.

### 15 · Word sense disambiguation (WordNet)
```
vision  (in "trusted her vision")  -> vision.n.03  "a vivid mental image / foresight"
                                       NOT vision.n.01 "the faculty of sight"
founded (in "founded a startup")   -> found.v.01   "set up, establish"
```

### 16 · Semantic parsing (AMR)
A rooted, reentrant meaning graph keyed on PropBank frames; concepts are reified and
names are structured.
```
(f / found-01
   :ARG0 (p / person
            :name (n / name :op1 "Elena"))
   :ARG1 (s / startup)
   :location (c / city
            :name (m / name :op1 "Berlin")))
```

### 17 · Discourse parsing (PDTB connectives → senses)
The explicit connectives in the text and their discourse relations:
```
but        (P1) -> Comparison.Contrast
because    (P2) -> Contingency.Cause.Reason      [company grew] ← [investors trusted]
However    (P2) -> Comparison.Contrast
Therefore  (P2) -> Contingency.Cause.Result      [hardware expensive] → [pivoted]
though     (P3) -> Comparison.Concession
```
An RST tree would nest these into a single hierarchy over the paragraphs (nucleus vs.
satellite spans); PDTB is the flatter, connective‑anchored view shown here.

### 18 · Temporal & event annotation (TimeML)
Time expressions, events, and the links that order them.
```
TIMEX3:  <March 2023 | DATE | value=2023-03>
         <Today      | DATE | value=SPEECH_TIME>
         <next year  | DATE | value=SPEECH_TIME+1Y>

EVENT:   founded, studied, grew, trusted, realized, pivoted, released,
         employs, powers, predict, double, remains, knows

TLINK:   studied  BEFORE  founded
         founded  BEFORE  grew
         employs  IS_INCLUDED  Today
         double   AFTER   Today
```

### 19 · Sentiment & pragmatics
```
"Elena remains optimistic"        -> sentiment: POSITIVE (target: Elena's outlook)
"the road ahead is difficult"     -> sentiment: NEGATIVE (target: outlook/prospects)
"Analysts predict that…"          -> speech act: ASSERTIVE / prediction
```

### 20 · Distributed representations (embeddings)
Every unit above also maps to a dense vector — the form transformers actually operate
on. Symbolic layers become *implicit* here rather than annotated.
```
"Elena"                 -> [ 0.12, -0.34, 0.88, …, 0.05 ]   (e.g. 768 dims)
"Elena founded a startup in Berlin."  -> one sentence vector
```
`Berlin` and `Munich` land near each other in this space without any explicit LOC tag;
that proximity is the "annotation," learned rather than declared.

---

## 4. How each layer maps back to your schema

The layers fall into three storage shapes, which tells you exactly how a persistence layer
would extend the `node` tree. *(Today that tree is in‑memory / JSON — nodes carry `text`
and children, and offsets are derived on demand, not stored. The SQL below is the target
relational store, e.g. the repo's Postgres `db/`.)*

- **Containment layers** (1–3, and the chunk/constituency nesting) → the existing
  `node` tree via `parent_id`. Already done.
- **Span layers** (NER, chunking, sentences, TIMEX3) → `node` rows with
  `(start_offset, end_offset)` + a `label`. Pure stand‑off annotation; no new table.
- **Graph layers** (dependency arcs, coreference chains, SRL roles, discourse
  relations, entity links) → these are **arbitrary links between nodes**, not
  containment. Add one table:

```sql
CREATE TABLE edge (
    id          INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES document(id),
    source_id   INTEGER NOT NULL REFERENCES node(id),   -- e.g. head token, mention
    target_id   INTEGER          REFERENCES node(id),   -- in-document target (NULL for external)
    target_ref  TEXT,                                   -- external target, e.g. 'wikidata:Q64' (entity links)
    layer       TEXT    NOT NULL,   -- 'dependency' | 'coref' | 'srl' | 'discourse' | 'entity'
    relation    TEXT    NOT NULL,   -- 'nsubj' | 'same-entity' | 'ARG0' | 'Cause'
    attributes  TEXT                -- JSON: confidence, sense key, etc.
);
```

With that single addition, the same store holds the whole stack: containment in
`node.parent_id`, spans in `node` offsets, and every relational layer as typed edges.
That is the difference I flagged at the schema stage — a strict tree covers structure,
but semantics, reference, and discourse are graphs.
