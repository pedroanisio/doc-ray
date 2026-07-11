"""Generate docs/DATABASE.md — the doc-ray ERD spec + data dictionary.

Introspects the LIVE schema (all applied migrations) so the reference never
drifts from reality. Curated prose, groupings, and the Mermaid ER diagrams are
authored here; every table/column/key/constraint/index/policy figure is pulled
from the catalog.

    DATABASE_URL=postgresql://docray:docray@localhost:5455/docray \
        python scripts/gen_data_dictionary.py
"""

from __future__ import annotations

import os
import textwrap

import psycopg
from _data_dictionary_content import MIGRATIONS, RLS_PATH
from psycopg.rows import dict_row

DB = os.environ.get("DATABASE_URL", "postgresql://docray:docray@localhost:5455/docray")
OUT = os.path.join(os.path.dirname(__file__), "..", "docs", "DATABASE.md")

# ---------------------------------------------------------------------------
# Curated metadata: how tables group into layers, and a one-line purpose each.
# (Table shapes/columns come from the catalog; this is just narrative + order.)
# ---------------------------------------------------------------------------

GROUPS: list[tuple[str, str, list[str]]] = [
    (
        "Document entity & bibliographic identity",
        "The document is a first-class entity. Independent multivalued facts "
        "(authors, tags, identifiers, format metadata) are decomposed to their own "
        "relations (4NF); blobs live in object storage, only an `s3://` pointer + "
        "sha256 are stored.",
        [
            "documents",
            "document_content",
            "authors",
            "document_authors",
            "tags",
            "document_tags",
            "document_identifiers",
            "document_metadata",
        ],
    ),
    (
        "Provenance (append-only lineage)",
        "Every processing/lineage event on a document, with its parameters "
        "normalized out of a JSON bag into `(event, key) -> value`. "
        "`document_metadata_history` is the analogous append-only trail for "
        "editable title/description/tags edits (BL-058) — a distinct concern "
        "from pipeline/ingest events, RLS-scoped and read-granted the same way.",
        ["document_provenance", "provenance_params", "document_metadata_history"],
    ),
    (
        "Segmentation & the span registry",
        "Character offsets are a first-class, addressable entity (`spans`); every "
        "stand-off annotation references a span by FK rather than repeating offset "
        "triples. Sentences are ULID-keyed and reference a span.",
        ["spans", "sentences"],
    ),
    (
        "Lexeme & annotation backbone",
        "`lexemes` is analyzer-independent type identity; `annotations` is one "
        "analyzer run over a document scoped by `(layer, tool)`; `lexeme_occurrences` "
        "is the materialized projection of token-level lexeme links that the "
        "webapp/API render for lexeme and KWIC reads.",
        ["lexemes", "annotations", "lexeme_occurrences"],
    ),
    (
        "Tokens, morphology & subwords (NLP layers 3–7)",
        "The token is the addressable unit syntax/morphology reference. UD FEATS and "
        "BPE/WordPiece pieces decompose into child relations (no arrays, no pipe-strings).",
        ["tokens", "token_feature", "subword"],
    ),
    (
        "Syntax — shallow & deep (layers 8–10)",
        "Chunking (BIO collapsed to spans), constituency (adjacency-list tree), and "
        "dependency (each token has exactly one head → a function).",
        ["chunk", "constituent", "dependency"],
    ),
    (
        "Entities & reference (layers 11–13)",
        "Named-entity mentions, entity linking to a KB, and coreference chains.",
        ["entity_mention", "entity_link", "coref_chain", "coref_mention"],
    ),
    (
        "Meaning, discourse, temporal & sentiment (layers 14–19)",
        "Semantic role labeling, word-sense disambiguation, AMR meaning graphs, "
        "PDTB discourse relations, TimeML (TIMEX/EVENT/TLINK), and sentiment.",
        [
            "srl_predicate",
            "srl_argument",
            "word_sense",
            "amr_node",
            "amr_edge",
            "discourse_relation",
            "timex",
            "event",
            "tlink",
            "sentiment",
        ],
    ),
    (
        "Distributed representations (layer 20)",
        "pgvector embeddings over spans, ANN-indexed with HNSW (cosine).",
        ["embedding"],
    ),
    (
        "Access control — RBAC & row-level security",
        "Principals, roles, permissions and per-document ACLs. Every document-scoped "
        "table above is protected by RLS keyed on these (see §Access control).",
        [
            "principals",
            "roles",
            "permissions",
            "role_permissions",
            "principal_roles",
            "document_acl",
        ],
    ),
    (
        "Evaluation & drift artifacts",
        "Corpus snapshots, evaluation runs, metric rows, and gold samples make quality and drift "
        "measurements durable and citeable instead of one-off operator observations.",
        ["corpus_snapshot", "evaluation_run", "evaluation_metric", "gold_sample"],
    ),
    (
        "Meta",
        "Applied-migration ledger, the schema's SemVer, and the corpus-backup "
        "catalogue. `backups` is operational: it and the `jobs` queue are excluded "
        "from every snapshot and from restore, so a restore never clobbers the "
        "queue or the archive index it is reading from.",
        ["schema_migrations", "schema_version", "backups"],
    ),
]


