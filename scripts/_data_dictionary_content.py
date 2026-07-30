"""Hand-authored content constants for the data-dictionary generator.

Split out of ``gen_data_dictionary.py`` (the generation logic) so that file
stays within the D-015 400-net-LOC cap. Pure data — no DB, no logic.
"""

from __future__ import annotations

# Owning-document path used by the RLS predicate for each table (for the dict).
RLS_PATH = {
    "documents": "id (self)",
    "document_content": "document_id",
    "document_authors": "document_id",
    "document_tags": "document_id",
    "document_identifiers": "document_id",
    "document_metadata": "document_id",
    "document_provenance": "document_id",
    "document_collection_members": "document_id",
    "document_pages": "document_id",
    "document_layout_runs": "document_id",
    "provenance_params": "event_id → document_provenance",
    "document_metadata_history": "document_id",
    "document_metadata_suggestion": "document_id",
    "sentences": "document_id",
    "spans": "document_id",
    "annotations": "document_id",
    "coref_chain": "document_id",
    "coref_mention": "span_id → spans",
    "lexeme_occurrences": "annotation_id → annotations",
    "tokens": "annotation_id → annotations",
    "token_feature": "token_id → tokens → annotations",
    "subword": "token_id → tokens → annotations",
    "chunk": "annotation_id → annotations",
    "constituent": "annotation_id → annotations",
    "dependency": "annotation_id → annotations",
    "entity_mention": "annotation_id → annotations",
    "resolution_review": "mention_id → entity_mention → annotations",
    "entity_mention_correction": "document_id",
    "document_classification": "document_id",
    "entity_projection": "document_id",
    "entity_projection_review": "document_id",
    "lens_entity_rollup": "lens_id → document_collection_members → documents",
    "lens_entity_relation": "lens_id → document_collection_members → documents",
    "lens_local_entity": "admin-only (batch-owned local-identity registry)",
    "entity_allusion": "both lenses via document_collection_members + evidence_document_id",
    "entity_link": "mention_id → entity_mention → annotations",
    "srl_predicate": "annotation_id → annotations",
    "srl_argument": "span_id → spans",
    "word_sense": "annotation_id → annotations",
    "amr_node": "annotation_id → annotations",
    "amr_edge": "parent_node_id → amr_node → annotations",
    "discourse_relation": "annotation_id → annotations",
    "timex": "annotation_id → annotations",
    "event": "annotation_id → annotations",
    "tlink": "annotation_id → annotations",
    "sentiment": "annotation_id → annotations",
    "embedding": "span_id → spans",
    "document_image": "document_id",
    "image_tier1_features": "document_id",
    "image_embedding": "document_id",
    "sentence_vision_embedding": "document_id",
    "visual_caption": "document_id",
    "visual_review": "document_id",
    "jobs": "document_id (admin-only when NULL)",
    "seed_suggestion": "document_id",
    "backups": "docray_is_admin()",
    "audit_events": "insert-only security audit log",
    "tenant_billing_accounts": "tenant_id = docray_current_tenant() and docray_is_admin()",
    "tenant_subscriptions": "tenant_id = docray_current_tenant() and docray_is_admin()",
    "stripe_webhook_events": "signed Stripe webhook ledger, app-owned",
}

# §9 migration narrative lives in its own module to stay within the D-015 cap;
# re-exported so the generator's import surface is unchanged.
# ---------------------------------------------------------------------------
# Curated metadata: how tables group into layers, and a one-line purpose each.
# (Table shapes/columns come from the catalog; this is just narrative + order.)
# §5 table grouping lives in its own module to stay within the D-015 cap;
# re-exported so the generator's import surface is unchanged.
from _data_dictionary_groups import GROUPS as GROUPS  # noqa: E402
from _data_dictionary_migrations import MIGRATIONS as MIGRATIONS  # noqa: E402
