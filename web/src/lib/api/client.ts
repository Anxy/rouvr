/**
 * Typed client for the Rouvr ledger API (FL-A1).
 *
 * This module holds the ONLY network calls in the web app: every screen must
 * go through `createRouvrClient` (ledger) or the shared `requestJson` helper
 * (same-origin Next.js routes, see ./contact). No other file may call fetch.
 */
import type { paths } from "./schema";
import type {
  ActiveGrant,
  Assignment,
  Attempt,
  AttemptResult,
  Contract,
  CreateGrantInput,
  CreateKnowledgeInput,
  CreateTaskInput,
  CreateTaskWithContractInput,
  CreateUserInput,
  CuratorSuggestion,
  EvidenceInput,
  EvidenceRow,
  Grant,
  Health,
  KnowledgeEntry,
  KnowledgeShortlist,
  KnowledgeStoreItem,
  Lease,
  OnboardCompleteInput,
  OnboardCompleteResult,
  OnboardRegisterInput,
  Project,
  Receipt,
  Task,
  TaskState,
  Tenant,
  TransitionInput,
  TransitionResult,
  User,
  VersionKnowledgeInput,
} from "./types";

/** Raw OpenAPI path table (see ./schema). Re-exported for query helpers. */
export type ApiPaths = paths;

export type TokenSource = string | (() => string | undefined);

export interface ClientOptions {
  /** Ledger base URL, e.g. https://ledger.internal. Default: same origin. */
  baseUrl?: string;
  /** Rouvr token, sent as X-Rouvr-Token. */
  token?: TokenSource;
  /** Hyeve JWT, sent as Authorization: Bearer. */
  bearer?: TokenSource;
  fetchImpl?: typeof fetch;
}

export type QueryValue = string | number | boolean | null | undefined;

function resolveToken(source: TokenSource | undefined): string | undefined {
  if (!source) return undefined;
  const value = typeof source === "function" ? source() : source;
  return value || undefined;
}

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly body: unknown;

  constructor(status: number, detail: string, body: unknown) {
    super(detail || `Request failed (${status})`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.body = body;
  }
}

function extractDetail(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail) return detail;
    if (Array.isArray(detail)) {
      const first = detail[0] as { msg?: unknown } | undefined;
      if (first && typeof first.msg === "string") return first.msg;
    }
  }
  return fallback;
}

export interface RequestOptions {
  method?: "GET" | "POST";
  baseUrl?: string;
  query?: Record<string, QueryValue>;
  body?: unknown;
  headers?: Record<string, string>;
  fetchImpl?: typeof fetch;
}

/**
 * Single fetch call site for the whole app. Same-origin by default; pass
 * baseUrl for the ledger. Throws ApiError on non-2xx.
 */
export async function requestJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const impl = options.fetchImpl ?? fetch;
  const query = Object.entries(options.query ?? {})
    .filter(([, value]) => value !== undefined && value !== null)
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
    .join("&");
  const url = `${options.baseUrl ?? ""}${path}${query ? `?${query}` : ""}`;
  const headers: Record<string, string> = { ...(options.headers ?? {}) };
  const init: RequestInit = { method: options.method ?? "GET", headers };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  const response = await impl(url, init);
  const data = (await response.json().catch(() => null)) as unknown;
  if (!response.ok) {
    throw new ApiError(response.status, extractDetail(data, `Request failed (${response.status})`), data);
  }
  return data as T;
}

