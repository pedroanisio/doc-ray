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

/**
 * Whether this deployment has the in-app backup subsystem switched on
 * (`DOCRAY_BACKUP_ENABLED`, surfaced as `Query.backupEnabled`).
 *
 * This is a capability hint used to decide whether to *offer* the actions —
 * never a substitute for the server-side check. createBackup/restoreBackup
 * enforce the same flag and additionally require an admin principal.
 */
export async function fetchBackupEnabled(client: ApiClient): Promise<boolean> {
  const data = await client.graphql<{ backupEnabled: boolean }>(`query { backupEnabled }`);
  return data.backupEnabled;
}

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
