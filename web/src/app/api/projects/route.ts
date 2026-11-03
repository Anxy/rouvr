import { incomingCookie, ledgerClient, passJson } from "@/lib/api/server";

export async function GET(req: Request) {
  const client = ledgerClient(incomingCookie(req));
  const { data, error, response } = await client.GET("/v1/projects");
  return passJson(response.status, response.ok ? data : (error ?? { detail: "projects failed" }), response);
}
