import { incomingCookie, ledgerClient, passJson } from "@/lib/api/server";

// Review verdict on a knowledge entry: verified | rejected | needs_revision,
// plus an optional reviewer note. Tenant owners may review any entry in
// their tenant (broader than creator-only confirm).
export async function POST(req: Request, { params }: { params: Promise<{ key: string }> }) {
  const { key } = await params;
  const body = (await req.json().catch(() => ({}))) as {
    verdict?: string;
    note?: string;
  };
  const client = ledgerClient(incomingCookie(req));
  const csrf = req.headers.get("x-csrf-token") ?? "";
  const { data, error, response } = await client.POST("/v1/knowledge/{kid}/review", {
    params: { path: { kid: Number(key) } },
    body: { verdict: body.verdict, note: body.note ?? "" } as never,
    headers: csrf ? { "x-csrf-token": csrf } : {},
  });
  return passJson(
    response.status,
    response.ok ? data : (error ?? { detail: "knowledge review failed" }),
    response,
  );
}
