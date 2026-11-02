import createClient, { type Client } from "openapi-fetch";
import type { paths } from "./schema";

/** Base URL of the Rouvr ledger API. Override per environment. */
export const LEDGER_BASE_URL =
  process.env.NEXT_PUBLIC_LEDGER_BASE_URL ?? "http://127.0.0.1:8221";

export type ApiClient = Client<paths>;

/** Build a typed ledger client (per-request instances can add auth headers). */
export function createApiClient(baseUrl: string = LEDGER_BASE_URL): ApiClient {
  return createClient<paths>({ baseUrl });
}

/** Shared typed client for the Rouvr ledger API. Regenerate types with `pnpm --filter web gen:api`. */
export const api = createApiClient();
