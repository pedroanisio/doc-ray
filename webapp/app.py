"""doc-ray browser — a small Flask frontend over the annotated corpus.

Navigates the layers built by the pipeline:
    documents → sentences → lexemes → occurrences (keyword-in-context)

Read-only. Connects to the same Postgres the ingester writes to.

    DATABASE_URL=postgresql://docray:docray@localhost:5455/docray \
        .venv/bin/flask --app webapp.app run --port 5001
or simply:
    .venv/bin/python -m webapp.app          # serves on http://localhost:5001
"""

from __future__ import annotations

import os

import psycopg
from flask import Flask, g, render_template, request
from markupsafe import Markup, escape
from psycopg.rows import dict_row

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://docray:docray@localhost:5455/docray"
)

app = Flask(__name__)


# --- per-request DB connection --------------------------------------------

def get_conn():
    if "conn" not in g:
        g.conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    return g.conn


@app.teardown_appcontext
def _close_conn(_exc):
    conn = g.pop("conn", None)
    if conn is not None:
        conn.close()


def q(sql, params=()):
    with get_conn().cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def q1(sql, params=()):
    with get_conn().cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


# --- annotation rendering --------------------------------------------------

def annotate_html(text: str, occs: list[dict]) -> Markup:
    """Overlay POS-tagged, clickable word spans onto a raw text slice.

    ``occs`` are dicts with ``rel_start``/``rel_end`` (offsets into *text*),
    ``uid``, ``lemma``, ``pos``. Text between words (spaces, punctuation) is
    emitted verbatim so the passage reads normally with its words annotated.
    """
    out: list = []
    cur = 0
    for o in sorted(occs, key=lambda x: x["rel_start"]):
        a, b = o["rel_start"], o["rel_end"]
        if a < cur or a >= b or b > len(text):     # skip overlaps / out of range
            continue
        out.append(escape(text[cur:a]))
        out.append(Markup(
            '<a class="w p-{pos}" href="/lexeme/{uid}" data-t="{tip}">{w}</a>'
        ).format(pos=o["pos"], uid=o["uid"],
                 tip=f'{o["lemma"]} · {o["pos"]}', w=text[a:b]))
        cur = b
    out.append(escape(text[cur:]))
    return Markup("").join(out)


# --- routes ----------------------------------------------------------------

@app.route("/")
def documents():
    rows = q(
        """
        SELECT d.id, d.title, d.doc_type, d.language, d.char_count, d.page_count,
               (SELECT count(*) FROM sentences s WHERE s.document_id = d.id) AS n_sentences,
               (SELECT string_agg(DISTINCT a.tool, ', ')
                  FROM annotations a WHERE a.document_id = d.id) AS tools
        FROM documents d
        ORDER BY d.char_count DESC NULLS LAST
        """
    )
    totals = q1(
        "SELECT (SELECT count(*) FROM documents) docs, "
        "(SELECT count(*) FROM sentences) sents, "
        "(SELECT count(*) FROM lexemes) lexemes, "
        "(SELECT count(*) FROM lexeme_occurrences) occ"
    )
    return render_template("documents.html", rows=rows, totals=totals)


