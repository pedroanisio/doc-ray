import type { ReactNode } from "react";
import type { AsyncState } from "../hooks/useAsync";

export function PageHeader({ title, actions }: { title: string; actions?: ReactNode }) {
  return (
    <header className="page-header">
      <h1>{title}</h1>
      <div className="page-actions">{actions}</div>
    </header>
  );
}

/** Renders loading / error / empty / content states for an async slice. */
export function AsyncBoundary<T>({
  state,
  children,
  empty,
}: {
  state: AsyncState<T>;
  children: (data: T) => ReactNode;
  empty?: string;
}) {
  if (state.loading && state.data === null) return <p className="muted">Loading…</p>;
  if (state.error)
    return (
      <p role="alert" className="alert">
        {state.error}
      </p>
    );
  if (state.data === null || (Array.isArray(state.data) && state.data.length === 0))
    return <p className="muted">{empty ?? "Nothing to show."}</p>;
  return <>{children(state.data)}</>;
}
