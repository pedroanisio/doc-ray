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

import json
import os

import psycopg
from flask import Flask, g, render_template, request
from markupsafe import Markup, escape
from psycopg import sql
from psycopg.rows import dict_row

try:
    from webapp.operator_health import build_operator_health, require_operator_health
except ModuleNotFoundError:  # pragma: no cover - direct ``python webapp/app.py`` path.
    from operator_health import build_operator_health, require_operator_health

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://docray:docray@localhost:5455/docray")

# Row-Level Security enforcement (see db/migrations/018_rbac.sql). The superuser
# connection BYPASSES RLS by design. To actually enforce document-level access
# for end users, set DOCRAY_PRINCIPAL_ID: the app then drops to the non-superuser
# role (so policies apply) and stamps the request principal. Unset => superuser
# passthrough (local/dev only — no access control).
DB_ROLE = os.environ.get("DOCRAY_DB_ROLE", "docray_client")
PRINCIPAL_ID = os.environ.get("DOCRAY_PRINCIPAL_ID") or None

app = Flask(__name__)
# Uploads are forwarded to the API, which buffers the file for hashing — cap
# the request size here so an oversized POST is rejected at the front door.
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("DOCRAY_MAX_UPLOAD_MB", "200")) * 1024 * 1024
app.add_url_rule("/help", "help_page", lambda: render_template("help.html"))


# --- per-request DB connection --------------------------------------------


def _apply_principal(conn) -> None:
    """Enforce RLS on ``conn`` by dropping role and setting the request principal.

    No-op when no principal is configured. Trust boundary: the principal comes
    from server-side config (the trusted app tier), never from user input — the
    GUC-based model is only safe under that assumption. Mapping an authenticated
    end user to their principal, and per-principal LOGIN roles to defend against
    hostile direct SQL, are the next steps (see the ``018_rbac.sql`` header).

    Args:
        conn: The open ``psycopg`` connection to configure in place.
    """
    if not PRINCIPAL_ID:
        return
    with conn.cursor() as cur:
        # DB_ROLE is an operator-set identifier (never user input) — quote it safely.
        cur.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(DB_ROLE)))
        # SET cannot bind a value, so use set_config to pass the principal safely.
        cur.execute("SELECT set_config('app.principal_id', %s, false)", (PRINCIPAL_ID,))


def get_conn():
    """Return the request-scoped Postgres connection, opening it on first use.

    On first open the connection is configured for RLS enforcement when a
    principal is set (see :func:`_apply_principal`).

    Returns:
        The ``psycopg`` connection stored on Flask's request-global ``g``.
    """
    if "conn" not in g:
        g.conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        _apply_principal(g.conn)
    return g.conn


@app.teardown_appcontext
def _close_conn(_exc):
    conn = g.pop("conn", None)
    if conn is not None:
        conn.close()


def q(sql, params=()):
    """Run a query and return all rows.

    Args:
        sql: The SQL statement to execute.
        params: Bind parameters passed through to the cursor.

    Returns:
        A list of ``dict_row`` mappings, one per result row.
    """
    with get_conn().cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def q1(sql, params=()):
    """Run a query and return the first row, or ``None`` if there are none.

    Args:
        sql: The SQL statement to execute.
        params: Bind parameters passed through to the cursor.

    Returns:
        The first ``dict_row`` mapping, or ``None`` when the query yields no rows.
    """
    with get_conn().cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


# --- GraphQL write path -----------------------------------------------------
#
# The browser stays read-only over Postgres; every WRITE goes through the
# GraphQL API so there is exactly one write path (mutations enqueue worker
# jobs — the webapp never touches the queue or the registry directly).

API_URL = os.environ.get("DOCRAY_API_URL", "http://localhost:8000/graphql")


def gql(query: str, variables: dict | None = None) -> dict:
    """POST one GraphQL operation to the API and return its ``data`` payload.

    Raises:
        RuntimeError: If the API reports GraphQL errors.
    """
    import httpx

    response = httpx.post(
        API_URL, json={"query": query, "variables": variables or {}}, timeout=30.0
    )
    response.raise_for_status()
    body = response.json()
    if body.get("errors"):
        raise RuntimeError(body["errors"][0].get("message", "GraphQL error"))
    return body["data"]


# --- annotation rendering --------------------------------------------------

