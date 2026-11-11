import { incomingCookie, ledgerClient, passJson } from "@/lib/api/server";

// Cookie session, read back. A new tab has the httponly cookie and not the
// login reply; this GET refills username and csrf_token for later posts.
export async function GET(req: Request) {
  const client = ledgerClient(incomingCookie(req));
  const { data, error, response } = await client.GET("/auth/session");
  return passJson(
    response.status,
    response.ok ? data : (error ?? { detail: "session failed" }),
    response,
  );
}
