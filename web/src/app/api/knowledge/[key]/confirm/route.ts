import { incomingCookie, ledgerClient, passJson } from "@/lib/api/server";

// Confirm (verify) a knowledge entry. Only the creator or an admin
// confirms; refusal surfaces as an error for the caller to show.
export async function POST(_req: Request, { params }: { params: Promise<{ key: string }> }) {
  const { key } = await params;
  const client = ledgerClient(incomingCookie(_req));
  const csrf = _req.headers.get("x-csrf-token") ?? "";
  const { data, error, response } = await client.POST("/v1/knowledge/{kid}/confirm", {
    params: { path: { kid: Number(key) } },
    headers: csrf ? { "x-csrf-token": csrf } : {},
  });
  return passJson(
    response.status,
    response.ok ? data : (error ?? { detail: "knowledge confirm failed" }),
    response,
  );
}
