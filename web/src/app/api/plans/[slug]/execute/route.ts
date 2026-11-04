import { incomingCookie, ledgerClient, passJson } from "@/lib/api/server";

// Dispatch a materialized plan: chain tasks stay open/claimable, an
// optional worker principal assigns them server-side (409-style contests
// surface per task, never forced). 422 unless materialized.
export async function POST(req: Request, { params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const body = (await req.json().catch(() => ({}))) as { worker?: string };
  const client = ledgerClient(incomingCookie(req));
  const csrf = req.headers.get("x-csrf-token") ?? "";
  const { data, error, response } = await client.POST("/v1/plans/{slug}/execute", {
    params: { path: { slug } },
    body: { ...(body.worker?.trim() ? { worker: body.worker.trim() } : {}) },
    headers: csrf ? { "x-csrf-token": csrf } : {},
  });
  return passJson(
    response.status,
    response.ok ? data : (error ?? { detail: "plan execute failed" }),
    response,
  );
}