export interface RouvrClient {
  health(): Promise<Health>;
  // users / tenants / projects
  createUser(input: CreateUserInput): Promise<User>;
  listUsers(): Promise<{ items: User[] }>;
  disableUser(username: string): Promise<{ disabled: string }>;
  createTenant(tenant: string): Promise<Tenant>;
  createProject(tenant: string, project: string): Promise<Project>;
  listProjects(tenant?: string): Promise<{ projects: Project[] }>;
  // onboarding
  onboardRegister(input: OnboardRegisterInput): Promise<Record<string, unknown>>;
  onboardLogin(username: string, password: string): Promise<Record<string, unknown>>;
  onboardComplete(input: OnboardCompleteInput): Promise<OnboardCompleteResult>;
  // tasks
  createTask(input: CreateTaskInput): Promise<Task>;
  createTaskWithContract(input: CreateTaskWithContractInput): Promise<Task>;
  listTasks(query?: { state?: TaskState | string; tenant?: string }): Promise<{ items: Task[] }>;
  getTask(tid: number): Promise<Task>;
  addContract(tid: number, contract: Record<string, unknown>): Promise<Contract>;
  addEvidence(tid: number, input: EvidenceInput): Promise<EvidenceRow>;
  lookupEvidence(tid: number, kind: string, fence: string): Promise<EvidenceRow>;
  transitionTask(tid: number, input: TransitionInput): Promise<TransitionResult>;
  getBinding(tid: number): Promise<{ assignment: Assignment; lease: Lease | null }>;
  claimAssignment(
    tid: number,
    input: { key: string; principal: string; attempt_no?: number; ttl_s?: number },
  ): Promise<{ replayed: boolean; assignment: Assignment; lease_epoch: number; lease: Lease }>;
  renewLease(
    tid: number,
    input: { key: string; holder: string; expected_lease_epoch: number; ttl_s?: number },
  ): Promise<{ replayed: boolean; lease: Lease }>;
  reclaimAssignment(tid: number, key: string): Promise<{ replayed: boolean; reclaimed: boolean }>;
  openAttempt(
    tid: number,
    input: { key: string; attempt_no: number; lease_epoch: number },
  ): Promise<{ replayed: boolean; attempt: Attempt }>;
  recordResult(
    tid: number,
    input: { key: string; attempt_id: number; lease_epoch: number; verdict: string; result?: unknown },
  ): Promise<{ replayed: boolean; result: AttemptResult }>;
  // grants
  createGrant(input: CreateGrantInput): Promise<Grant>;
  activeGrant(scope: string, contractRev?: string): Promise<ActiveGrant>;
  // knowledge
  createKnowledge(input: CreateKnowledgeInput): Promise<KnowledgeEntry>;
  searchKnowledge(
    q: string,
    query?: { project?: string; n?: number; full?: boolean },
  ): Promise<{ items: KnowledgeShortlist[] | KnowledgeEntry[] }>;
  getKnowledge(key: string | number, project?: string): Promise<KnowledgeEntry>;
  knowledgeHistory(kid: number): Promise<{ items: KnowledgeEntry[] }>;
  versionKnowledge(kid: number, input: VersionKnowledgeInput): Promise<KnowledgeEntry>;
  confirmKnowledge(kid: number): Promise<KnowledgeEntry>;
  knowledgeStore(
    tenant: string,
    project: string,
    items: KnowledgeStoreItem[],
  ): Promise<{ items: KnowledgeEntry[] }>;
  knowledgeCompact(
    tenant: string,
    project: string,
    title?: string,
  ): Promise<{ snapshot: KnowledgeEntry; sources: string[] }>;
  knowledgeSnapshot(
    tenant: string,
    project: string,
  ): Promise<{ path: string; count: number; citations: string[] }>;
  knowledgeStoreAndClose(
    tenant: string,
    project: string,
    items: KnowledgeStoreItem[],
    title?: string,
  ): Promise<{
    stored: KnowledgeEntry[];
    compact: KnowledgeEntry;
    sources: string[];
    snapshot: { path: string; count: number; citations: string[] };
  }>;
  knowledgeRules(project?: string): Promise<{ items: KnowledgeEntry[]; citations: string[] }>;
  curateKnowledge(
    tenant: string,
    project: string,
  ): Promise<{ suggestions: CuratorSuggestion[]; count: number; contradictions_mode: string }>;
}

