import { LEDGER_URL, incomingCookie, jsonThrough } from "@/lib/api/server";

// Cookie session, read back. The browser lost the login reply in a new
// tab; this GET refills username and csrf_token. The proxy does not
// forward Origin, so later posts still need the token this returns.
export async function GET(req: Request) {
  const upstream = await fetch(`${LEDGER_URL}/auth/session`, {
    headers: { cookie: incomingCookie(req) },
    cache: "no-store",
  });
  const data = (await upstream.json().catch(() => ({ detail: "session failed" }))) as unknown;
  return jsonThrough(upstream.status, data, upstream);
}
