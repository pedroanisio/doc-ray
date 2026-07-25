import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { DataTable, type Column } from "./DataTable";

interface Row {
  id: string;
  kind: string;
  status: string;
}

const columns: Column<Row>[] = [
  { key: "kind", header: "Kind" },
  { key: "status", header: "Status", render: (r) => <span className="badge">{r.status}</span> },
];

describe("DataTable", () => {
  it("renders a header per column and a row per datum", () => {
    const rows: Row[] = [
      { id: "1", kind: "ingest", status: "succeeded" },
      { id: "2", kind: "annotate", status: "queued" },
    ];
    render(<DataTable columns={columns} rows={rows} rowKey={(r) => r.id} />);
    expect(screen.getByRole("columnheader", { name: "Kind" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Status" })).toBeInTheDocument();
    // two body rows (excluding the header row)
    const bodyRows = screen.getAllByRole("row").slice(1);
    expect(bodyRows).toHaveLength(2);
    expect(within(bodyRows[0]).getByText("ingest")).toBeInTheDocument();
  });

  it("uses a column's custom render for the cell", () => {
    render(
      <DataTable
        columns={columns}
        rows={[{ id: "1", kind: "backup", status: "failed" }]}
        rowKey={(r) => r.id}
      />,
    );
    const cell = screen.getByText("failed");
    expect(cell).toHaveClass("badge");
  });

  it("falls back to the field value when a column has no render", () => {
    render(
      <DataTable
        columns={[{ key: "kind", header: "Kind" }]}
        rows={[{ id: "1", kind: "reannotate", status: "x" }]}
        rowKey={(r) => r.id}
      />,
    );
    expect(screen.getByText("reannotate")).toBeInTheDocument();
  });

  it("shows the empty message when there are no rows", () => {
    render(
      <DataTable columns={columns} rows={[]} rowKey={(r) => r.id} empty="No jobs in the queue." />,
    );
    expect(screen.getByText("No jobs in the queue.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
