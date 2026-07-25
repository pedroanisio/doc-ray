"""Human briefing evidence surface backed only by the GraphQL contract."""

from __future__ import annotations

from types import ModuleType

from flask import current_app, redirect, request, url_for

_core: ModuleType

_BRIEFING_QUERY = """
query BriefingPack(
  $topic: String!
  $maxDocuments: Int!
  $passagesPerDocument: Int!
  $maxEntities: Int!
) {
  briefingPack(
    topic: $topic
    maxDocuments: $maxDocuments
    passagesPerDocument: $passagesPerDocument
    maxEntities: $maxEntities
  ) {
    topic retrieval guidance gaps
    coverage { documents passages }
    sources { documentId title rank cite { documentId sentenceOrdinal ref } }
    keyPassages {
      documentId title sentenceOrdinal text matchedTerms
      cite { documentId sentenceOrdinal ref }
    }
    entities { text entityType count }
  }
}
"""


def briefing():
    """Render an evidence pack, or redirect a POST to its shareable GET URL."""
    if request.method == "POST":
        return redirect(url_for("briefing", topic=request.form.get("topic", "").strip()))
    topic = request.args.get("topic", "", type=str).strip()
    pack = None
    notice = None
    if topic:
        try:
            data = _core.gql(
                _BRIEFING_QUERY,
                {
                    "topic": topic,
                    "maxDocuments": 6,
                    "passagesPerDocument": 4,
                    "maxEntities": 25,
                },
            )
            pack = data.get("briefingPack")
        except Exception as exc:  # noqa: BLE001 - API failure is a rendered state
            current_app.logger.warning("briefing unavailable: %s", exc)
            notice = "Briefing unavailable — the evidence service could not be reached."
    coverage = (pack or {}).get("coverage") or {"documents": 0, "passages": 0}
    gaps = (pack or {}).get("gaps") or []
    return _core.render_template(
        "briefing.html",
        topic=topic,
        pack=pack,
        coverage=coverage,
        gaps=gaps,
        notice=notice,
    )


def register(app, core: ModuleType) -> None:
    """Attach the briefing read route with late-bound webapp helpers."""
    global _core
    _core = core
    app.add_url_rule("/briefing", "briefing", briefing, methods=["GET", "POST"])