_POS_SIGIL_SPEC = (
    "NOUN:N PROPN:PN VERB:V ADJ:J ADV:R PRON:PR NUM:# DET:D "
    "ADP:P CCONJ:C SCONJ:S PART:T AUX:A PUNCT:. SYM:* X:X INTJ:I"
)
_POS_SIGILS = dict(item.split(":") for item in _POS_SIGIL_SPEC.split())


def _pos_sigil(pos: str) -> str:
    """Return the compact visible POS marker used as a non-color cue."""
    return _POS_SIGILS.get(pos, (pos[:2] or "?").upper())


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
        if a < cur or a >= b or b > len(text):  # skip overlaps / out of range
            continue
        out.append(escape(text[cur:a]))
        word = text[a:b]
        pos = str(o["pos"])
        lemma = str(o["lemma"])
        out.append(
            Markup(
                '<a class="w p-{pos}" href="/lexeme/{uid}" data-t="{tip}" '
                'data-pos="{pos}" aria-label="{aria}">'
                '<span class="pos-sigil" aria-hidden="true">{cue}</span>{w}</a>'
            ).format(
                pos=pos,
                uid=o["uid"],
                tip=f"{lemma} · {pos}",
                aria=f"{word}, lemma {lemma}, part of speech {pos}",
                cue=_pos_sigil(pos),
                w=word,
            )
        )
        cur = b
    out.append(escape(text[cur:]))
    return Markup("").join(out)


# --- routes ----------------------------------------------------------------


@app.route("/")
def documents():
    """Render the corpus landing page: all documents plus corpus-wide totals."""
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


@app.route("/operator/health")
def operator_health():
    """Render read-only corpus and database health snapshots for operators."""
    require_operator_health()
    return render_template("operator_health.html", **build_operator_health(q, q1))


