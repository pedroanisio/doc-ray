"""Job handlers: one function per job kind, plus the dispatch registry.

Split out of :mod:`doc_ray.application.jobs` (D-015). Handlers lazily import
the blocking machinery (spaCy, the ingest pipeline, backup) so this module
stays importable without those heavy dependencies present; ``jobs.run_once``
routes claimed jobs here through :data:`HANDLERS`.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import psycopg

from doc_ray.application.document_stats import refresh_document_stats as _refresh_document_stats
from doc_ray.application.semantic_embed import handle_semantic_embed
from doc_ray.domain.jobs import (
    KIND_ADD_DOCUMENT_TAGS,
    KIND_ANNOTATE,
    KIND_APPLY_CORRECTIONS,
    KIND_ARCHIVE_DOCUMENT,
    KIND_BACKUP,
    KIND_CANONICALIZE,
    KIND_DELETE,
    KIND_INGEST,
    KIND_REANNOTATE,
    KIND_REEXTRACT,
    KIND_RESTORE,
    KIND_RESTORE_DOCUMENT,
    KIND_SEED_APPROVED,
    KIND_SEED_CANONICAL,
    KIND_SEED_SUGGEST,
    KIND_SEMANTIC_EMBED,
    KIND_SEMANTIC_ENRICH,
    KIND_SUGGEST_DOCUMENT_METADATA,
    KIND_UPDATE_METADATA,
    KIND_VISUAL_CAPTION,
    KIND_VISUAL_EMBED,
    KIND_VISUAL_EXTRACT,
    Job,
)
from doc_ray.domain.progress import report_progress
from doc_ray.ports.object_store import ObjectStore

if TYPE_CHECKING:
    # Concrete adapter type for the (path-based) pipeline, imported for typing only
    # so this module stays importable without boto3.
    from doc_ray.adapters.object_store import ObjectStore as S3ObjectStore

_logger = logging.getLogger("doc_ray.jobs")

#: A handler runs one job and returns a JSON-able result recorded on the job row.
Handler = Callable[[psycopg.Connection[Any], ObjectStore | None, Job], dict[str, Any]]


def handle_delete(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Soft-delete a document so it can be restored individually (BL-059)."""
    from doc_ray.adapters import persistence as db

    if job.document_id is None:
        raise RuntimeError("delete requires a document_id")
    db.set_document_lifecycle(
        cast(db.DictConnection, conn),
        job.document_id,
        "deleted",
        actor=job.payload.get("actor"),
    )
    return {"deleted": job.document_id, "soft": True}


