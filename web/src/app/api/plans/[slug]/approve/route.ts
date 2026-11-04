import { incomingCookie, ledgerClient, passJson } from "@/lib/api/server";

// Approve a plan: confirm the entry, materialize slice + tasks, idempotent.
export async function POST(req: Request, { params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const client = ledgerClient(incomingCookie(req));
  const csrf = req.headers.get("x-csrf-token") ?? "";
  const { data, error, response } = await client.POST("/v1/plans/{slug}/approve", {
    params: { path: { slug } },
    headers: csrf ? { "x-csrf-token": csrf } : {},
  });
  return passJson(
    response.status,
    response.ok ? data : (error ?? { detail: "plan approve failed" }),
    response,
  );
}
