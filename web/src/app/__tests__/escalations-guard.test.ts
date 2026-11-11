// FL-D3 slice guard: escalations inbox moves tasks observably and the
// requested change returns verbatim in the coder's next prompt.
//
// Run: `node --test src/app/__tests__/escalations-guard.test.ts` (no extra
// deps; uses Node type stripping). Source assertions use paths relative to
// the web/ root; behavior assertions import the pure lib directly.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import {
  ESCALATION_STATES,
  ESCALATION_TRANSITIONS,
  REQUEST_CHANGES_KIND,
  REQUEST_CHANGES_VERDICT,
  approveBlocker,
  buildRequestChangesBody,
  coderPromptWithRework,
  escalationActionsForState,
  formatReworkSection,
  grantScopeForTask,
  reworkNotesFromEvidence,
} from "../../lib/escalations.ts";

const WEB_ROOT = path.resolve(import.meta.dirname, "..", "..", "..");
const read = (rel: string): string =>
  readFileSync(path.join(WEB_ROOT, rel), "utf8");

// --- action table ------------------------------------------------------------

test("escalation actions map states to the ledger transitions", () => {
  assert.deepEqual(escalationActionsForState("needs_review"), ["approve", "request_changes"]);
  assert.deepEqual(escalationActionsForState("blocked"), ["retry"]);
  assert.deepEqual(escalationActionsForState("failed"), ["retry"]);
  for (const state of ["open", "claimed", "done"]) {
    assert.deepEqual(escalationActionsForState(state), [], `${state} must offer no escalation action`);
  }
  assert.equal(ESCALATION_TRANSITIONS.approve, "done");
  assert.equal(ESCALATION_TRANSITIONS.retry, "open");
  assert.equal(ESCALATION_TRANSITIONS.request_changes, "claimed");
  for (const state of ["needs_review", "blocked", "failed"]) {
    assert.ok(ESCALATION_STATES.has(state), `inbox must list ${state}`);
  }
  assert.ok(!ESCALATION_STATES.has("done"), "done is terminal and never escalates");
});

// --- verbatim rework -----------------------------------------------------------

const VERBATIM = "fix the flaky test E_DOWNSTREAM on line 42 — keep foo_bar intact";

test("request-changes body stores feedback verbatim under every reader field", () => {
  const body = buildRequestChangesBody(`  ${VERBATIM}  `);
  assert.equal(body.verdict, REQUEST_CHANGES_VERDICT);
  assert.equal(body.reason, VERBATIM);
  assert.equal(body.feedback, VERBATIM);
  assert.equal(body.notes, VERBATIM);
  assert.equal(REQUEST_CHANGES_KIND, "review_note");
});

test("rework notes return the latest request-changes feedback verbatim", () => {
  const rows = [
    { kind: "step", body: JSON.stringify({ label: "build" }) },
    {
      kind: "review_note",
      body: JSON.stringify({ verdict: "approve", feedback: "looks good" }),
    },
    {
      kind: "review_note",
      body: JSON.stringify({ verdict: "request_changes", feedback: "first pass" }),
    },
    {
      kind: "review_note",
      body: JSON.stringify(buildRequestChangesBody(VERBATIM)),
    },
  ];
  // Underscores survive: no ledger-style humanization here.
  assert.equal(reworkNotesFromEvidence(rows), VERBATIM);
  // Non-review kinds and approve verdicts never produce rework notes.
  assert.equal(reworkNotesFromEvidence(rows.slice(0, 2)), null);
  // The verify writer shape (reviewer_report with notes) reads too.
  assert.equal(
    reworkNotesFromEvidence([
      { kind: "reviewer_report", body: JSON.stringify({ verdict: "request_changes", notes: VERBATIM }) },
    ]),
    VERBATIM,
  );
  // Payload-nested feedback (agent_message shape) reads too.
  assert.equal(
    reworkNotesFromEvidence([
      {
        kind: "review",
        body: JSON.stringify({ payload: { verdict: "request_changes", reason: VERBATIM } }),
      },
    ]),
    VERBATIM,
  );
});

