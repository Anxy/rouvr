import createClient from "openapi-fetch";

// Same-origin workspace proxy (see src/app/api/**). Screens talk only to
// these typed paths; the proxy forwards to the ledger with the session
// cookie. Mirrors the contact-form precedent in ./client.
export interface ProxyPaths {
  "/api/auth/login": {
    post: {
      requestBody: { content: { "application/json": { username: string; password: string; remember?: boolean } } };
      responses: {
        200: { content: { "application/json": LoginReply } };
        401: { content: { "application/json": { detail: string } } };
        422: { content: { "application/json": { detail: string } } };
        429: { content: { "application/json": { detail: string } } };
      };
    };
  };
  "/api/auth/logout": {
    post: {
      responses: { 200: { content: { "application/json": { ok: boolean } } } };
    };
  };
  "/api/auth/session": {
    get: {
      responses: {
        200: { content: { "application/json": LoginReply } };
        401: { content: { "application/json": { detail: string } } };
      };
    };
  };
  "/api/tasks": {
    get: {
      parameters: { query?: { state?: string; limit?: string; offset?: string } };
      responses: {
        200: {
          content: {
            "application/json": { items: TaskRow[]; total?: number; offset?: number };
          };
        };
        401: { content: { "application/json": { detail: string } } };
        422: { content: { "application/json": { detail: string } } };
      };
    };
    post: {
      requestBody: {
        content: {
          "application/json": {
            title: string;
            intent?: string;
            project?: string;
            acceptance_criteria: string[];
          };
        };
      };
      responses: {
        200: { content: { "application/json": TaskRow } };
        401: { content: { "application/json": { detail: string } } };
        404: { content: { "application/json": { detail: string } } };
        422: { content: { "application/json": { detail: string } } };
      };
    };
  };
  "/api/tasks/{id}": {
    get: {
      parameters: { path: { id: string } };
      responses: {
        200: { content: { "application/json": TaskDetail } };
        401: { content: { "application/json": { detail: string } } };
        404: { content: { "application/json": { detail: string } } };
      };
    };
    patch: {
      parameters: { path: { id: string } };
      requestBody: {
        content: {
          "application/json": {
            title?: string;
            intent?: string;
            project?: string;
            idempotency_key: string;
          };
        };
      };
      responses: {
        200: {
          content: {
            "application/json": {
              replayed: boolean;
              task_id: number;
              title?: string;
              intent?: string;
            };
          };
        };
        401: { content: { "application/json": { detail: string } } };
        404: { content: { "application/json": { detail: string } } };
        409: { content: { "application/json": { detail: string } } };
        422: { content: { "application/json": { detail: string } } };
      };
    };
  };
  "/api/tasks/{id}/retire": {
    post: {
      parameters: { path: { id: string } };
      requestBody: {
        content: { "application/json": { reason: string; idempotency_key: string } };
      };
      responses: {
        200: {
          content: {
            "application/json": { replayed: boolean; task_id: number };
          };
        };
        401: { content: { "application/json": { detail: string } } };
        404: { content: { "application/json": { detail: string } } };
        409: { content: { "application/json": { detail: string } } };
        422: { content: { "application/json": { detail: string } } };
      };
    };
  };
  "/api/tasks/{id}/transition": {
    post: {
      parameters: { path: { id: string } };
      requestBody: {
        content: { "application/json": { to: string; idempotency_key?: string } };
      };
      responses: {
        200: { content: { "application/json": unknown } };
        401: { content: { "application/json": { detail: string } } };
        404: { content: { "application/json": { detail: string } } };
        409: { content: { "application/json": { detail: string } } };
        422: { content: { "application/json": { detail: string } } };
      };
    };
  };
  "/api/tasks/{id}/evidence": {
    post: {
      parameters: { path: { id: string } };
      requestBody: {
        content: { "application/json": { kind: string; body: unknown } };
      };
      responses: {
        200: { content: { "application/json": unknown } };
        401: { content: { "application/json": { detail: string } } };
        404: { content: { "application/json": { detail: string } } };
        422: { content: { "application/json": { detail: string } } };
      };
    };
  };
  "/api/tasks/{id}/claim": {
    post: {
      parameters: { path: { id: string } };
      requestBody: {
        content: {
          "application/json": {
            key?: string;
            principal: string;
            attempt_no?: number;
            ttl_s?: number;
          };
        };
      };
      responses: {
        200: { content: { "application/json": unknown } };
        401: { content: { "application/json": { detail: string } } };
        403: { content: { "application/json": { detail: string } } };
        404: { content: { "application/json": { detail: string } } };
        409: { content: { "application/json": { detail: string } } };
        422: { content: { "application/json": { detail: string } } };
      };
    };
  };
  "/api/grants": {
    post: {
      requestBody: {
        content: {
          "application/json": {
            scope: string;
            expires_at: string;
            approved_by: string;
            contract_rev?: string;
          };
        };
      };
      responses: {
        200: { content: { "application/json": GrantRow } };
        401: { content: { "application/json": { detail: string } } };
        403: { content: { "application/json": { detail: string } } };
        422: { content: { "application/json": { detail: string } } };
      };
    };
  };
  "/api/grants/active": {
    get: {
      parameters: { query: { scope: string; contract_rev?: string } };
      responses: {
        200: { content: { "application/json": { active: boolean; grant: GrantRow | null } } };
        401: { content: { "application/json": { detail: string } } };
        422: { content: { "application/json": { detail: string } } };
      };
    };
  };
  "/api/tasks/{id}/seal": {
    post: {
      parameters: { path: { id: string } };
      requestBody: { content: { "application/json": Record<string, never> } };
      responses: {
        200: { content: { "application/json": unknown } };
        401: { content: { "application/json": { detail: string } } };
        404: { content: { "application/json": { detail: string } } };
        409: { content: { "application/json": { detail: string } } };
        422: { content: { "application/json": { detail: string } } };
      };
    };
  };
  "/api/chat": {
    post: {
      requestBody: {
        content: {
          "application/json": {
            message: string;
            project: string;
            message_id?: string;
            autonomy?: string;
            plan_action?: string;
            plan_entry?: number;
          };
        };
      };
      responses: {
        200: { content: { "text/event-stream": unknown } };
        401: { content: { "application/json": { detail: string } } };
        422: { content: { "application/json": { detail: string } } };
      };
    };
  };
  "/api/chat/autonomy": {
    get: {
      parameters: { query?: { project?: string } };
      responses: {
        200: { content: { "application/json": { autonomy: string } } };
        401: { content: { "application/json": { detail: string } } };
      };
    };
    post: {
      requestBody: {
        content: { "application/json": { project: string; autonomy: string } };
      };
      responses: {
        200: { content: { "application/json": { autonomy: string } } };
        401: { content: { "application/json": { detail: string } } };
        422: { content: { "application/json": { detail: string } } };
      };
    };
  };
  "/api/events": {
    get: {
      responses: { 200: { content: { "text/event-stream": unknown } } };
    };
  };
  "/api/projects": {
    get: {
      parameters: { query?: { include_archived?: string } };
      responses: {
        200: { content: { "application/json": { projects: ProjectRow[] } } };
        401: { content: { "application/json": { detail: string } } };
      };
    };
    post: {
      requestBody: {
        content: {
          "application/json": {
            tenant: string;
            project: string;
            title?: string;
            code?: string;
            description?: string;
            domain?: string;
          };
        };
      };
      responses: {
        200: {
          content: {
            "application/json": {
              tenant: string;
              project: string;
              created_at?: string;
              title?: string | null;
              code?: string | null;
              description?: string | null;
              domain?: string | null;
            };
          };
        };
        401: { content: { "application/json": { detail: string } } };
        404: { content: { "application/json": { detail: string } } };
        422: { content: { "application/json": { detail: string } } };
      };
    };
  };
  "/api/projects/{name}": {
    patch: {
      parameters: { path: { name: string } };
      requestBody: {
        content: {
          "application/json": {
            tenant?: string;
            name?: string;
            title?: string;
            code?: string;
            description?: string;
            domain?: string;
            archived?: boolean;
          };
        };
      };
      responses: {
        200: { content: { "application/json": ProjectRow } };
        401: { content: { "application/json": { detail: string } } };
        403: { content: { "application/json": { detail: string } } };
        404: { content: { "application/json": { detail: string } } };
        409: { content: { "application/json": { detail: string } } };
        422: { content: { "application/json": { detail: string } } };
      };
    };
  };
  "/api/knowledge/search": {
    get: {
      parameters: { query?: { q: string; project?: string; n?: string; full?: string; offset?: string } };
      responses: {
        200: { content: { "application/json": { items: KnowledgeHit[] } } };
        401: { content: { "application/json": { detail: string } } };
        422: { content: { "application/json": { detail: string } } };
      };
    };
  };
  "/api/knowledge/{key}": {
    get: {
      parameters: { path: { key: string } };
      responses: {
        200: { content: { "application/json": KnowledgeEntry } };
        401: { content: { "application/json": { detail: string } } };
        404: { content: { "application/json": { detail: string } } };
      };
    };
  };
  "/api/knowledge/{key}/history": {
    get: {
      parameters: { path: { key: string } };
      responses: {
        200: { content: { "application/json": { items: KnowledgeEntry[] } } };
        401: { content: { "application/json": { detail: string } } };
        404: { content: { "application/json": { detail: string } } };
      };
    };
  };
  "/api/knowledge/{key}/usage": {
    get: {
      parameters: { path: { key: string } };
      responses: {
        200: { content: { "application/json": KnowledgeUsage } };
        401: { content: { "application/json": { detail: string } } };
        404: { content: { "application/json": { detail: string } } };
      };
    };
  };
  "/api/knowledge/{key}/confirm": {
    post: {
      parameters: { path: { key: string } };
      responses: {
        200: { content: { "application/json": KnowledgeEntry } };
        401: { content: { "application/json": { detail: string } } };
        403: { content: { "application/json": { detail: string } } };
        404: { content: { "application/json": { detail: string } } };
        422: { content: { "application/json": { detail: string } } };
      };
    };
  };
  "/api/knowledge/{key}/review": {
    post: {
      parameters: { path: { key: string } };
      requestBody: {
        content: {
          "application/json": { verdict: string; note?: string };
        };
      };
      responses: {
        200: { content: { "application/json": KnowledgeEntry } };
        401: { content: { "application/json": { detail: string } } };
        403: { content: { "application/json": { detail: string } } };
        404: { content: { "application/json": { detail: string } } };
        422: { content: { "application/json": { detail: string } } };
      };
    };
  };
  "/api/knowledge/{key}/archive": {
    post: {
      parameters: { path: { key: string } };
      requestBody: {
        content: { "application/json": { archived: boolean } };
      };
      responses: {
        200: { content: { "application/json": KnowledgeEntry } };
        401: { content: { "application/json": { detail: string } } };
        403: { content: { "application/json": { detail: string } } };
        404: { content: { "application/json": { detail: string } } };
        422: { content: { "application/json": { detail: string } } };
      };
    };
  };
  "/api/knowledge/{key}/pin": {
    post: {
      parameters: { path: { key: string } };
      requestBody: {
        content: { "application/json": { pinned: boolean } };
      };
      responses: {
        200: { content: { "application/json": KnowledgeEntry } };
        401: { content: { "application/json": { detail: string } } };
        403: { content: { "application/json": { detail: string } } };
        404: { content: { "application/json": { detail: string } } };
        422: { content: { "application/json": { detail: string } } };
      };
    };
  };
  "/api/plans/{slug}": {
    get: {
      parameters: { path: { slug: string } };
      responses: {
        200: { content: { "application/json": PlanDetail } };
        401: { content: { "application/json": { detail: string } } };
        404: { content: { "application/json": { detail: string } } };
        422: { content: { "application/json": { detail: string } } };
      };
    };
  };
  "/api/plans/{slug}/approve": {
    post: {
      parameters: { path: { slug: string } };
      responses: {
        200: { content: { "application/json": PlanDetail } };
        401: { content: { "application/json": { detail: string } } };
        403: { content: { "application/json": { detail: string } } };
        404: { content: { "application/json": { detail: string } } };
        422: { content: { "application/json": { detail: string } } };
      };
    };
  };
  "/api/plans/{slug}/execute": {
    post: {
      parameters: { path: { slug: string } };
      requestBody: {
        content: { "application/json": { worker?: string } };
      };
      responses: {
        200: { content: { "application/json": PlanExecuteResult } };
        401: { content: { "application/json": { detail: string } } };
        403: { content: { "application/json": { detail: string } } };
        404: { content: { "application/json": { detail: string } } };
        422: { content: { "application/json": { detail: string } } };
      };
    };
  };
  "/api/knowledge/{key}/versions": {
    post: {
      parameters: { path: { key: string } };
      requestBody: {
        content: {
          "application/json": { title: string; body: string; summary?: string };
        };
      };
      responses: {
        200: { content: { "application/json": KnowledgeEntry } };
        401: { content: { "application/json": { detail: string } } };
        403: { content: { "application/json": { detail: string } } };
        404: { content: { "application/json": { detail: string } } };
        409: { content: { "application/json": { detail: string } } };
        422: { content: { "application/json": { detail: string } } };
      };
    };
  };
}

