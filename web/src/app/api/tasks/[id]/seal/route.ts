import { incomingCookie, jsonThrough, ledgerClient, passJson } from "@/lib/api/server";

// Retry the hidden-suite seal for one task (async-hold fallback).
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const client = ledgerClient(incomingCookie(req));
  const csrf = req.headers.get("x-csrf-token") ?? "";
  const { data, error, response } = await client.POST("/v1/tasks/{tid}/seal", {
    params: { path: { tid: Number(id) } },
    body: {},
    headers: csrf ? { "x-csrf-token": csrf } : {},
  });
  return passJson(response.status, response.ok ? data : (error ?? { detail: "seal failed" }), response);
}
