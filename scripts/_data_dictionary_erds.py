"""ERD (mermaid) diagrams for the data dictionary — split from
_data_dictionary_content.py to keep both files within the D-015 cap. Pure data.
"""

from __future__ import annotations

ERDS = [
    (
        "Document entity, 4NF satellites & provenance",
        """\
erDiagram
    documents ||--o| document_content : "1:1 text + FTS"
    documents ||--o{ document_authors : ""
    authors   ||--o{ document_authors : ""
    documents ||--o{ document_tags : ""
    tags      ||--o{ document_tags : ""
    documents ||--o{ document_identifiers : "(scheme,value)"
    documents ||--o{ document_metadata : "(key,value)"
    documents ||--o{ document_pages : "page geometry"
    documents ||--o{ document_provenance : "events"
    document_provenance ||--o{ provenance_params : "(key,value)"
    documents ||--o| documents : "derived_from"
""",
    ),
    (
        "Segmentation, span registry & the annotation backbone",
        """\
erDiagram
    documents   ||--o{ spans : "owns offsets"
    documents   ||--o{ sentences : ""
    documents   ||--o{ annotations : "analyzer runs"
    spans       ||--o{ sentences : "span_id"
    annotations ||--o{ lexeme_occurrences : ""
    spans       ||--o{ lexeme_occurrences : "span_id"
    lexemes     ||--o{ lexeme_occurrences : "type identity"
""",
    ),
    (
        "Tokens, morphology & syntax",
        """\
erDiagram
    annotations ||--o{ tokens : ""
    spans       ||--o{ tokens : "span_id"
    sentences   ||--o{ tokens : "sentence_id (SET NULL)"
    lexemes     ||--o{ tokens : "lemma+UPOS"
    tokens      ||--o{ token_feature : "UD FEATS"
    tokens      ||--o{ subword : "BPE/WordPiece"
    tokens      ||--o{ dependency : "dependent/head"
    annotations ||--o{ dependency : ""
    annotations ||--o{ chunk : "NP/VP/PP"
    annotations ||--o{ constituent : "tree"
    constituent ||--o{ constituent : "parent_id"
""",
    ),
    (
        "Entities, reference, meaning, temporal & embeddings",
        """\
erDiagram
    annotations   ||--o{ entity_mention : ""
    entity_mention ||--o{ entity_link : "KB id"
    annotations   ||--o{ coref_chain : ""
    coref_chain   ||--o{ coref_mention : ""
    annotations   ||--o{ srl_predicate : ""
    srl_predicate ||--o{ srl_argument : "ARG*"
    tokens        ||--o{ word_sense : ""
    annotations   ||--o{ amr_node : ""
    amr_node      ||--o{ amr_edge : "role"
    annotations   ||--o{ timex : ""
    annotations   ||--o{ event : ""
    timex         ||--o{ tlink : "source/target"
    event         ||--o{ tlink : "source/target"
    spans         ||--o{ embedding : "vector(768)"
""",
    ),
    (
        "Canonical entities, resolution workflow & curation",
        """\
erDiagram
    canonical      ||--o{ form_index : "normalized surface"
    canonical      ||--o{ canonical_merge : "into_id (append-only)"
    canonical      ||--o{ resolution_review : "proposed_canonical_id"
    entity_mention ||--o{ resolution_review : "ambiguous link"
    principals     ||--o{ resolution_review : "reviewer"
    principals     ||--o{ canonical_merge : "actor"
    documents      ||--o{ seed_suggestion : "per-document seeds"
    documents      ||--o{ document_metadata_suggestion : "title/desc proposals"
    document_collections ||--o{ document_collection_members : ""
    documents      ||--o{ document_collection_members : ""
""",
    ),
    (
        "Jobs & operations",
        """\
erDiagram
    documents ||--o{ jobs : "document_id (NULL = corpus-wide)"
    jobs      }o--o| jobs : "chain (payload)"
    backups   ||..o{ jobs : "created by backup/restore kinds"
""",
    ),
    (
        "Access control (RBAC) — decided per document, inherited via RLS",
        """\
erDiagram
    tenants          ||--o{ principals : ""
    tenants          ||--o{ documents : ""
    principals       ||--o{ principal_roles : ""
    roles            ||--o{ principal_roles : ""
    roles            ||--o{ role_permissions : ""
    permissions      ||--o{ role_permissions : ""
    principals       ||--o{ document_acl : "grantee"
    documents        ||--o{ document_acl : "resource"
    permissions      ||--o{ document_acl : "action"
    principals       ||--o{ document_provenance : "actor_principal_id"
""",
    ),
    (
        "Authentication & identity provisioning",
        """\
erDiagram
    principals ||--o| principal_credentials : "argon2id (1:1)"
    tenants    ||--o{ principal_credentials : "(tenant, email) unique"
    principals ||--o{ email_verification_tokens : "hashed, expiring"
    principals ||--o{ auth_refresh_tokens : "hashed, rotating"
    tenants    ||--o{ scim_groups : ""
    scim_groups ||--o{ scim_group_members : ""
    principals ||--o{ scim_group_members : ""
    tenants    ||--o{ tenant_saml_identity_providers : "SAML IdP config"
""",
    ),
]
