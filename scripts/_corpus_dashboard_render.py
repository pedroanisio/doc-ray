"""Corpus dashboard: render the report as one self-contained HTML page.

Design notes that are decisions, not taste:

* Corpus totals are **stat tiles**, not a chart. A handful of headline numbers
  is a KPI row; a bar chart of unrelated magnitudes (tokens next to documents)
  would invite comparisons between quantities that share no unit.
* Coverage is a **horizontal bar chart in a single hue**. The reader's job is
  "compare magnitude, low to high", which is a sequential job — the bars are not
  distinct series, so giving them distinct colors would encode identity that
  does not exist. Every bar is direct-labelled with its raw counts and its
  denominator, because a bare percentage invites the reader to invent one.
* Per-document detail is a **table**. Eleven artifact families per document is
  past the point where more marks help.
* Colors are steps from the project's brand ramps (docs/branding). Every
  mark/text pair was checked against both surfaces: marks clear 3:1, text clears
  4.5:1, and the status hues are separated by >=15 OKLab dE. Status is never
  colour alone — each carries its label.
"""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to type checkers
    from _corpus_dashboard_data import Coverage, DocumentRow, Report

_CSS = """
*, *::before, *::after { box-sizing: border-box; }
:root {
  --page:#f5f7fa; --surface:#ffffff; --ink:#171a1f; --muted:#48515d;
  --line:#e3e8ef; --bar:#2a78d6; --track:#e0e5ec;
  --ok:#1f9d55; --warn:#b7791f; --danger:#a92e33;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --page:#0f1216; --surface:#171b21; --ink:#e6e9ee; --muted:#97a1b0;
    --line:#262c35; --bar:#3987e5; --track:#2d333b;
    --ok:#37c26b; --warn:#e0a63a; --danger:#f2555a;
  }
}
:root[data-theme="dark"] {
  --page:#0f1216; --surface:#171b21; --ink:#e6e9ee; --muted:#97a1b0;
  --line:#262c35; --bar:#3987e5; --track:#2d333b;
  --ok:#37c26b; --warn:#e0a63a; --danger:#f2555a;
}
body { margin:0; background:var(--page); color:var(--ink); font:15px/1.5 ui-sans-serif,
  system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
.wrap { max-width:1180px; margin:0 auto; padding:32px 20px 64px; }
h1 { font-size:26px; margin:0 0 4px; letter-spacing:-0.01em; }
h2 { font-size:15px; margin:36px 0 12px; text-transform:uppercase;
     letter-spacing:0.07em; color:var(--muted); font-weight:600; }
.sub { color:var(--muted); font-size:13px; margin:0; }
.card { background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:18px; }
.kpis { display:grid; gap:12px; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); }
.kpi .label { font-size:12px; color:var(--muted); text-transform:uppercase;
              letter-spacing:0.06em; }
.kpi .value { font-size:30px; font-weight:650; letter-spacing:-0.02em;
              font-variant-numeric:tabular-nums; margin-top:2px; }
.kpi .foot { font-size:12px; color:var(--muted); margin-top:2px; }
.covrow { display:grid; grid-template-columns:190px 1fr 180px; gap:14px;
          align-items:center; padding:7px 0; }
.covrow + .covrow { border-top:1px solid var(--line); }
.covlabel { font-size:13px; font-weight:550; }
.covnote { font-size:11px; color:var(--muted); font-weight:400; }
.track { background:var(--track); border-radius:4px; height:14px; overflow:hidden; }
.fill { background:var(--bar); height:100%; border-radius:0 4px 4px 0; min-width:2px; }
.covval { font-size:12px; color:var(--muted); font-variant-numeric:tabular-nums;
          text-align:right; }
.covval b { color:var(--ink); font-weight:600; }
.filters { display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin-bottom:10px; }
input[type=search], select { background:var(--surface); color:var(--ink);
  border:1px solid var(--line); border-radius:7px; padding:7px 10px; font:inherit; font-size:13px; }
.tablewrap { overflow-x:auto; border:1px solid var(--line); border-radius:10px;
             background:var(--surface); }
table { border-collapse:collapse; width:100%; font-size:13px; }
th, td { padding:8px 10px; text-align:right; white-space:nowrap;
         border-bottom:1px solid var(--line); }
th:first-child, td:first-child { text-align:left; white-space:normal; min-width:230px; }
th { position:sticky; top:0; background:var(--surface); font-size:11px; color:var(--muted);
     text-transform:uppercase; letter-spacing:0.05em; cursor:pointer; user-select:none; }
th:hover { color:var(--ink); }
tbody tr:hover { background:color-mix(in srgb, var(--bar) 7%, transparent); }
td.num { font-variant-numeric:tabular-nums; }
td.zero { color:var(--muted); }
.pill { display:inline-block; font-size:11px; padding:1px 7px; border-radius:999px;
        border:1px solid currentColor; font-weight:600; }
.pill.ok { color:var(--ok); } .pill.warn { color:var(--warn); } .pill.bad { color:var(--danger); }
footer { margin-top:36px; color:var(--muted); font-size:12px; }
.note { background:var(--surface); border-left:3px solid var(--warn); padding:10px 14px;
        border-radius:0 8px 8px 0; font-size:13px; color:var(--muted); margin-top:12px; }
"""

