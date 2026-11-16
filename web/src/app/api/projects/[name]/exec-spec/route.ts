import { incomingCookie, ledgerClient, passJson } from "@/lib/api/server";

export async function GET(req: Request, { params }: { params: Promise<{ name: string }> }) {
  const { name } = await params;
  const url = new URL(req.url);
  const tenant = url.searchParams.get("tenant") ?? undefined;
  const client = ledgerClient(incomingCookie(req));
  const { data, error, response } = await client.GET("/v1/projects/{project}/exec-spec", {
    params: {
      path: { project: decodeURIComponent(name) },
      query: tenant ? { tenant } : {},
    },
  });
  return passJson(
    response.status,
    response.ok ? data : (error ?? { detail: "exec-spec lookup failed" }),
    response,
  );
}

export async function PUT(req: Request, { params }: { params: Promise<{ name: string }> }) {
  const { name } = await params;
  const body = (await req.json().catch(() => ({}))) as {
    tenant?: string;
    repo?: string;
    base_branch?: string;
    test_command?: string[];
    timeout_s?: number;
  };
  const client = ledgerClient(incomingCookie(req));
  const csrf = req.headers.get("x-csrf-token") ?? "";
  const { data, error, response } = await client.PUT("/v1/projects/{project}/exec-spec", {
    params: { path: { project: decodeURIComponent(name) } },
    body: body as never,
    headers: csrf ? { "x-csrf-token": csrf } : {},
  });
  return passJson(
    response.status,
    response.ok ? data : (error ?? { detail: "exec-spec save failed" }),
    response,
  );
}
