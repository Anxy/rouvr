import { incomingCookie, ledgerClient, passJson } from "@/lib/api/server";

/** Start Temporal TaskWorkflow for one task (idempotent; headless Muse). */
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = (await req.json().catch(() => ({}))) as {
    requested_by?: string;
    restart?: boolean;
  };
  const client = ledgerClient(incomingCookie(req));
  const csrf = req.headers.get("x-csrf-token") ?? "";
  const { data, error, response } = await client.POST("/v1/tasks/{tid}/start", {
    params: { path: { tid: Number(id) } },
    body: {
      ...(body.requested_by?.trim() ? { requested_by: body.requested_by.trim() } : {}),
      ...(body.restart ? { restart: true } : {}),
    } as never,
    headers: csrf ? { "x-csrf-token": csrf } : {},
  });
  return passJson(response.status, response.ok ? data : (error ?? { detail: "start failed" }), response);
}
