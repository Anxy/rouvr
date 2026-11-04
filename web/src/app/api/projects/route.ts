import { incomingCookie, jsonThrough, ledgerClient, passJson } from "@/lib/api/server";

export async function GET(req: Request) {
  const client = ledgerClient(incomingCookie(req));
  const { data, error, response } = await client.GET("/v1/projects");
  return passJson(response.status, response.ok ? data : (error ?? { detail: "projects failed" }), response);
}

export async function POST(req: Request) {
  const body = (await req.json().catch(() => ({}))) as {
    tenant?: string;
    project?: string;
    title?: string;
    code?: string;
    description?: string;
    domain?: string;
  };
  if (!body.project?.trim()) {
    return jsonThrough(422, { detail: "project name is required" });
  }
  const meta: Record<string, string> = {};
  for (const field of ["title", "code", "description", "domain"] as const) {
    const value = body[field]?.trim();
    if (value) meta[field] = value;
  }
  const client = ledgerClient(incomingCookie(req));
  const csrf = req.headers.get("x-csrf-token") ?? "";
  const { data, error, response } = await client.POST("/v1/projects", {
    body: {
      ...(body.tenant?.trim() ? { tenant: body.tenant.trim() } : {}),
      project: body.project.trim(),
      ...meta,
    } as never,
    headers: csrf ? { "x-csrf-token": csrf } : {},
  });
  return passJson(response.status, response.ok ? data : (error ?? { detail: "project create failed" }), response);
}
