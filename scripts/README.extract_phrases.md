# extract_phrases.py

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

Extract **all sentences** from a document — one per line.

Reads many document formats, rejoins lines that were wrapped mid-sentence (as
PDFs and column layouts do), and splits the result into sentences. Standard
library only for text/Markdown/EPUB/HTML; PDF and DOCX need one small package
each.

```bash
python extract_phrases.py report.pdf
python extract_phrases.py notes.txt -o sentences.txt
python extract_phrases.py book.epub --unique
```

---

## Supported formats

| Extension | Dependency |
|---|---|
| `.txt` `.md` `.text` | — |
| `.epub` | — (stdlib) |
| `.html` `.htm` | — (stdlib) |
| `.pdf` | `pip install pypdf` |
| `.docx` | `pip install python-docx` |

An unsupported extension exits with a message listing the supported types.

---

## How it works

1. **Read** the document's raw text (reader chosen by file extension). EPUBs are
   read in spine/reading order; HTML has tags, `<script>`, and `<style>` stripped.
2. **Reflow** — rejoin lines wrapped mid-sentence and de-hyphenate words broken
   across a line break, while keeping blank-line paragraph breaks. This is what
   turns shredded PDF layout back into whole sentences.
3. **Split** into sentences on `.`, `!`, `?` — guarding abbreviations (`Dr.`,
   `Fig.`, `e.g.`), single initials (`J. R.`), and decimals (`1.5`) so they
   don't split. Semicolons and colons do **not** end a sentence.

No language models, no configuration — it works on any text.

---

## Options

| Option | Default | Description |
|---|---|---|
| `file` | — | Document to read. |
| `--min-words N` | `1` (keep all) | Skip sentences shorter than N words (drops page numbers / stray fragments). |
| `--unique` | off | Drop duplicate sentences, keeping first-seen order. |
| `--paragraphs` | off | Group sentences by paragraph: one sentence per line, a blank line between paragraphs. |
| `-o, --output FILE` | stdout | Write sentences to FILE instead of stdout. |
| `-q, --quiet` | off | Suppress the one-line summary printed to stderr. |

Sentences go to **stdout** (or the `-o` file); a one-line count goes to
**stderr**, so it never mixes into the output:

```bash
python extract_phrases.py report.pdf > sentences.txt   # sentences only in the file
```

---

## Programmatic use

```python
import extract_phrases as ep

text = ep.extract_text(Path("report.pdf"))  # raw text from any supported format
sentences = ep.extract_sentences(text)  # flat list of sentences
paragraphs = ep.extract_paragraphs(text)  # list[list[str]] — sentences grouped by paragraph
# or go straight through the reflow / split steps:
ep.reflow(raw)  # rejoin wrapped lines (blank lines mark paragraphs)
ep.split_sentences(reflowed)  # split already-reflowed text
```

---

## Lossless decomposition — `docmodel.py`

`extract_phrases.py` *cleans* text (reflow, de-hyphenate) — great for reading,
but lossy. When you need the full structure **and** the ability to rebuild the
exact original, use the companion module `docmodel.py`. It decomposes text into

```
document → section → heading | paragraph → sentence → phrase → word
```

and guarantees `compose(decompose(t)) == t` for any text `t`. Every character —
words, punctuation, and whitespace — is kept as a leaf node, so rebuilding is a
depth-first join. (Lossless is with respect to the *extracted text*: the whole
file for `.txt`, or whatever the reader extracts for `.pdf`/`.docx`/`.epub`.)

```bash
python docmodel.py report.pdf --outline     # indented structure + node counts
python docmodel.py report.pdf --json > tree.json
python docmodel.py --compose tree.json      # reconstruct the original, byte-for-byte
```

Every decompose run verifies the round-trip and exits non-zero if it ever fails.

