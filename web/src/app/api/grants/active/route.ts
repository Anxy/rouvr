import { incomingCookie, jsonThrough, ledgerClient, passJson } from "@/lib/api/server";

// Grant status for an inbox row: newest unexpired grant for the scope.
export async function GET(req: Request) {
  const url = new URL(req.url);
  const scope = url.searchParams.get("scope") ?? "";
  if (!scope) return jsonThrough(422, { detail: "scope is required" });
  const contractRev = url.searchParams.get("contract_rev") ?? undefined;
  const client = ledgerClient(incomingCookie(req));
  const { data, error, response } = await client.GET("/v1/grants/active", {
    params: { query: { scope, contract_rev: contractRev } as never },
  });
  return passJson(response.status, response.ok ? data : (error ?? { detail: "grant lookup failed" }), response);
}
