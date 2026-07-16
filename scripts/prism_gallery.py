#!/usr/bin/env python3
"""Prism visual-layer gallery — a read-only Flask tool to browse the corpus's
extracted images and drill into each one's Tier-1 features and Tier-3 caption.

STRICTLY READ-ONLY (SELECT only) and connects as the table owner, so it shows
every image across the corpus regardless of per-user RLS. Crops are streamed from
the object store on demand. Loopback-only by default — tunnel to reach it.

Usage:
    DATABASE_URL=... S3_* ... python scripts/prism_gallery.py           # :8013
    PRISM_GALLERY_PORT=9000 python scripts/prism_gallery.py

Disclaimer: see ../DISCLAIMER.md. No statement or premise not backed by a real
logical definition or verifiable reference should be taken for granted.
"""

# ruff: noqa: E501 — inline HTML/CSS template strings carry inherently long lines.
from __future__ import annotations

import os
import threading
from typing import Any

from flask import Flask, Response, abort, render_template_string, request
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from doc_ray.adapters.object_store import ObjectStore
from doc_ray.config import Config

PER_PAGE = 60
_pool = ConnectionPool(
    os.environ["DATABASE_URL"], min_size=2, max_size=16, kwargs={"row_factory": dict_row}
)
# A page load fires ~60 concurrent /crop requests; a single shared boto3 client's
# connection pool would exhaust and drop some. Give each server thread its own.
_tls = threading.local()


def _store() -> ObjectStore:
    store = getattr(_tls, "store", None)
    if store is None:
        store = ObjectStore(Config.from_env())
        _tls.store = store
    return store


app = Flask(__name__)

_FILTERS = (("q", "caption"), ("type", "content_type"), ("src", "source"), ("status", "status"))

_BASE = """
<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>{{ title }}</title>
<style>
:root{--bg:#0c0f14;--surface:#141922;--surface2:#11151d;--ink:#e7ebf1;--muted:#98a2b2;
--faint:#6b7688;--line:#242c38;--accent:#37d3cb;--good:#49c877;--warn:#e2a63f;--crit:#f0715f;
--mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;--sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
@media(prefers-color-scheme:light){:root{--bg:#eef0f3;--surface:#fff;--surface2:#f6f7f9;--ink:#12151b;
--muted:#5a6472;--faint:#8a93a1;--line:#dde1e8;--accent:#0e8f87;--good:#1f8f4e;--warn:#b3720a;--crit:#c0392b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);line-height:1.5}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
header{position:sticky;top:0;background:var(--surface);border-bottom:1px solid var(--line);
padding:.7rem 1rem;display:flex;flex-wrap:wrap;gap:.6rem;align-items:center;z-index:5}
header h1{font-size:1rem;margin:0 1rem 0 0;font-family:var(--mono);letter-spacing:.02em}
header .sp{flex:1}
input,select{background:var(--surface2);color:var(--ink);border:1px solid var(--line);
border-radius:7px;padding:.35rem .5rem;font-family:var(--mono);font-size:.78rem}
.count{font-family:var(--mono);font-size:.75rem;color:var(--faint)}
.wrap{padding:1rem;max-width:1500px;margin:0 auto}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:.8rem}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;overflow:hidden;display:flex;flex-direction:column}
.card .thumb{background:var(--surface2);aspect-ratio:4/3;display:flex;align-items:center;justify-content:center;overflow:hidden}
.card img{max-width:100%;max-height:100%;object-fit:contain}
.card .cap{padding:.45rem .55rem;font-size:.72rem;color:var(--muted);border-top:1px solid var(--line)}
.chip{font-family:var(--mono);font-size:.6rem;text-transform:uppercase;letter-spacing:.05em;
padding:.1rem .35rem;border-radius:999px;border:1px solid var(--line);color:var(--faint)}
.st-committed{color:var(--good);border-color:var(--good)}.st-review{color:var(--warn);border-color:var(--warn)}
.st-rejected{color:var(--crit);border-color:var(--crit)}
.pager{display:flex;gap:.5rem;align-items:center;justify-content:center;margin:1.2rem 0;font-family:var(--mono);font-size:.8rem}
.pager a,.pager span{padding:.3rem .7rem;border:1px solid var(--line);border-radius:7px;background:var(--surface)}
.detail{display:grid;grid-template-columns:minmax(320px,1fr) minmax(300px,480px);gap:1.5rem;align-items:start}
.detail .img{background:var(--surface2);border:1px solid var(--line);border-radius:10px;padding:12px;text-align:center}
.detail .img img{max-width:100%;border-radius:5px}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:1rem}
.panel h2{font-size:.72rem;font-family:var(--mono);text-transform:uppercase;letter-spacing:.1em;color:var(--faint);margin:0 0 .6rem}
.caption{font-size:.95rem;white-space:pre-wrap;margin:.2rem 0 .8rem}
dl.kv{display:grid;grid-template-columns:auto 1fr;gap:.25rem .8rem;margin:0;font-family:var(--mono);font-size:.76rem}
dl.kv dt{color:var(--faint)}dl.kv dd{margin:0;text-align:right;overflow-wrap:anywhere}
.sw{display:inline-block;width:11px;height:11px;border-radius:3px;border:1px solid #8886;vertical-align:middle;margin-right:3px}
.gbar{position:relative;height:6px;background:#8883;border-radius:999px;margin:.3rem 0}
.gbar>span{position:absolute;left:0;top:0;bottom:0;border-radius:999px;background:var(--warn)}
.gbar>i{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--good)}
</style>
"""