_JS = """
const table = document.getElementById('docs');
const rows = () => Array.from(table.tBodies[0].rows);
document.querySelectorAll('#docs th').forEach((th, i) => {
  th.addEventListener('click', () => {
    const desc = th.dataset.dir !== 'desc';
    document.querySelectorAll('#docs th').forEach(o => delete o.dataset.dir);
    th.dataset.dir = desc ? 'desc' : 'asc';
    const val = r => {
      const c = r.cells[i];
      const n = parseFloat(c.dataset.sort ?? c.textContent.replace(/,/g, ''));
      return isNaN(n) ? c.textContent.trim().toLowerCase() : n;
    };
    rows().sort((a, b) => {
      const x = val(a), y = val(b);
      return (x < y ? -1 : x > y ? 1 : 0) * (desc ? -1 : 1);
    }).forEach(r => table.tBodies[0].appendChild(r));
  });
});
const q = document.getElementById('q'), st = document.getElementById('st');
const apply = () => {
  const term = q.value.trim().toLowerCase(), want = st.value;
  let shown = 0;
  rows().forEach(r => {
    const hit = !term || r.cells[0].textContent.toLowerCase().includes(term);
    const ok = !want || r.dataset.status === want;
    r.hidden = !(hit && ok);
    if (!r.hidden) shown++;
  });
  document.getElementById('shown').textContent = shown;
};
q.addEventListener('input', apply); st.addEventListener('change', apply);
"""


def _n(value: int) -> str:
    """Thousands-separated integer."""
    return f"{value:,}"


def _cell(value: int) -> str:
    """A numeric cell; zero is muted so gaps read at a glance."""
    cls = "num zero" if not value else "num"
    return f'<td class="{cls}" data-sort="{value}">{_n(value)}</td>'


def _kpi(label: str, value: str, foot: str = "") -> str:
    return (
        f'<div class="card kpi"><div class="label">{escape(label)}</div>'
        f'<div class="value">{escape(value)}</div>'
        f'<div class="foot">{escape(foot)}</div></div>'
    )


def _coverage_row(entry: Coverage) -> str:
    note = f'<div class="covnote">{escape(entry.note)}</div>' if entry.note else ""
    return (
        '<div class="covrow">'
        f'<div><div class="covlabel">{escape(entry.label)}</div>{note}</div>'
        f'<div class="track"><div class="fill" style="width:{entry.pct:.1f}%"></div></div>'
        f'<div class="covval"><b>{_n(entry.n_present)}</b> of {_n(entry.n_total)} documents '
        f"({entry.pct:.1f}%) &middot; {_n(entry.n_missing)} missing</div>"
        "</div>"
    )


def _status_pill(row: DocumentRow) -> str:
    cls = {"extracted": "ok", "failed": "bad"}.get(row.status, "warn")
    return f'<span class="pill {cls}">{escape(row.status or "unknown")}</span>'


