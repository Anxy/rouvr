import { incomingCookie, ledgerClient, passJson } from "@/lib/api/server";

// Board: tasks visible to the caller's tenant.
export async function GET(req: Request) {
  const client = ledgerClient(incomingCookie(req));
  const url = new URL(req.url);
  const state = url.searchParams.get("state") ?? undefined;
  const { data, error, response } = await client.GET("/v1/tasks", {
    params: { query: { state } as never },
  });
  return passJson(response.status, response.ok ? data : (error ?? { detail: "tasks failed" }), response);
}
