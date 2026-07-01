"""Generate a capability showcase report into static/fixtures/ from the live DB.

Reads the REAL spaCy annotation layers and writes a Markdown report showing:
multi-format ingestion, the complete NLP mapping counts, and a sample of the
actual annotations (POS-tagged sentence, top entities). No fakes.

    DATABASE_URL=... python scripts/generate_showcase.py
"""

from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row

DB = os.environ.get("DATABASE_URL", "postgresql://docray:docray@localhost:5455/docray")
OUT = os.path.join(os.path.dirname(__file__), "..", "static", "fixtures", "SHOWCASE.md")


def main() -> int:
    conn = psycopg.connect(DB, row_factory=dict_row, autocommit=True)
    q = lambda sql, p=(): conn.execute(sql, p).fetchall()
    one = lambda sql, p=(): conn.execute(sql, p).fetchone()

    L: list[str] = ["# doc-ray — Capability Showcase",
                    "",
                    "_Generated from the live database. All annotations are real "
                    "spaCy output (no heuristics, no fakes)._", ""]

    # 1. multi-format ingestion
    L += ["## 1. Documents ingested (any format)", "",
          "| format | docs | sentences |", "|---|---|---|"]
    for r in q("""SELECT d.doc_type, count(DISTINCT d.id) docs,
                         sum((SELECT count(*) FROM sentences s WHERE s.document_id=d.id)) sents
                  FROM documents d WHERE d.status='extracted'
                  GROUP BY d.doc_type ORDER BY docs DESC"""):
        L.append(f"| {r['doc_type']} | {r['docs']} | {r['sents'] or 0} |")

    # 2. complete NLP mapping (real layers)
    counts = {
        "tokens (layer 3/5/6)": one("SELECT count(*) n FROM tokens")["n"],
        "morphology FEATS (7)": one("SELECT count(*) n FROM token_feature")["n"],
        "noun chunks (8)": one("SELECT count(*) n FROM chunk")["n"],
        "dependencies (10)": one("SELECT count(*) n FROM dependency")["n"],
        "named entities (11)": one("SELECT count(*) n FROM entity_mention")["n"],
        "coref mentions (13)": one("SELECT count(*) n FROM coref_mention")["n"],
        "lexeme POS occurrences (webapp)": one("SELECT count(*) n FROM lexeme_occurrences")["n"],
    }
    L += ["", "## 2. Complete NLP mapping (real spaCy)", "", "| layer | rows |", "|---|---|"]
    for k, v in counts.items():
        L.append(f"| {k} | {v:,} |")

    L += ["", "## 3. Annotation tools (all real)", "", "| layer | tool | docs |", "|---|---|---|"]
    for r in q("SELECT layer, tool, count(*) n FROM annotations GROUP BY 1,2 ORDER BY 1,2"):
        L.append(f"| {r['layer']} | {r['tool']} | {r['n']} |")

    # 4. corpus POS distribution
    L += ["", "## 4. Part-of-speech distribution (corpus)", "", "| UPOS | count |", "|---|---|"]
    for r in q("""SELECT l.pos, count(*) n FROM lexeme_occurrences o
                  JOIN lexemes l ON l.uid=o.lexeme_uid
                  GROUP BY l.pos ORDER BY n DESC LIMIT 12"""):
        L.append(f"| {r['pos']} | {r['n']:,} |")

    # 5. sample annotated sentence (real POS on real text)
    sample_doc = one("SELECT d.id, d.original_filename FROM documents d "
                     "JOIN annotations a ON a.document_id=d.id AND a.layer='lexeme' "
                     "WHERE d.original_filename ILIKE %s LIMIT 1", ("%orwell%",)) or \
                 one("SELECT d.id, d.original_filename FROM documents d "
                     "JOIN annotations a ON a.document_id=d.id AND a.layer='lexeme' LIMIT 1")
    if sample_doc:
        rows = q("""SELECT substring(c.full_text FROM sp.char_start+1 FOR sp.char_end-sp.char_start) form,
                           l.lemma, l.pos
                    FROM lexeme_occurrences o
                    JOIN annotations a ON a.id=o.annotation_id
                    JOIN spans sp ON sp.id=o.span_id
                    JOIN lexemes l ON l.uid=o.lexeme_uid
                    JOIN document_content c ON c.document_id=a.document_id
                    WHERE a.document_id=%s AND a.layer='lexeme'
                    ORDER BY sp.char_start LIMIT 14""", (sample_doc["id"],))
        L += ["", f"## 5. Sample tokens — {sample_doc['original_filename']}", "",
              "| form | lemma | UPOS |", "|---|---|---|"]
        for r in rows:
            L.append(f"| {r['form']} | {r['lemma']} | {r['pos']} |")

    # 6. top named entities
    L += ["", "## 6. Top named entities (real NER)", "", "| type | mention | count |", "|---|---|---|"]
    for r in q("""SELECT em.entity_type,
                         substring(c.full_text FROM sp.char_start+1 FOR sp.char_end-sp.char_start) m,
                         count(*) n
                  FROM entity_mention em JOIN spans sp ON sp.id=em.span_id
                  JOIN annotations a ON a.id=em.annotation_id
                  JOIN document_content c ON c.document_id=a.document_id
                  WHERE em.entity_type IN ('PERSON','PER','ORG','GPE','LOC')
                    AND length(substring(c.full_text FROM sp.char_start+1 FOR sp.char_end-sp.char_start)) BETWEEN 4 AND 24
                  GROUP BY 1,2 ORDER BY n DESC LIMIT 10"""):
        L.append(f"| {r['entity_type']} | {r['m']} | {r['n']} |")

    text = "\n".join(L) + "\n"
    with open(os.path.abspath(OUT), "w", encoding="utf-8") as f:
        f.write(text)
    print(f"Wrote {os.path.abspath(OUT)} ({len(text):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
