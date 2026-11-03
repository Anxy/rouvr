import { incomingCookie, jsonThrough, ledgerClient, passJson, streamThrough } from "@/lib/api/server";

// Supervisor chat: streams the model turn (SSE) straight through.
export async function POST(req: Request) {
  const { message, project, message_id } = (await req.json().catch(() => ({}))) as {
    message?: string;
    project?: string;
    message_id?: string;
  };
  if (!message || !project) {
    return jsonThrough(422, { detail: "message and project are required" });
  }
  const client = ledgerClient(incomingCookie(req));
  const csrf = req.headers.get("x-csrf-token") ?? "";
  const { data, error, response } = await client.POST("/v1/chat", {
    parseAs: "stream",
    body: { message, project, message_id } as never,
    headers: csrf ? { "x-csrf-token": csrf } : {},
  });
  if (!response.ok) {
    return passJson(response.status, error ?? { detail: "chat failed" }, response);
  }
  return streamThrough(data as ReadableStream<Uint8Array> | null, response);
}
