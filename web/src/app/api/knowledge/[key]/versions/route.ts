import { incomingCookie, jsonThrough, ledgerClient, passJson } from "@/lib/api/server";

// Write a new version of a knowledge entry (append-only: the old row is
// superseded, never rewritten). Title and body are required; summary
// follows the newest body unless the caller pins one.
export async function POST(req: Request, { params }: { params: Promise<{ key: string }> }) {
  const { key } = await params;
  const { title, body, summary } = (await req.json().catch(() => ({}))) as {
    title?: string;
    body?: string;
    summary?: string;
  };
  if (!title?.trim() || !body?.trim()) {
    return jsonThrough(422, { detail: "title and body are required for a new version" });
  }
  const client = ledgerClient(incomingCookie(req));
  const csrf = req.headers.get("x-csrf-token") ?? "";
  const { data, error, response } = await client.POST("/v1/knowledge/{kid}/versions", {
    params: { path: { kid: Number(key) } },
    body: {
      title: title.trim(),
      body,
      ...(summary !== undefined ? { summary } : {}),
    } as never,
    headers: csrf ? { "x-csrf-token": csrf } : {},
  });
  return passJson(
    response.status,
    response.ok ? data : (error ?? { detail: "knowledge new version failed" }),
    response,
  );
}
