import { incomingCookie, ledgerClient, passJson } from "@/lib/api/server";

/** Accept a per-task GitHub PAT for push/PR. Token is never echoed. */
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = (await req.json().catch(() => ({}))) as { github_token?: string };
  const client = ledgerClient(incomingCookie(req));
  const csrf = req.headers.get("x-csrf-token") ?? "";
  const { data, error, response } = await client.POST("/v1/tasks/{tid}/git-grant", {
    params: { path: { tid: Number(id) } },
    body: { github_token: body.github_token ?? "" },
    headers: csrf ? { "x-csrf-token": csrf } : {},
  });
  return passJson(
    response.status,
    response.ok ? data : (error ?? { detail: "git-grant failed" }),
    response,
  );
}
