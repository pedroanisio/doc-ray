import type { CadenceInput, CadenceKind } from "../api/types";

const WEEKDAYS = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
];

/** Server-side floor (domain/operation_cadence.MIN_INTERVAL_MINUTES), mirrored so
 *  the form refuses a hot-loop cadence before a round trip rejects it. */
export const MIN_INTERVAL_MINUTES = 5;

export function describeCadence(cadence: CadenceInput): string {
  const clock = `${String(cadence.hour ?? 0).padStart(2, "0")}:${String(
    cadence.minute ?? 0,
  ).padStart(2, "0")} UTC`;
  switch (cadence.kind) {
    case "interval":
      return `every ${cadence.everyMinutes ?? MIN_INTERVAL_MINUTES} min`;
    case "daily":
      return `daily at ${clock}`;
    case "weekly":
      return `${WEEKDAYS[cadence.weekday ?? 0]}s at ${clock}`;
    default:
      return `day ${cadence.dayOfMonth ?? 1} monthly at ${clock}`;
  }
}

/**
 * A form for a recurrence, not a cron string.
 *
 * The server models cadence as a validated structure precisely so a human never
 * has to hand-write `0 2 * * *` and discover weeks later that it never fired.
 * This component is the other half of that decision: every field is bounded by
 * the input itself, so the only rejections left are ones the UI cannot know.
 */
export function CadenceEditor({
  value,
  onChange,
  idPrefix = "cadence",
}: {
  value: CadenceInput;
  onChange: (next: CadenceInput) => void;
  idPrefix?: string;
}) {
  const set = (patch: Partial<CadenceInput>) => onChange({ ...value, ...patch });
  const showClock = value.kind !== "interval";

  return (
    <div className="cadence-editor">
      <label htmlFor={`${idPrefix}-kind`}>Repeats</label>
      <select
        id={`${idPrefix}-kind`}
        value={value.kind}
        onChange={(e) => set({ kind: e.target.value as CadenceKind })}
      >
        <option value="interval">On an interval</option>
        <option value="daily">Daily</option>
        <option value="weekly">Weekly</option>
        <option value="monthly">Monthly</option>
      </select>

      {value.kind === "interval" && (
        <>
          <label htmlFor={`${idPrefix}-every`}>Every (minutes)</label>
          <input
            id={`${idPrefix}-every`}
            type="number"
            min={MIN_INTERVAL_MINUTES}
            value={value.everyMinutes ?? 60}
            onChange={(e) => set({ everyMinutes: Number(e.target.value) })}
          />
        </>
      )}

      {value.kind === "weekly" && (
        <>
          <label htmlFor={`${idPrefix}-weekday`}>On</label>
          <select
            id={`${idPrefix}-weekday`}
            value={value.weekday ?? 0}
            onChange={(e) => set({ weekday: Number(e.target.value) })}
          >
            {WEEKDAYS.map((day, index) => (
              <option key={day} value={index}>
                {day}
              </option>
            ))}
          </select>
        </>
      )}

      {value.kind === "monthly" && (
        <>
          <label htmlFor={`${idPrefix}-dom`}>Day of month</label>
          <input
            id={`${idPrefix}-dom`}
            type="number"
            min={1}
            max={31}
            value={value.dayOfMonth ?? 1}
            onChange={(e) => set({ dayOfMonth: Number(e.target.value) })}
          />
          <p className="muted small">
            Months without this day are skipped, not clamped — day 31 never fires in
            February.
          </p>
        </>
      )}

      {showClock && (
        <>
          <label htmlFor={`${idPrefix}-time`}>At (UTC)</label>
          <input
            id={`${idPrefix}-time`}
            type="time"
            value={`${String(value.hour ?? 2).padStart(2, "0")}:${String(
              value.minute ?? 0,
            ).padStart(2, "0")}`}
            onChange={(e) => {
              const [hour, minute] = e.target.value.split(":").map(Number);
              set({ hour, minute });
            }}
          />
        </>
      )}

      <p className="muted small">Runs {describeCadence(value)}.</p>
    </div>
  );
}