export function createRouvrClient(options: ClientOptions = {}): RouvrClient {
  const baseUrl = options.baseUrl ?? "";
  const fetchImpl = options.fetchImpl;
  const authHeaders = (): Record<string, string> => {
    const headers: Record<string, string> = {};
    const token = resolveToken(options.token);
    if (token) headers["X-Rouvr-Token"] = token;
    const bearer = resolveToken(options.bearer);
    if (bearer) headers["Authorization"] = `Bearer ${bearer}`;
    return headers;
  };
  const get = <T,>(path: string, query?: Record<string, QueryValue>): Promise<T> =>
    requestJson<T>(path, { method: "GET", baseUrl, query, headers: authHeaders(), fetchImpl });
  const post = <T,>(path: string, body?: unknown): Promise<T> =>
    requestJson<T>(path, { method: "POST", baseUrl, body, headers: authHeaders(), fetchImpl });
  const taskPath = (tid: number, suffix = ""): string => `/v1/tasks/${tid}${suffix}`;

  return {
    health: () => get<Health>("/health"),
    createUser: (input) => post<User>("/v1/users", input),
    listUsers: () => get<{ items: User[] }>("/v1/users"),
    disableUser: (username) => post<{ disabled: string }>(`/v1/users/${encodeURIComponent(username)}/disable`),
    createTenant: (tenant) => post<Tenant>("/v1/tenants", { tenant }),
    createProject: (tenant, project) => post<Project>("/v1/projects", { tenant, project }),
    listProjects: (tenant) => get<{ projects: Project[] }>("/v1/projects", { tenant }),
    onboardRegister: (input) => post<Record<string, unknown>>("/v1/onboard/register", input),
    onboardLogin: (username, password) =>
      post<Record<string, unknown>>("/v1/onboard/login", { username, password }),
    onboardComplete: (input) => post<OnboardCompleteResult>("/v1/onboard/complete", input),
    createTask: (input) => post<Task>("/v1/tasks", input),
    createTaskWithContract: (input) => post<Task>("/v1/tasks_with_contract", input),
    listTasks: (query) => get<{ items: Task[] }>("/v1/tasks", query),
    getTask: (tid) => get<Task>(taskPath(tid)),
    addContract: (tid, contract) => post<Contract>(taskPath(tid, "/contracts"), contract),
    addEvidence: (tid, input) => post<EvidenceRow>(taskPath(tid, "/evidence"), input),
    lookupEvidence: (tid, kind, fence) =>
      get<EvidenceRow>(taskPath(tid, "/evidence/lookup"), { kind, fence }),
    transitionTask: (tid, input) => post<TransitionResult>(taskPath(tid, "/transition"), input),
    getBinding: (tid) => get<{ assignment: Assignment; lease: Lease | null }>(taskPath(tid, "/binding")),
    claimAssignment: (tid, input) =>
      post<{ replayed: boolean; assignment: Assignment; lease_epoch: number; lease: Lease }>(
        taskPath(tid, "/assignments/claim"),
        input,
      ),
    renewLease: (tid, input) =>
      post<{ replayed: boolean; lease: Lease }>(taskPath(tid, "/leases/renew"), input),
    reclaimAssignment: (tid, key) =>
      post<{ replayed: boolean; reclaimed: boolean }>(taskPath(tid, "/assignments/reclaim"), { key }),
    openAttempt: (tid, input) =>
      post<{ replayed: boolean; attempt: Attempt }>(taskPath(tid, "/attempts"), input),
    recordResult: (tid, input) =>
      post<{ replayed: boolean; result: AttemptResult }>(taskPath(tid, "/results"), input),
    createGrant: (input) => post<Grant>("/v1/grants", input),
    activeGrant: (scope, contractRev) =>
      get<ActiveGrant>("/v1/grants/active", { scope, contract_rev: contractRev }),
    createKnowledge: (input) => post<KnowledgeEntry>("/v1/knowledge", input),
    searchKnowledge: (q, query) =>
      get<{ items: KnowledgeShortlist[] | KnowledgeEntry[] }>("/v1/knowledge/search", {
        q,
        ...query,
      }),
    getKnowledge: (key, project) =>
      get<KnowledgeEntry>(`/v1/knowledge/${encodeURIComponent(String(key))}`, { project }),
    knowledgeHistory: (kid) => get<{ items: KnowledgeEntry[] }>(`/v1/knowledge/${kid}/history`),
    versionKnowledge: (kid, input) =>
      post<KnowledgeEntry>(`/v1/knowledge/${kid}/versions`, input),
    confirmKnowledge: (kid) => post<KnowledgeEntry>(`/v1/knowledge/${kid}/confirm`),
    knowledgeStore: (tenant, project, items) =>
      post<{ items: KnowledgeEntry[] }>("/v1/knowledge/store", { tenant, project, items }),
    knowledgeCompact: (tenant, project, title) =>
      post<{ snapshot: KnowledgeEntry; sources: string[] }>("/v1/knowledge/compact", {
        tenant,
        project,
        title,
      }),
    knowledgeSnapshot: (tenant, project) =>
      post<{ path: string; count: number; citations: string[] }>("/v1/knowledge/snapshot", {
        tenant,
        project,
      }),
    knowledgeStoreAndClose: (tenant, project, items, title) =>
      post<{
        stored: KnowledgeEntry[];
        compact: KnowledgeEntry;
        sources: string[];
        snapshot: { path: string; count: number; citations: string[] };
      }>("/v1/knowledge/store-and-close", { tenant, project, items, title }),
    knowledgeRules: (project) =>
      get<{ items: KnowledgeEntry[]; citations: string[] }>("/v1/knowledge-rules", { project }),
    curateKnowledge: (tenant, project) =>
      post<{ suggestions: CuratorSuggestion[]; count: number; contradictions_mode: string }>(
        "/v1/knowledge/curate",
        { tenant, project },
      ),
  };
}
