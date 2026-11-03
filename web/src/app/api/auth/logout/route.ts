import { incomingCookie, jsonThrough, ledgerClient } from "@/lib/api/server";

export async function POST(req: Request) {
  const client = ledgerClient(incomingCookie(req));
  const { data, error, response } = await client.POST("/auth/logout");
  if (!response.ok) {
    return jsonThrough(response.status, error ?? { detail: "logout failed" }, response);
  }
  return jsonThrough(200, data ?? { ok: true }, response);
}
