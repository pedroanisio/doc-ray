import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ServicesProvider, type Services } from "../services";
import { AuthProvider } from "../auth/AuthContext";
import { AuthService } from "../auth/authService";
import { TokenStore } from "../auth/tokenStore";
import { ApiClient } from "../api/client";
import { UnavailableIdentityProvider } from "../api/identity";
import { DocumentsPage } from "./DocumentsPage";

/**
 * The corpus statuses this console filters on must be the ones the corpus
 * actually stores. `documents(status:)` is an exact match (persistence_read),
 * so an option naming a value no document carries is a filter that can only
 * ever return "No documents match."
 */

function servicesWithFetch(fetchImpl: typeof fetch): Services {
  const store = new TokenStore(localStorage);
  const auth = new AuthService({
    store,
    now: () => Date.now(),
    makeClient: (refresh) =>
      new ApiClient({ baseUrl: "", fetchImpl, getToken: () => store.accessToken(), refresh }),
  });
  return { auth, identity: new UnavailableIdentityProvider() };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const EXTRACTED = {
  id: "d1",
  title: "A readable document",
  docType: "pdf",
  status: "extracted",
  lifecycleState: "active",
  language: "en",
  charCount: 1234,
  createdAt: "2026-07-01T00:00:00Z",
  updatedAt: null,
  error: null,
};

const FAILED = {
  id: "d2",
  title: "A scanned document",
  docType: "pdf",
  status: "failed",
  lifecycleState: "active",
  language: null,
  charCount: 0,
  createdAt: "2026-07-01T00:00:00Z",
  updatedAt: null,
  error: "no extractable text — scanned/image-only file? OCR required before ingest",
};

function renderPage(docs: unknown[]) {
  const calls: Array<Record<string, unknown>> = [];
  const fetchImpl = vi.fn(async (_url: string, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body ?? "{}"));
    calls.push(body.variables ?? {});
    return jsonResponse({ data: { documents: docs } });
  }) as unknown as typeof fetch;

  render(
    <ServicesProvider services={servicesWithFetch(fetchImpl)}>
      <AuthProvider>
        <DocumentsPage />
      </AuthProvider>
    </ServicesProvider>,
  );
  return calls;
}

describe("DocumentsPage", () => {
  it("offers only status values the corpus actually stores", async () => {
    renderPage([EXTRACTED]);
    await screen.findByText("A readable document");

    const options = Array.from(
      screen.getByRole("combobox").querySelectorAll("option"),
    ).map((o) => (o as HTMLOptionElement).value);

    expect(options).toContain("extracted");
    expect(options).toContain("failed");
    // 'ready' and 'processing' are values no document has ever carried; an
    // operator picking one saw an empty table and no explanation.
    expect(options).not.toContain("ready");
    expect(options).not.toContain("processing");
  });

  it("filters the query by the selected status", async () => {
    const calls = renderPage([FAILED]);
    await screen.findByText("A scanned document");

    await userEvent.selectOptions(screen.getByRole("combobox"), "failed");

    await waitFor(() => {
      expect(calls[calls.length - 1]).toMatchObject({ status: "failed" });
    });
  });

  it("shows why a failed document failed", async () => {
    renderPage([FAILED]);

    expect(await screen.findByText(/no extractable text/i)).toBeInTheDocument();
    expect(screen.getByText(/OCR required before ingest/i)).toBeInTheDocument();
  });

  it("does not show a failure reason for a healthy document", async () => {
    renderPage([EXTRACTED]);
    await screen.findByText("A readable document");

    expect(screen.queryByText(/no extractable text/i)).not.toBeInTheDocument();
  });

  it("requests the error field so the reason can be rendered", async () => {
    const fetchImpl = vi.fn(async (_url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body ?? "{}"));
      expect(body.query).toContain("error");
      return jsonResponse({ data: { documents: [EXTRACTED] } });
    }) as unknown as typeof fetch;

    render(
      <ServicesProvider services={servicesWithFetch(fetchImpl)}>
        <AuthProvider>
          <DocumentsPage />
        </AuthProvider>
      </ServicesProvider>,
    );

    await screen.findByText("A readable document");
    expect(fetchImpl).toHaveBeenCalled();
  });
});
