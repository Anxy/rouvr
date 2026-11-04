import { incomingCookie, jsonThrough, ledgerClient, passJson } from "@/lib/api/server";

// Claim a task for a worker principal (assignment + lease). The ledger
// owns the rules (supervisor-only, one active assignment, lease epochs);
// refusal surfaces as an error, mirroring the edit/retire precedent.
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { key, principal, attempt_no, ttl_s } = (await req.json().catch(() => ({}))) as {
    key?: string;
    principal?: string;
    attempt_no?: number;
    ttl_s?: number;
  };
  if (!principal?.trim()) {
    return jsonThrough(422, { detail: "principal is required to claim a task" });
  }
  const claimKey =
    typeof key === "string" && key.trim()
      ? key.trim()
      : `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  const client = ledgerClient(incomingCookie(req));
  const csrf = req.headers.get("x-csrf-token") ?? "";
  const { data, error, response } = await client.POST("/v1/tasks/{tid}/assignments/claim", {
    params: { path: { tid: Number(id) } },
    body: { key: claimKey, principal: principal.trim(), attempt_no, ttl_s } as never,
    headers: csrf ? { "x-csrf-token": csrf } : {},
  });
  return passJson(response.status, response.ok ? data : (error ?? { detail: "task claim failed" }), response);
}
