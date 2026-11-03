import { incomingCookie, ledgerClient, passJson } from "@/lib/api/server";

export async function GET(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const client = ledgerClient(incomingCookie(req));
  const { data, error, response } = await client.GET("/v1/tasks/{tid}", {
    params: { path: { tid: Number(id) } },
  });
  return passJson(response.status, response.ok ? data : (error ?? { detail: "task not found" }), response);
}
