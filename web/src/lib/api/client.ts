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

// Local Next.js route (not part of the ledger OpenAPI spec): same
// openapi-fetch machinery with a small local paths type, so no screen ever
// calls fetch directly. Throws Error with the server message on failure.
interface ContactPaths {
  "/api/contact": {
    post: {
      requestBody: { content: { "application/json": ContactInput } };
      responses: {
        200: { content: { "application/json": ContactResult } };
        400: { content: { "application/json": ContactResult } };
        422: { content: { "application/json": ContactResult } };
        500: { content: { "application/json": ContactResult } };
      };
    };
  };
}

const localApi = createClient<ContactPaths>();

export async function submitContact(values: ContactInput): Promise<void> {
  const { data, error } = await localApi.POST("/api/contact", {
    body: values,
  });
  const result = data ?? error;
  if (!result?.ok) throw new Error(result?.error ?? "Something went wrong.");
}
