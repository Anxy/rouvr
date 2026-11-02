/**
 * Typed client for the Rouvr API (FL-A1).
 *
 * `paths` is generated from the FastAPI OpenAPI document
 * (`pnpm --filter web gen:api`); `api` is the openapi-fetch wrapper over
 * it. This module is the only place that calls fetch() against API routes:
 * screens import helpers from here instead of hand-rolling fetch calls.
 */
import createClient from "openapi-fetch";
import type { paths } from "./schema";

/** Ledger API base URL (same-origin proxy in prod; local ledger in dev). */
export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8221"
).replace(/\/+$/, "");

/** Typed fetch client for every path in the OpenAPI document. */
export const api = createClient<paths>({ baseUrl: API_BASE_URL });
export type ApiClient = typeof api;

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function errorMessage(data: unknown, status: number): string {
  if (typeof data === "object" && data !== null && "error" in data) {
    const err = (data as { error?: unknown }).error;
    if (typeof err === "string" && err) return err;
  }
  return `Request failed (${status})`;
}

async function requestJson<T>(path: string, init: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  const data: unknown = await res.json().catch(() => null);
  if (!res.ok || data === null) {
    throw new ApiError(res.status, errorMessage(data, res.status));
  }
  return data as T;
}

export interface ContactPayload {
  name: string;
  email: string;
  company?: string;
  message: string;
  website?: string;
}

export interface ContactResult {
  ok: boolean;
  error?: string;
}

/** Submit the contact form via the site API route. */
export function submitContact(payload: ContactPayload): Promise<ContactResult> {
  return requestJson<ContactResult>("/api/contact", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}
