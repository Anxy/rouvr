import createClient, { type Middleware } from "openapi-fetch";
import type { paths } from "./schema";

// Ledger origin for server-side proxying. Same default as the direct
// browser client; override with ROUVR_LEDGER_URL when the ledger runs
// elsewhere. Never exposed to the browser.
export const LEDGER_URL =
  process.env.ROUVR_LEDGER_URL ?? "http://127.0.0.1:8221";

// The browser talks to these routes same-origin, so its Origin (the Next
// origin) never matches the ledger host and would fail the ledger CSRF
// Origin check. The proxy strips origin/referer and relies on the
// double-submit X-CSRF-Token the browser received at login instead.
const stripOrigin: Middleware = {
  async onRequest({ request }) {
    request.headers.delete("origin");
    request.headers.delete("referer");
    return request;
  },
};

export function ledgerClient(cookie?: string) {
  const client = createClient<paths>({ baseUrl: LEDGER_URL });
  client.use(stripOrigin);
  const jar = (cookie ?? "").trim();
  if (jar) {
    client.use({
      async onRequest({ request }) {
        request.headers.set("cookie", jar);
        return request;
      },
    });
  }
  return client;
}

export function incomingCookie(req: Request): string {
  return req.headers.get("cookie") ?? "";
}

function forwardedHeaders(upstream: Response): Headers {
  const headers = new Headers();
  const contentType = upstream.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  const setCookie =
    typeof upstream.headers.getSetCookie === "function"
      ? upstream.headers.getSetCookie()
      : [];
  for (const value of setCookie) headers.append("set-cookie", value);
  return headers;
}

// Stream an upstream SSE body through. openapi-fetch hands the live
// ReadableStream back as `data` under parseAs:"stream"; that is what flows
// byte-for-byte here. Set-Cookie still reaches the browser under the Next
// origin so the httponly session cookie stays same-origin.
export function streamThrough(stream: ReadableStream<Uint8Array> | null, upstream: Response): Response {
  return new Response(stream, {
    status: upstream.status,
    headers: forwardedHeaders(upstream),
  });
}

// JSON pass-through from openapi-fetch's already-parsed payload (the
// library consumes the raw body itself). Forwards Set-Cookie so login and
// logout cookies land under the Next origin.
export function passJson(status: number, payload: unknown, upstream: Response): Response {
  return jsonThrough(status, payload, upstream);
}

export function jsonThrough(status: number, payload: unknown, upstream?: Response): Response {
  const headers = new Headers({ "content-type": "application/json" });
  if (upstream) {
    for (const [key, value] of forwardedHeaders(upstream)) {
      if (key === "set-cookie") headers.append(key, value);
    }
  }
  return new Response(JSON.stringify(payload), { status, headers });
}
