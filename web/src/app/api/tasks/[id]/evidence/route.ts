import { incomingCookie, jsonThrough, ledgerClient, passJson } from "@/lib/api/server";

// Request-changes step 1: post the feedback as a review-note evidence
// entry (kind review_note -> review.note SSE). The inbox follows with a
// needs_review -> claimed transition; the rework loop picks the task up
// with the note in the coder's next prompt.
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { kind, body } = (await req.json().catch(() => ({}))) as {
    kind?: string;
    body?: unknown;
  };
  if (body === undefined) return jsonThrough(422, { detail: "body is required" });
  const client = ledgerClient(incomingCookie(req));
  const csrf = req.headers.get("x-csrf-token") ?? "";
  const { data, error, response } = await client.POST("/v1/tasks/{tid}/evidence", {
    params: { path: { tid: Number(id) } },
    body: { kind: kind ?? "note", body } as never,
    headers: csrf ? { "x-csrf-token": csrf } : {},
  });
  return passJson(response.status, response.ok ? data : (error ?? { detail: "evidence failed" }), response);
}
