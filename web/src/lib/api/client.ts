/**
 * Typed API client for the Rouvr ledger (FastAPI).
 *
 * `ledger` is typed from the generated OpenAPI schema (`schema.d.ts`, via
 * openapi-fetch): paths, methods and params are checked at compile time.
 * `requestJson` is the single hand-written fetch in the web app — screens
 * calling same-origin Next.js routes go through it, never `fetch` directly.
 */
import createClient from "openapi-fetch";
import type { paths } from "./schema";

export const LEDGER_BASE_URL =
  process.env.NEXT_PUBLIC_ROUVR_API_BASE_URL ?? "http://127.0.0.1:8221";

export function createLedgerClient(baseUrl: string = LEDGER_BASE_URL) {
  return createClient<paths>({ baseUrl });
}

/** Shared typed client for the ledger API. */
export const ledger = createLedgerClient();

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

/** Typed JSON request; the only fetch() call in web/src. */
export async function requestJson<T>(input: string, init?: RequestInit): Promise<T> {
  const res = await fetch(input, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  const data: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const message =
      typeof data === "object" && data !== null && "error" in data
        ? String((data as { error: unknown }).error)
        : `Request failed (${res.status})`;
    throw new ApiError(message, res.status, data);
  }
  return data as T;
}
