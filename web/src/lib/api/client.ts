import createClient from "openapi-fetch";
import type { ContactInput } from "../validations";
import type { paths } from "./schema";

// Base URL of the Rouvr ledger API. Override with NEXT_PUBLIC_API_BASE_URL
// when the API runs somewhere other than local dev.
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8221";

// Typed client for every ledger route, bound to the generated schema.
// Regenerate after ledger route changes: `pnpm --filter web openapi:export`
// then `pnpm --filter web gen:api`. Auth headers arrive with slice B.
export function createRouvrClient(baseUrl: string = API_BASE_URL) {
  return createClient<paths>({ baseUrl });
}

export const api = createRouvrClient();

export interface ContactResult {
  ok: boolean;
  error?: string;
}

// Local Next.js route (not part of the ledger OpenAPI spec). Kept in this
// module — the only file allowed to call fetch — so every screen goes
// through the client layer. Throws Error with the server message on failure.
export async function submitContact(values: ContactInput): Promise<void> {
  const res = await fetch("/api/contact", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(values),
  });
  const data = (await res.json()) as ContactResult;
  if (!res.ok || !data.ok) throw new Error(data.error ?? "Something went wrong.");
}
