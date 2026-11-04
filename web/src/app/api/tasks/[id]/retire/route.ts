import { incomingCookie, jsonThrough, ledgerClient, passJson } from "@/lib/api/server";

// Retire a task with a mandatory reason. Done work is never retired:
// done is earned by verification, retired is the terminal state for
// obsolete or superseded open/blocked/claimed/failed tasks.
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = (await req.json().catch(() => ({}))) as {
    reason?: string;
    idempotency_key?: string;
  };
  if (!body.reason?.trim()) {
    return jsonThrough(422, { detail: "a reason is required to retire a task" });
  }
  const client = ledgerClient(incomingCookie(req));
  const csrf = req.headers.get("x-csrf-token") ?? "";
  const { data, error, response } = await client.POST("/v1/tasks/{tid}/retire", {
    params: { path: { tid: Number(id) } },
    body: body as never,
    headers: csrf ? { "x-csrf-token": csrf } : {},
  });
  return passJson(response.status, response.ok ? data : (error ?? { detail: "task retire failed" }), response);
}
