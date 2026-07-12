"""doc-ray browser — a small Flask frontend over the annotated corpus.

Navigates the layers built by the pipeline:
    documents → sentences → lexemes → occurrences (keyword-in-context)

Read-only. Connects to the same Postgres the ingester writes to. Load the
deployment settings first so DATABASE_URL carries the real host port/password:

    set -a; source .env; set +a
    .venv/bin/flask --app webapp.app run --port 5001
or simply:
    .venv/bin/python -m webapp.app          # serves on http://localhost:5001
"""

from __future__ import annotations

import json
import os
import secrets
from html import escape as html_escape

import psycopg
from flask import (
    Flask,
    abort,
    g,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from psycopg import sql
from psycopg.rows import dict_row

from doc_ray.config import DEFAULT_DATABASE_URL
from doc_ray.entrypoints.api.context import MAX_BATCH_IDS

try:
    from webapp.auth_session import (
        AuthenticatedWebPrincipal,
        auth_sessions,
        oidc_authorization_redirect,
        oidc_claims_from_code,
        saml_assertion_from_response,
        saml_login_redirect,
        session_cookie_secure,
    )
    from webapp.csrf import csrf_token, require_csrf_token
    from webapp.document_metadata import submit_metadata_update
    from webapp.graphql_client import API_URL, api_auth_headers, gql
    from webapp.health_contract import operator_health_payload
    from webapp.job_view import decorate_job_rows, is_active_job, summarize_progress
    from webapp.operator_health import build_operator_health, require_operator_health
    from webapp.rate_limit import FixedWindowRateLimiter, rate_limit_rule_from_env
    from webapp.reader_blocks import attach_page_blocks, latest_annotation_state
    from webapp.security_headers import apply_security_headers
except ModuleNotFoundError:  # pragma: no cover - direct ``python webapp/app.py`` path.
    from auth_session import (
        AuthenticatedWebPrincipal,
        auth_sessions,
        oidc_authorization_redirect,
        oidc_claims_from_code,
        saml_assertion_from_response,
        saml_login_redirect,
        session_cookie_secure,
    )
    from csrf import csrf_token, require_csrf_token
    from document_metadata import submit_metadata_update
    from graphql_client import API_URL, api_auth_headers, gql
    from health_contract import operator_health_payload
    from job_view import decorate_job_rows, is_active_job, summarize_progress
    from operator_health import build_operator_health, require_operator_health
    from rate_limit import FixedWindowRateLimiter, rate_limit_rule_from_env
    from reader_blocks import attach_page_blocks, latest_annotation_state
    from security_headers import apply_security_headers

DATABASE_URL = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)

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
# Signs the session cookie that carries the CSRF token below; stable for the life
# of one process even when unset (dev-only — set explicitly for a real deployment).
app.secret_key = os.environ.get("DOCRAY_WEBAPP_SECRET_KEY") or secrets.token_hex(32)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = session_cookie_secure()
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.jinja_env.globals["csrf_token"] = csrf_token

_RATE_LIMITER = FixedWindowRateLimiter()


def reset_rate_limits() -> None:
    """Clear webapp rate-limit counters; used by tests and local diagnostics."""
    _RATE_LIMITER.reset()


def _client_identifier() -> str:
    return request.access_route[0] if request.access_route else (request.remote_addr or "unknown")


def _rate_limit_response(retry_after_seconds: int):
    response = make_response("Too many requests. Please retry later.\n", 429)
    response.headers["Retry-After"] = str(retry_after_seconds)
    return response


def enforce_webapp_rate_limits():
    """Throttle expensive unauthenticated webapp routes per client."""
    route = None
    rule = None
    if request.endpoint == "search":
        route = "search"
        rule = rate_limit_rule_from_env(
            os.environ,
            "DOCRAY_WEBAPP_SEARCH_RATE_LIMIT",
            default_requests=60,
            default_window_seconds=60,
        )
    elif request.endpoint == "upload_submit":
        route = "upload"
        rule = rate_limit_rule_from_env(
            os.environ,
            "DOCRAY_WEBAPP_UPLOAD_RATE_LIMIT",
            default_requests=10,
            default_window_seconds=60,
        )
    if route is None or rule is None:
        return None
    result = _RATE_LIMITER.check(route, _client_identifier(), rule)
    if not result.allowed:
        return _rate_limit_response(result.retry_after_seconds)
    return None


app.before_request(enforce_webapp_rate_limits)
app.before_request(require_csrf_token)
app.after_request(apply_security_headers)


# --- web auth session ------------------------------------------------------


def _auth_state() -> str:
    """Mint and store a login CSRF state token."""
    state = secrets.token_urlsafe(32)
    session["_auth_state"] = state
    return state


def _validate_auth_state(sent: str | None) -> None:
    expected = session.get("_auth_state")
    if not sent or not expected or sent != expected:
        abort(400, "invalid login state")
    session.pop("_auth_state", None)


def _resolve_web_principal(external_subject: str) -> str:
    """Resolve an external login subject to a doc-ray principal id."""
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM principals WHERE external_subject = %s LIMIT 1",
            (external_subject,),
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError("external subject is not mapped to a principal")
    return str(row["id"])


def complete_oidc_login(code: str, redirect_uri: str) -> AuthenticatedWebPrincipal:
    """Exchange an OIDC authorization code and resolve the authenticated principal."""
    claims = oidc_claims_from_code(code=code, redirect_uri=redirect_uri)
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise ValueError("OIDC sub is required")
    return AuthenticatedWebPrincipal(
        principal_id=_resolve_web_principal(subject),
        subject=f"oidc:{subject}",
    )


def complete_saml_login(saml_response: str) -> AuthenticatedWebPrincipal:
    """Validate a SAML response and resolve the authenticated principal."""
    assertion = saml_assertion_from_response(saml_response)
    return AuthenticatedWebPrincipal(
        principal_id=_resolve_web_principal(assertion.external_subject),
        subject=assertion.external_subject,
    )


def _establish_auth_session(principal: AuthenticatedWebPrincipal) -> None:
    sid = auth_sessions.create(principal_id=principal.principal_id, subject=principal.subject)
    session["_auth_sid"] = sid


def _session_principal_id() -> tuple[str | None, bool]:
    sid = session.get("_auth_sid")
    if not sid:
        return None, False
    record = auth_sessions.get(sid)
    if record is None:
        session.pop("_auth_sid", None)
        return None, True
    return record.principal_id, True


