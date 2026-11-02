/**
 * Rouvr ledger API client (FL-A1).
 *
 * `schema.d.ts` is generated from the served OpenAPI spec — never edit it by
 * hand. Regenerate after route changes:
 *
 *   python scripts/export_openapi.py   # ledger/openapi.json
 *   pnpm --filter web run gen:api      # web/src/lib/api/schema.d.ts
 *
 * All screens must call the API through this module: it owns the only raw
 * `fetch` in web/src (the small same-origin helper below; ledger calls go
 * through the typed openapi-fetch client).
 */
import createClient from "openapi-fetch";
import type { paths } from "./schema";

export type LedgerPaths = paths;

export interface LedgerClientOptions {
  baseUrl?: string;
  /** Ledger token, sent as `x-rouvr-token`. */
  token?: string;
}

export function createLedgerClient(options: LedgerClientOptions = {}) {
  const client = createClient<paths>({
    baseUrl:
      options.baseUrl ??
      process.env.NEXT_PUBLIC_LEDGER_BASE_URL ??
      "http://127.0.0.1:8221",
  });
  const token = options.token;
  if (token) {
    client.use({
      async onRequest({ request }) {
        request.headers.set("x-rouvr-token", token);
        return request;
      },
    });
  }
  return client;
}

/** Default client for screens; pass an explicit token client where needed. */
export const ledger = createLedgerClient();

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, body: unknown) {
    super(`API request failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

/** Single raw-fetch site for same-origin Next.js routes (see module doc). */
async function requestJson<T>(input: string, init?: RequestInit): Promise<T> {
  const res = await fetch(input, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const data = (await res.json().catch((): null => null)) as T | null;
  if (!res.ok || data === null) {
    throw new ApiError(res.status, data);
  }
  return data;
}

export interface ContactResult {
  ok: boolean;
  error?: string;
}

export async function submitContact(values: unknown): Promise<ContactResult> {
  const data = await requestJson<ContactResult>("/api/contact", {
    method: "POST",
    body: JSON.stringify(values),
  });
  if (!data.ok) {
    throw new Error(data.error ?? "Something went wrong.");
  }
  return data;
}
