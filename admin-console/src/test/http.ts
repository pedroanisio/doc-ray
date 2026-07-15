import { vi } from "vitest";

/** Build a JSON Response for a fake fetch, defaulting to 200/application-json. */
export function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
    ...init,
  });
}

/** The subset of RequestInit our client actually sets, with headers narrowed
 *  to a plain record so assertions can index it without HeadersInit gymnastics. */
export interface RecordedInit {
  method?: string;
  body?: string;
  headers: Record<string, string>;
}

type Handler = (url: string, init: RecordedInit) => Response | Promise<Response>;

export interface TypedFetchMock {
  mock: { calls: Array<[string, RecordedInit]> };
}

/**
 * A fetch double that is assignable to `typeof fetch` at call sites AND exposes
 * a strongly-typed `.mock.calls` so specs can assert URL/method/headers/body
 * without casts. The handler may ignore its args.
 */
export function makeFetchMock(handler: Handler): typeof fetch & TypedFetchMock {
  return vi.fn(handler) as unknown as typeof fetch & TypedFetchMock;
}
