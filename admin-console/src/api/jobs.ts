/**
 * Job queue reads + admin operations.
 *   • jobs(documentId, limit, after)                 — query.py
 *   • cancelJob(jobId, reason)         [admin-gated]  — mutation.py::cancel_job
 *   • reextractDocuments(onlyMissingGeometry) [admin] — mutation.py::reextract_documents
 */
import { ApiClient } from "./client";
import type { Job } from "./types";

const JOB_FIELDS = `
  id kind status documentId createdAt startedAt finishedAt
  attempts maxAttempts error progressCurrent progressTotal progressStage
`;

export async function listJobs(
  client: ApiClient,
  vars: { documentId?: string; limit?: number; after?: string } = {},
): Promise<Job[]> {
  const data = await client.graphql<{ jobs: Job[] }>(
    `query Jobs($documentId: ID, $limit: Int, $after: String) {
       jobs(documentId: $documentId, limit: $limit, after: $after) { ${JOB_FIELDS} }
     }`,
    { documentId: vars.documentId ?? null, limit: vars.limit ?? 20, after: vars.after ?? null },
  );
  return data.jobs;
}

export async function cancelJob(client: ApiClient, jobId: string, reason?: string): Promise<Job> {
  const data = await client.graphql<{ cancelJob: Job }>(
    `mutation Cancel($jobId: ID!, $reason: String) {
       cancelJob(jobId: $jobId, reason: $reason) { ${JOB_FIELDS} }
     }`,
    { jobId, reason: reason ?? null },
  );
  return data.cancelJob;
}

export async function reextractAllDocuments(
  client: ApiClient,
  onlyMissingGeometry = true,
): Promise<Job[]> {
  const data = await client.graphql<{ reextractDocuments: Job[] }>(
    `mutation Reextract($onlyMissingGeometry: Boolean!) {
       reextractDocuments(onlyMissingGeometry: $onlyMissingGeometry) { ${JOB_FIELDS} }
     }`,
    { onlyMissingGeometry },
  );
  return data.reextractDocuments;
}