def _request_principal_id() -> str | None:
    principal_id, had_auth_session = _session_principal_id()
    if principal_id:
        return principal_id
    if had_auth_session:
        return None
    return PRINCIPAL_ID


@app.get("/login")
def login():
    """Redirect the browser to the configured OIDC or SAML IdP."""
    state = _auth_state()
    oidc_url = oidc_authorization_redirect(
        redirect_uri=url_for("oidc_callback", _external=True),
        state=state,
    )
    if oidc_url:
        return redirect(oidc_url)
    saml_url = saml_login_redirect(state=state)
    if saml_url:
        return redirect(saml_url)
    return "webapp login is not configured", 404


@app.get("/auth/oidc/callback")
def oidc_callback():
    """Complete OIDC login and establish the server-side web session."""
    _validate_auth_state(request.args.get("state"))
    code = request.args.get("code")
    if not code:
        abort(400, "missing OIDC code")
    try:
        principal = complete_oidc_login(code, url_for("oidc_callback", _external=True))
    except ValueError as exc:
        abort(401, str(exc))
    _establish_auth_session(principal)
    return redirect(url_for("documents"))


@app.post("/auth/saml/callback")
def saml_callback():
    """Complete SAML login and establish the server-side web session."""
    _validate_auth_state(request.form.get("RelayState"))
    response = request.form.get("SAMLResponse", "")
    if not response:
        abort(400, "missing SAMLResponse")
    try:
        principal = complete_saml_login(response)
    except ValueError as exc:
        abort(401, str(exc))
    _establish_auth_session(principal)
    return redirect(url_for("documents"))


@app.post("/logout")
def logout():
    """Revoke the server-side auth session and clear the Flask session cookie."""
    auth_sessions.revoke(session.get("_auth_sid"))
    session.clear()
    return redirect(url_for("documents"))


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
    principal_id = _request_principal_id()
    if not principal_id:
        return
    with conn.cursor() as cur:
        # DB_ROLE is an operator-set identifier (never user input) — quote it safely.
        cur.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(DB_ROLE)))
        # SET cannot bind a value, so use set_config to pass the principal safely.
        cur.execute("SELECT set_config('app.principal_id', %s, false)", (principal_id,))


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


def exec_sql(sql, params=()):
    """Run a statement that does not return rows."""
    with get_conn().cursor() as cur:
        cur.execute(sql, params)


_REPROCESS_CONFIG = {
    "reextract": {
        "label": "Re-extract",
        "description": "Rebuild text and page geometry from the original PDF.",
        "mutation": "mutation($id: ID!) { reextractDocument(documentId: $id) { id } }",
        "response_key": "reextractDocument",
        "active_kinds": {"reextract"},
        "pdf_only": True,
    },
    "reannotate": {
        "label": "Re-annotate",
        "description": "Run the annotation pipeline over the document text again.",
        "mutation": "mutation($id: ID!) { reannotate(documentId: $id) { id } }",
        "response_key": "reannotate",
        "active_kinds": {"annotate", "reannotate"},
        "pdf_only": False,
    },
    "semantic_enrich": {
        "label": "Re-enrich",
        "description": "Recompute deterministic semantic enrichment.",
        "mutation": "mutation($id: ID!) { semanticEnrichDocument(documentId: $id) { id } }",
        "response_key": "semanticEnrichDocument",
        "active_kinds": {"semantic_enrich"},
        "pdf_only": False,
    },
    "canonicalize": {
        "label": "Re-canonicalize",
        "description": "Re-run deterministic entity canonicalization.",
        "mutation": "mutation($id: ID!) { canonicalizeDocument(documentId: $id) { id } }",
        "response_key": "canonicalizeDocument",
        "active_kinds": {"canonicalize"},
        "pdf_only": False,
    },
}


def _active_reprocess_jobs(doc_id: str) -> dict[str, dict]:
    """Return active pipeline jobs keyed by reprocess step.

    A running job whose heartbeat went stale (worker killed mid-run) is
    excluded — it must not block re-running the step it will never finish.
    """
    rows = q(
        """
        SELECT id, kind, status, progress_current, progress_total, progress_stage,
               started_at, heartbeat_at
        FROM jobs
        WHERE document_id = %s AND status IN ('queued', 'running')
        ORDER BY id DESC
        """,
        (doc_id,),
    )
    active: dict[str, dict] = {}
    for row in rows:
        if not is_active_job(row):
            continue
        for step, cfg in _REPROCESS_CONFIG.items():
            if row.get("kind") in cfg["active_kinds"] and step not in active:
                active[step] = row
    return active


def _reprocess_steps(doc: dict, doc_id: str) -> list[dict]:
    """Build document-page reprocess controls from live job state."""
    active_jobs = _active_reprocess_jobs(doc_id)
    is_pdf = str(doc.get("doc_type") or "").lower() == "pdf"
    steps = []
    for step, cfg in _REPROCESS_CONFIG.items():
        available = is_pdf or not cfg["pdf_only"]
        active = active_jobs.get(step)
        steps.append(
            {
                "step": step,
                "label": cfg["label"],
                "description": cfg["description"],
                "available": available,
                "active": active,
                "active_status": active.get("status") if active else None,
                "progress": summarize_progress(active) if active else None,
            }
        )
    return steps


def _selected_document_types(ids: list[str]) -> dict[str, str]:
    """Return selected document types keyed by id."""
    rows = q(
        "SELECT id, doc_type FROM documents WHERE id = ANY(%s::uuid[])",
        (ids,),
    )
    return {str(row["id"]): str(row.get("doc_type") or "") for row in rows}


def _enqueue_each(ids: list[str], mutation: str, response_key: str) -> int:
    """Run one per-document GraphQL mutation for each selected id."""
    queued = 0
    for doc_id in ids:
        data = gql(mutation, {"id": doc_id})
        if data.get(response_key):
            queued += 1
    return queued


