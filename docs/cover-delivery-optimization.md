---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 (1M context) via Claude Code"
  date: "2026-07-15"
---

# Plan — Cover / thumbnail delivery optimization

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

## Problem (measured, not assumed)

The corpus, collection, and document-list surfaces render a first-page cover in
a small `.doc-preview` cell, but the browser downloads the **full-resolution**
rendered page and scales it down in CSS.

| Signal | Measured value |
| --- | --- |
| Render DPI (`downloads.py:_PDF_PAGE_RENDER_DPI`) | **144** |
| Rendered pixels (letter page) | **1224 × 1584** (~1.9 MP) |
| Bytes on the wire per cover (PNG) | **~2.3 MB** |
| Displayed size (`.doc-preview`: `4.8rem`, `aspect-ratio: 3/4`) | **~77 × 102 CSS px** (≈154 × 205 @2×) |
| Over-fetch | **~250× the displayed pixels, ~150× the bytes** |

A 20-row list therefore ships **~46 MB** of covers to paint 20 postage-stamp
thumbnails; through the Cloudflare Tunnel this is the dominant page weight and the
main cause of slow list rendering on the corpus surface.

Note the render is already **cached** in Spaces
(`rendered-pages/{version}/p{n}-dpi144.png`) and served with
`Cache-Control: public, max-age=86400`. So this is **not** a re-render problem —
it is a **transfer-size** problem: every cold cache / new viewer pulls 2.3 MB per
cover, and the browser discards ~99.6% of those pixels.

## Goals

- Cut cover transfer for list surfaces by **~99%** (target ≤ ~20 KB/cover).
- Keep the **full-resolution** render for the document reader / overlay views
  (they legitimately need it).
- Reuse the existing content-versioned cache + `Cache-Control` machinery; add
  edge caching at Cloudflare.
- No new heavy dependency (PyMuPDF already renders; Pillow is available for
  resize/encode; both are in the image).

## Non-goals

- Client-side image processing (keep rendering server-side, cached).
- Changing the reader's full-page rendering or the overlay-on-original work.
- A generic image-proxy/CDN service — scope is doc-ray's own covers.

## Design

### 1. A size- and format-parameterized cover, cached per variant

Add optional `w` (target CSS width) and `fmt` (`webp`|`png`) to the page-image
endpoint, or a dedicated `GET /documents/{id}/cover.webp?w=320` route. Each
variant is a **separate cache object**, keyed by content version + width +
format, so variants never collide and stay immutable:

```
rendered-pages/{version}/cover-w{width}-{dpr}x.{fmt}   # e.g. cover-w160-2x.webp
rendered-pages/{version}/p{n}-dpi144.png               # unchanged (reader/full)
```

Allow only a **small allowlist** of widths (e.g. `160`, `320`, `640`) so the
cache can't be fanned out by arbitrary `w=` values (a cheap cache-poisoning /
storage-amplification guard); clamp anything else to the nearest allowed size.

### 2. Render small, encode efficiently

- **Render at the target size, not downscale from 144 DPI.** PyMuPDF renders
  directly at any scale: `page.get_pixmap(matrix=fitz.Matrix(z, z))` with
  `z = target_px / page_point_width`, so a 320-px cover renders ~20× fewer pixels
  than the 144-DPI page (cheaper CPU *and* smaller output). Downscaling the cached
  full render with Pillow is the fallback when the full render is already warm.
- **Encode WebP** (`pixmap.tobytes("webp")` or Pillow `save(…, "WEBP", quality=80,
  method=6)`). WebP at q80 for a 320-px cover is ~8–20 KB vs ~2.3 MB PNG.
- Serve `fmt=png` only when a client can't take WebP (rare; content-negotiate on
  `Accept: image/webp` or explicit `fmt`).

### 3. Responsive markup (the webapp side)

`previews.py` emits a `srcset` so the browser picks 1×/2× for the actual cell,
and the reader keeps requesting the full page:

```html
<img
  src="{{ cover(w=160) }}"
  srcset="{{ cover(w=160) }} 1x, {{ cover(w=320) }} 2x"
  sizes="4.8rem" alt="…" loading="lazy" decoding="async" width="77" height="102">
```

`loading="lazy"` is already present; add `decoding="async"` and explicit
`width`/`height` to kill layout shift.

### 4. Caching all the way down

- Keep `Cache-Control: public, max-age=…`; bump to `max-age=31536000, immutable`
  because the key already embeds the content **version** (a re-extract changes the
  version → new URL, so the old one can be cached forever).
- The URL is content-versioned, so **Cloudflare edge-caches** each cover after the
  first request — subsequent viewers never touch the origin. This is the biggest
  real-world win now that ingress is the tunnel.
- Spaces object cache stays as the origin store (unchanged).

### 5. Warm the cache on ingest (optional, phase 2)

The pipeline already renders/persists the full page on demand. Add cover-variant
generation to the extract/ingest completion (a `render_cover` step or a
first-view side effect) so the first list view is never cold. Cheap: one small
`get_pixmap` per document at ingest.

## Implementation phases

| Phase | Change | Payoff |
| --- | --- | --- |
| **1** | Cover variant endpoint (`w`/`fmt`, allowlisted widths) rendering small WebP, cached per variant; `previews.py` requests `w=160`/`320`; `immutable` cache header. | **~99% smaller list transfer**, no schema change |
| **2** | Responsive `srcset`/`sizes`/`width`/`height` in the list templates. | Correct DPR selection, no layout shift |
| **3** | Confirm Cloudflare edge-caches the versioned URLs (public + immutable). | Origin offload; repeat viewers hit the edge |
| **4** | Pre-generate the `w=320` cover on ingest completion. | No cold-cache stall on first list view |

## Expected impact

- Per-cover transfer: **~2.3 MB → ~15 KB** (~150×; ~99.3% reduction).
- 20-row corpus page: **~46 MB → ~0.3 MB**.
- Origin CPU: lower (renders ~20× fewer pixels than the 144-DPI page).
- Full-res reader render: unchanged.

## Testing (regression + verification)

- **Unit:** width allowlist clamps out-of-range `w`; cache key includes
  version+width+format; WebP magic bytes on the response; `fmt=png` fallback path.
- **Contract:** cover variant is authorized by the same RLS path as the full page
  (a caller who can't read the document gets 404, not a leaked thumbnail) — reuse
  the auth-forwarding guards.
- **Byte-budget guard:** a rendered `w=320` cover for the fixture PDF stays under a
  small ceiling (e.g. ≤ 40 KB), so a regression that re-serves the full page fails
  the test.
- **Visual:** `scripts/visual_check.py` corpus shots stay pixel-stable (the cell
  size is unchanged; only the source resolution shrinks).

## Risks / notes

- **Cache-key fan-out** — mitigated by the width allowlist.
- **WebP support** — universal in current browsers; keep the `png` fallback for
  non-`Accept: image/webp` callers.
- **Version derivation** — the cover key must change when the document is
  re-extracted; reuse the existing `{version}` the full-page key already uses so
  the two stay consistent.
- This plan is delivery-only; it does not touch extraction, annotation, or the
  overlay-on-original feature.
