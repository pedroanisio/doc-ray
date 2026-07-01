# doc-ray — Capability Showcase

_Generated from the live database. All annotations are real spaCy output (no heuristics, no fakes)._

## 1. Documents ingested (any format)

| format | docs | sentences |
|---|---|---|
| pdf | 23 | 19749 |
| epub | 2 | 9584 |

## 2. Complete NLP mapping (real spaCy)

| layer | rows |
|---|---|
| tokens (layer 3/5/6) | 916,565 |
| morphology FEATS (7) | 1,385,076 |
| noun chunks (8) | 225,050 |
| dependencies (10) | 916,565 |
| named entities (11) | 44,476 |
| coref mentions (13) | 17,953 |
| lexeme POS occurrences (webapp) | 701,641 |

## 3. Annotation tools (all real)

| layer | tool | docs |
|---|---|---|
| lexeme | spacy/en_core_web_sm | 20 |
| lexeme | spacy/pt_core_news_sm | 4 |
| spacy | spacy/en_core_web_sm@3.8.14 | 20 |
| spacy | spacy/pt_core_news_sm@3.8.14 | 4 |

## 4. Part-of-speech distribution (corpus)

| UPOS | count |
|---|---|
| NOUN | 166,133 |
| ADP | 112,071 |
| DET | 74,839 |
| VERB | 74,673 |
| ADJ | 55,901 |
| PROPN | 50,283 |
| PRON | 45,357 |
| AUX | 33,277 |
| ADV | 30,449 |
| CCONJ | 27,716 |
| SCONJ | 15,334 |
| PART | 7,721 |

## 5. Sample tokens — George-Orwell_Nineteen-Eighty-Four.epub

| form | lemma | UPOS |
|---|---|---|
| Nineteen | nineteen | NUM |
| Eighty | eighty | NUM |
| Four | four | PROPN |
| George | george | PROPN |
| Orwell | orwell | PROPN |
| Text | text | PROPN |
| based | base | VERB |
| on | on | ADP |
| Project | project | PROPN |
| Gutenberg | gutenberg | PROPN |
| Cover | cover | PROPN |
| by | by | ADP |
| Jon | jon | PROPN |
| Vallance | vallance | PROPN |

## 6. Top named entities (real NER)

| type | mention | count |
|---|---|---|
| GPE | Winston | 1008 |
| ORG | Constituição Federal | 613 |
| LOC | União | 558 |
| LOC | Estados | 539 |
| LOC | Distrito Federal | 531 |
| ORG | Party | 469 |
| LOC | Municípios | 412 |
| PER | Emenda Constitucional | 377 |
| LOC | Estado | 247 |
| ORG | Constituição | 237 |
