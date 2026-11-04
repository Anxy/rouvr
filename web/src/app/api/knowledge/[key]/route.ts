import { incomingCookie, ledgerClient, passJson } from "@/lib/api/server";

// Knowledge entry detail (latest version; superseded ids resolve forward).
export async function GET(req: Request, { params }: { params: Promise<{ key: string }> }) {
  const { key } = await params;
  const url = new URL(req.url);
  const client = ledgerClient(incomingCookie(req));
  const { data, error, response } = await client.GET("/v1/knowledge/{key}", {
    params: {
      path: { key },
      query: { project: url.searchParams.get("project") ?? undefined } as never,
    },
  });
  return passJson(
    response.status,
    response.ok ? data : (error ?? { detail: "knowledge entry not found" }),
    response,
  );
}
