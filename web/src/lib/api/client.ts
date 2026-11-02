import createClient from "openapi-fetch";
import type { paths } from "./schema";

// Base URL of the Rouvr ledger API. Override with NEXT_PUBLIC_API_BASE_URL
// when the API runs somewhere other than local dev.
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8221";

// Typed client for every ledger route. Regenerate the schema after ledger
// route changes: `pnpm --filter web openapi:export` then `gen:api`.
export const api = createClient<paths>({ baseUrl: API_BASE_URL });

// Local Next.js route (not part of the ledger OpenAPI spec). Kept in this
// module so all API traffic flows through the generated client layer.
export interface ContactPayload {
  name: string;
  email: string;
  company?: string | null;
  message: string;
  website?: string | null;
}

export interface ContactResult {
  ok: boolean;
  error?: string;
}

export async function submitContact(
  payload: ContactPayload,
): Promise<ContactResult> {
  const res = await fetch("/api/contact", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = (await res.json()) as ContactResult;
  if (!res.ok || !data.ok) {
    throw new Error(data.error ?? "Something went wrong.");
  }
  return data;
}
