// Escalations inbox helpers (FL-D3): pure functions over real ledger rows.
// No fetches, no invented text — every string returned comes from the
// task's evidence bodies, or from the explicit action table below.

export type EscalationAction = "approve" | "retry" | "grant" | "request_changes";

// Ledger states that land in the inbox (same set the board calls
// NEEDS_ATTENTION in web/src/app/workspace/page.tsx).
export const ESCALATION_STATES = new Set(["needs_review", "blocked", "failed"]);

// One inbox row offers exactly the actions its state allows. The `to`
// values are the ledger TRANSITIONS targets (ledger/app.py):
//   needs_review -> done (approve) | claimed (request changes)
//   blocked/failed -> open (retry, or grant then open)
export function escalationActionsForState(state: string): EscalationAction[] {
  if (state === "needs_review") return ["approve", "request_changes"];
  if (state === "blocked" || state === "failed") return ["retry", "grant"];
  return [];
}

// The transition target each action moves the task to. Every action moves
// a task observably: the inbox reloads after each call and the row's
// state pill (or its absence from the inbox) shows the move.
export const ESCALATION_TRANSITIONS: Record<EscalationAction, string> = {
  approve: "done",
  retry: "open",
  grant: "open",
  request_changes: "claimed",
};

// Request-changes posts a review-note evidence entry (kind review_note,
// one of the three kinds the ledger maps to the review.note SSE event in
// EVIDENCE_EVENT_KINDS) then transitions needs_review -> claimed. The
// rework loop feeds the note into the coder's next prompt.
export const REQUEST_CHANGES_KIND = "review_note";
export const REQUEST_CHANGES_VERDICT = "request_changes";

// Evidence kinds that can carry reviewer feedback. The inbox writes
// review_note; verify writes reviewer_report (rouvr/activities.py); older
// rows may use reviewer/review. All four are read for rework notes.
export const REWORK_EVIDENCE_KINDS = new Set([
  "reviewer",
  "review",
  "review_note",
  "reviewer_report",
]);

export interface EvidenceLike {
  kind: string;
  body: string;
}

// Build the inner evidence body for a request-changes note. The feedback
// string is stored verbatim under reason/feedback/notes so every reader
// (ledger attention, gate, coder prompt) finds the same text.
export function buildRequestChangesBody(feedback: string): {
  verdict: string;
  reason: string;
  feedback: string;
  notes: string;
} {
  const text = feedback.trim();
  return {
    verdict: REQUEST_CHANGES_VERDICT,
    reason: text,
    feedback: text,
    notes: text,
  };
}

function verdictOf(record: Record<string, unknown>): string | null {
  const direct = record["verdict"];
  if (typeof direct === "string" && direct.trim()) return direct.trim();
  const payload = record["payload"];
  if (payload !== null && typeof payload === "object" && !Array.isArray(payload)) {
    const v = (payload as Record<string, unknown>)["verdict"];
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  return null;
}

function feedbackOf(record: Record<string, unknown>): string | null {
  const payload =
    record["payload"] !== null && typeof record["payload"] === "object" && !Array.isArray(record["payload"])
      ? (record["payload"] as Record<string, unknown>)
      : null;
  // Payload first (supervisor/agent_message shape), then the top level.
  // Order prefers the exact fields the inbox and verify write.
  for (const source of [payload, record]) {
    if (!source) continue;
    for (const key of ["feedback", "reason", "notes", "text", "message", "note"]) {
      const val = source[key];
      if (typeof val === "string" && val.trim()) return val;
    }
  }
  return null;
}

// Latest request-changes feedback across the evidence rows, verbatim:
// no underscore humanization, no truncation. Returns null when no
// review-kind row carries a request_changes verdict with feedback.
export function reworkNotesFromEvidence(evidence: EvidenceLike[]): string | null {
  let latest: string | null = null;
  for (const e of evidence) {
    if (!REWORK_EVIDENCE_KINDS.has(e.kind)) continue;
    let parsed: unknown;
    try {
      parsed = JSON.parse(e.body);
    } catch {
      continue;
    }
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) continue;
    const record = parsed as Record<string, unknown>;
    if (verdictOf(record) !== REQUEST_CHANGES_VERDICT) continue;
    const feedback = feedbackOf(record);
    if (feedback !== null) latest = feedback;
  }
  return latest;
}

// The rework section appended to the coder's next prompt. Mirrors
// rouvr/muse.py::build_task_prompt ("Reviewer requested changes:" followed
// by the notes verbatim): the feedback string is interpolated untouched.
export function formatReworkSection(notes: string): string {
  return `\n\nReviewer requested changes:\n${notes}`;
}

export function coderPromptWithRework(basePrompt: string, notes: string): string {
  if (!notes) return basePrompt;
  return `${basePrompt}${formatReworkSection(notes)}`;
}

// Per-task grant scope for the inbox grant action. One scope per task keeps
// grants bounded; the grant expiry is the time box (T5).
export function grantScopeForTask(taskId: number): string {
  return `task:${taskId}`;
}

// ISO expiry `days` out (default 7). The caller may pass an explicit date
// instead; this is the inbox default.
export function grantExpiryIso(days = 7, now: Date = new Date()): string {
  const ms = now.getTime() + days * 24 * 60 * 60 * 1000;
  return new Date(ms).toISOString();
}