def _document_preview(doc: dict) -> dict[str, str | None]:
    """Return the webapp preview model for a document row."""
    doc_type = str(doc.get("doc_type") or "file").lower()
    status = str(doc.get("status") or "").lower()
    title = str(doc.get("title") or "document")
    icon = doc_type[:4].upper() if doc_type else "FILE"

    if status in {"failed", "error"}:
        return {
            "kind": "icon",
            "state": "failed",
            "icon": icon,
            "label": f"{title} preview failed",
            "status_label": "Preview failed",
            "src": None,
            "alt": None,
        }
    if status != "extracted":
        return {
            "kind": "icon",
            "state": "pending",
            "icon": icon,
            "label": f"{title} preview pending",
            "status_label": "Preview pending",
            "src": None,
            "alt": None,
        }
    if doc_type == "pdf":
        return {
            "kind": "image",
            "state": "ready",
            "icon": icon,
            "label": f"First-page preview for {title}",
            "status_label": "First-page preview",
            "src": url_for("document_page_image", doc_id=doc["id"], page_number=1),
            "alt": f"First page preview of {title}",
        }
    if doc_type in {"png", "jpg", "jpeg", "gif", "webp", "tif", "tiff", "image"}:
        return {
            "kind": "image",
            "state": "ready",
            "icon": icon,
            "label": f"Image preview for {title}",
            "status_label": "Image preview",
            "src": url_for("document_download", doc_id=doc["id"]),
            "alt": f"Preview of {title}",
        }
    return {
        "kind": "icon",
        "state": "unsupported",
        "icon": icon,
        "label": f"{doc_type.upper()} file preview unavailable",
        "status_label": "Preview unavailable",
        "src": None,
        "alt": None,
    }


def _collection_rule_from_form(form) -> dict[str, object]:
    """Normalize smart-collection form fields into a compact JSON rule."""
    rule: dict[str, object] = {}
    doc_type = (form.get("doc_type") or "").strip().lower()
    status = (form.get("status") or "").strip().lower()
    tags = [tag.strip() for tag in (form.get("tags") or "").split(",") if tag.strip()]
    if doc_type:
        rule["doc_type"] = doc_type
    if status:
        rule["status"] = status
    if tags:
        rule["tags"] = tags
    return rule


def _collection_rule(rule) -> dict[str, object]:
    """Return a collection rule as a dict regardless of DB driver representation."""
    if isinstance(rule, dict):
        return rule
    if isinstance(rule, str) and rule.strip():
        return json.loads(rule)
    return {}


def _collection_rule_summary(rule) -> str:
    """Return a concise human label for a smart collection rule."""
    normalized = _collection_rule(rule)
    parts = []
    if normalized.get("doc_type"):
        parts.append(f"type={normalized['doc_type']}")
    if normalized.get("status"):
        parts.append(f"status={normalized['status']}")
    if normalized.get("tags"):
        parts.append("tags=" + ", ".join(normalized["tags"]))
    return "; ".join(parts) if parts else "all documents"


def _collection_document_rows(collection: dict) -> list[dict]:
    """Return the documents that belong to a manual or smart collection."""
    if collection["kind"] == "manual":
        rows = q(
            """
            SELECT d.id, d.title, d.doc_type, d.language, d.char_count, d.page_count,
                   d.status,
                   (SELECT count(*) FROM sentences s WHERE s.document_id = d.id) AS n_sentences,
                   (SELECT string_agg(DISTINCT a.tool, ', ')
                      FROM annotations a WHERE a.document_id = d.id) AS tools
            FROM document_collection_members m
            JOIN documents d ON d.id = m.document_id
            WHERE m.collection_id = %s
            ORDER BY m.added_at DESC, d.title
            """,
            (str(collection["id"]),),
        )
    else:
        rule = _collection_rule(collection.get("rule"))
        rows = q(
            """
            SELECT d.id, d.title, d.doc_type, d.language, d.char_count, d.page_count,
                   d.status,
                   (SELECT count(*) FROM sentences s WHERE s.document_id = d.id) AS n_sentences,
                   (SELECT string_agg(DISTINCT a.tool, ', ')
                      FROM annotations a WHERE a.document_id = d.id) AS tools
            FROM documents d
            WHERE (%(doc_type)s::text IS NULL OR d.doc_type = %(doc_type)s)
              AND (%(status)s::text IS NULL OR d.status = %(status)s)
              AND (%(tags)s::text[] IS NULL OR EXISTS (
                    SELECT 1 FROM document_tags dt JOIN tags t ON t.id = dt.tag_id
                    WHERE dt.document_id = d.id AND t.tag = ANY(%(tags)s::text[])
                  ))
            ORDER BY d.char_count DESC NULLS LAST, d.title
            LIMIT 200
            """,
            {
                "doc_type": rule.get("doc_type"),
                "status": rule.get("status"),
                "tags": rule.get("tags"),
            },
        )
    return [dict(row, preview=_document_preview(row)) for row in rows]


def _available_manual_collections(doc_id) -> list[dict]:
    """Return manual collections that do not already contain *doc_id*."""
    return q(
        """
        SELECT c.id, c.name
        FROM document_collections c
        WHERE c.kind = 'manual'
          AND NOT EXISTS (
                SELECT 1 FROM document_collection_members m
                WHERE m.collection_id = c.id AND m.document_id = %s
              )
        ORDER BY c.name
        """,
        (str(doc_id),),
    )


def _overlay_box_style(box: dict, page_width: float, page_height: float) -> str:
    """Return percentage positioning for a source-space bbox."""
    left = 100.0 * float(box["bbox_x0"]) / page_width
    top = 100.0 * float(box["bbox_y0"]) / page_height
    width = 100.0 * (float(box["bbox_x1"]) - float(box["bbox_x0"])) / page_width
    height = 100.0 * (float(box["bbox_y1"]) - float(box["bbox_y0"])) / page_height
    return f"left:{left:.3f}%;top:{top:.3f}%;width:{width:.3f}%;height:{height:.3f}%"


def _pos_overlay_boxes(doc_id, page: int, page_width: float, page_height: float) -> list[dict]:
    """Build POS/lexeme overlay boxes for one rendered PDF page."""
    boxes = q(
        """
        SELECT l.uid, l.lemma, l.pos,
               sp.bbox_x0, sp.bbox_y0, sp.bbox_x1, sp.bbox_y1
        FROM lexeme_occurrences o
        JOIN annotations an ON an.id = o.annotation_id
        JOIN spans sp       ON sp.id = o.span_id
        JOIN lexemes l      ON l.uid = o.lexeme_uid
        WHERE an.document_id = %s
          AND sp.page_number = %s
          AND sp.bbox_x0 IS NOT NULL
          AND sp.bbox_y0 IS NOT NULL
          AND sp.bbox_x1 IS NOT NULL
          AND sp.bbox_y1 IS NOT NULL
        ORDER BY sp.char_start
        """,
        (str(doc_id), page),
    )
    return [
        {
            **box,
            "href": url_for("lexeme", uid=box["uid"]),
            "class_name": f"p-{box['pos']}",
            "style": _overlay_box_style(box, page_width, page_height),
            "tip": f"{box['lemma']} · {box['pos']}",
            "aria_label": f"{box['lemma']}, part of speech {box['pos']}",
        }
        for box in boxes
    ]