@app.route("/doc/<uuid:doc_id>")
def document(doc_id):
    """Render a single document with metadata, POS stats, and annotated sentences.

    Args:
        doc_id: The UUID of the document to display.

    Returns:
        The rendered document page, or a 404 tuple when the document is unknown.
    """
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
    n_sent = q1("SELECT count(*) n FROM sentences WHERE document_id = %s", (str(doc_id),))["n"]
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
            body = page_text[s["ss"] - lo : s["se"] - lo]
            inside = [
                {
                    "rel_start": o["a"] - s["ss"],
                    "rel_end": o["b"] - s["ss"],
                    "uid": o["uid"],
                    "lemma": o["lemma"],
                    "pos": o["pos"],
                }
                for o in occ
                if o["a"] >= s["ss"] and o["b"] <= s["se"]
            ]
            s["html"] = annotate_html(body, inside)

    pages = max(1, -(-n_sent // per_page))
    return render_template(
        "document.html",
        doc=doc,
        authors=[a["name"] for a in authors],
        tags=[t["tag"] for t in tags],
        pos=pos,
        top_verbs=top_verbs,
        sentences=sentences,
        page=page,
        pages=pages,
        n_sent=n_sent,
        annotate=annotate,
    )


@app.errorhandler(413)
def _upload_too_large(_exc):
    """Redirect oversized uploads back to the form with a friendly message."""
    from flask import redirect, url_for

    limit_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    return redirect(url_for("upload_form", error=f"file exceeds the {limit_mb} MB upload limit"))


@app.route("/upload")
def upload_form():
    """Render the document upload form (EPUB, PDF, TXT, MD, DOCX)."""
    return render_template("upload.html", error=request.args.get("error"))


@app.route("/upload", methods=["POST"])
def upload_submit():
    """Send the uploaded file to the GraphQL ingest mutation and track its job.

    The full pipeline (ingest -> annotate -> canonicalize) is enqueued by
    default; unticking the checkbox uploads ingest-only. The browser never
    touches the object store or the queue — the API is the one write path.
    """
    import httpx
    from flask import redirect, url_for

    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return redirect(url_for("upload_form", error="choose a file first"))
    operations = {
        "query": (
            "mutation($file: Upload!, $title: String, $pipeline: Boolean!) {"
            " ingestDocument(file: $file, title: $title, pipeline: $pipeline) { id } }"
        ),
        "variables": {
            "file": None,
            "title": request.form.get("title") or None,
            "pipeline": request.form.get("pipeline") == "on",
        },
    }
    try:
        response = httpx.post(
            API_URL,
            data={
                "operations": json.dumps(operations),
                "map": json.dumps({"0": ["variables.file"]}),
            },
            files={"0": (upload.filename, upload.stream, upload.mimetype)},
            timeout=120.0,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("errors"):
            # Our own API's message (validation text) — safe and useful to show.
            raise RuntimeError(body["errors"][0].get("message", "GraphQL error"))
        job_id = ((body.get("data") or {}).get("ingestDocument") or {}).get("id")
        if not job_id:
            raise RuntimeError("the API returned no job for this upload")
    except httpx.HTTPError:
        # Transport-level details (URLs, stack text) belong in the log, not the UI.
        app.logger.exception("upload forwarding failed")
        return redirect(url_for("upload_form", error="ingest API unreachable — is it running?"))
    except Exception as exc:
        app.logger.exception("upload rejected")
        return redirect(url_for("upload_form", error=str(exc)))
    return redirect(url_for("job_status", job_id=job_id))


@app.route("/job/<job_id>")
def job_status(job_id):
    """Render one job's status plus its document's whole pipeline cascade.

    Auto-refreshes while anything is queued or running; once the ingest step
    reports a document id, every job for that document is shown so the chained
    annotate/canonicalize steps are visible as they land.
    """
    job = q1(
        "SELECT id, kind, status, document_id, error, result, created_at, finished_at "
        "FROM jobs WHERE id = %s",
        (job_id,),
    )
    if job is None:
        return ("job not found", 404)
    doc_id = job["document_id"] or (job["result"] or {}).get("document_id")
    cascade = []
    doc = None
    if doc_id:
        cascade = q(
            "SELECT id, kind, status, error, result, created_at, finished_at "
            "FROM jobs WHERE document_id = %s ORDER BY id",
            (doc_id,),
        )
        # The originating ingest job carries document_id NULL (the id lives only
        # in its result) — keep it visible at the head of the cascade.
        if all(j["id"] != job["id"] for j in cascade):
            cascade.insert(0, job)
        doc = q1("SELECT id, title FROM documents WHERE id = %s", (doc_id,))
    active = job["status"] in ("queued", "running") or any(
        j["status"] in ("queued", "running") for j in cascade
    )
    return render_template(
        "job.html", job=job, cascade=cascade, doc=doc, doc_id=doc_id, active=active
    )


@app.route("/doc/<uuid:doc_id>/seeding")
def seeding(doc_id):
    """Render the domain-seeding curation screen for one document.

    Suggestions are read directly (like every read on this app); the QID,
    label, and description columns are the audit surface — a curator approves
    or rejects each row before anything reaches the canonical registry.
    """
    doc = q1("SELECT id, title FROM documents WHERE id = %s", (doc_id,))
    if doc is None:
        return ("document not found", 404)
    rows = q(
        """
        SELECT id, term, occurrences, source, qid, label, description, entity_type,
               status
        FROM seed_suggestion WHERE document_id = %s
        ORDER BY (qid IS NULL), (status <> 'pending'), occurrences DESC, term
        """,
        (doc_id,),
    )
    counts = {s: 0 for s in ("pending", "approved", "rejected", "seeded")}
    for r in rows:
        counts[r["status"]] += 1
    return render_template(
        "seeding.html",
        doc=doc,
        rows=rows,
        counts=counts,
        notice=request.args.get("notice"),
        error=request.args.get("error"),
    )


@app.route("/doc/<uuid:doc_id>/seeding", methods=["POST"])
def seeding_action(doc_id):
    """Dispatch a curation action to the GraphQL API and redirect back.

    Actions: ``mine`` enqueues candidate mining, ``approve``/``reject`` record
    decisions for the checked rows, ``seed`` enqueues seeding of everything
    approved. All writes go through the API — one write path.
    """
    from flask import redirect, url_for

    action = request.form.get("action", "")
    try:
        if action == "mine":
            job = gql(
                "mutation($d: ID!) { suggestSeeds(documentId: $d) { id } }",
                {"d": str(doc_id)},
            )["suggestSeeds"]
            notice = f"mining job {job['id']} queued — refresh shortly"
        elif action in ("approve", "reject"):
            ids = request.form.getlist("sid")
            key = "approveIds" if action == "approve" else "rejectIds"
            n = gql(
                f"mutation($ids: [ID!]) {{ decideSeeds({key}: $ids) }}",
                {"ids": ids},
            )["decideSeeds"]
            notice = f"{n} suggestion(s) {action}d"
        elif action == "seed":
            job = gql(
                "mutation($d: ID!) { seedApproved(documentId: $d) { id } }",
                {"d": str(doc_id)},
            )["seedApproved"]
            notice = f"seeding job {job['id']} queued — refresh shortly"
        else:
            return ("unknown action", 400)
    except Exception as exc:
        return redirect(url_for("seeding", doc_id=doc_id, error=str(exc)))
    return redirect(url_for("seeding", doc_id=doc_id, notice=notice))


@app.route("/lexemes")
def lexemes():
    """Render the paginated lexicon, filtered by lemma, part of speech, and language."""
    term = request.args.get("q", "", type=str).strip()
    pos = request.args.get("pos", "", type=str)
    lang = request.args.get("lang", "", type=str)
    per_page = 100
    page = max(1, request.args.get("page", 1, type=int))

    params = {
        "q": term,
        "qpat": f"%{term}%",
        "pos": pos,
        "lang": lang,
        "limit": per_page,
        "offset": (page - 1) * per_page,
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
        "lexemes.html",
        rows=rows,
        term=term,
        pos=pos,
        lang=lang,
        langs=langs,
        poses=poses,
        page=page,
        has_next=len(rows) == per_page,
    )


@app.route("/lexeme/<uid>")
def lexeme(uid):
    """Render one lexeme with its surface forms and keyword-in-context occurrences.

    Args:
        uid: The unique identifier of the lexeme to display.

    Returns:
        The rendered lexeme page, or a 404 tuple when the lexeme is unknown.
    """
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
    n_occ = q1("SELECT count(*) n FROM lexeme_occurrences WHERE lexeme_uid = %s", (uid,))["n"]
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
    return render_template(
        "lexeme.html", lex=lex, forms=[f["form"] for f in forms], occ=occ, n_occ=n_occ
    )


@app.route("/search")
def search():
    """Render full-text sentence search results for the ``q`` query parameter."""
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


# --- backup / restore --------------------------------------------------------
#
# The catalog is read straight from Postgres (like every other list); the
# create/restore WRITES go through the GraphQL API (the single write path), which
# enqueues worker jobs. Download streams the archive back from the object store.


@app.route("/backups")
def backups():
    """List backup archives with a create button and per-row restore/download."""
    rows = q(
        "SELECT id, created_at, status, object_key, archive_bytes, table_rows, "
        "       blob_count, doc_count, note, error FROM backups ORDER BY created_at DESC"
    )
    return render_template("backups.html", rows=rows, error=request.args.get("error"))


@app.route("/backups", methods=["POST"])
def backups_create():
    """Enqueue a full backup via the GraphQL API, then follow its job."""
    from flask import redirect, url_for

    note = (request.form.get("note") or "").strip() or None
    try:
        data = gql("mutation($n: String){ createBackup(note: $n){ id } }", {"n": note})
    except Exception as exc:
        return redirect(url_for("backups", error=str(exc)))
    return redirect(url_for("job_status", job_id=data["createBackup"]["id"]))


@app.route("/backups/<backup_id>/restore", methods=["POST"])
def backups_restore(backup_id):
    """Enqueue a restore of the whole corpus from *backup_id* (destructive)."""
    from flask import redirect, url_for

    try:
        data = gql("mutation($id: ID!){ restoreBackup(backupId: $id){ id } }", {"id": backup_id})
    except Exception as exc:
        return redirect(url_for("backups", error=str(exc)))
    return redirect(url_for("job_status", job_id=data["restoreBackup"]["id"]))


@app.route("/backups/<backup_id>/download")
def backups_download(backup_id):
    """Stream a backup archive to the browser, proxied from the API.

    The API owns object-store access (it lives inside the store's network); the
    webapp never reaches S3 directly, so a host-vs-container ``S3_ENDPOINT``
    mismatch cannot break downloads. The archive is streamed chunk-by-chunk so a
    large corpus never has to be buffered whole in the webapp. A 404 from the API
    (unknown or not-yet-available backup) propagates unchanged.
    """
    import httpx
    from flask import Response, abort, stream_with_context

    base = API_URL.rsplit("/graphql", 1)[0]
    url = f"{base}/backups/{backup_id}/download"
    client = httpx.Client(timeout=None)
    upstream = client.send(client.build_request("GET", url), stream=True)
    if upstream.status_code == 404:
        upstream.close()
        client.close()
        abort(404)
    upstream.raise_for_status()

    def _pump():
        try:
            yield from upstream.iter_bytes(chunk_size=1 << 20)
        finally:
            upstream.close()
            client.close()

    return Response(
        stream_with_context(_pump()),
        mimetype="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{backup_id}.tar.gz"'},
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5001")), debug=True)
