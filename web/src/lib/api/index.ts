/** Generated API client (FL-A1). Screens import from here, never fetch directly. */
export { ApiError, createRouvrClient, requestJson } from "./client";
export type { ApiPaths, ClientOptions, QueryValue, RequestOptions, RouvrClient, TokenSource } from "./client";
export { submitContact } from "./contact";
export type { ContactResult } from "./contact";
export type {
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
  KnowledgeKind,
  KnowledgeShortlist,
  KnowledgeStoreItem,
  KnowledgeVisibility,
  Lease,
  OnboardCompleteInput,
  OnboardCompleteResult,
  OnboardRegisterInput,
  Project,
  Receipt,
  Task,
  TaskContract,
  TaskState,
  Tenant,
  TransitionInput,
  TransitionResult,
  User,
  VersionKnowledgeInput,
} from "./types";
export type { paths as ApiSchema } from "./schema";