def _entity_overlay_boxes(doc_id, page: int, page_width: float, page_height: float) -> list[dict]:
    """Build entity/canonical overlay boxes for one rendered PDF page."""
    boxes = q(
        """
        SELECT em.id AS mention_id, em.surface, em.entity_type,
               el.scheme, el.kb_id, c.preferred,
               sp.bbox_x0, sp.bbox_y0, sp.bbox_x1, sp.bbox_y1
        FROM entity_mention em
        JOIN annotations an ON an.id = em.annotation_id
        JOIN spans sp       ON sp.id = em.span_id
        LEFT JOIN entity_link el ON el.mention_id = em.id
        LEFT JOIN canonical c ON c.id::text = el.kb_id
        WHERE an.document_id = %s
          AND sp.page_number = %s
          AND sp.bbox_x0 IS NOT NULL
          AND sp.bbox_y0 IS NOT NULL
          AND sp.bbox_x1 IS NOT NULL
          AND sp.bbox_y1 IS NOT NULL
        ORDER BY sp.char_start
        """,
        (str(doc_id), page),
    )
    rendered = []
    for box in boxes:
        name = box.get("preferred") or box.get("surface") or "entity"
        entity_type = box.get("entity_type") or "ENTITY"
        canonical = f" · {box['scheme']}:{box['kb_id']}" if box.get("kb_id") else ""
        rendered.append(
            {
                **box,
                "href": f"#entity-{box['mention_id']}",
                "class_name": f"entity-box e-{entity_type}",
                "style": _overlay_box_style(box, page_width, page_height),
                "tip": f"{name} · {entity_type}{canonical}",
                "aria_label": f"{name}, entity type {entity_type}",
            }
        )
    return rendered


def _markup_highlights(doc_id, mode: str) -> list[dict]:
    """Return text-range highlights for non-paginated markup originals."""
    if mode == "entities":
        rows = q(
            """
            SELECT em.id AS mention_id, em.surface, em.entity_type,
                   el.scheme, el.kb_id, c.preferred
            FROM entity_mention em
            JOIN annotations an ON an.id = em.annotation_id
            LEFT JOIN entity_link el ON el.mention_id = em.id
            LEFT JOIN canonical c ON c.id::text = el.kb_id
            WHERE an.document_id = %s
            ORDER BY em.id
            """,
            (str(doc_id),),
        )
        highlights = []
        for row in rows:
            name = row.get("preferred") or row.get("surface") or "entity"
            entity_type = row.get("entity_type") or "ENTITY"
            canonical = f" · {row['scheme']}:{row['kb_id']}" if row.get("kb_id") else ""
            highlights.append(
                {
                    "surface": row.get("surface") or name,
                    "href": f"#entity-{row['mention_id']}",
                    "class_name": f"w entity-box e-{entity_type}",
                    "tip": f"{name} · {entity_type}{canonical}",
                    "aria_label": f"{name}, entity type {entity_type}",
                }
            )
        return highlights
    rows = q(
        """
        SELECT substring(c.full_text FROM sp.char_start + 1
                         FOR sp.char_end - sp.char_start) AS surface,
               l.uid, l.lemma, l.pos
        FROM lexeme_occurrences o
        JOIN annotations an      ON an.id = o.annotation_id
        JOIN spans sp            ON sp.id = o.span_id
        JOIN lexemes l           ON l.uid = o.lexeme_uid
        JOIN document_content c  ON c.document_id = an.document_id
        WHERE an.document_id = %s
        ORDER BY sp.char_start
        """,
        (str(doc_id),),
    )
    return [
        {
            "surface": row.get("surface") or "",
            "href": url_for("lexeme", uid=row["uid"]),
            "class_name": f"w p-{row['pos']}",
            "tip": f"{row['lemma']} · {row['pos']}",
            "aria_label": f"{row['lemma']}, part of speech {row['pos']}",
        }
        for row in rows
    ]


def _apply_markup_highlights(render_html: str, highlights: list[dict]) -> str:
    """Wrap the first matching escaped surface for each highlight in sanitized HTML."""
    result = render_html
    for item in highlights:
        surface = str(item.get("surface") or "")
        if not surface:
            continue
        needle = html_escape(surface, quote=False)
        replacement = (
            f'<a href="{html_escape(item["href"], quote=True)}" '
            f'class="{html_escape(item["class_name"], quote=True)}" '
            f'data-t="{html_escape(item["tip"], quote=True)}" '
            f'aria-label="{html_escape(item["aria_label"], quote=True)}">{needle}</a>'
        )
        result = result.replace(needle, replacement, 1)
    return result


def _original_markup(doc: dict, doc_id, mode: str = "grammar") -> dict:
    """Build the non-paginated original-markup overlay model."""
    mode = mode if mode in {"grammar", "entities"} else "grammar"
    if doc.get("doc_type") not in {"docx", "html", "epub"}:
        return {
            "kind": "markup",
            "available": False,
            "message": f"Original markup is not available for {doc.get('doc_type')} documents yet.",
        }
    row = q1(
        "SELECT value FROM document_metadata WHERE document_id = %s AND key = 'render_html'",
        (str(doc_id),),
    )
    if not row or not row.get("value"):
        return {
            "kind": "markup",
            "available": False,
            "message": "Original markup is not available until this document is re-extracted.",
        }
    html = _apply_markup_highlights(str(row["value"]), _markup_highlights(doc_id, mode))
    return {"kind": "markup", "available": True, "mode": mode, "html": html}