# ---------------------------------------------------------------------------
# Static authored sections
# ---------------------------------------------------------------------------

OVERVIEW = """\
**doc-ray** is a document-ingestion and NLP-corpus store on **PostgreSQL 16**
(with the **pgvector** and **pg_trgm** extensions) plus **MinIO/S3** for the
original bytes. A document is ingested (any of pdf/txt/md/html/epub/docx),
its text and metadata extracted, then annotated by real spaCy into the full
20-layer NLP stack. Everything queryable lives in Postgres; blobs never do.

**Design principles**

- **4NF normalization.** No arrays or JSON bags for independent multivalued
  facts — each becomes its own relation whose key determines a single value.
  Anything derivable is a view, not a stored column.
- **Stand-off annotation over a shared span registry.** Character offsets are
  a first-class entity (`spans`); every layer references a span by FK instead
  of repeating `(document_id, char_start, char_end)`.
- **Dual identity.** Top-level entities use `uuid` (`documents`, `spans`,
  `principals`); high-volume, time-ordered annotation rows use a **ULID**
  domain (Crockford base32, 26 chars) so `ORDER BY id` is time order.
- **Layer-aware runs.** An `annotations` row is one analyzer run over a
  document, scoped by `(layer, tool)`, so many tools/layers coexist without
  collision. Deleting a document cascades through the entire graph.
- **Document-level RBAC via RLS.** Access is decided per document and inherited
  by every document-scoped table through row-level-security policies.
"""

CONVENTIONS = (
    "| Convention | Detail |\n"
    "|---|---|\n"
    "| `ulid` domain | `char(26)` matching `^[0-7][0-9A-HJKMNP-TV-Z]{25}$` "
    "(Crockford base32; excludes I/L/O/U). Sorts in creation-time order. |\n"
    "| `uuid` ids | `gen_random_uuid()` defaults on top-level entities. |\n"
    "| Content addressing | `content_sha256` / `text_sha256` dedupe + integrity; "
    "blob body is an `s3://` URI, never a row. |\n"
    "| Offsets | Half-open `[char_start, char_end)` into "
    "`document_content.full_text`; owned by `spans`. |\n"
    "| `ON DELETE` | Document-owned edges CASCADE (or SET NULL for advisory links); "
    "references to global dimensions (`lexemes`, `authors`, `tags`, "
    "`documents.derived_from`) do not. |\n"
    "| Timestamps | `timestamptz`, default `now()`. |\n"
)

