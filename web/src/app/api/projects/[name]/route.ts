import { incomingCookie, ledgerClient, passJson } from "@/lib/api/server";

// Edit a project: rename (name), metadata, archived flag.
export async function PATCH(req: Request, { params }: { params: Promise<{ name: string }> }) {
  const { name } = await params;
  const body = (await req.json().catch(() => ({}))) as {
    name?: string;
    title?: string;
    code?: string;
    description?: string;
    domain?: string;
    archived?: boolean;
  };
  const client = ledgerClient(incomingCookie(req));
  const csrf = req.headers.get("x-csrf-token") ?? "";
  const { data, error, response } = await client.PATCH("/v1/projects/{project}", {
    params: { path: { project: decodeURIComponent(name) } },
    body: body as never,
    headers: csrf ? { "x-csrf-token": csrf } : {},
  });
  return passJson(
    response.status,
    response.ok ? data : (error ?? { detail: "project update failed" }),
    response,
  );
}