def _original_overlay(
    doc: dict, doc_id, page: int, page_numbers: list[int], mode: str = "grammar"
) -> dict:
    """Build the PDF original-page overlay model for the document template."""
    mode = mode if mode in {"grammar", "entities"} else "grammar"
    if doc.get("doc_type") != "pdf":
        return {
            "available": False,
            "message": (
                f"Original overlay is not available for {doc.get('doc_type')} documents yet."
            ),
        }
    if not page_numbers:
        return {
            "available": False,
            "message": "Original overlay is not available until this PDF is re-extracted.",
        }
    meta_rows = q(
        """
        SELECT page_width, page_height, has_text, text_run_count
        FROM document_pages
        WHERE document_id = %s AND page_number = %s
        """,
        (str(doc_id), page),
    )
    meta = meta_rows[0] if meta_rows else {}
    from_document_pages = bool(meta)
    if not meta:
        meta_rows = q(
            """
            SELECT max(page_width) AS page_width, max(page_height) AS page_height
            FROM document_layout_runs
            WHERE document_id = %s AND page_number = %s
            """,
            (str(doc_id), page),
        )
        meta = meta_rows[0] if meta_rows else {}
    page_width = float(meta.get("page_width") or 0)
    page_height = float(meta.get("page_height") or 0)
    has_text = bool(meta.get("has_text", True))
    text_run_count = int(meta.get("text_run_count") or 0)
    text_free_page = from_document_pages and (not has_text or text_run_count == 0)
    if page_width <= 0 or page_height <= 0 or text_free_page:
        return {
            "kind": "pdf",
            "available": True,
            "image_src": url_for("document_page_image", doc_id=doc_id, page_number=page),
            "page_width": page_width if page_width > 0 else None,
            "page_height": page_height if page_height > 0 else None,
            "mode": mode,
            "boxes": [],
            "message": "No text detected on this page — nothing to overlay.",
        }
    boxes = (
        _entity_overlay_boxes(doc_id, page, page_width, page_height)
        if mode == "entities"
        else _pos_overlay_boxes(doc_id, page, page_width, page_height)
    )
    return {
        "kind": "pdf",
        "available": True,
        "image_src": url_for("document_page_image", doc_id=doc_id, page_number=page),
        "page_width": page_width,
        "page_height": page_height,
        "mode": mode,
        "boxes": boxes,
    }


# --- routes ----------------------------------------------------------------


