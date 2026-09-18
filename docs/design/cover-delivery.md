---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 5 (1M context) via Claude Code"
  date: "2026-09-18"
doc_type: design
status: current
---

# Cover and thumbnail delivery

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

Design record for how list surfaces (corpus, collection, document list) obtain
a document's first-page cover without downloading the full-resolution page
render. Implemented in `src/doc_ray/entrypoints/api/downloads.py` and
`webapp/previews.py`; guarded by `tests/test_previews_cover.py` and the cover
cases in `src/doc_ray/tests/test_api_document_download.py`.

## The problem this solves

The full page render used by the reader is produced at 144 DPI (a letter page
is ~1.9 megapixels, ~2 MB as PNG). List surfaces display the cover in a
`.doc-preview` cell of roughly 77 × 102 CSS pixels. Serving the reader's render
to a list therefore over-fetches by two orders of magnitude per row, and a
20-row list becomes tens of megabytes of covers — the dominant page weight on
the corpus surface. The render is already cached per content version, so the
issue is transfer size, not re-rendering.

## Design

### 1. A size- and format-parameterised cover, cached per variant

The page-image route accepts an optional target width `w` and a format
(`webp`, default, or `png`). Each variant is a **separate cache object** keyed by
content version, width and format, so variants never collide and remain
immutable:

```
rendered-pages/{version}/cover-w{width}-{dpr}x.{fmt}   # e.g. cover-w160-2x.webp
rendered-pages/{version}/p{n}-dpi144.png               # unchanged (reader / full page)
```

Only a **small allow-list of widths** is honoured (`160`, `320`, `640`); any
other value is clamped to the nearest allowed size. This bounds the cache
fan-out an arbitrary `w=` could otherwise cause (storage amplification and
cache poisoning).

### 2. Render small, encode efficiently

- **Render at the target size rather than downscaling the 144-DPI page.**
  PyMuPDF renders at any scale (`page.get_pixmap(matrix=fitz.Matrix(z, z))`
  with `z = target_px / page_point_width`), so a 320-px cover is ~20× fewer
  pixels than the full render — cheaper CPU and smaller output. Downscaling
  the cached full render is the fallback when that render is already warm.
- **Encode WebP** (`quality=80`); a 320-px cover is ~8–20 KB. `png` is served
  only on explicit request or when the client cannot accept WebP.

### 3. Responsive markup

`webapp/previews.py` emits a `srcset` so the browser picks the 1× or 2× variant
for the actual cell size, while the reader continues to request the full page:

```html
<img src="{{ cover(w=160) }}"
     srcset="{{ cover(w=160) }} 1x, {{ cover(w=320) }} 2x"
     sizes="4.8rem" alt="…" loading="lazy" decoding="async" width="77" height="102">
```

`decoding="async"` and explicit `width`/`height` avoid layout shift.

### 4. Caching all the way down

- The URL embeds the content **version**, so a re-extract yields a new URL and
  the old one can be cached indefinitely: `Cache-Control: public,
  max-age=31536000, immutable`.
- Any edge cache in front of the origin (a CDN or tunnel) can therefore serve
  every cover after its first request; the object store remains the origin
  store for all variants.

### 5. Non-goals

- Client-side image processing — rendering stays server-side and cached.
- Changing the reader's full-page rendering or the overlay-on-original views.
- A generic image proxy — the scope is doc-ray's own covers.

[← root README](../../README.md) · [Architecture](../architecture.md)
