import createClient from "openapi-fetch";
import type { paths } from "./schema";

/**
 * Base URL of the Rouvr ledger API. Override with NEXT_PUBLIC_API_BASE_URL
 * (e.g. the deployed Lite host); defaults to a local ledger (see `make ledger`).
 */
export function resolveBaseUrl(): string {
  const fromEnv = process.env.NEXT_PUBLIC_API_BASE_URL?.trim().replace(
    /\/+$/,
    "",
  );
  return fromEnv ? fromEnv : "http://127.0.0.1:8221";
}

export interface ApiClientOptions {
  baseUrl?: string;
  /** Ledger token, sent as `x-rouvr-token`. */
  token?: string;
}

/** Typed client for the ledger API, generated from `openapi.json` (see `gen:api`). */
export function createApiClient(options: ApiClientOptions = {}) {
  const client = createClient<paths>({
    baseUrl: options.baseUrl ?? resolveBaseUrl(),
  });
  const token = options.token?.trim();
  if (token) {
    client.use({
      onRequest({ request }) {
        request.headers.set("x-rouvr-token", token);
        return request;
      },
    });
  }
  return client;
}

export type ApiClient = ReturnType<typeof createApiClient>;

/** Shared typed client for the ledger API. */
export const api = createApiClient();

/** Error thrown by {@link apiFetch} for non-2xx responses. */
export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

export interface ApiFetchOptions extends Omit<RequestInit, "body"> {
  /** JSON body; sets Content-Type automatically. */
  json?: unknown;
}

function errorMessage(status: number, data: unknown): string {
  if (typeof data === "object" && data !== null) {
    const record = data as Record<string, unknown>;
    if (typeof record.error === "string" && record.error) {
      return record.error;
    }
    if (typeof record.detail === "string" && record.detail) {
      return record.detail;
    }
  }
  return `Request failed with status ${status}`;
}

/**
 * Shared fetch for same-origin Next.js routes (e.g. `/api/contact`).
 * This is the only raw `fetch` call in `web/src` — screens either use the
 * typed {@link api} client above or this wrapper, never bare `fetch`.
 */
export async function apiFetch<T = unknown>(
  url: string,
  options: ApiFetchOptions = {},
): Promise<T> {
  const { json, headers, ...rest } = options;
  const reqHeaders = new Headers(headers);
  if (json !== undefined && !reqHeaders.has("Content-Type")) {
    reqHeaders.set("Content-Type", "application/json");
  }
  const res = await fetch(url, {
    ...rest,
    headers: reqHeaders,
    body: json !== undefined ? JSON.stringify(json) : undefined,
  });
  const data: unknown = await res.json().catch(() => undefined);
  if (!res.ok) {
    throw new ApiError(res.status, errorMessage(res.status, data), data);
  }
  return data as T;
}
