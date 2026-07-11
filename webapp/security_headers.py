"""Baseline security response headers for the Flask browser (BL-104).

CSP allows 'unsafe-inline' for script/style: the reader/theme-toggle scripts
and dozens of inline `style=` attributes across templates are pre-existing,
and rewriting all of them to eliminate inline script/style entirely is a much
larger refactor than this header hook — every other directive is as strict as
a server-rendered app with Google Fonts allows.
"""

from __future__ import annotations

CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "frame-ancestors 'none'"
)


def apply_security_headers(response):
    """Set CSP/X-Frame-Options/X-Content-Type-Options/Referrer-Policy on *response*."""
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response
