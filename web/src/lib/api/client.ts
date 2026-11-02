/**
 * Typed fetch wrapper over the generated OpenAPI schema (`./schema.d.ts`).
 *
 * This is the only module under `web/src` allowed to call `fetch`
 * directly (enforced by the eslint `no-restricted-globals` rule and by
 * `tests/test_openapi_client.py`). Screens call the typed `apiFetch`
 * for ledger API paths, or `jsonFetch` for same-origin Next.js routes.
 * Regenerate the schema with `pnpm --filter web gen:api`.
 */
import type { paths } from "./schema";

/** Ledger API origin. Same-origin by default; set for a split deploy. */
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/+$/, "") ?? "";

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, message: string, body: unknown = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

export type ApiPath = keyof paths & string;
type HttpMethod =
  | "get"
  | "post"
  | "put"
  | "patch"
  | "delete"
  | "head"
  | "options";
export type ApiMethod<P extends ApiPath> = Extract<
  keyof paths[P],
  HttpMethod
>;

type Operation<P extends ApiPath, M extends ApiMethod<P>> = paths[P][M];

type RequestBodyOf<P extends ApiPath, M extends ApiMethod<P>> =
  Operation<P, M> extends {
    requestBody?: { content: { "application/json"?: infer B } };
  }
    ? B
    : unknown;

type ResponseOf<P extends ApiPath, M extends ApiMethod<P>> = Operation<
  P,
  M
> extends {
  responses: { 200: { content: { "application/json"?: infer R } } };
}
  ? ([R] extends [never] ? unknown : R)
  : unknown;

type QueryOf<P extends ApiPath, M extends ApiMethod<P>> = Operation<
  P,
  M
> extends {
  parameters: { query?: infer Q };
}
  ? Q
  : never;

type PathParamsOf<P extends ApiPath, M extends ApiMethod<P>> = Operation<
  P,
  M
> extends {
  parameters: { path: infer Q };
}
  ? Q
  : never;

export interface ApiRequestOptions<P extends ApiPath, M extends ApiMethod<P>> {
  path?: PathParamsOf<P, M>;
  query?: QueryOf<P, M>;
  body?: RequestBodyOf<P, M> | BodyInit | null;
  headers?: HeadersInit;
  signal?: AbortSignal;
}

function isRawBody(value: unknown): value is BodyInit {
  return (
    value instanceof FormData ||
    value instanceof URLSearchParams ||
    value instanceof Blob ||
    value instanceof ArrayBuffer ||
    ArrayBuffer.isView(value) ||
    typeof value === "string"
  );
}

function errorMessage(status: number, body: unknown): string {
  if (body !== null && typeof body === "object") {
    const record = body as Record<string, unknown>;
    const detail = record.error ?? record.detail ?? record.message;
    if (typeof detail === "string" && detail.trim() !== "") return detail;
  }
  if (typeof body === "string" && body.trim() !== "") {
    return body.slice(0, 300);
  }
  return `Request failed with status ${status}`;
}

async function parseBody(res: Response): Promise<unknown> {
  if (res.status === 204) return undefined;
  const contentType = res.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    try {
      return await res.json();
    } catch {
      return null;
    }
  }
  return res.text();
}

async function doFetch(
  url: string,
  init: RequestInit,
  json: unknown,
): Promise<unknown> {
  const headers = new Headers(init.headers ?? {});
  let body: BodyInit | null | undefined;
  if (json === undefined) {
    body = (init.body as BodyInit | null | undefined) ?? undefined;
  } else if (isRawBody(json)) {
    body = json;
  } else {
    if (!headers.has("content-type")) {
      headers.set("content-type", "application/json");
    }
    body = JSON.stringify(json);
  }
  const res = await fetch(url, { ...init, headers, body });
  const data = await parseBody(res);
  if (!res.ok) {
    throw new ApiError(res.status, errorMessage(res.status, data), data);
  }
  return data;
}

function buildQuery(query: Record<string, unknown> | undefined): string {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue;
    if (Array.isArray(value)) {
      for (const item of value) params.append(key, String(item));
    } else {
      params.append(key, String(value));
    }
  }
  const text = params.toString();
  return text === "" ? "" : `?${text}`;
}

/**
 * Typed ledger API call. Path and method are checked against the
 * generated schema; `T` defaults to the schema's 200 response.
 */
export async function apiFetch<
  P extends ApiPath,
  M extends ApiMethod<P>,
  T = ResponseOf<P, M>,
>(
  path: P,
  method: M,
  options: ApiRequestOptions<P, M> = {},
): Promise<T> {
  let url = path as string;
  const pathParams = (options.path ?? {}) as Record<string, string | number>;
  for (const [key, value] of Object.entries(pathParams)) {
    url = url.replace(`{${key}}`, encodeURIComponent(String(value)));
  }
  const query = buildQuery(
    options.query as Record<string, unknown> | undefined,
  );
  const data = await doFetch(
    `${API_BASE_URL}${url}${query}`,
    {
      method: (method as string).toUpperCase(),
      headers: options.headers,
      signal: options.signal,
    },
    options.body,
  );
  return data as T;
}

/** Untyped JSON helper for same-origin Next.js routes (e.g. `/api/contact`). */
export async function jsonFetch<T>(
  input: string,
  init: Omit<RequestInit, "body"> & { json?: unknown } = {},
): Promise<T> {
  const { json, ...rest } = init;
  return (await doFetch(input, rest, json)) as T;
}
