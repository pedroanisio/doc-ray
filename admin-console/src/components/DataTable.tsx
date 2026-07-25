import type { ReactNode } from "react";

/** One column of a {@link DataTable}. */
export interface Column<T> {
  /** Field name; also the default cell value when {@link Column.render} is absent. */
  key: keyof T & string;
  /** Column header text. */
  header: string;
  /** Custom cell renderer; defaults to the row's stringified `key` value. */
  render?: (row: T) => ReactNode;
  /** Optional class applied to every cell in the column. */
  className?: string;
}

/**
 * A generic, presentation-only data table.
 *
 * Rendering only — filtering, sorting, and pagination are owned by the page so
 * they can drive server-side queries (a Jobs page filters 100k rows in SQL, not
 * over one fetched page). Renders nothing but the empty message when `rows` is
 * empty, so a caller never shows an empty grid.
 */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  empty,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  empty?: string;
}) {
  if (rows.length === 0) {
    return <p className="muted">{empty ?? "Nothing to show."}</p>;
  }
  return (
    <table className="grid">
      <thead>
        <tr>
          {columns.map((col) => (
            <th key={col.key}>{col.header}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={rowKey(row)}>
            {columns.map((col) => (
              <td key={col.key} className={col.className}>
                {col.render ? col.render(row) : String(row[col.key] ?? "")}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
