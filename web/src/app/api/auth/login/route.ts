import { incomingCookie, jsonThrough, ledgerClient } from "@/lib/api/server";

export async function POST(req: Request) {
  const { username, password } = (await req.json().catch(() => ({}))) as {
    username?: string;
    password?: string;
  };
  if (!username || !password) {
    return jsonThrough(422, { detail: "username and password are required" });
  }
  const client = ledgerClient(incomingCookie(req));
  const { data, error, response } = await client.POST("/auth/login", {
    body: { username, password } as never,
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
  });
  if (!response.ok) {
    return jsonThrough(response.status, error ?? { detail: "login failed" }, response);
  }
  return jsonThrough(200, data, response);
}
