import { incomingCookie, ledgerClient, passJson } from "@/lib/api/server";

// Knowledge usage line: applied-task count + last use for one entry.
// Unknown keys 404; unused entries read zero (never invented client-side).
export async function GET(_req: Request, { params }: { params: Promise<{ key: string }> }) {
  const { key } = await params;
  const client = ledgerClient(incomingCookie(_req));
  const { data, error, response } = await client.GET("/v1/knowledge/{key}/usage", {
    params: { path: { key } },
  });
  return passJson(
    response.status,
    response.ok ? data : (error ?? { detail: "knowledge usage not found" }),
    response,
  );
}
