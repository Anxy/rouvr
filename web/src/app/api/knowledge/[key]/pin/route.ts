import { incomingCookie, ledgerClient, passJson } from "@/lib/api/server";

// Pin (or unpin) a knowledge entry. Pinned rows get the same rank vote as
// verified rows, so they surface first.
export async function POST(req: Request, { params }: { params: Promise<{ key: string }> }) {
  const { key } = await params;
  const body = (await req.json().catch(() => ({}))) as { pinned?: boolean };
  const client = ledgerClient(incomingCookie(req));
  const csrf = req.headers.get("x-csrf-token") ?? "";
  const { data, error, response } = await client.POST("/v1/knowledge/{kid}/pin", {
    params: { path: { kid: Number(key) } },
    body: { pinned: body.pinned !== false } as never,
    headers: csrf ? { "x-csrf-token": csrf } : {},
  });
  return passJson(
    response.status,
    response.ok ? data : (error ?? { detail: "knowledge pin failed" }),
    response,
  );
}