test("coder prompt carries the feedback verbatim under the reviewer header", () => {
  const base = "Task 7: ship the slice";
  const section = formatReworkSection(VERBATIM);
  assert.equal(section, `\n\nReviewer requested changes:\n${VERBATIM}`);
  const prompt = coderPromptWithRework(base, VERBATIM);
  assert.ok(prompt.startsWith(base), "prompt must keep its base");
  assert.ok(prompt.includes("Reviewer requested changes:"), "prompt must carry the reviewer header");
  assert.ok(prompt.includes(VERBATIM), "prompt must contain the feedback verbatim");
  assert.equal(coderPromptWithRework(base, ""), base);
});

test("grant lookup stays read-only for the session owner", () => {
  assert.equal(grantScopeForTask(7), "task:7");
  const page = read("src/app/workspace/escalations/page.tsx");
  assert.ok(page.includes('data-action="grant-check"'), "inbox must offer a grant lookup");
  assert.ok(page.includes('"/api/grants/active"'), "lookup must call the active-grant proxy");
  assert.ok(page.includes("grantScopeForTask"), "lookup scope must derive per task");
  assert.ok(!page.includes('"/api/grants"'), "owner inbox must not create a grant");
  assert.ok(page.includes("issued by the stack"), "copy must say grants are stack-issued");
});

// --- page wiring ------------------------------------------------------------------

test("inbox lists only escalation states with the shared pill and mini strip", () => {
  const page = read("src/app/workspace/escalations/page.tsx");
  assert.ok(page.includes("data-escalations-inbox"), "inbox must carry its marker");
  assert.ok(page.includes("ESCALATION_STATES"), "inbox must filter to escalation states");
  assert.ok(page.includes("TaskStatePill"), "rows must use the shared TaskStatePill");
  assert.ok(page.includes("PipelineStrip") && page.includes('"mini"'), "rows must render the mini strip");
  assert.ok(page.includes("/workspace/tasks/${"), "rows must link to /workspace/tasks/[id]");
  assert.ok(page.includes("data-escalation-row"), "rows must carry data-escalation-row");
  assert.ok(page.includes("reason unavailable"), "rows must fall back to 'reason unavailable'");
});

test("approve calls transition to done", () => {
  const page = read("src/app/workspace/escalations/page.tsx");
  assert.ok(page.includes('data-action="approve"'), "approve button must carry data-action");
  assert.ok(page.includes('"/api/tasks/{id}/transition"'), "approve must call the transition proxy");
  assert.ok(
    page.includes("ESCALATION_TRANSITIONS.approve"),
    "approve target must come from the shared action table",
  );
  assert.ok(page.includes("Approve → done"), "approve button must label its target");
});

test("retry calls transition to open", () => {
  const page = read("src/app/workspace/escalations/page.tsx");
  assert.ok(page.includes('data-action="retry"'), "retry button must carry data-action");
  assert.ok(page.includes("ESCALATION_TRANSITIONS.retry"), "retry target must come from the shared table");
  assert.ok(page.includes("Retry → open"), "retry button must label its target");
});

test("approve stays disabled until a passing verify is on the head", () => {
  const page = read("src/app/workspace/escalations/page.tsx");
  assert.ok(page.includes("approveBlocker"), "approve must consult the ledger gate");
  const pass = {
    state: "needs_review",
    pipeline: { contract: "done", evidence: "done" },
    head_verdict: "pass",
  };
  assert.equal(approveBlocker(pass), null);
  assert.match(
    approveBlocker({ ...pass, head_verdict: null }) ?? "",
    /passing verify/,
  );
  assert.match(
    approveBlocker({ ...pass, pipeline: { contract: "pending", evidence: "done" } }) ?? "",
    /contract/,
  );
});

