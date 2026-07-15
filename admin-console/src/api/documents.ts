/**
 * Corpus document reads — query.py::documents. RLS in Postgres already scopes
 * what the authenticated principal may see; this is a read-only overview.
 */
import { ApiClient } from "./client";
import type { Document } from "./types";

const DOCUMENT_FIELDS = `
  id title docType status lifecycleState language charCount createdAt updatedAt
`;

export async function listDocuments(
  client: ApiClient,
  vars: { limit?: number; after?: string; docType?: string; status?: string } = {},
): Promise<Document[]> {
  const data = await client.graphql<{ documents: Document[] }>(
    `query Documents($limit: Int, $after: String, $docType: String, $status: String) {
       documents(limit: $limit, after: $after, docType: $docType, status: $status) { ${DOCUMENT_FIELDS} }
     }`,
    {
      limit: vars.limit ?? 50,
      after: vars.after ?? null,
      docType: vars.docType ?? null,
      status: vars.status ?? null,
    },
  );
  return data.documents;
}