```python
import docmodel as dm

tree = dm.decompose(text)
assert dm.compose(tree) == text  # lossless
list(tree.iter_kind("sentence"))  # every sentence node
list(tree.iter_kind("word"))  # every word leaf
dm.Node.from_dict(tree.to_dict())  # JSON-safe, still lossless
```

---

## Lexeme identity — `lexeme.py`

Layers stable word identities over the lossless tree. A *lexeme* is a canonical
`(lang, pos, lemma)` — e.g. `en:VERB:run` — and every inflected form (`ran`,
`runs`, `running`) maps to the same short id like `lx_en.v_3f9a21c0`.

- **Identity, not a hash:** a `Registry` maps key ⇄ uid bidirectionally; the uid
  is a derived alias, made collision-safe by growing the hash when two different
  keys clash. It's namespaced by language and POS, so `run`/verb, `run`/noun, and
  a same-hash foreign lemma never collide.
- **Occurrences are spans, not minted ids:** each appearance is `(doc, start,
  end)` into the lossless tree — deterministic and reproducible.
- **Pluggable analysis:** the identity/registry core is pure stdlib; lemma+POS
  come from an analyzer. `SpacyAnalyzer` feeds spaCy our *exact* tokens
  (pre-tokenized, so the lossless tree stays authoritative) for accurate,
  context-aware tagging; `RuleAnalyzer` is a zero-dependency fallback (light
  normalization only — it can't identify verbs).

```bash
python lexeme.py report.pdf --verbs                       # verb occurrences (needs spaCy)
python lexeme.py report.pdf --registry lexemes.json       # write the lexeme table
python lexeme.py notes.txt --backend rule                 # no-dependency fallback
```

```python
import lexeme as lx, docmodel as dm

tree = dm.decompose(text)
registry, occurrences = lx.annotate(tree, lx.SpacyAnalyzer("en"), doc_id="af447")
verbs = [o for o in occurrences if o.pos == "VERB"]  # o.surface, o.lemma, o.uid, o.start, o.end
```

> Analysis is language-specific: run the model matching the text (`--lang pt` for
> Portuguese). Tagging text with the wrong-language model mislabels words.

**Multi-language documents.** Add `--detect` to identify each segment's language and route
it to the matching model, instead of forcing one language over the whole file:

```bash
python lexeme.py bilingual.pdf --detect      # tags each segment en / pt / fr
```

Detection is a lightweight stopword classifier (`en`/`pt`/`fr`); ambiguous or very short
segments fall back to the document's dominant language. Each occurrence carries its
detected `lang`, and the stats show a `By language` breakdown. Each language needs its
spaCy model (`en_core_web_sm`, `pt_core_news_sm`, `fr_core_news_sm`); a detected language
with no model is still tagged correctly but only normalized (no POS).

> **Limitation:** routing is per **segment** (sentence). Intra-sentence code-switching — a
> single sentence that mixes languages, e.g. a French masthead line that also contains
> English product names — can't be split, so those tokens take the sentence's majority
> language.

---

## Testing

```bash
python -m pytest tests/ -q
```

The suite is hermetic (builds EPUBs in-memory, stubs missing PDF/DOCX
dependencies) and covers reflow, sentence boundaries (abbreviations, initials,
decimals), every reader, and the CLI.

---

## Notes & limits

- PDFs sometimes embed page headers/footers into the text stream; those can land
  inside a neighbouring sentence. Since the goal is to keep **all** sentences,
  nothing is dropped by default — use `--min-words` to trim obvious fragments.
- Sentence splitting is rule-based (no ML), tuned for prose. Text with no
  sentence punctuation (e.g. a bare list) comes through as one line per
  paragraph/block.
- `docmodel` heading detection is heuristic (extracted text has lost its
  markup): a heading is a short line that starts uppercase, doesn't end with
  sentence/clause punctuation, and isn't a bullet or enumerated list item (`#`
  and dotted-decimal `1.6.9` count). Front-matter lines shaped like headings
  (e.g. a person's name) can't be told apart from text alone. Purely cosmetic —
  losslessness is unaffected.
