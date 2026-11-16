import { incomingCookie, LEDGER_URL, passJson } from "@/lib/api/server";

/** Start Temporal delivery for every chain task on a materialized plan. */
export async function POST(req: Request, { params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const body = (await req.json().catch(() => ({}))) as { requested_by?: string };
  const csrf = req.headers.get("x-csrf-token") ?? "";
  const headers: Record<string, string> = {
    "content-type": "application/json",
    accept: "application/json",
  };
  const cookie = incomingCookie(req);
  if (cookie) headers.cookie = cookie;
  if (csrf) headers["x-csrf-token"] = csrf;
  const response = await fetch(
    `${LEDGER_URL.replace(/\/$/, "")}/v1/plans/${encodeURIComponent(slug)}/start`,
    {
      method: "POST",
      headers,
      body: JSON.stringify({
        ...(body.requested_by?.trim() ? { requested_by: body.requested_by.trim() } : {}),
      }),
    },
  );
  const data = await response.json().catch(() => ({ detail: "plan start failed" }));
  return passJson(response.status, data, response);
}