ERDS = [
    (
        "Document entity, 4NF satellites & provenance",
        """\
erDiagram
    documents ||--o| document_content : "1:1 text + FTS"
    documents ||--o{ document_authors : ""
    authors   ||--o{ document_authors : ""
    documents ||--o{ document_tags : ""
    tags      ||--o{ document_tags : ""
    documents ||--o{ document_identifiers : "(scheme,value)"
    documents ||--o{ document_metadata : "(key,value)"
    documents ||--o{ document_provenance : "events"
    document_provenance ||--o{ provenance_params : "(key,value)"
    documents ||--o| documents : "derived_from"
""",
    ),
    (
        "Segmentation, span registry & the annotation backbone",
        """\
erDiagram
    documents   ||--o{ spans : "owns offsets"
    documents   ||--o{ sentences : ""
    documents   ||--o{ annotations : "analyzer runs"
    spans       ||--o{ sentences : "span_id"
    annotations ||--o{ lexeme_occurrences : ""
    spans       ||--o{ lexeme_occurrences : "span_id"
    lexemes     ||--o{ lexeme_occurrences : "type identity"
""",
    ),
    (
        "Tokens, morphology & syntax",
        """\
erDiagram
    annotations ||--o{ tokens : ""
    spans       ||--o{ tokens : "span_id"
    sentences   ||--o{ tokens : "sentence_id (SET NULL)"
    lexemes     ||--o{ tokens : "lemma+UPOS"
    tokens      ||--o{ token_feature : "UD FEATS"
    tokens      ||--o{ subword : "BPE/WordPiece"
    tokens      ||--o{ dependency : "dependent/head"
    annotations ||--o{ dependency : ""
    annotations ||--o{ chunk : "NP/VP/PP"
    annotations ||--o{ constituent : "tree"
    constituent ||--o{ constituent : "parent_id"
""",
    ),
    (
        "Entities, reference, meaning, temporal & embeddings",
        """\
erDiagram
    annotations   ||--o{ entity_mention : ""
    entity_mention ||--o{ entity_link : "KB id"
    annotations   ||--o{ coref_chain : ""
    coref_chain   ||--o{ coref_mention : ""
    annotations   ||--o{ srl_predicate : ""
    srl_predicate ||--o{ srl_argument : "ARG*"
    tokens        ||--o{ word_sense : ""
    annotations   ||--o{ amr_node : ""
    amr_node      ||--o{ amr_edge : "role"
    annotations   ||--o{ timex : ""
    annotations   ||--o{ event : ""
    timex         ||--o{ tlink : "source/target"
    event         ||--o{ tlink : "source/target"
    spans         ||--o{ embedding : "vector(768)"
""",
    ),
    (
        "Access control (RBAC) — decided per document, inherited via RLS",
        """\
erDiagram
    principals       ||--o{ principal_roles : ""
    roles            ||--o{ principal_roles : ""
    roles            ||--o{ role_permissions : ""
    permissions      ||--o{ role_permissions : ""
    principals       ||--o{ document_acl : "grantee"
    documents        ||--o{ document_acl : "resource"
    permissions      ||--o{ document_acl : "action"
    principals       ||--o{ document_provenance : "actor_principal_id"
""",
    ),
]

RLS_NARRATIVE = """\
Authorization is decided **per document** and inherited by every
document-scoped table. A principal may **read** a document iff it is *admin* or
holds any `document_acl` row on it, and may **write** iff *admin* or holds an
`annotate`/`delete`/`admin` grant.

**Enforcement.** RLS is `ENABLE`d (not `FORCE`) on 36 tables (incl. `jobs` and
`backups` since migration 043 — BL-064). The `docray`
role is a **superuser and table owner**, so it **bypasses RLS** — the ingest
pipeline and admin tooling use it as the trusted batch writer. End-user / API
traffic must run as the non-superuser role **`docray_api`** (`SET ROLE
docray_api`) and set the principal per transaction:

```sql
SET ROLE docray_api;
SET app.principal_id = '<principal uuid>';
```

**How the policies work.** Two `SECURITY DEFINER` set-returning helpers,
`docray_readable_documents()` and `docray_writable_documents()`, compute the
caller's visible-document set **once per query**. Each table's policies then
semi-join the row's owning-document path against that set (see the *RLS path*
line in each table entry) — the planner turns this into a hashed semi-join
rather than a per-row function call, so a full scan of a million-row table
stays cheap. Reads use a `FOR SELECT` policy only; writes use separate
`FOR INSERT/UPDATE/DELETE` policies (with `WITH CHECK`) so they never
contaminate read plans.

**Trust boundary.** The HTTP API maps bearer or `X-DocRay-API-Token`
credentials to principals, then drops every request to `docray_api`. The
principal GUC is honored only for that trusted app-tier role; anonymous API
requests run as `docray_api` with no principal id rather than as the superuser.
Direct SQL users are isolated with per-principal `LOGIN` roles recorded in
`principals.db_role` and created with
`docray_create_principal_login(principal, role_name, password)`. Those roles
inherit `docray_client` and are mapped by authenticated database identity, so
setting `app.principal_id` cannot impersonate a different principal. The
derived views (`lexeme_occurrence_detail`, `lexeme_forms`, `lexeme_keys`) are
set `security_invoker = true` so they inherit the caller's RLS instead of the
owner's bypass.
"""


