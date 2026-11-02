/**
 * Rouvr ledger API client (FL-A1).
 *
 * `schema.d.ts` is generated from the served OpenAPI spec — never edit it by
 * hand. Regenerate after route changes:
 *
 *   python scripts/export_openapi.py   # ledger/openapi.json
 *   pnpm --filter web run gen:api      # web/src/lib/api/schema.d.ts
 *
 * All screens must call APIs through this module. It is the only module in
 * web/src allowed to talk to API routes, and it does so exclusively through
 * the typed openapi-fetch client below (no hand-written transport calls).
 */
import createClient, { type Client } from "openapi-fetch";
import type { paths } from "./schema";
import type { ContactInput } from "@/lib/validations";

export type LedgerPaths = paths;
export type LedgerClient = Client<paths>;

/** Base URL of the Rouvr ledger API. Override per environment. */
export const LEDGER_BASE_URL =
  process.env.NEXT_PUBLIC_LEDGER_BASE_URL ?? "http://127.0.0.1:8221";

export interface LedgerClientOptions {
  baseUrl?: string;
  /** Ledger token, sent as `x-rouvr-token`. */
  token?: string;
}

/** Build a typed ledger client (per-request instances can add auth headers). */
export function createLedgerClient(
  options: LedgerClientOptions = {},
): LedgerClient {
  const client = createClient<paths>({
    baseUrl: options.baseUrl ?? LEDGER_BASE_URL,
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

/** Shared typed client for the Rouvr ledger API. */
export const ledger = createLedgerClient();

/** Alias for the shared ledger client (screens may use either name). */
export const api = ledger;

export type ApiClient = LedgerClient;

export function createApiClient(
  baseUrl: string = LEDGER_BASE_URL,
): ApiClient {
  return createLedgerClient({ baseUrl });
}

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

/** Same-origin site routes, served by Next.js (typed inline: not in the ledger spec). */
interface SitePaths {
  "/api/contact": {
    post: {
      requestBody: {
        content: {
          "application/json": ContactInput;
        };
      };
      responses: {
        200: {
          content: {
            "application/json": ContactResult;
          };
        };
      };
    };
  };
}

const site = createClient<SitePaths>();

export interface ContactResult {
  ok: boolean;
  error?: string;
}

/** Submit the site contact form (site-local route, via the typed client). */
export async function submitContact(
  values: ContactInput,
): Promise<ContactResult> {
  const { data, response } = await site.POST("/api/contact", {
    body: values,
  });
  if (!response.ok || !data?.ok) {
    throw new Error(data?.error ?? "Something went wrong.");
  }
  return data;
}
