/**
 * Corpus maintenance operations: status, ad-hoc runs, and recurring schedules.
 *   • operations(onlyMissing)            [admin] — operations_query.py
 *   • operationSchedules                 [admin] — operations_query.py
 *   • runOperation(...)                  [admin] — operations_mutation.py
 *   • create/update/deleteOperationSchedule [admin] — operations_mutation.py
 *
 * Every field here is admin-gated server-side; the UI hides rather than guards.
 */
import { ApiClient } from "./client";
import type { Operation, OperationSchedule, OperationRun, CadenceInput } from "./types";

const OPERATION_FIELDS = `
  key label jobKind lane summary completeness expensive defaultPriority
  pendingDocuments pendingUnits unitLabel activeJobs
  lastStatus lastFinishedAt lastError
`;

const SCHEDULE_FIELDS = `
  id operationKey onlyMissing priority enabled note
  nextRunAt lastRunAt lastJobCount lastError createdAt updatedAt
  cadence { kind everyMinutes hour minute weekday dayOfMonth description }
`;

export async function listOperations(
  client: ApiClient,
  onlyMissing = true,
): Promise<Operation[]> {
  const data = await client.graphql<{ operations: Operation[] }>(
    `query Operations($onlyMissing: Boolean!) {
       operations(onlyMissing: $onlyMissing) { ${OPERATION_FIELDS} }
     }`,
    { onlyMissing },
  );
  return data.operations;
}

export async function listOperationSchedules(client: ApiClient): Promise<OperationSchedule[]> {
  const data = await client.graphql<{ operationSchedules: OperationSchedule[] }>(
    `query OperationSchedules { operationSchedules { ${SCHEDULE_FIELDS} } }`,
    {},
  );
  return data.operationSchedules;
}

export async function runOperation(
  client: ApiClient,
  vars: {
    key: string;
    onlyMissing?: boolean;
    priority?: number | null;
    /** ISO-8601 instant; omit to start immediately. */
    runAfter?: string | null;
  },
): Promise<OperationRun> {
  const data = await client.graphql<{ runOperation: OperationRun }>(
    `mutation RunOperation(
       $key: String!, $onlyMissing: Boolean!, $priority: Int, $runAfter: DateTime
     ) {
       runOperation(
         key: $key, onlyMissing: $onlyMissing, priority: $priority, runAfter: $runAfter
       ) { operationKey enqueued priority runAfter jobIds }
     }`,
    {
      key: vars.key,
      onlyMissing: vars.onlyMissing ?? true,
      priority: vars.priority ?? null,
      runAfter: vars.runAfter ?? null,
    },
  );
  return data.runOperation;
}

export async function createOperationSchedule(
  client: ApiClient,
  vars: {
    key: string;
    cadence: CadenceInput;
    onlyMissing?: boolean;
    priority?: number | null;
    note?: string | null;
  },
): Promise<OperationSchedule> {
  const data = await client.graphql<{ createOperationSchedule: OperationSchedule }>(
    `mutation CreateSchedule(
       $key: String!, $cadence: OperationCadenceInput!, $onlyMissing: Boolean!,
       $priority: Int, $note: String
     ) {
       createOperationSchedule(
         key: $key, cadence: $cadence, onlyMissing: $onlyMissing,
         priority: $priority, note: $note
       ) { ${SCHEDULE_FIELDS} }
     }`,
    {
      key: vars.key,
      cadence: vars.cadence,
      onlyMissing: vars.onlyMissing ?? true,
      priority: vars.priority ?? null,
      note: vars.note ?? null,
    },
  );
  return data.createOperationSchedule;
}

export async function updateOperationSchedule(
  client: ApiClient,
  scheduleId: string,
  patch: {
    cadence?: CadenceInput | null;
    onlyMissing?: boolean | null;
    priority?: number | null;
    enabled?: boolean | null;
    note?: string | null;
  },
): Promise<OperationSchedule> {
  const data = await client.graphql<{ updateOperationSchedule: OperationSchedule }>(
    `mutation UpdateSchedule(
       $scheduleId: ID!, $cadence: OperationCadenceInput, $onlyMissing: Boolean,
       $priority: Int, $enabled: Boolean, $note: String
     ) {
       updateOperationSchedule(
         scheduleId: $scheduleId, cadence: $cadence, onlyMissing: $onlyMissing,
         priority: $priority, enabled: $enabled, note: $note
       ) { ${SCHEDULE_FIELDS} }
     }`,
    {
      scheduleId,
      cadence: patch.cadence ?? null,
      onlyMissing: patch.onlyMissing ?? null,
      priority: patch.priority ?? null,
      enabled: patch.enabled ?? null,
      note: patch.note ?? null,
    },
  );
  return data.updateOperationSchedule;
}

export async function deleteOperationSchedule(
  client: ApiClient,
  scheduleId: string,
): Promise<boolean> {
  const data = await client.graphql<{ deleteOperationSchedule: boolean }>(
    `mutation DeleteSchedule($scheduleId: ID!) {
       deleteOperationSchedule(scheduleId: $scheduleId)
     }`,
    { scheduleId },
  );
  return data.deleteOperationSchedule;
}