def handle_archive_document(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Archive a document without changing its ingestion status."""
    from doc_ray.adapters import persistence as db

    if job.document_id is None:
        raise RuntimeError("archive_document requires a document_id")
    changed = db.set_document_lifecycle(
        cast(db.DictConnection, conn),
        job.document_id,
        "archived",
        actor=job.payload.get("actor"),
    )
    return {"archived": job.document_id, "changed": changed}


def handle_restore_document(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Restore an archived or soft-deleted document to active lifecycle."""
    from doc_ray.adapters import persistence as db

    if job.document_id is None:
        raise RuntimeError("restore_document requires a document_id")
    db.set_document_lifecycle(
        cast(db.DictConnection, conn),
        job.document_id,
        "active",
        actor=job.payload.get("actor"),
    )
    return {"restored": job.document_id}


def handle_update_metadata(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Edit a document's title/description/tags, recording change history."""
    from doc_ray.adapters import persistence as db

    if job.document_id is None:
        raise RuntimeError("update_metadata requires a document_id")
    changed = db.update_document_metadata(
        cast(db.DictConnection, conn),
        job.document_id,
        title=job.payload.get("title"),
        description=job.payload.get("description"),
        tags=job.payload.get("tags"),
        authors=job.payload.get("authors"),
        actor=job.payload.get("actor"),
    )
    return {"changed": changed}


def handle_add_document_tags(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Merge tags into a document and record the BL-058 tags history row."""
    from doc_ray.adapters import persistence as db

    if job.document_id is None:
        raise RuntimeError("add_document_tags requires a document_id")
    changed = db.add_document_tags(
        cast(db.DictConnection, conn),
        job.document_id,
        [str(tag) for tag in job.payload.get("tags") or []],
        actor=job.payload.get("actor"),
    )
    return {"changed": changed}


def handle_reannotate(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Re-run linguistic annotation for a document (loads spaCy lazily)."""
    from doc_ray.application import reannotate as reann

    if job.document_id is None:
        raise RuntimeError("reannotate requires a document_id")
    lang = job.payload.get("lang", "en")
    report_progress(0, 1, "Re-annotating document")
    info = reann.reannotate_document(conn, job.document_id, cache={}, lang_default=lang)
    _refresh_document_stats(conn, job.document_id)
    return info or {"skipped": True}


def handle_reextract(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Backfill page/bbox geometry by re-reading a document's original blob."""
    from doc_ray.application import reextract

    if job.document_id is None:
        raise RuntimeError("reextract requires a document_id")
    if store is None:
        raise RuntimeError("reextract requires an object store")
    report_progress(0, 1, "Re-extracting document geometry")
    return reextract.backfill_document_geometry_from_store(conn, store, job.document_id)


def handle_ingest(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Ingest an uploaded blob: fetch bytes, run the path-based pipeline.

    The blob is materialised under the payload's ``original_filename`` (basename
    only — never a caller-controlled path) so the pipeline derives the document's
    title/filename from the *uploaded* name, not a random temp name. An explicit
    payload ``title`` overrides whatever the extractor derived.

    The payload's ``owner_principal_id`` is the uploader the API resolved at
    mutation time (migration 102) — the worker never derives ownership itself,
    because by the time the job runs there is no request principal to consult.
    """
    from doc_ray.application.integrity import verify_bytes
    from doc_ray.application.pipeline import ingest_file

    if store is None:
        raise RuntimeError("ingest requires an object store")
    report_progress(0, 3, "Reading upload")
    storage_key = str(job.payload["storage_key"])
    data = verify_bytes(
        store.get(storage_key),
        job.payload.get("sha256"),
        context=f"ingest upload {storage_key}",
    )
    report_progress(1, 3, "Preparing uploaded file")
    original = job.payload.get("original_filename") or f"upload{job.payload.get('extension', '')}"
    safe_name = Path(str(original)).name or "upload"
    # pipeline.ingest_file is path-based; materialise the blob under its real name so
    # the proven pipeline is reused unchanged (its re-PUT is content-addressed/idempotent).
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / safe_name
        tmp_path.write_bytes(data)
        # store is the concrete S3 adapter at runtime; the pipeline is typed against it.
        report_progress(2, 3, "Extracting and storing document")
        owner = job.payload.get("owner_principal_id")
        result = ingest_file(
            conn,
            cast("S3ObjectStore", store),
            tmp_path,
            owner_principal_id=str(owner) if owner else None,
        )

    title = job.payload.get("title")
    if title and result.status == "ingested" and result.document_id is not None:
        conn.execute(
            "UPDATE documents SET title = %s WHERE id = %s", (str(title), result.document_id)
        )
    if result.status == "ingested" and result.document_id is not None:
        _refresh_document_stats(conn, result.document_id)
    return {"document_id": result.document_id, "status": result.status}


def handle_annotate(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Run Stage-1 spaCy annotation (tokens/NER/deps/coref) for one document.

    This is the worker-invocable form of the ``annotate_spacy`` CLI — required so
    the deployed worker can produce the ``entity_mention`` rows the
    canonicalization stages consume (spaCy loads lazily; ~sm model per language).
    """
    from doc_ray.adapters import ner_corrections_store
    from doc_ray.adapters.annotation_spacy import SpacyAnnotator

    if job.document_id is None:
        raise RuntimeError("annotate requires a document_id")
    report_progress(0, 1, "Annotating document")
    info = SpacyAnnotator().annotate(
        conn, job.document_id, lang_default=str(job.payload.get("lang", "en"))
    )
    # PALS's Law: entity_mention is ephemeral (CASCADE from annotations), so the
    # fresh ML pass just discarded any human fixes. Re-apply the correction log
    # onto the new mentions before the read-model rollup sees them.
    if ner_corrections_store.apply_corrections_to_document(conn, job.document_id):
        info["ner_corrections_applied"] = True
    # Record the detected language on the document: the migration-016 trigger
    # then rebuilds the content tsvector with the language-aware config, so a
    # separate reannotate pass is no longer needed for stemmed search.
    lang = info.get("lang")
    if lang:
        conn.execute(
            "UPDATE documents SET language = %s WHERE id = %s AND language IS DISTINCT FROM %s",
            (lang, job.document_id, lang),
        )
    _refresh_document_stats(conn, job.document_id)
    return info


def handle_apply_corrections(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Re-apply the durable NER-correction log onto a document's ML mentions.

    Cheap: no spaCy, just the deterministic reconcile. Enqueued by the API's
    ``correctEntityMention`` mutation so a freshly recorded human fix takes
    effect without a full re-annotation (docray_api may record a correction but
    only the batch worker owns ``entity_mention``).
    """
    from doc_ray.adapters import ner_corrections_store

    if job.document_id is None:
        raise RuntimeError("apply_corrections requires a document_id")
    report_progress(0, 1, "Applying NER corrections")
    changed = ner_corrections_store.apply_corrections_to_document(conn, job.document_id)
    if changed:
        _refresh_document_stats(conn, job.document_id)
    return {"document_id": job.document_id, "corrections_applied": changed}


def handle_semantic_enrich(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Run deterministic discourse/time/sentiment/embedding enrichment."""
    from doc_ray.application import semantic_enrichment

    if job.document_id is None:
        raise RuntimeError("semantic_enrich requires a document_id")
    report_progress(0, 1, "Enriching semantic layers")
    return semantic_enrichment.annotate_document(conn, job.document_id)


def handle_visual_extract(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Prism Tier-1: detect image regions and store deterministic visual features."""
    from doc_ray.adapters.pdf_image_source_pymupdf import PyMuPDFImageSource
    from doc_ray.adapters.pdf_renderer_pymupdf import PyMuPDFRenderer
    from doc_ray.application import visual_extract

    if job.document_id is None:
        raise RuntimeError("visual_extract requires a document_id")
    if store is None:
        raise RuntimeError("visual_extract requires an object store")
    report_progress(0, 1, "Extracting visual features")
    return visual_extract.extract_document_visuals(
        conn,
        store,
        job.document_id,
        renderer=PyMuPDFRenderer(),
        image_source=PyMuPDFImageSource(),
    )


def handle_visual_embed(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Prism Tier-2: embed a document's images + sentences (GPU dual encoder)."""
    import os

    from doc_ray.adapters.vision_encoder_openclip import OpenClipVisionEncoder
    from doc_ray.application import visual_embed

    if job.document_id is None:
        raise RuntimeError("visual_embed requires a document_id")
    if store is None:
        raise RuntimeError("visual_embed requires an object store")
    encoder = OpenClipVisionEncoder(
        model_name=os.environ.get("DOCRAY_VISION_MODEL", "ViT-L-14"),
        pretrained=os.environ.get("DOCRAY_VISION_PRETRAINED", "openai"),
    )
    report_progress(0, 1, "Embedding document visuals")
    return visual_embed.embed_document_visuals(conn, store, job.document_id, encoder=encoder)


def handle_visual_caption(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Prism Tier-3: grounded VLM captions with the PALS commit/review gate."""
    from doc_ray.adapters.image_ocr_pymupdf import PyMuPDFImageOcr
    from doc_ray.adapters.vlm_ollama import OllamaVisionLanguageModel
    from doc_ray.application import visual_caption

    if job.document_id is None:
        raise RuntimeError("visual_caption requires a document_id")
    if store is None:
        raise RuntimeError("visual_caption requires an object store")
    report_progress(0, 1, "Captioning document images")
    return visual_caption.caption_document_images(
        conn,
        store,
        job.document_id,
        vlm=OllamaVisionLanguageModel(),
        image_ocr=PyMuPDFImageOcr(),
    )


def handle_seed_canonical(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Seed the canonical registry from Wikidata (payload: ``qids`` and/or ``terms``)."""
    from doc_ray.adapters.wikidata import WikidataClient
    from doc_ray.application import canonical_seed

    kb = WikidataClient()
    try:
        ids: list[int] = []
        if job.payload.get("qids"):
            ids += canonical_seed.seed_from_ids(conn, kb, list(job.payload["qids"]))
        if job.payload.get("terms"):
            ids += canonical_seed.seed_from_terms(conn, kb, list(job.payload["terms"]))
    finally:
        kb.close()
    return {"seeded": len(ids)}


def handle_seed_suggest(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Mine and KB-resolve a document's seed candidates into ``seed_suggestion``.

    Nothing is seeded here — rows land as ``pending`` for human curation (the
    webapp's audit screen). Payload knobs: ``limit``, ``min_mentions``,
    ``min_lexemes``.
    """
    from doc_ray.adapters.wikidata import WikidataClient
    from doc_ray.adapters.wikipedia import WikipediaClient
    from doc_ray.application import seed_suggest

    if job.document_id is None:
        raise RuntimeError("seed_suggest requires a document_id")
    report_progress(0, 1, "Mining seed candidates")
    wiki = WikipediaClient()
    kb = WikidataClient()
    try:
        return seed_suggest.suggest_for_document(
            conn,
            wiki,
            kb,
            job.document_id,
            limit=int(job.payload.get("limit", 40)),
            min_mentions=int(job.payload.get("min_mentions", 3)),
            min_lexemes=int(job.payload.get("min_lexemes", 10)),
        )
    finally:
        wiki.close()
        kb.close()


def handle_seed_approved(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Seed this document's curator-approved suggestions into the registry.

    Reads the approved+resolved QIDs, seeds them through the ordinary
    ``canonical_seed`` path, and advances those rows to ``seeded`` (final).
    """
    from doc_ray.adapters import seed_suggestion_store as sstore
    from doc_ray.adapters.wikidata import WikidataClient
    from doc_ray.application import canonical_seed

    if job.document_id is None:
        raise RuntimeError("seed_approved requires a document_id")
    report_progress(0, 1, "Seeding approved entities")
    qids = sstore.approved_qids(conn, job.document_id)
    if not qids:
        return {"seeded": 0}
    kb = WikidataClient()
    try:
        ids = canonical_seed.seed_from_ids(conn, kb, qids)
    finally:
        kb.close()
    sstore.mark_seeded(conn, job.document_id)
    return {"seeded": len(ids)}


def handle_suggest_document_metadata(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Generate verified document metadata suggestions for human review."""
    from doc_ray.application import document_metadata_suggest

    if job.document_id is None:
        raise RuntimeError("suggest_document_metadata requires a document_id")
    report_progress(0, 1, "Generating metadata suggestions")
    return document_metadata_suggest.generate_suggestions(
        cast(psycopg.Connection[dict[str, Any]], conn), job.document_id
    )


def handle_canonicalize(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Canonicalize a document's mentions: term scan + Stage-2 auto-link, then (opt-in) Stage 3.

    The registry term scan (Stage 1.5) runs first so technical vocabulary the
    registry knows — which NER never tags — becomes linkable mentions. Then the
    frontier resolver runs only when the payload sets ``resolve`` truthy —
    LLM cost is an explicit request, never a side effect. It processes only the
    Stage-2 residue (index-first, D-019) and its verdicts are validated by the
    adapter (PALS): uncommitted ones land in ``resolution_review``.
    """
    from doc_ray.application import canonicalize, term_scan

    if job.document_id is None:
        raise RuntimeError("canonicalize requires a document_id")
    report_progress(0, 2, "Scanning registry terms")
    terms = term_scan.scan_document(conn, job.document_id)
    report_progress(1, 2, "Linking entity mentions")
    result = canonicalize.autolink_document(conn, job.document_id)
    out: dict[str, Any] = {
        "term_mentions": terms,
        "linked": result.linked,
        "residue": len(result.residue),
    }
    if job.payload.get("resolve") and result.residue:
        report_progress(2, 3, "Resolving remaining mentions")
        from doc_ray.adapters.resolver_factory import make_resolver
        from doc_ray.application.resolution import resolve_residue

        outcome = resolve_residue(conn, make_resolver(), job.document_id, result.residue)
        out.update(
            {
                "resolved": outcome.linked + outcome.created,
                "created": outcome.created,
                "review": outcome.review,
            }
        )
    return out


def handle_backup(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Create a full backup archive (DB dump + object-store blobs)."""
    from doc_ray.application import backup as bk

    if store is None:
        raise RuntimeError("backup requires an object store")
    report_progress(0, 1, "Creating backup archive")
    summary = bk.create_backup(conn, store, note=job.payload.get("note"))
    # Keep the recorded result compact; the per-table breakdown lives in the archive.
    return {
        k: summary[k]
        for k in ("id", "object_key", "archive_bytes", "table_rows", "blob_count", "doc_count")
    }


def handle_restore(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Restore the corpus (DB data + blobs) from a catalogued backup archive."""
    from doc_ray.application import backup as bk

    if store is None:
        raise RuntimeError("restore requires an object store")
    backup_id = job.payload.get("backup_id")
    if not backup_id:
        raise RuntimeError("restore requires a backup_id")
    report_progress(0, 1, "Restoring backup archive")
    return bk.restore_backup(conn, store, str(backup_id))


HANDLERS: dict[str, Handler] = {
    KIND_ANNOTATE: handle_annotate,
    KIND_APPLY_CORRECTIONS: handle_apply_corrections,
    KIND_SEMANTIC_ENRICH: handle_semantic_enrich,
    KIND_SEMANTIC_EMBED: handle_semantic_embed,
    KIND_VISUAL_EXTRACT: handle_visual_extract,
    KIND_VISUAL_EMBED: handle_visual_embed,
    KIND_VISUAL_CAPTION: handle_visual_caption,
    KIND_REANNOTATE: handle_reannotate,
    KIND_REEXTRACT: handle_reextract,
    KIND_DELETE: handle_delete,
    KIND_ARCHIVE_DOCUMENT: handle_archive_document,
    KIND_RESTORE_DOCUMENT: handle_restore_document,
    KIND_INGEST: handle_ingest,
    KIND_SEED_CANONICAL: handle_seed_canonical,
    KIND_SEED_SUGGEST: handle_seed_suggest,
    KIND_SEED_APPROVED: handle_seed_approved,
    KIND_SUGGEST_DOCUMENT_METADATA: handle_suggest_document_metadata,
    KIND_CANONICALIZE: handle_canonicalize,
    KIND_BACKUP: handle_backup,
    KIND_RESTORE: handle_restore,
    KIND_UPDATE_METADATA: handle_update_metadata,
    KIND_ADD_DOCUMENT_TAGS: handle_add_document_tags,
}
