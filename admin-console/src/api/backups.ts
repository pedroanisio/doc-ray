/**
 * Backup operations — mutation.py::create_backup / restore_backup, both
 * admin-gated (_require_admin). The worker performs the actual archive work;
 * these mutations only enqueue the job and return it.
 *
 * NOTE: there is no GraphQL/REST *list backups* endpoint — the webapp lists the
 * `backups` table via direct SQL. Listing therefore stays out of the console's
 * typed client until a backend query exists (see identity.ts for the same
 * honest-gap pattern). Do not fabricate a listBackups() call here.
 */
import { ApiClient } from "./client";
import type { Job } from "./types";

const JOB_FIELDS = `id kind status documentId createdAt startedAt finishedAt error`;

export async function createBackup(client: ApiClient, note?: string): Promise<Job> {
  const data = await client.graphql<{ createBackup: Job }>(
    `mutation CreateBackup($note: String) {
       createBackup(note: $note) { ${JOB_FIELDS} }
     }`,
    { note: note ?? null },
  );
  return data.createBackup;
}

export async function restoreBackup(client: ApiClient, backupId: string): Promise<Job> {
  const data = await client.graphql<{ restoreBackup: Job }>(
    `mutation RestoreBackup($backupId: ID!) {
       restoreBackup(backupId: $backupId) { ${JOB_FIELDS} }
     }`,
    { backupId },
  );
  return data.restoreBackup;
}
