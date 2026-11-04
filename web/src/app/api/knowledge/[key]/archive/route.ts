import { incomingCookie, ledgerClient, passJson } from "@/lib/api/server";

// Archive (or restore) a knowledge entry. Archived rows leave search but
// stay readable by id; history is never deleted.
export async function POST(req: Request, { params }: { params: Promise<{ key: string }> }) {
  const { key } = await params;
  const body = (await req.json().catch(() => ({}))) as { archived?: boolean };
  const client = ledgerClient(incomingCookie(req));
  const csrf = req.headers.get("x-csrf-token") ?? "";
  const { data, error, response } = await client.POST("/v1/knowledge/{kid}/archive", {
    params: { path: { kid: Number(key) } },
    body: { archived: body.archived !== false } as never,
    headers: csrf ? { "x-csrf-token": csrf } : {},
  });
  return passJson(
    response.status,
    response.ok ? data : (error ?? { detail: "knowledge archive failed" }),
    response,
  );
}
