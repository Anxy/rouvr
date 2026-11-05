import { incomingCookie, ledgerClient, passJson } from "@/lib/api/server";

// The board polls this read for fresh stats + list: never serve a cached
// ledger response.
export const dynamic = "force-dynamic";

// Board: tasks visible to the caller's tenant. Bounded pages: callers pass
// limit/offset for server-side windows; without them the full list comes
// back for client-side grouping.
export async function GET(req: Request) {
  const client = ledgerClient(incomingCookie(req));
  const url = new URL(req.url);
  const state = url.searchParams.get("state") ?? undefined;
  const limit = url.searchParams.get("limit") ?? undefined;
  const offset = url.searchParams.get("offset") ?? undefined;
  const { data, error, response } = await client.GET("/v1/tasks", {
    params: { query: { state, limit, offset } as never },
  });
  return passJson(response.status, response.ok ? data : (error ?? { detail: "tasks failed" }), response);
}

// Create a task with its contract. Acceptance criteria are mandatory —
// the ledger refuses contract-less tasks (422), so the UI collects one
// criterion per line and maps them to the contract body.
export async function POST(req: Request) {
  const body = (await req.json().catch(() => ({}))) as {
    title?: string;
    intent?: string;
    project?: string;
    acceptance_criteria?: string[];
  };
  const criteria = (body.acceptance_criteria ?? []).filter((c) => c.trim());
  const client = ledgerClient(incomingCookie(req));
  const csrf = req.headers.get("x-csrf-token") ?? "";
  const { data, error, response } = await client.POST("/v1/tasks_with_contract", {
    body: {
      title: body.title,
      intent: body.intent ?? "",
      project: body.project,
      contract: { acceptance_criteria: criteria },
    } as never,
    headers: csrf ? { "x-csrf-token": csrf } : {},
  });
  return passJson(response.status, response.ok ? data : (error ?? { detail: "task create failed" }), response);
}