export interface KnowledgeUsage {
  key: string;
  id: number;
  applied_to_tasks: number;
  task_ids?: number[];
  last_used_at: string | null;
}

export interface LoginReply {
  id: string;
  username: string;
  tenant: string;
  role: string;
  csrf_token: string;
}

// One step of the pipeline strip. Values come from the ledger, computed
// ONLY from real rows (contracts / evidence kinds / link receipts / result
// verdicts) — the strip never fetches per-task detail to derive them.
export type PipelineFlag = "done" | "attention" | "pending";

export interface PipelineState {
  contract: PipelineFlag;
  knowledge: PipelineFlag;
  build: PipelineFlag;
  review: PipelineFlag;
  evidence: PipelineFlag;
}

export interface TaskRow {
  id: number;
  title: string;
  state: string;
  tenant: string;
  project: string;
  created_at: string;
  updated_at: string;
  attention?: string;
  pipeline?: PipelineState;
  // Seal flags (ledger-computed from evidence rows + spec presence):
  // sealing — hidden suite pending (async hold, retry seals it);
  // needs_spec — worker host has no exec spec for this project.
  sealing?: boolean;
  needs_spec?: boolean;
  // Latest results.verdict, or null when no result exists. Owner approve
  // is legal only when this is "pass" (ledger transition, non-supervisor).
  head_verdict?: string | null;
}

