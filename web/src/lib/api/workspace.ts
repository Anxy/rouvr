import createClient from "openapi-fetch";

// Same-origin workspace proxy (see src/app/api/**). Screens talk only to
// these typed paths; the proxy forwards to the ledger with the session
// cookie. Mirrors the contact-form precedent in ./client.
export interface ProxyPaths {
  "/api/auth/login": {
    post: {
      requestBody: { content: { "application/json": { username: string; password: string } } };
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
  "/api/tasks": {
    get: {
      responses: {
        200: { content: { "application/json": { items: TaskRow[] } } };
        401: { content: { "application/json": { detail: string } } };
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
      responses: {
        200: { content: { "application/json": { projects: ProjectRow[] } } };
        401: { content: { "application/json": { detail: string } } };
      };
    };
  };
  "/api/knowledge/search": {
    get: {
      parameters: { query?: { q: string; project?: string; n?: string; full?: string } };
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
}

export interface KnowledgeHit {
  slug: string;
  title: string;
  kind: string;
  summary: string;
  verified: number | boolean;
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
