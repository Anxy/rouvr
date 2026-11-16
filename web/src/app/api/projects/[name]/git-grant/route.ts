import {
  incomingCookie,
  jsonThrough,
  LEDGER_URL,
  passJson,
} from "@/lib/api/server";

/** Accept a project-default GitHub PAT (onboarding). Token is never echoed. */
export async function POST(
  req: Request,
  { params }: { params: Promise<{ name: string }> },
) {
  const { name } = await params;
  const body = (await req.json().catch(() => ({}))) as {
    github_token?: string;
    tenant?: string;
  };
  const csrf = req.headers.get("x-csrf-token") ?? "";
  const cookie = incomingCookie(req);
  const upstream = await fetch(
    `${LEDGER_URL}/v1/projects/${encodeURIComponent(name)}/git-grant`,
    {
      method: "POST",
      headers: {
        "content-type": "application/json",
        ...(csrf ? { "x-csrf-token": csrf } : {}),
        ...(cookie ? { cookie } : {}),
      },
      body: JSON.stringify({
        github_token: body.github_token ?? "",
        ...(body.tenant?.trim() ? { tenant: body.tenant.trim() } : {}),
      }),
    },
  );
  const payload = await upstream.json().catch(() => ({ detail: "git-grant failed" }));
  if (!upstream.ok) {
    return passJson(upstream.status, payload, upstream);
  }
  return jsonThrough(upstream.status, payload, upstream);
}