_INDEX = (
    _BASE
    + """
<header>
  <h1><a href="/">prism · gallery</a></h1>
  <form method=get style="display:flex;flex-wrap:wrap;gap:.5rem;flex:1">
    <input name=q placeholder="search caption…" value="{{ f.q or '' }}" size=18>
    <select name=type><option value="">type: any</option>{% for c in types %}<option {{ 'selected' if f.type==c }}>{{ c }}</option>{% endfor %}</select>
    <select name=src><option value="">source: any</option>{% for s in sources %}<option {{ 'selected' if f.src==s }}>{{ s }}</option>{% endfor %}</select>
    <select name=status><option value="">status: any</option>{% for s in statuses %}<option {{ 'selected' if f.status==s }}>{{ s }}</option>{% endfor %}</select>
    <input name=doc placeholder="doc id…" value="{{ f.doc or '' }}" size=12>
    <button>filter</button>
  </form>
  <span class=count>{{ "{:,}".format(total) }} images</span>
</header>
<div class=wrap>
  <div class=grid>
    {% for im in rows %}
    <a class=card href="/image/{{ im.id }}">
      <div class=thumb><img loading=lazy src="/crop/{{ im.id }}"></div>
      <div class=cap>
        <span class="chip st-{{ im.status or 'none' }}">{{ im.status or 'no caption' }}</span>
        <span class=chip>{{ im.content_type or im.image_class or '—' }}</span>
        <div style="margin-top:.3rem">{{ (im.caption or '')[:70] }}</div>
      </div>
    </a>
    {% endfor %}
  </div>
  <div class=pager>
    {% if page>1 %}<a href="{{ url(page-1) }}">← prev</a>{% else %}<span>←</span>{% endif %}
    <span>page {{ page }} / {{ pages }}</span>
    {% if page<pages %}<a href="{{ url(page+1) }}">next →</a>{% else %}<span>→</span>{% endif %}
  </div>
</div>
"""
)

_DETAIL = (
    _BASE
    + """
<header><h1><a href="/">prism · gallery</a></h1><span class=sp></span>
  <a class=count href="/?doc={{ im.document_id }}">all images in this doc →</a></header>
<div class=wrap>
  <div class=detail>
    <div class=img><img src="/crop/{{ im.id }}"></div>
    <div style="display:flex;flex-direction:column;gap:1rem">
      <div class=panel>
        <h2>Description — {{ im.status or 'no caption' }}
          {% if im.grounding_score is not none %}· grounding {{ '%.2f'|format(im.grounding_score) }}{% endif %}</h2>
        {% if im.grounding_score is not none %}
        <div class=gbar><span style="width:{{ [2,(im.grounding_score*100)|int]|max }}%"></span><i style="left:80%"></i></div>
        {% endif %}
        <div class=caption>{{ im.caption or '(not captioned yet)' }}</div>
        <dl class=kv>
          <dt>content type</dt><dd>{{ im.content_type or '—' }}</dd>
          <dt>model</dt><dd>{{ im.model or '—' }}</dd>
          {% if im.matched %}<dt>grounded terms</dt><dd>{{ im.matched|join(', ') }}</dd>{% endif %}
        </dl>
      </div>
      <div class=panel>
        <h2>Tier-1 features</h2>
        <dl class=kv>
          <dt>class</dt><dd>{{ im.image_class or '—' }}</dd>
          <dt>size</dt><dd>{{ im.width_px }}×{{ im.height_px }}</dd>
          <dt>saturation</dt><dd>{{ '%.3f'|format(im.mean_saturation) if im.mean_saturation is not none else '—' }}</dd>
          <dt>contrast</dt><dd>{{ '%.3f'|format(im.contrast) if im.contrast is not none else '—' }}</dd>
          <dt>edge density</dt><dd>{{ '%.3f'|format(im.edge_density) if im.edge_density is not none else '—' }}</dd>
          <dt>dominant</dt><dd>{% for c in im.colors %}<span class=sw style="background:{{ c }}"></span>{% endfor %}</dd>
        </dl>
      </div>
      <div class=panel>
        <h2>Provenance</h2>
        <dl class=kv>
          <dt>document</dt><dd><a href="https://doc-ray.com/doc/{{ im.document_id }}?page={{ im.page_number }}&view=original">{{ (im.title or '')[:40] }}</a></dd>
          <dt>page</dt><dd>{{ im.page_number }}</dd>
          <dt>source</dt><dd>{{ im.source }}</dd>
          <dt>detector</dt><dd>{{ im.detector }}</dd>
          <dt>object key</dt><dd>{{ im.object_key }}</dd>
        </dl>
      </div>
    </div>
  </div>
</div>
"""
)


