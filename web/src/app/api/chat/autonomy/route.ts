import { incomingCookie, jsonThrough, ledgerClient, passJson } from "@/lib/api/server";

// Plan-gate autonomy setting: session/user level for the scope.
export async function GET(req: Request) {
  const url = new URL(req.url);
  const client = ledgerClient(incomingCookie(req));
  const { data, error, response } = await client.GET("/v1/chat/autonomy", {
    params: {
      query: { project: url.searchParams.get("project") ?? undefined } as never,
    },
  });
  return passJson(
    response.status,
    response.ok ? data : (error ?? { detail: "autonomy lookup failed" }),
    response,
  );
}

export async function POST(req: Request) {
  const { project, autonomy } = (await req.json().catch(() => ({}))) as {
    project?: string;
    autonomy?: string;
  };
  if (!project || !autonomy) {
    return jsonThrough(422, { detail: "project and autonomy are required" });
  }
  const client = ledgerClient(incomingCookie(req));
  const csrf = req.headers.get("x-csrf-token") ?? "";
  const { data, error, response } = await client.POST("/v1/chat/autonomy", {
    body: { project, autonomy } as never,
    headers: csrf ? { "x-csrf-token": csrf } : {},
  });
  return passJson(
    response.status,
    response.ok ? data : (error ?? { detail: "autonomy update failed" }),
    response,
  );
}
