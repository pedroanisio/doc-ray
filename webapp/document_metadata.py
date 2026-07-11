"""Document metadata edit — proxies to the updateDocumentMetadata mutation (BL-058).

The webapp never writes to the corpus directly; this builds the mutation
variables from the submitted form and lets the caller's ``gql`` do the actual
proxying, the same way ``upload_submit``/``backups_create`` proxy their writes.
"""

from __future__ import annotations

_MUTATION = """
    mutation($id: ID!, $title: String, $description: String, $tags: [String!]) {
      updateDocumentMetadata(
        documentId: $id, title: $title, description: $description, tags: $tags
      ) {
        id
      }
    }
"""


def metadata_update_variables(doc_id, form) -> dict:
    """Parse title/description/tags form fields into mutation variables."""
    variables: dict = {"id": str(doc_id)}
    title = (form.get("title") or "").strip() or None
    if title is not None:
        variables["title"] = title
    description = (form.get("description") or "").strip() or None
    if description is not None:
        variables["description"] = description
    tags_raw = form.get("tags")
    if tags_raw is not None:
        variables["tags"] = [t.strip() for t in tags_raw.split(",") if t.strip()]
    return variables


def submit_metadata_update(gql, doc_id, form) -> str | None:
    """Call the mutation via *gql*; return an error string on failure, else None."""
    try:
        gql(_MUTATION, metadata_update_variables(doc_id, form))
    except Exception as exc:
        return str(exc)
    return None