@app.route("/")
def documents():
    """Render the corpus landing page: a paginated document list plus corpus-wide totals."""
    per_page = 50
    page = max(1, request.args.get("page", 1, type=int))
    rows = q(
        """
        SELECT d.id, d.title, d.doc_type, d.language, d.char_count, d.page_count, d.status,
               (SELECT count(*) FROM sentences s WHERE s.document_id = d.id) AS n_sentences,
               (SELECT string_agg(DISTINCT a.tool, ', ')
                  FROM annotations a WHERE a.document_id = d.id) AS tools
        FROM documents d
        ORDER BY d.char_count DESC NULLS LAST
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        {"limit": per_page, "offset": (page - 1) * per_page},
    )
    rows = [dict(row, preview=_document_preview(row)) for row in rows]
    totals = q1(
        "SELECT (SELECT count(*) FROM documents) docs, "
        "(SELECT count(*) FROM sentences) sents, "
        "(SELECT count(*) FROM lexemes) lexemes, "
        "(SELECT count(*) FROM lexeme_occurrences) occ"
    )
    return render_template(
        "documents.html",
        rows=rows,
        totals=totals,
        page=page,
        has_next=len(rows) == per_page,
        bulk_queued=request.args.get("bulk_queued"),
        bulk_skipped=request.args.get("bulk_skipped"),
        bulk_error=request.args.get("bulk_error"),
    )


@app.route("/documents/bulk", methods=["POST"])
def documents_bulk_action():
    """Submit corpus-list bulk actions through queued GraphQL mutations."""
    ids = request.form.getlist("document_id")
    action = request.form.get("bulk_action")
    if not ids:
        return redirect(url_for("documents", bulk_error="select at least one document"))
    if len(ids) > MAX_BATCH_IDS:
        return redirect(url_for("documents", bulk_error=f"batch size exceeds {MAX_BATCH_IDS}"))
    try:
        if action == "archive":
            data = gql(
                "mutation($ids: [ID!]!) { bulkArchiveDocuments(documentIds: $ids) { id } }",
                {"ids": ids},
            )
            queued = len(data["bulkArchiveDocuments"])
        elif action == "tag":
            tags = [
                tag.strip() for tag in (request.form.get("tags") or "").split(",") if tag.strip()
            ]
            if not tags:
                return redirect(url_for("documents", bulk_error="enter at least one tag"))
            data = gql(
                "mutation($ids: [ID!]!, $tags: [String!]!) { "
                "bulkTagDocuments(documentIds: $ids, tags: $tags) { id } }",
                {"ids": ids, "tags": tags},
            )
            queued = len(data["bulkTagDocuments"])
        elif action == "reannotate":
            queued = _enqueue_each(
                ids,
                "mutation($id: ID!) { reannotate(documentId: $id) { id } }",
                "reannotate",
            )
        elif action == "reextract":
            doc_types = _selected_document_types(ids)
            pdf_ids = [doc_id for doc_id in ids if doc_types.get(doc_id) == "pdf"]
            queued = _enqueue_each(
                pdf_ids,
                "mutation($id: ID!) { reextractDocument(documentId: $id) { id } }",
                "reextractDocument",
            )
            skipped = len(ids) - len(pdf_ids)
            return redirect(url_for("documents", bulk_queued=queued, bulk_skipped=skipped))
        else:
            return redirect(url_for("documents", bulk_error="choose a bulk action"))
    except Exception as exc:
        return redirect(url_for("documents", bulk_error=str(exc)))
    return redirect(url_for("documents", bulk_queued=queued))


@app.route("/operator/health")
def operator_health():
    """Render read-only corpus and database health snapshots for operators."""
    require_operator_health()
    return render_template("operator_health.html", **build_operator_health(q, q1))


@app.route("/operator/health.json")
def operator_health_json():
    """Return the stable operator-health JSON contract for operators."""
    require_operator_health()
    return jsonify(operator_health_payload(build_operator_health(q, q1)))


@app.route("/operator/doc/<uuid:doc_id>/metadata")
def operator_document_metadata(doc_id):
    """Render an operator-gated document metadata correction form."""
    require_operator_health()
    doc = q1("SELECT * FROM documents WHERE id = %s", (str(doc_id),))
    if not doc:
        return ("Document not found", 404)
    tags = q("SELECT tag FROM document_tags WHERE document_id = %s ORDER BY tag", (str(doc_id),))
    metadata_history = q(
        "SELECT field, old_value, new_value, changed_at, actor "
        "FROM document_metadata_history WHERE document_id = %s ORDER BY id DESC",
        (str(doc_id),),
    )
    return render_template(
        "operator_metadata.html",
        doc=doc,
        tags=[t["tag"] for t in tags],
        metadata_history=metadata_history,
        issue=request.args.get("issue"),
        metadata_error=request.args.get("metadata_error"),
        metadata_queued=request.args.get("metadata_queued"),
    )


@app.route("/operator/doc/<uuid:doc_id>/metadata", methods=["POST"])
def operator_document_metadata_update(doc_id):
    """Submit an operator-gated metadata correction through BL-058's mutation."""
    from flask import redirect, url_for

    require_operator_health()
    error = submit_metadata_update(gql, doc_id, request.form)
    if error is not None:
        return redirect(url_for("operator_document_metadata", doc_id=doc_id, metadata_error=error))
    return redirect(url_for("operator_document_metadata", doc_id=doc_id, metadata_queued=1))


@app.route("/collections")
def collections():
    """Render document collections and the create-collection form."""
    rows = q(
        """
        SELECT c.id, c.name, c.description, c.kind, c.rule,
               CASE
                 WHEN c.kind = 'manual' THEN (
                   SELECT count(*) FROM document_collection_members m
                   WHERE m.collection_id = c.id
                 )
                 ELSE NULL
               END AS document_count
        FROM document_collections c
        ORDER BY c.name
        """
    )
    for row in rows:
        if row.get("document_count") is None:
            row["document_count"] = len(_collection_document_rows(row))
        row["rule_summary"] = _collection_rule_summary(row.get("rule"))
    return render_template("collections.html", rows=rows, error=request.args.get("error"))


@app.route("/collections", methods=["POST"])
def collections_create():
    """Create a manual collection or a smart collection rule."""
    from flask import redirect

    kind = request.form.get("kind", "manual")
    if kind not in {"manual", "smart"}:
        return redirect(url_for("collections", error="unknown collection type"))
    rule = _collection_rule_from_form(request.form) if kind == "smart" else {}
    row = q1(
        """
        INSERT INTO document_collections (name, description, kind, rule)
        VALUES (%(name)s, %(description)s, %(kind)s, %(rule)s::jsonb)
        RETURNING id
        """,
        {
            "name": (request.form.get("name") or "").strip(),
            "description": (request.form.get("description") or "").strip(),
            "kind": kind,
            "rule": json.dumps(rule, sort_keys=True),
        },
    )
    return redirect(url_for("collection_detail", collection_id=row["id"]))


@app.route("/collections/<uuid:collection_id>")
def collection_detail(collection_id):
    """Render one collection and its current documents."""
    collection = q1("SELECT * FROM document_collections WHERE id = %s", (str(collection_id),))
    if not collection:
        return ("Collection not found", 404)
    collection["rule_summary"] = _collection_rule_summary(collection.get("rule"))
    documents_in_collection = _collection_document_rows(collection)
    available = []
    if collection["kind"] == "manual":
        available = q(
            """
            SELECT d.id, d.title, d.doc_type
            FROM documents d
            WHERE NOT EXISTS (
                  SELECT 1 FROM document_collection_members m
                  WHERE m.collection_id = %s AND m.document_id = d.id
            )
            ORDER BY d.title
            LIMIT 100
            """,
            (str(collection_id),),
        )
    return render_template(
        "collection.html",
        collection=collection,
        rows=documents_in_collection,
        available=available,
    )


@app.route("/collections/<uuid:collection_id>/documents/<uuid:doc_id>", methods=["POST"])
def collection_document_update(collection_id, doc_id):
    """Add or remove one document from a manual collection."""
    from flask import redirect

    action = request.form.get("action")
    if action == "add":
        exec_sql(
            """
            INSERT INTO document_collection_members (collection_id, document_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
            (str(collection_id), str(doc_id)),
        )
    elif action == "remove":
        exec_sql(
            "DELETE FROM document_collection_members WHERE collection_id = %s AND document_id = %s",
            (str(collection_id), str(doc_id)),
        )
    else:
        return ("unknown action", 400)
    return redirect(request.referrer or url_for("collection_detail", collection_id=collection_id))


@app.route("/doc/<uuid:doc_id>")
def document(doc_id):
    """Render a single document with metadata, POS stats, and annotated sentences."""
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
    view = request.args.get("view")
    original_view = view == "original"
    full_view = view == "all"
    overlay_mode = request.args.get("overlay", "grammar")
    if overlay_mode not in {"grammar", "entities"}:
        overlay_mode = "grammar"
    annotate = request.args.get("annotate", "1") != "0"
    n_sent = q1("SELECT count(*) n FROM sentences WHERE document_id = %s", (str(doc_id),))["n"]
    sentence_base_sql = (
        "SELECT sen.ordinal, sen.text, sp.char_start AS ss, sp.char_end AS se, "
        "sp.page_number "
        "FROM sentences sen JOIN spans sp ON sp.id = sen.span_id "
        "WHERE sen.document_id = %s"
    )
    sentence_sql = f"{sentence_base_sql} ORDER BY sen.ordinal"
    page_rows = []
    page_numbers: list[int] = []
    if not full_view:
        page_rows = q(
            """
            SELECT page_number
            FROM (
                SELECT DISTINCT sp.page_number
                FROM sentences sen JOIN spans sp ON sp.id = sen.span_id
                WHERE sen.document_id = %s AND sp.page_number IS NOT NULL
                UNION
                SELECT dp.page_number
                FROM document_pages dp
                WHERE dp.document_id = %s
            ) pages_with_geometry
            ORDER BY page_number
            """,
            (str(doc_id), str(doc_id)),
        )
        page_numbers = [int(row["page_number"]) for row in page_rows]

    if full_view:
        sentences = q(sentence_sql, (str(doc_id),))
        page = 1
        pages = 1
    else:
        if page_numbers:
            pages = max(page_numbers + [int(doc.get("page_count") or 0)])
            page = min(page, pages)
            sentences = q(
                f"{sentence_base_sql} AND sp.page_number = %s ORDER BY sen.ordinal",
                (str(doc_id), page),
            )
        else:
            sentences = q(
                f"{sentence_sql} LIMIT %s OFFSET %s",
                (str(doc_id), per_page, (page - 1) * per_page),
            )
            pages = max(1, -(-n_sent // per_page))

    attach_page_blocks(q, q1, sentences, doc_id, annotate=annotate)
    overlay = None
    if original_view:
        overlay = (
            _original_overlay(doc, doc_id, page, page_numbers, overlay_mode)
            if doc.get("doc_type") == "pdf"
            else _original_markup(doc, doc_id, overlay_mode)
        )

    metadata_history = q(
        "SELECT field, old_value, new_value, changed_at, actor "
        "FROM document_metadata_history WHERE document_id = %s ORDER BY id DESC",
        (str(doc_id),),
    )
    metadata_suggestions = q(
        "SELECT id, field, current_value, suggested_value, confidence, reason, source, status "
        "FROM document_metadata_suggestion WHERE document_id = %s "
        "ORDER BY status, confidence DESC, field, suggested_value",
        (str(doc_id),),
    )
    for suggestion in metadata_suggestions:
        confidence = float(suggestion.get("confidence") or 0)
        if confidence >= 0.85:
            suggestion["confidence_label"] = "High confidence"
        elif confidence >= 0.75:
            suggestion["confidence_label"] = "Medium confidence"
        else:
            suggestion["confidence_label"] = "Low confidence"
    return render_template(
        "document.html",
        doc=doc,
        authors=[a["name"] for a in authors],
        tags=[t["tag"] for t in tags],
        pos=pos,
        top_verbs=top_verbs,
        preview=_document_preview(doc),
        manual_collections=_available_manual_collections(doc_id),
        sentences=sentences,
        page=page,
        pages=pages,
        full_view=full_view,
        original_view=original_view,
        overlay=overlay,
        overlay_mode=overlay_mode,
        n_sent=n_sent,
        annotate=annotate,
        annotation_state=latest_annotation_state(q, doc_id),
        metadata_history=metadata_history,
        metadata_suggestions=metadata_suggestions,
        metadata_error=request.args.get("metadata_error"),
        metadata_queued=request.args.get("metadata_queued"),
        suggestions_error=request.args.get("suggestions_error"),
        suggestions_queued=request.args.get("suggestions_queued"),
        reprocess_steps=_reprocess_steps(doc, str(doc_id)),
        reprocess_queued=request.args.get("reprocess_queued"),
        reprocess_error=request.args.get("reprocess_error"),
    )


@app.route("/doc/<uuid:doc_id>/reprocess", methods=["POST"])
def document_reprocess(doc_id):
    """Queue one document pipeline step again through the GraphQL API."""
    from flask import redirect, url_for

    doc = q1("SELECT * FROM documents WHERE id = %s", (str(doc_id),))
    if doc is None:
        return ("Document not found", 404)
    step = request.form.get("step", "")
    cfg = _REPROCESS_CONFIG.get(step)
    if cfg is None:
        return redirect(url_for("document", doc_id=doc_id, reprocess_error="unknown step"))
    if cfg["pdf_only"] and str(doc.get("doc_type") or "").lower() != "pdf":
        return redirect(
            url_for("document", doc_id=doc_id, reprocess_error="re-extract is PDF-only")
        )
    active = _active_reprocess_jobs(str(doc_id)).get(step)
    if active is not None:
        return redirect(
            url_for(
                "document",
                doc_id=doc_id,
                reprocess_error=f"{cfg['label']} is already {active['status']}",
            )
        )
    try:
        gql(cfg["mutation"], {"id": str(doc_id)})
    except Exception as exc:
        app.logger.exception("document reprocess action failed")
        return redirect(url_for("document", doc_id=doc_id, reprocess_error=str(exc)))
    return redirect(url_for("document", doc_id=doc_id, reprocess_queued=step))


@app.route("/doc/<uuid:doc_id>/metadata-suggestions", methods=["POST"])
def document_metadata_suggestions_request(doc_id):
    """Queue verified document-metadata suggestions for human review."""
    from flask import redirect, url_for

    try:
        gql(
            """
            mutation SuggestDocumentMetadata($id: ID!) {
              suggestDocumentMetadata(documentId: $id) { id }
            }
            """,
            {"id": str(doc_id)},
        )
    except Exception as exc:
        return redirect(url_for("document", doc_id=doc_id, suggestions_error=str(exc)))
    return redirect(url_for("document", doc_id=doc_id, suggestions_queued=1))


@app.route("/doc/<uuid:doc_id>/metadata", methods=["POST"])
def document_metadata_update(doc_id):
    """Submit a title/description/tags edit via the GraphQL mutation (BL-058).

    The edit is enqueued, not applied synchronously (D-004/D-005: writes never
    happen in the request) — the worker typically catches up within
    WORKER_POLL_SECONDS (5s by default); flag that on the redirect rather than
    let the reloaded page look like the edit silently did nothing.
    """
    from flask import redirect, url_for

    error = submit_metadata_update(gql, doc_id, request.form)
    if error is not None:
        return redirect(url_for("document", doc_id=doc_id, metadata_error=error))
    return redirect(url_for("document", doc_id=doc_id, metadata_queued=1))


@app.route("/doc/<uuid:doc_id>/download")
def document_download(doc_id):
    """Stream an original document download through the API boundary."""
    import httpx
    from flask import Response, abort, stream_with_context

    base = API_URL.rsplit("/graphql", 1)[0]
    url = f"{base}/documents/{doc_id}/download"
    client = httpx.Client(timeout=None)
    upstream = client.send(
        client.build_request("GET", url, headers=api_auth_headers()), stream=True
    )
    if upstream.status_code in (401, 403, 404, 409):
        status = upstream.status_code
        upstream.close()
        client.close()
        abort(status)
    upstream.raise_for_status()

    def _pump():
        try:
            yield from upstream.iter_bytes(chunk_size=1 << 20)
        finally:
            upstream.close()
            client.close()

    headers = {}
    if upstream.headers.get("content-disposition"):
        headers["Content-Disposition"] = upstream.headers["content-disposition"]
    return Response(
        stream_with_context(_pump()),
        mimetype=upstream.headers.get("content-type", "application/octet-stream"),
        headers=headers,
    )


@app.route("/doc/<uuid:doc_id>/page/<int:page_number>.png")
def document_page_image(doc_id, page_number):
    """Stream a rendered original-PDF page PNG through the API boundary."""
    import httpx
    from flask import Response, abort, stream_with_context

    base = API_URL.rsplit("/graphql", 1)[0]
    url = f"{base}/documents/{doc_id}/pages/{page_number}.png"
    client = httpx.Client(timeout=None)
    upstream = client.send(
        client.build_request("GET", url, headers=api_auth_headers()), stream=True
    )
    if upstream.status_code in (401, 403, 404, 409, 415):
        status = upstream.status_code
        upstream.close()
        client.close()
        abort(status)
    upstream.raise_for_status()

    def _pump():
        try:
            yield from upstream.iter_bytes(chunk_size=1 << 20)
        finally:
            upstream.close()
            client.close()

    headers = {}
    if upstream.headers.get("x-docray-cache"):
        headers["X-DocRay-Cache"] = upstream.headers["x-docray-cache"]
    return Response(
        stream_with_context(_pump()),
        mimetype=upstream.headers.get("content-type", "image/png"),
        headers=headers,
    )


@app.errorhandler(413)
def _upload_too_large(_exc):
    """Redirect oversized uploads back to the form with a friendly message."""
    from flask import redirect, url_for

    limit_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    return redirect(url_for("upload_form", error=f"file exceeds the {limit_mb} MB upload limit"))


@app.errorhandler(psycopg.OperationalError)
def _database_unavailable(exc: psycopg.OperationalError):
    """Render a graceful page instead of leaking the raw Werkzeug debugger.

    ``OperationalError`` covers both "server unreachable" and "authentication
    failed" — anything meaning the connection itself couldn't be established,
    as opposed to a query-level bug. Unhandled, this would otherwise surface
    a full interactive traceback (and, in debug mode, a code-execution
    console) to whoever hits any DB-touching route.

    Args:
        exc: The connection failure raised by ``psycopg.connect``.

    Returns:
        A rendered error page with a 503 status.
    """
    app.logger.error("database connection failed: %s", exc)
    return render_template("error.html", detail=str(exc)), 503


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
            headers=api_auth_headers(),
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
        "SELECT id, kind, status, document_id, error, result, created_at, finished_at, "
        "progress_current, progress_total, progress_stage "
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
            "SELECT id, kind, status, error, result, created_at, finished_at, "
            "progress_current, progress_total, progress_stage "
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
    job_rows = decorate_job_rows(cascade or [job])
    return render_template(
        "job.html", job=job, jobs=job_rows, doc=doc, doc_id=doc_id, active=active
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
        app.logger.exception("seeding action failed")
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
        JOIN lexeme_occurrences o ON o.lexeme_uid = l.uid
        WHERE (%(q)s = '' OR l.lemma ILIKE %(qpat)s)
          AND (%(pos)s = '' OR l.pos = %(pos)s)
          AND (%(lang)s = '' OR l.lang = %(lang)s)
        GROUP BY l.uid, l.lang, l.pos, l.lemma
        ORDER BY uses DESC, l.lemma
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    )
    langs = [
        r["lang"]
        for r in q(
            """
            SELECT DISTINCT l.lang
            FROM lexemes l
            JOIN lexeme_occurrences o ON o.lexeme_uid = l.uid
            ORDER BY l.lang
            """
        )
    ]
    poses = [
        r["pos"]
        for r in q(
            """
            SELECT DISTINCT l.pos
            FROM lexemes l
            JOIN lexeme_occurrences o ON o.lexeme_uid = l.uid
            ORDER BY l.pos
            """
        )
    ]
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
    per_page = 100
    page = max(1, request.args.get("page", 1, type=int))
    rows = []
    has_next = False
    if term:
        rows = q(
            """
            SELECT d.id AS doc_id, d.title, s.ordinal, s.text
            FROM sentences s JOIN documents d ON d.id = s.document_id
            WHERE s.text ILIKE %(term)s
            ORDER BY d.title, s.ordinal LIMIT %(limit)s OFFSET %(offset)s
            """,
            {"term": f"%{term}%", "limit": per_page, "offset": (page - 1) * per_page},
        )
        has_next = len(rows) == per_page
        for r in rows:
            pre, _, post = r["text"].partition(term)
            r["pre"], r["hit"], r["post"] = pre, (term if _ else ""), post
    return render_template("search.html", term=term, rows=rows, page=page, has_next=has_next)


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
        app.logger.exception("backup creation failed")
        return redirect(url_for("backups", error=str(exc)))
    return redirect(url_for("job_status", job_id=data["createBackup"]["id"]))


@app.route("/backups/<backup_id>/restore", methods=["POST"])
def backups_restore(backup_id):
    """Enqueue a restore of the whole corpus from *backup_id* (destructive)."""
    from flask import redirect, url_for

    try:
        data = gql("mutation($id: ID!){ restoreBackup(backupId: $id){ id } }", {"id": backup_id})
    except Exception as exc:
        app.logger.exception("backup restore failed")
        return redirect(url_for("backups", error=str(exc)))
    return redirect(url_for("job_status", job_id=data["restoreBackup"]["id"]))


@app.route("/backups/<backup_id>/download")
def backups_download(backup_id):
    """Stream a backup archive to the browser, proxied from the API.

    The API owns object-store access (it lives inside the store's network); the
    webapp never reaches S3 directly, so a host-vs-container ``S3_ENDPOINT``
    mismatch cannot break downloads. The archive is streamed chunk-by-chunk so a
    large corpus never has to be buffered whole in the webapp. The download is
    admin-only at the API (BL-094), so the same credential every other write
    forward uses (``_api_auth_headers``) rides along here too; a 401/403/404
    from the API propagates as the same status rather than a proxy 500.
    """
    import httpx
    from flask import Response, abort, stream_with_context

    base = API_URL.rsplit("/graphql", 1)[0]
    url = f"{base}/backups/{backup_id}/download"
    client = httpx.Client(timeout=None)
    upstream = client.send(
        client.build_request("GET", url, headers=api_auth_headers()), stream=True
    )
    if upstream.status_code in (401, 403, 404):
        status = upstream.status_code
        upstream.close()
        client.close()
        abort(status)
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
