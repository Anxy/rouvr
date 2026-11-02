/** Domain types for the Rouvr ledger API (FL-A1). Shapes mirror ledger/app.py. */

export type TaskState = "open" | "claimed" | "needs_review" | "blocked" | "failed" | "done";

export interface Task {
  id: number;
  title: string;
  intent: string;
  state: TaskState;
  owner: string | null;
  created_at: string;
  updated_at: string;
  created_by: string;
  tenant: string;
  project: string;
  contracts?: Contract[];
  evidence?: EvidenceRow[];
  receipts?: Receipt[];
}

export interface Contract {
  id: number;
  task_id: number;
  body: string;
  sha256: string;
  created_at: string;
}

export interface EvidenceRow {
  id: number;
  task_id: number;
  kind: string;
  body: string;
  sha256: string;
  created_at: string;
}

export interface Receipt {
  id: number;
  task_id: number;
  idempotency_key: string;
  action: string;
  body: string;
  created_at: string;
}

export interface TaskContract {
  acceptance_criteria: string[];
  [key: string]: unknown;
}

export interface CreateTaskInput {
  tenant: string;
  project: string;
  title?: string;
  intent?: string;
}

export interface CreateTaskWithContractInput extends CreateTaskInput {
  contract: TaskContract;
}

export interface TransitionInput {
  idempotency_key: string;
  to: TaskState;
  owner?: string;
  note?: string;
}

export interface TransitionResult {
  replayed: boolean;
  receipt: Receipt;
}

export interface EvidenceInput {
  kind?: string;
  body?: unknown;
}

export interface Assignment {
  id: number;
  task_id: number;
  principal: string;
  attempt_no: number;
  status: string;
  created_at: string;
}

export interface Lease {
  task_id: number;
  holder: string;
  lease_epoch: number;
  expires_at: string;
  updated_at: string;
}

export interface Attempt {
  id: number;
  task_id: number;
  attempt_no: number;
  lease_epoch: number;
  created_at: string;
}

export interface AttemptResult {
  id: number;
  task_id: number;
  attempt_id: number;
  lease_epoch: number;
  verdict: string;
  body: string;
  created_at: string;
}

export interface User {
  username: string;
  display_name: string;
  tenant: string;
  disabled: number;
  created_at: string;
}

export interface CreateUserInput {
  username: string;
  token_hash: string;
  tenant: string;
  display_name?: string;
}

export interface Tenant {
  tenant: string;
  created_at: string;
}

export interface Project {
  tenant: string;
  project: string;
  created_at?: string;
}

export interface Grant {
  id: number;
  scope: string;
  contract_rev: string;
  expires_at: string;
  approved_by: string;
  created_at: string;
}

export interface CreateGrantInput {
  scope: string;
  expires_at: string;
  approved_by: string;
  contract_rev?: string;
}

export interface ActiveGrant {
  active: boolean;
  grant: Grant | null;
}

export type KnowledgeKind =
  | "user_profile"
  | "project_profile"
  | "note"
  | "decision"
  | "rule"
  | "snapshot";

export type KnowledgeVisibility = "restricted" | "company" | "public";

export interface KnowledgeEntry {
  id: number;
  tenant: string;
  project: string;
  kind: string;
  title: string;
  body: string;
  version: number;
  supersedes_id: number | null;
  verified: number;
  author: string;
  visibility: string;
  allowed_users: string[];
  slug: string;
  tags: string[];
  summary: string;
  created_at: string;
  citation: string;
}

export interface KnowledgeShortlist {
  slug: string;
  title: string;
  kind: string;
  summary: string;
  verified: number;
  citation: string;
}

export interface CreateKnowledgeInput {
  tenant: string;
  project: string;
  kind: KnowledgeKind;
  title: string;
  body: string;
  visibility?: KnowledgeVisibility;
  allowed_users?: string[];
  slug?: string;
  tags?: string[];
  summary?: string;
}

export interface VersionKnowledgeInput {
  title: string;
  body: string;
  visibility?: KnowledgeVisibility;
  allowed_users?: string[];
  tags?: string[];
  summary?: string;
}

export interface KnowledgeStoreItem {
  kind: KnowledgeKind;
  title: string;
  body: string;
  visibility?: KnowledgeVisibility;
  allowed_users?: string[];
  slug?: string;
  tags?: string[];
  summary?: string;
}

export interface CuratorSuggestion {
  type: string;
  entries: number[];
  note: string;
  proposal: string;
  confidence?: string;
  reason?: string;
}

export interface OnboardRegisterInput {
  username: string;
  email: string;
  password: string;
  given_name?: string;
  family_name?: string;
  display_name?: string;
  person_id?: string;
  person_type?: string;
}

export interface OnboardCompleteInput {
  display_name?: string;
  role_title?: string;
  organisation?: string;
  use_case?: string;
  project_name: string;
  project_purpose: string;
  project_type?: string;
  north_star?: string;
  standing_rules?: string;
}

export interface OnboardCompleteResult {
  username: string;
  tenant: string;
  project: string | null;
  rouvr_token: string | null;
  user_profile?: string;
  project_profile?: string;
}

export interface Health {
  status: string;
  service: string;
}