@app.route("/doc/<uuid:doc_id>")
def document(doc_id):
    doc = q1("SELECT * FROM documents WHERE id = %s", (str(doc_id),))
    if not doc:
        return ("Document not found", 404)

    authors = q(
        "SELECT a.name FROM document_authors da JOIN authors a ON a.id = da.author_id "
        "WHERE da.document_id = %s ORDER BY da.position",
        (str(doc_id),),
    )
    tags = q(
        "SELECT t.tag FROM document_tags dt JOIN tags t ON t.id = dt.tag_id "
        "WHERE dt.document_id = %s ORDER BY t.tag",
        (str(doc_id),),
    )
    pos = q(
        """
        SELECT l.pos, count(*) n
        FROM lexeme_occurrences o
        JOIN annotations a ON a.id = o.annotation_id
        JOIN lexemes l     ON l.uid = o.lexeme_uid
        WHERE a.document_id = %s
        GROUP BY l.pos ORDER BY n DESC
        """,
        (str(doc_id),),
    )
    top_verbs = q(
        """
        SELECT l.uid, l.lemma, count(*) n
        FROM lexeme_occurrences o
        JOIN annotations a ON a.id = o.annotation_id
        JOIN lexemes l     ON l.uid = o.lexeme_uid
        WHERE a.document_id = %s AND l.pos = 'VERB'
        GROUP BY l.uid, l.lemma ORDER BY n DESC LIMIT 15
        """,
        (str(doc_id),),
    )

    per_page = 40
    page = max(1, request.args.get("page", 1, type=int))
    annotate = request.args.get("annotate", "1") != "0"
    n_sent = q1("SELECT count(*) n FROM sentences WHERE document_id = %s",
                (str(doc_id),))["n"]
    sentences = q(
        "SELECT sen.ordinal, sen.text, sp.char_start AS ss, sp.char_end AS se "
        "FROM sentences sen JOIN spans sp ON sp.id = sen.span_id "
        "WHERE sen.document_id = %s ORDER BY sen.ordinal LIMIT %s OFFSET %s",
        (str(doc_id), per_page, (page - 1) * per_page),
    )

    if annotate and sentences:
        lo = min(s["ss"] for s in sentences)
        hi = max(s["se"] for s in sentences)
        slice_row = q1(
            "SELECT substring(full_text FROM %s FOR %s) AS t "
            "FROM document_content WHERE document_id = %s",
            (lo + 1, hi - lo, str(doc_id)),
        )
        page_text = (slice_row or {}).get("t") or ""
        occ = q(
            """
            SELECT sp.char_start AS a, sp.char_end AS b, l.uid, l.lemma, l.pos
            FROM lexeme_occurrences o
            JOIN annotations an ON an.id = o.annotation_id
            JOIN spans sp       ON sp.id = o.span_id
            JOIN lexemes l      ON l.uid = o.lexeme_uid
            WHERE an.document_id = %s AND sp.char_start >= %s AND sp.char_end <= %s
            ORDER BY sp.char_start
            """,
            (str(doc_id), lo, hi),
        )
        for s in sentences:
            body = page_text[s["ss"] - lo: s["se"] - lo]
            inside = [
                {"rel_start": o["a"] - s["ss"], "rel_end": o["b"] - s["ss"],
                 "uid": o["uid"], "lemma": o["lemma"], "pos": o["pos"]}
                for o in occ if o["a"] >= s["ss"] and o["b"] <= s["se"]
            ]
            s["html"] = annotate_html(body, inside)

    pages = max(1, -(-n_sent // per_page))
    return render_template(
        "document.html", doc=doc, authors=[a["name"] for a in authors],
        tags=[t["tag"] for t in tags], pos=pos, top_verbs=top_verbs,
        sentences=sentences, page=page, pages=pages, n_sent=n_sent,
        annotate=annotate,
    )


@app.route("/lexemes")
def lexemes():
    term = request.args.get("q", "", type=str).strip()
    pos = request.args.get("pos", "", type=str)
    lang = request.args.get("lang", "", type=str)
    per_page = 100
    page = max(1, request.args.get("page", 1, type=int))

    params = {
        "q": term, "qpat": f"%{term}%", "pos": pos, "lang": lang,
        "limit": per_page, "offset": (page - 1) * per_page,
    }
    rows = q(
        """
        SELECT l.uid, l.lang, l.pos, l.lemma, count(o.*) AS uses
        FROM lexemes l
        LEFT JOIN lexeme_occurrences o ON o.lexeme_uid = l.uid
        WHERE (%(q)s = '' OR l.lemma ILIKE %(qpat)s)
          AND (%(pos)s = '' OR l.pos = %(pos)s)
          AND (%(lang)s = '' OR l.lang = %(lang)s)
        GROUP BY l.uid, l.lang, l.pos, l.lemma
        ORDER BY uses DESC, l.lemma
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    )
    langs = [r["lang"] for r in q("SELECT DISTINCT lang FROM lexemes ORDER BY lang")]
    poses = [r["pos"] for r in q("SELECT DISTINCT pos FROM lexemes ORDER BY pos")]
    return render_template(
        "lexemes.html", rows=rows, term=term, pos=pos, lang=lang,
        langs=langs, poses=poses, page=page, has_next=len(rows) == per_page,
    )


@app.route("/lexeme/<uid>")
def lexeme(uid):
    lex = q1("SELECT * FROM lexemes WHERE uid = %s", (uid,))
    if not lex:
        return ("Lexeme not found", 404)
    forms = q(
        """
        SELECT DISTINCT substring(c.full_text FROM s.char_start + 1
                                  FOR s.char_end - s.char_start) AS form
        FROM lexeme_occurrences o
        JOIN spans s            ON s.id = o.span_id
        JOIN document_content c ON c.document_id = s.document_id
        WHERE o.lexeme_uid = %s LIMIT 25
        """,
        (uid,),
    )
    n_occ = q1("SELECT count(*) n FROM lexeme_occurrences WHERE lexeme_uid = %s",
               (uid,))["n"]
    occ = q(
        """
        SELECT d.id AS doc_id, d.title, s.char_start,
               substring(c.full_text FROM greatest(s.char_start - 45, 0) + 1
                         FOR (s.char_end - s.char_start) + 90) AS kwic,
               substring(c.full_text FROM s.char_start + 1
                         FOR s.char_end - s.char_start) AS surface
        FROM lexeme_occurrences o
        JOIN annotations a      ON a.id = o.annotation_id
        JOIN spans s            ON s.id = o.span_id
        JOIN documents d        ON d.id = a.document_id
        JOIN document_content c ON c.document_id = d.id
        WHERE o.lexeme_uid = %s
        ORDER BY d.title, s.char_start LIMIT 80
        """,
        (uid,),
    )
    # Split each snippet around the surface form for highlighting.
    for o in occ:
        pre, _, post = (o["kwic"] or "").partition(o["surface"] or "\0")
        o["pre"], o["post"] = pre, post
    return render_template("lexeme.html", lex=lex, forms=[f["form"] for f in forms],
                           occ=occ, n_occ=n_occ)


@app.route("/search")
def search():
    term = request.args.get("q", "", type=str).strip()
    rows = []
    if term:
        rows = q(
            """
            SELECT d.id AS doc_id, d.title, s.ordinal, s.text
            FROM sentences s JOIN documents d ON d.id = s.document_id
            WHERE s.text ILIKE %s
            ORDER BY d.title, s.ordinal LIMIT 100
            """,
            (f"%{term}%",),
        )
        for r in rows:
            pre, _, post = r["text"].partition(term)
            r["pre"], r["hit"], r["post"] = pre, (term if _ else ""), post
    return render_template("search.html", term=term, rows=rows)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5001")), debug=True)
