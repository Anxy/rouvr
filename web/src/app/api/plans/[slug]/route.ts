import { incomingCookie, ledgerClient, passJson } from "@/lib/api/server";

// Plan detail: the entry plus its derived status and slice->task chain.
export async function GET(_req: Request, { params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const client = ledgerClient(incomingCookie(_req));
  const { data, error, response } = await client.GET("/v1/plans/{slug}", {
    params: { path: { slug } },
  });
  return passJson(
    response.status,
    response.ok ? data : (error ?? { detail: "plan lookup failed" }),
    response,
  );
}
