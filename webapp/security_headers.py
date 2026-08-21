"""Baseline security response headers for the Flask browser (BL-104).

``style-src`` is strict on every screen but the document reader.

That exception is not a leftover: it is the one place where the geometry
genuinely cannot leave the element. ``webapp.overlay_web.overlay_box_style``
computes a ``left/top/width/height`` for every word box on a rendered PDF page
from its OCR bounding box, and a page carries hundreds of them. Percentages
derived per element cannot be expressed as stylesheet rules, CSP hashes do not
apply to style *attributes*, and the same route also renders extracted source
markup verbatim, which brings its own author styles. So the reader keeps
``'unsafe-inline'`` for style and every other screen does not.

Everything else that used to need it is gone: the eleven per-template
``<style>`` blocks and ninety-nine ``style=`` attributes were replaced by rules
and utility classes in ``webapp/static/design-system.css``, and the two
data-driven proportion bars became SVG, where geometry is markup rather than
CSS (see the ``meter`` component).

``script-src`` still allows inline script everywhere: the pre-paint theme
script, the reader's keyboard handlers, and several ``onclick`` attributes are
inline by design, and removing them means threading a per-response nonce through
every template. That is a separate change, and pretending otherwise by tightening
the directive would only break the pages.
"""

from __future__ import annotations

_BASE = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "frame-ancestors 'none'"
)

#: Google Fonts serves the webfont CSS from this origin, so it is a stylesheet
#: *host* allowance and has nothing to do with inline style.
_STYLE_HOST = "https://fonts.googleapis.com"

#: Endpoints that render per-element geometry or third-party markup and therefore
#: need inline style. Keep this as short as the product allows: every name here
#: is a screen where a style-injection bug would not be caught by the policy.
INLINE_STYLE_ENDPOINTS = frozenset({"document"})

CSP = f"{_BASE}; style-src 'self' {_STYLE_HOST}"
CSP_INLINE_STYLE = f"{_BASE}; style-src 'self' 'unsafe-inline' {_STYLE_HOST}"


def csp_for_endpoint(endpoint: str | None) -> str:
    """Return the policy for *endpoint*: strict unless it needs inline style."""
    return CSP_INLINE_STYLE if endpoint in INLINE_STYLE_ENDPOINTS else CSP


def apply_security_headers(response):
    """Set CSP/X-Frame-Options/X-Content-Type-Options/Referrer-Policy on *response*."""
    from flask import request

    response.headers["Content-Security-Policy"] = csp_for_endpoint(request.endpoint)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response
