/**
 * Typed API client for the Rouvr ledger (FastAPI).
 *
 * `schema.d.ts` is generated from the live OpenAPI spec — refresh it with
 * `pnpm gen:api` from the repo root (export + regenerate). This module is
 * the ONLY place in web/src allowed to call fetch() against API routes:
 * every screen and component imports from `@/lib/api` instead of using
 * hand-written fetch.
 */
import createClient from "openapi-fetch";
import type { paths } from "./schema";

/** Base URL of the Rouvr ledger API (FastAPI). */
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8221";

/** Create a typed ledger client (override the base URL in tests). */
export function createApiClient(baseUrl: string = API_BASE_URL) {
  return createClient<paths>({ baseUrl });
}

/** Shared typed ledger client for all screens. */
export const api = createApiClient();

export type ApiClient = ReturnType<typeof createApiClient>;

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

/** Submit the contact form via the Next.js /api/contact route. */
export async function submitContact(
  payload: ContactPayload,
): Promise<ContactResult> {
  const res = await fetch("/api/contact", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = (await res.json()) as ContactResult;
  if (!res.ok || !data.ok)
    throw new Error(data.error ?? "Something went wrong.");
  return { ok: true };
}
