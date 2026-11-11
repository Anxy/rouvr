import { incomingCookie, jsonThrough, ledgerClient, passJson, streamThrough } from "@/lib/api/server";

// Supervisor chat: streams the model turn (SSE) straight through, including
// the Slice C intake fields (autonomy override, plan actions).
export async function POST(req: Request) {
  const { message, project, message_id, autonomy, plan_action, plan_entry, surface } =
    (await req.json().catch(() => ({}))) as {
      message?: string;
      project?: string;
      message_id?: string;
      autonomy?: string;
      plan_action?: string;
      plan_entry?: number;
      surface?: string;
    };
  if (!message || !project) {
    return jsonThrough(422, { detail: "message and project are required" });
  }
  if (surface != null && surface !== "supervisor" && surface !== "planner") {
    return jsonThrough(422, { detail: "surface must be supervisor or planner" });
  }
  const client = ledgerClient(incomingCookie(req));
  const csrf = req.headers.get("x-csrf-token") ?? "";
  const { data, error, response } = await client.POST("/v1/chat", {
    parseAs: "stream",
    body: {
      message,
      project,
      message_id,
      autonomy,
      plan_action,
      plan_entry,
      surface: surface ?? "supervisor",
    } as never,
    headers: csrf ? { "x-csrf-token": csrf } : {},
  });
  if (!response.ok) {
    return passJson(response.status, error ?? { detail: "chat failed" }, response);
  }
  return streamThrough(data as ReadableStream<Uint8Array> | null, response);
}
