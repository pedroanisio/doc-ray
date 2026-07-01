"""End-to-end ingest: read bytes -> hash -> store blob -> extract -> persist row."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from . import (
    db,
    extract as extract_mod,
    provenance as prov_mod,
    sentences as sent_mod,
)
from .config import Config
from .storage import ObjectStore


@dataclass
class IngestResult:
    path: str
    document_id: str | None
    status: str          # 'ingested' | 'duplicate' | 'failed'
    detail: str = ""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ingest_file(conn, store: ObjectStore, path: Path) -> IngestResult:
    data = path.read_bytes()
    sha = _sha256(data)

    # Dedupe: identical bytes are skipped.
    existing = db.find_by_sha256(conn, sha)
    if existing:
        return IngestResult(str(path), str(existing["id"]), "duplicate")

    ext = path.suffix
    # Content-addressed key keeps the bucket flat and collision-free.
    key = f"{sha[:2]}/{sha}{ext}"

    try:
        extracted = extract_mod.extract(data, filename=path.stem, extension=ext)
    except Exception as exc:  # extraction failed -> still record the blob + failure
        uri = store.put(key, data, "application/octet-stream")
        provenance = prov_mod.build_events(
            content_sha256=sha,
            source_filename=path.name,
            byte_size=len(data),
            storage_bucket=store.bucket,
            storage_key=key,
            storage_uri=uri,
            status="failed",
            error=str(exc),
        )
        doc_id = db.insert_document(
            conn,
            title=path.stem,
            doc_type=ext.lstrip(".") or "unknown",
            original_filename=path.name,
            source_uri=uri,
            content_sha256=sha,
            byte_size=len(data),
            page_count=None,
            full_text=None,
            status="failed",
            error=str(exc),
            provenance=provenance,
        )
        return IngestResult(str(path), doc_id, "failed", str(exc))

    uri = store.put(key, data, extracted.content_type)

    full_text = extracted.full_text or ""
    text_sha = _sha256(full_text.encode("utf-8"))
    sentences = sent_mod.build_sentences(full_text)
    provenance = prov_mod.build_events(
        content_sha256=sha,
        source_filename=path.name,
        byte_size=len(data),
        storage_bucket=store.bucket,
        storage_key=key,
        storage_uri=uri,
        status="extracted",
        extractor=extracted.extractor,
        extractor_version=extracted.extractor_version,
        page_count=extracted.page_count,
        char_count=len(full_text),
        text_sha256=text_sha,
        sentence_count=len(sentences),
    )
    doc_id = db.insert_document(
        conn,
        title=extracted.title,
        doc_type=extracted.doc_type,
        mime_type=extracted.content_type,
        original_filename=path.name,
        source_uri=uri,
        content_sha256=sha,
        byte_size=len(data),
        page_count=extracted.page_count,
        char_count=len(full_text),
        text_sha256=text_sha,
        full_text=extracted.full_text,
        status="extracted",
        authors=[extracted.author] if extracted.author else [],
        metadata=extracted.metadata,          # {pdf_producer, pdf_creator, ...}
        sentences=sentences,
        provenance=provenance,
    )
    return IngestResult(str(path), doc_id, "ingested",
                        f"{len(sentences)} sentences, {len(provenance)} prov")


def ingest_paths(cfg: Config, paths: list[Path]) -> list[IngestResult]:
    store = ObjectStore(cfg)
    store.ensure_bucket()
    results: list[IngestResult] = []
    with db.connect(cfg) as conn:
        for p in paths:
            results.append(ingest_file(conn, store, p))
    return results


def expand_paths(inputs: list[str], extensions: set[str]) -> list[Path]:
    """Expand files and directories into a flat list of supported files."""
    out: list[Path] = []
    for raw in inputs:
        p = Path(raw)
        if p.is_dir():
            for child in sorted(p.rglob("*")):
                if child.is_file() and child.suffix.lower() in extensions:
                    out.append(child)
        elif p.is_file():
            out.append(p)
    return out