def q(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()


def table_oid(conn, name):
    r = conn.execute("SELECT %s::regclass::oid AS oid", (name,)).fetchone()
    return r["oid"]


def columns(conn, oid):
    return q(
        conn,
        """
        SELECT a.attname, format_type(a.atttypid, a.atttypmod) AS type,
               a.attnotnull, pg_get_expr(ad.adbin, ad.adrelid) AS dflt,
               col_description(a.attrelid, a.attnum) AS comment
        FROM pg_attribute a
        LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
        WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY a.attnum""",
        (oid,),
    )


def constraints(conn, oid):
    return q(
        conn,
        """
        SELECT conname, contype, pg_get_constraintdef(oid) AS def,
               array(SELECT attname FROM pg_attribute
                     WHERE attrelid = c.conrelid AND attnum = ANY(c.conkey)) AS cols
        FROM pg_constraint c WHERE conrelid = %s
        ORDER BY contype, conname""",
        (oid,),
    )


def indexes(conn, name):
    return q(
        conn,
        "SELECT indexname, indexdef FROM pg_indexes "
        "WHERE schemaname='public' AND tablename=%s ORDER BY indexname",
        (name,),
    )


def rls_enabled(conn, oid):
    r = conn.execute(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid=%s", (oid,)
    ).fetchone()
    return r["relrowsecurity"], r["relforcerowsecurity"]


def md_escape(s):
    return (s or "").replace("|", "\\|").replace("\n", " ")


def render_table(conn, name, out):
    oid = table_oid(conn, name)
    cons = constraints(conn, oid)
    pk = next((c for c in cons if c["contype"] == "p"), None)
    pk_cols = set(pk["cols"]) if pk else set()
    uks = [c for c in cons if c["contype"] == "u"]
    uk_cols = {col for c in uks for col in c["cols"]}
    fks = [c for c in cons if c["contype"] == "f"]
    checks = [c for c in cons if c["contype"] == "c"]
    # map column -> "FK→target"
    fk_by_col = {}
    for c in fks:
        # def looks like: FOREIGN KEY (x) REFERENCES t(y) ON DELETE ...
        d = c["def"]
        ref = (
            d.split("REFERENCES", 1)[1].strip().split("(", 1)[0].strip()
            if "REFERENCES" in d
            else "?"
        )
        for col in c["cols"]:
            fk_by_col[col] = ref

    out.append(f"#### `{name}`\n")
    tbl_comment = conn.execute("SELECT obj_description(%s)", (oid,)).fetchone()["obj_description"]
    if tbl_comment:
        out.append(md_escape(tbl_comment) + "\n")

    out.append("| Column | Type | Null | Key | Default | Notes |")
    out.append("|---|---|---|---|---|---|")
    for c in columns(conn, oid):
        keyparts = []
        if c["attname"] in pk_cols:
            keyparts.append("PK")
        if c["attname"] in fk_by_col:
            keyparts.append(f"FK→{fk_by_col[c['attname']]}")
        if c["attname"] in uk_cols and c["attname"] not in pk_cols:
            keyparts.append("UQ")
        out.append(
            "| `{name}` | {type} | {null} | {key} | {dflt} | {note} |".format(
                name=c["attname"],
                type=md_escape(c["type"]),
                null="" if c["attnotnull"] else "yes",
                key=", ".join(keyparts),
                dflt=f"`{md_escape(c['dflt'])}`" if c["dflt"] else "",
                note=md_escape(c["comment"]),
            )
        )
    out.append("")

    if pk:
        pk_def = pk["def"]
        if pk_def.startswith("PRIMARY KEY "):
            pk_def = pk_def[len("PRIMARY KEY ") :]
        out.append(f"- **Primary key:** {pk_def}")
    for c in uks:
        out.append(f"- **Unique:** {c['def']}")
    for c in fks:
        out.append(f"- **FK:** {c['def']}")
    for c in checks:
        out.append(f"- **Check:** {c['def']}")
    idx = [i for i in indexes(conn, name) if not i["indexname"].endswith("_pkey")]
    for i in idx:
        # strip the leading "CREATE [UNIQUE] INDEX name ON schema.table "
        body = i["indexdef"].split(" ON ", 1)[1] if " ON " in i["indexdef"] else i["indexdef"]
        out.append(f"- **Index** `{i['indexname']}`: {md_escape(body)}")
    en, force = rls_enabled(conn, oid)
    if en:
        out.append(
            f"- **RLS:** enabled — read/write policies via *{RLS_PATH.get(name, '?')}*"
            + (" (FORCED)" if force else "")
        )
    out.append("")


def _in_sync(text: str, out_path: str) -> tuple[bool, str]:
    """Compare freshly generated *text* to the committed file at *out_path*.

    Returns ``(is_in_sync, message)``. Pure and offline (no DB) so the drift logic
    is unit-testable; generating *text* still requires the live schema.
    """
    try:
        with open(out_path, encoding="utf-8") as f:
            current = f.read()
    except FileNotFoundError:
        return False, f"{out_path} does not exist — run the generator to create it."
    if current == text:
        return True, f"{out_path} is in sync with the live schema."
    cur_lines, new_lines = current.splitlines(), text.splitlines()
    for i, (a, b) in enumerate(zip(cur_lines, new_lines, strict=False), 1):
        if a != b:
            return False, (
                f"{out_path} is STALE at line {i} — re-run "
                "`python scripts/gen_data_dictionary.py`.\n"
                f"  committed: {a[:80]!r}\n  expected:  {b[:80]!r}"
            )
    return False, (
        f"{out_path} is STALE ({len(cur_lines)} vs {len(new_lines)} lines) — "
        "re-run `python scripts/gen_data_dictionary.py`."
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="Generate (or --check) docs/DATABASE.md from the live schema."
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if docs/DATABASE.md is stale vs the live schema; write nothing.",
    )
    args = ap.parse_args(argv)
    conn = psycopg.connect(DB, row_factory=dict_row, autocommit=True)
    applied = conn.execute("SELECT count(*) n FROM schema_migrations").fetchone()["n"]
    try:
        ver = conn.execute("SELECT docray_schema_version() v").fetchone()["v"]
    except Exception:
        ver = None

    o: list[str] = []
    o.append("# doc-ray — Database ERD & Data Dictionary\n")
    if ver:
        o.append(f"**Schema version `{ver}`** (SemVer) · {applied} migrations applied\n")
    o.append(
        "> Generated from the live schema by `scripts/gen_data_dictionary.py`. "
        "Do not edit by hand — re-run the generator.\n"
    )
    o.append("## 1. Overview\n")
    o.append(OVERVIEW)
    o.append("\n## 2. Conventions\n")
    o.append(CONVENTIONS)

    o.append("\n## 3. Entity-Relationship Diagrams\n")
    o.append(
        "_Rendered as Mermaid; attributes are catalogued in §5. Diagrams are grouped "
        "by domain for legibility (the full schema is one connected graph)._\n"
    )
    for title, mer in ERDS:
        o.append(f"### {title}\n")
        o.append("```mermaid\n" + mer + "```\n")

    # domains + enums
    o.append("\n## 4. Domains & enumerated types\n")
    doms = q(
        conn,
        """
        SELECT t.typname,
               format_type(t.typbasetype, t.typtypmod) AS base,
               (SELECT pg_get_constraintdef(oid) FROM pg_constraint
                WHERE contypid=t.oid LIMIT 1) AS chk
        FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
        WHERE n.nspname='public' AND t.typtype='d' ORDER BY t.typname""",
    )
    for d in doms:
        o.append(
            f"- **domain `{d['typname']}`** = `{d['base']}` "
            + (f"— `{md_escape(d['chk'])}`" if d["chk"] else "")
        )
    enums = q(
        conn,
        """
        SELECT t.typname,
               string_agg(quote_literal(e.enumlabel), ', ' ORDER BY e.enumsortorder) AS labels
        FROM pg_type t JOIN pg_enum e ON e.enumtypid=t.oid
        JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public'
        GROUP BY t.typname ORDER BY t.typname""",
    )
    for e in enums:
        o.append(f"- **enum `{e['typname']}`** ∈ {{ {md_escape(e['labels'])} }}")
    o.append("")

    o.append("\n## 5. Data dictionary\n")
    for gi, (title, desc, tables) in enumerate(GROUPS, 1):
        o.append(f"### 5.{gi} {title}\n")
        o.append(textwrap.fill(desc, 100) + "\n")
        for t in tables:
            render_table(conn, t, o)

    # views
    o.append("\n## 6. Views\n")
    views = q(
        conn,
        """
        SELECT c.relname,
               COALESCE((SELECT option_value FROM pg_options_to_table(c.reloptions)
                         WHERE option_name='security_invoker'), 'false') AS invoker,
               pg_get_viewdef(c.oid, true) AS def
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind='v' ORDER BY c.relname""",
    )
    for v in views:
        o.append(f"#### `{v['relname']}`  \n`security_invoker = {v['invoker']}`\n")
        o.append("```sql\n" + v["def"].strip() + "\n```\n")

    # functions
    o.append("\n## 7. Functions & triggers\n")
    fns = q(
        conn,
        """
        SELECT p.proname, pg_get_function_arguments(p.oid) AS args,
               pg_get_function_result(p.oid) AS ret, p.prosecdef, l.lanname
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        JOIN pg_language l ON l.oid=p.prolang
        WHERE n.nspname='public'
          AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid=p.oid AND d.deptype='e')
        ORDER BY p.proname""",
    )
    o.append("| Function | Returns | Security | Lang |")
    o.append("|---|---|---|---|")
    for f in fns:
        o.append(
            f"| `{f['proname']}({md_escape(f['args'])})` | `{md_escape(f['ret'])}` | "
            f"{'DEFINER' if f['prosecdef'] else 'invoker'} | {f['lanname']} |"
        )
    o.append("")
    trigs = q(
        conn,
        """
        SELECT c.relname AS tbl, t.tgname, pg_get_triggerdef(t.oid) AS def
        FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY c.relname, t.tgname""",
    )
    for tr in trigs:
        o.append(f"- **{tr['tbl']}** `{tr['tgname']}`: {md_escape(tr['def'].split(' ON ')[0])} …")
    o.append("")

    o.append("\n## 8. Access control (RBAC + RLS)\n")
    o.append(RLS_NARRATIVE)

    o.append("\n## 9. Migration history\n")
    o.append("| # | Migration | What it adds |")
    o.append("|---|---|---|")
    for i, (name, what) in enumerate(MIGRATIONS, 1):
        o.append(f"| {i:03d} | `{name}.sql` | {md_escape(what)} |")

    text = "\n".join(o) + "\n"
    out_path = os.path.abspath(OUT)
    if args.check:
        ok, message = _in_sync(text, out_path)
        print(message)
        return 0 if ok else 1
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(
        f"Wrote {out_path} ({len(text):,} bytes) — "
        f"{sum(len(g[2]) for g in GROUPS)} tables, {len(views)} views, {len(fns)} functions."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