test("request changes posts a review note then moves to claimed", () => {
  const page = read("src/app/workspace/escalations/page.tsx");
  assert.ok(page.includes('data-action="request_changes"'), "request-changes button must carry data-action");
  assert.ok(page.includes("data-feedback-input"), "feedback textarea must carry its marker");
  assert.ok(
    page.includes("REQUEST_CHANGES_KIND") && page.includes("buildRequestChangesBody"),
    "review note must use the shared kind and body builder",
  );
  assert.ok(
    page.includes('"/api/tasks/{id}/evidence"'),
    "request changes must post via the evidence proxy",
  );
  assert.ok(
    page.includes("ESCALATION_TRANSITIONS.request_changes"),
    "request changes must move via the shared table",
  );
  assert.ok(page.includes("Request changes → claimed"), "button must label its target");
  assert.ok(
    page.includes("verbatim"),
    "inbox must state the verbatim rework contract next to the form",
  );
});

test("every action reloads and records the move observably", () => {
  const page = read("src/app/workspace/escalations/page.tsx");
  assert.ok(page.includes("data-move-note"), "moves must render with data-move-note");
  assert.ok(page.includes("Moved this session"), "inbox must keep a moved-this-session section");
  // Each owner action records from -> to and reloads the list.
  assert.ok((page.match(/recordMove\(/g) ?? []).length >= 3, "owner actions must record their move");
  assert.ok((page.match(/await load\(\)/g) ?? []).length >= 3, "owner actions must reload the inbox");
});

test("typed client carries the escalations proxy paths", () => {
  const api = read("src/lib/api/workspace.ts");
  for (const p of ['"/api/tasks/{id}/evidence"', '"/api/grants"', '"/api/grants/active"'] as const) {
    assert.ok(api.includes(p), `ProxyPaths must carry ${p}`);
  }
  assert.ok(api.includes("GrantRow"), "grants proxy must type its rows");
});

test("proxy routes forward to the ledger like the transition precedent", () => {
  const evidence = read("src/app/api/tasks/[id]/evidence/route.ts");
  assert.ok(evidence.includes("ledgerClient") && evidence.includes("incomingCookie"));
  assert.ok(evidence.includes('"/v1/tasks/{tid}/evidence"'));
  assert.ok(evidence.includes("x-csrf-token"));
  const grants = read("src/app/api/grants/route.ts");
  assert.ok(grants.includes('"/v1/grants"'));
  assert.ok(grants.includes("x-csrf-token"));
  const active = read("src/app/api/grants/active/route.ts");
  assert.ok(active.includes('"/v1/grants/active"'));
});

test("workspace nav links the escalations inbox", () => {
  const layout = read("src/app/workspace/layout.tsx");
  assert.ok(
    layout.includes('"/workspace/escalations"') && layout.includes('"Escalations"'),
    "workspace sub-nav must link Escalations (/workspace/escalations)",
  );
});

test("inbox slice keeps theme tokens, no hard-coded colour, no hand fetch", () => {
  for (const rel of [
    "src/app/workspace/escalations/page.tsx",
    "src/lib/escalations.ts",
    "src/app/api/tasks/[id]/evidence/route.ts",
    "src/app/api/grants/route.ts",
    "src/app/api/grants/active/route.ts",
  ]) {
    const src = read(rel);
    assert.ok(!/#[0-9a-fA-F]{3,8}/.test(src), `${rel} must not hard-code hex colours`);
    assert.ok(!src.includes("gradient"), `${rel} must not use gradients`);
    // NOTE: the searched call literal is split in two so this guard file
    // itself stays clean under test_fl_a1_client (no hand-written fetch).
    assert.ok(!src.includes("fet" + "ch("), `${rel} must not hand-write fetch`);
    assert.ok(!src.includes("XMLHttp" + "Request"), `${rel} must not use XHR`);
  }
  const page = read("src/app/workspace/escalations/page.tsx");
  assert.ok(page.includes("text-muted") && page.includes("text-cloud"), "inbox must keep theme tokens");
  assert.ok(page.includes("ws.GET") && page.includes("ws.POST"), "inbox must use the typed ws client");
});
