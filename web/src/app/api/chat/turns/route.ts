import { incomingCookie, jsonThrough, ledgerClient, passJson } from "@/lib/api/server";

// Append-only conversation log for the active project scope.
export async function GET(req: Request) {
  const url = new URL(req.url);
  const project = url.searchParams.get("project") ?? "";
  const message_id = url.searchParams.get("message_id") ?? undefined;
  const surface = url.searchParams.get("surface") ?? "supervisor";
  if (!project) {
    return jsonThrough(422, { detail: "project is required" });
  }
  if (surface !== "supervisor" && surface !== "planner") {
    return jsonThrough(422, { detail: "surface must be supervisor or planner" });
  }
  const client = ledgerClient(incomingCookie(req));
  const { data, error, response } = await client.GET("/v1/chat/turns", {
    params: {
      query: {
        project,
        surface,
        ...(message_id ? { message_id } : {}),
      },
    },
  });
  return passJson(response.status, response.ok ? data : (error ?? { detail: "turns failed" }), response);
}
