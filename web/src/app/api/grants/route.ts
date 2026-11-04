import { incomingCookie, jsonThrough, ledgerClient, passJson } from "@/lib/api/server";

// Escalations grant: standing owner-approved bounded permission (T5).
// The inbox follows a fresh grant with an escalation_resolved note and a
// blocked/failed -> open transition so the task moves observably.
export async function POST(req: Request) {
  const { scope, expires_at, approved_by, contract_rev } = (await req.json().catch(() => ({}))) as {
    scope?: string;
    expires_at?: string;
    approved_by?: string;
    contract_rev?: string;
  };
  if (!scope || !expires_at || !approved_by) {
    return jsonThrough(422, { detail: "scope, expires_at and approved_by are required" });
  }
  const client = ledgerClient(incomingCookie(req));
  const csrf = req.headers.get("x-csrf-token") ?? "";
  const { data, error, response } = await client.POST("/v1/grants", {
    body: { scope, expires_at, approved_by, contract_rev } as never,
    headers: csrf ? { "x-csrf-token": csrf } : {},
  });
  return passJson(response.status, response.ok ? data : (error ?? { detail: "grant failed" }), response);
}
