import { incomingCookie, jsonThrough, ledgerClient, passJson } from "@/lib/api/server";

// Approve (to done) and retry (back to open) from the task page.
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { to, idempotency_key } = (await req.json().catch(() => ({}))) as {
    to?: string;
    idempotency_key?: string;
  };
  if (!to) return jsonThrough(422, { detail: "to is required" });
  const key =
    typeof idempotency_key === "string" && idempotency_key.trim()
      ? idempotency_key.trim()
      : `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  const client = ledgerClient(incomingCookie(req));
  const csrf = req.headers.get("x-csrf-token") ?? "";
  const { data, error, response } = await client.POST("/v1/tasks/{tid}/transition", {
    params: { path: { tid: Number(id) } },
    body: { to, idempotency_key: key } as never,
    headers: csrf ? { "x-csrf-token": csrf } : {},
  });
  return passJson(response.status, response.ok ? data : (error ?? { detail: "transition failed" }), response);
}
