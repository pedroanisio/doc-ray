"""Ports — the hexagon's boundary contracts.

The application core depends on these interfaces; the driven adapters in
``doc_ray.adapters`` implement them (structurally — they are ``Protocol``s, so
no explicit subclassing is required). This is what lets extraction, storage,
persistence, and annotation be swapped without touching the domain.

    TextExtractor   <- adapters.extraction   (PyMuPDF / pypdf / txt / html / epub / docx)
    ObjectStore     <- adapters.object_store (MinIO / S3)
    Repository      <- adapters.persistence  (Postgres via psycopg)
    Annotator       <- domain.lexemes.RuleAnalyzer / adapters.annotation_spacy.SpacyAnnotator
"""

from doc_ray.ports.annotator import Annotator
from doc_ray.ports.object_store import ObjectStore
from doc_ray.ports.repository import Repository
from doc_ray.ports.text_extractor import ExtractedText, TextExtractor

__all__ = ["Annotator", "ObjectStore", "Repository", "ExtractedText", "TextExtractor"]