export interface TaskDetail extends TaskRow {
  intent: string;
  owner: string | null;
  created_by: string;
  contracts: { id: number; body: string }[];
  evidence: { id: number; kind: string; body: string; created_at: string }[];
  receipts: { id: number; action: string; body: string; created_at: string }[];
}

export interface ProjectRow {
  tenant: string;
  project: string;
  title?: string | null;
  code?: string | null;
  description?: string | null;
  domain?: string | null;
  archived?: number | null;
}

export interface KnowledgeHit {
  slug: string;
  title: string;
  kind: string;
  summary: string;
  verified: number | boolean;
  review_state?: string;
  pinned?: number | boolean;
  citation: string;
}

export interface KnowledgeEntry extends KnowledgeHit {
  id: number;
  tenant: string;
  project: string;
  body: string;
  version: number;
  author: string;
  visibility: string;
  review_note?: string;
  archived?: number | boolean;
}

export interface PlanTaskCard {
  id: number;
  title: string;
  state: string;
  link: string;
  worker?: string | null;
  lease_expires_at?: string | null;
}

export interface PlanExecuteResult extends PlanDetail {
  execute_event: number;
  worker: string | null;
  dispatched: number[];
  assigned: number[];
  contested: number[];
}

export interface PlanDetail {
  tenant: string;
  project: string;
  slug: string;
  entry: {
    id: number;
    title: string;
    version: number;
    verified: boolean;
    author: string;
    created_at: string;
  };
  status: "draft" | "agreed" | "materialized";
  slice: { id: number; link: string } | null;
  tasks: PlanTaskCard[];
  replayed?: boolean;
  confirmed?: boolean;
}

