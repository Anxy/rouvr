import { incomingCookie, ledgerClient, passJson } from "@/lib/api/server";

// Knowledge version chain, oldest first. Append-only history.
export async function GET(_req: Request, { params }: { params: Promise<{ key: string }> }) {
  const { key } = await params;
  const client = ledgerClient(incomingCookie(_req));
  const { data, error, response } = await client.GET("/v1/knowledge/{kid}/history", {
    params: { path: { kid: Number(key) } },
  });
  return passJson(
    response.status,
    response.ok ? data : (error ?? { detail: "knowledge history not found" }),
    response,
  );
}