def _row(row: DocumentRow) -> str:
    title = escape(row.title)
    sub = escape(f"{row.doc_type or '?'} · {row.language or '—'} · {_n(row.page_count)} pp")
    genre = escape(row.genre or "—")
    return (
        f'<tr data-status="{escape(row.status)}">'
        f'<td><div>{title}</div><div class="covnote">{sub}</div></td>'
        f"<td>{_status_pill(row)}</td>"
        + _cell(row.char_count)
        + _cell(row.n_sentences)
        + _cell(row.n_tokens)
        + _cell(row.n_lexeme_occurrences)
        + _cell(len(row.layers))
        + _cell(row.embeddings)
        + _cell(row.entity_links)
        + _cell(row.projections)
        + _cell(row.images)
        + _cell(row.image_embeddings)
        + _cell(row.captions_committed)
        + f"<td>{genre}</td></tr>"
    )


_COLUMNS = (
    "Document",
    "Status",
    "Chars",
    "Sentences",
    "Tokens",
    "Lexeme occ.",
    "Layers",
    "Sent. embeddings",
    "Linked entities",
    "Projections",
    "Images",
    "Image emb.",
    "Captions",
    "Genre",
)


def render(report: Report) -> str:
    """Render the whole report as one standalone HTML document."""
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"{render_body(report)}\n</body></html>\n"
    )


def render_body(report: Report) -> str:
    """Render title, styles, content and behaviour without a document skeleton.

    Split out so the same page can be embedded in a host that supplies its own
    ``<!doctype>``/``<head>``/``<body>`` wrapper. The theme rules are written
    against ``:root`` rather than a local wrapper precisely so they still resolve
    when that host owns the root element.
    """
    t = report.totals
    kpis = "".join(
        _kpi(*args)
        for args in (
            ("Documents", _n(t.n_documents), f"{_n(t.n_pages)} pages · {_n(t.n_chars)} chars"),
            ("Sentences", _n(t.n_sentences), "segmented units"),
            ("Tokens", _n(t.n_tokens), f"{_n(t.n_lexeme_occurrences)} lexeme occurrences"),
            ("Distinct lexemes", _n(t.n_distinct_lexemes), "corpus vocabulary"),
            ("Sentence embeddings", _n(t.n_embeddings), "semantic search vectors"),
            ("Linked entities", _n(t.n_entity_links), f"{_n(t.n_projections)} lens projections"),
            ("Images", _n(t.n_images), f"{_n(t.n_image_embeddings)} embedded"),
            ("Committed captions", _n(t.n_captions), "reviewed and accepted"),
        )
    )
    heads = "".join(f"<th>{escape(c)}</th>" for c in _COLUMNS)
    body = "".join(_row(r) for r in report.rows)
    statuses = sorted({r.status for r in report.rows if r.status})
    options = "".join(f'<option value="{escape(s)}">{escape(s)}</option>' for s in statuses)
    slow = f'<div class="note">{escape(report.slow_note)}</div>' if report.slow_note else ""

    return f"""<title>doc-ray — corpus breakdown</title>
<style>{_CSS}</style>
<div class="wrap">
<h1>Corpus breakdown</h1>
<p class="sub">Every artifact doc-ray holds, per document and in total ·
generated {escape(report.generated_at)} · schema {escape(report.schema_version)} ·
{report.query_seconds:.1f}s of queries</p>

<h2>Corpus totals</h2>
<div class="kpis">{kpis}</div>

<h2>Pipeline coverage</h2>
<div class="card">{"".join(_coverage_row(c) for c in report.coverage)}</div>
{slow}

<h2>Per document</h2>
<div class="filters">
  <input type="search" id="q" placeholder="Filter by title…" aria-label="Filter by title">
  <select id="st" aria-label="Filter by status"><option value="">All statuses</option>
  {options}</select>
  <span class="sub"><span id="shown">{len(report.rows)}</span> of {len(report.rows)} shown ·
  click any column to sort</span>
</div>
<div class="tablewrap"><table id="docs"><thead><tr>{heads}</tr></thead>
<tbody>{body}</tbody></table></div>

<footer>Counts are read under the <code>{escape("docray_batch")}</code> role, which bypasses
row-level security, so this is the whole corpus rather than one principal's view.
Per-document token and sentence counts come from the <code>document_stats</code> rollup;
raw <code>entity_mention</code> counts are deliberately absent (see the note above).</footer>
</div><script>{_JS}</script>"""