export interface GrantRow {
  id: number;
  scope: string;
  contract_rev: string;
  expires_at: string;
  approved_by: string;
  created_at: string;
}

export const ws = createClient<ProxyPaths>();

const CSRF_KEY = "rouvr.csrf_token";

export function csrfToken(): string {
  try {
    return sessionStorage.getItem(CSRF_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setCsrfToken(token: string): void {
  try {
    sessionStorage.setItem(CSRF_KEY, token);
  } catch {
    /* private mode: fall back to memory-only below */
  }
}

export function clearCsrfToken(): void {
  try {
    sessionStorage.removeItem(CSRF_KEY);
  } catch {
    /* ignore */
  }
}

const USER_KEY = "rouvr.username";

// The ledger authenticated this username at login (LoginReply.username)
// and set the session cookie alongside it. The shell greeting reads it
// only while the session probe says authenticated; 401/logout clears it,
// so a stale name can never render past its session.
export function sessionUsername(): string | null {
  try {
    return sessionStorage.getItem(USER_KEY);
  } catch {
    return null;
  }
}

export function setSessionUsername(name: string): void {
  try {
    sessionStorage.setItem(USER_KEY, name);
  } catch {
    /* private mode: greeting falls back to the daypart alone */
  }
}

export function clearSessionUsername(): void {
  try {
    sessionStorage.removeItem(USER_KEY);
  } catch {
    /* ignore */
  }
}

export function csrfHeaders(): Record<string, string> {
  const token = csrfToken();
  return token ? { "x-csrf-token": token } : {};
}

export interface SseFrame {
  event: string;
  data: string;
}

// Parse one SSE text chunk into frames (split on blank lines).
export function parseSseFrames(text: string): SseFrame[] {
  const frames: SseFrame[] = [];
  for (const chunk of text.split("\n\n")) {
    const trimmed = chunk.trim();
    if (!trimmed) continue;
    let event = "";
    let data = "";
    for (const line of trimmed.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data += line.slice(5).trim();
      else if (line.startsWith(":")) continue;
    }
    if (event || data) frames.push({ event, data });
  }
  return frames;
}

// Consume a streaming Response body, forwarding each text piece. The
// caller owns the Response (from ws.* ... response); nothing here calls
// the network directly.
export async function pumpStream(
  response: Response,
  onText: (text: string) => void,
  signal?: AbortSignal,
): Promise<string> {
  const reader = response.body?.getReader();
  if (!reader) return "";
  const decoder = new TextDecoder();
  let all = "";
  for (;;) {
    if (signal?.aborted) {
      await reader.cancel().catch(() => undefined);
      break;
    }
    const { done, value } = await reader.read();
    if (done) break;
    const text = decoder.decode(value, { stream: true });
    all += text;
    onText(text);
  }
  return all;
}