def _distinct(col: str, table: str) -> list[str]:
    with _pool.connection() as c:
        rows = c.execute(
            f"SELECT DISTINCT {col} v FROM {table} WHERE {col} IS NOT NULL ORDER BY 1"
        ).fetchall()
    return [r["v"] for r in rows]


def _where(f: dict[str, str]) -> tuple[str, list[Any]]:
    clauses, params = ["1=1"], []
    for sql, val in (
        ("vc.analysis->>'content_type'=%s", f.get("type")),
        ("di.source=%s", f.get("src")),
        ("vc.status=%s", f.get("status")),
        ("di.document_id::text LIKE %s", (f.get("doc") or "") + "%" if f.get("doc") else ""),
        ("vc.caption ILIKE %s", "%" + f["q"] + "%" if f.get("q") else ""),
    ):
        if val:
            clauses.append(sql)
            params.append(val)
    return " AND ".join(clauses), params


@app.route("/")
def index() -> str:
    f = {k: (request.args.get(k) or "").strip() for k, _ in _FILTERS}
    f["doc"] = (request.args.get("doc") or "").strip()
    page = max(1, request.args.get("page", 1, type=int))
    where, params = _where(f)
    base = (
        "FROM document_image di LEFT JOIN image_tier1_features t ON t.document_image_id=di.id "
        "LEFT JOIN visual_caption vc ON vc.document_image_id=di.id WHERE " + where
    )
    with _pool.connection() as c:
        total = c.execute("SELECT count(*) n " + base, params).fetchone()["n"]
        rows = c.execute(
            "SELECT di.id, di.source, t.image_class, vc.analysis->>'content_type' content_type, "
            "vc.caption, vc.status "
            + base
            + " ORDER BY di.document_id, di.page_number, di.id LIMIT %s OFFSET %s",
            [*params, PER_PAGE, (page - 1) * PER_PAGE],
        ).fetchall()
    pages = max(1, -(-total // PER_PAGE))

    def url(p: int) -> str:
        args = {**{k: v for k, v in {**f}.items() if v}, "page": p}
        return "/?" + "&".join(f"{k}={v}" for k, v in args.items())

    return render_template_string(
        _INDEX,
        title="Prism gallery",
        rows=rows,
        total=total,
        page=page,
        pages=pages,
        f=f,
        url=url,
        types=_distinct("analysis->>'content_type'", "visual_caption"),
        sources=_distinct("source", "document_image"),
        statuses=_distinct("status", "visual_caption"),
    )


@app.route("/image/<image_id>")
def detail(image_id: str) -> str:
    with _pool.connection() as c:
        im = c.execute(
            "SELECT di.*, t.image_class, t.mean_saturation, t.contrast, t.edge_density, "
            "t.dominant_colors, vc.caption, vc.status, vc.model, vc.grounding_score, "
            "vc.grounded_against, vc.analysis->>'content_type' content_type, d.title "
            "FROM document_image di LEFT JOIN image_tier1_features t ON t.document_image_id=di.id "
            "LEFT JOIN visual_caption vc ON vc.document_image_id=di.id "
            "JOIN documents d ON d.id=di.document_id WHERE di.id=%s",
            (image_id,),
        ).fetchone()
    if im is None:
        abort(404)
    im["colors"] = [c.get("hex") for c in (im.get("dominant_colors") or [])][:6]
    im["matched"] = (im.get("grounded_against") or {}).get("matched_terms") or []
    return render_template_string(_DETAIL, title="Image · Prism gallery", im=im)


@app.route("/crop/<image_id>")
def crop(image_id: str) -> Response:
    with _pool.connection() as c:
        row = c.execute("SELECT object_key FROM document_image WHERE id=%s", (image_id,)).fetchone()
    if row is None:
        abort(404)
    try:
        data = _store().get(str(row["object_key"]))
    except Exception as exc:  # noqa: BLE001
        app.logger.warning("crop fetch failed %s: %s", image_id, exc)
        abort(502)
    return Response(data, mimetype="image/png", headers={"Cache-Control": "private, max-age=3600"})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PRISM_GALLERY_PORT", "8013")))
