import { incomingCookie, ledgerClient, passJson, streamThrough } from "@/lib/api/server";

// Live ledger events for the task page. Pass ?live=1 to tail.
export async function GET(req: Request) {
  const url = new URL(req.url);
  const client = ledgerClient(incomingCookie(req));
  const { data, error, response } = await client.GET("/v1/events", {
    parseAs: "stream",
    params: {
      query: {
        cursor: url.searchParams.get("cursor") ?? undefined,
        live: url.searchParams.get("live") ?? undefined,
        limit: url.searchParams.get("limit") ?? undefined,
      } as never,
    },
  });
  if (!response.ok) {
    return passJson(response.status, error ?? { detail: "events failed" }, response);
  }
  return streamThrough(data as ReadableStream<Uint8Array> | null, response);
}
