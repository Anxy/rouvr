import { incomingCookie, ledgerClient, passJson } from "@/lib/api/server";

// Knowledge search: shortlist (slug/title/kind/summary/verified/citation).
// Archived entries are excluded server-side unless include_archived.
export async function GET(req: Request) {
  const url = new URL(req.url);
  const q = url.searchParams.get("q") ?? "";
  if (!q.trim()) {
    return Response.json({ detail: "q is required" }, { status: 422 });
  }
  const client = ledgerClient(incomingCookie(req));
  const { data, error, response } = await client.GET("/v1/knowledge/search", {
    params: {
      query: {
        q,
        project: url.searchParams.get("project") ?? undefined,
        n: url.searchParams.get("n") ?? undefined,
        full: url.searchParams.get("full") ?? undefined,
        offset: url.searchParams.get("offset") ?? undefined,
        include_archived: url.searchParams.get("include_archived") ?? undefined,
      } as never,
    },
  });
  return passJson(
    response.status,
    response.ok ? data : (error ?? { detail: "knowledge search failed" }),
    response,
  );
}
