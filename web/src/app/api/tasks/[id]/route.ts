import { incomingCookie, ledgerClient, passJson } from "@/lib/api/server";

export async function GET(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const client = ledgerClient(incomingCookie(req));
  const { data, error, response } = await client.GET("/v1/tasks/{tid}", {
    params: { path: { tid: Number(id) } },
  });
  return passJson(response.status, response.ok ? data : (error ?? { detail: "task not found" }), response);
}

export async function PATCH(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = (await req.json().catch(() => ({}))) as {
    title?: string;
    intent?: string;
    idempotency_key?: string;
  };
  const client = ledgerClient(incomingCookie(req));
  const csrf = req.headers.get("x-csrf-token") ?? "";
  const { data, error, response } = await client.PATCH("/v1/tasks/{tid}", {
    params: { path: { tid: Number(id) } },
    body: body as never,
    headers: csrf ? { "x-csrf-token": csrf } : {},
  });
  return passJson(response.status, response.ok ? data : (error ?? { detail: "task edit failed" }), response);
}
