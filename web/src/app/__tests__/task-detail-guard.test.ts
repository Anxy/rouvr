// P3 slice guard: task detail explains itself from real ledger rows.
//
// Run: `node --test src/app/__tests__/task-detail-guard.test.ts` (no extra
// deps; uses Node type stripping). Source assertions use paths relative to
// the web/ root; behavior assertions import the pure lib directly.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import {
  ATTENTION_CODE_ENGLISH,
  attentionFromEvidence,
  collectKnowledgeRefs,
  describeBlockedReason,
  evidenceLabel,
  whyTaskExists,
} from "../../lib/task-detail.ts";

const WEB_ROOT = path.resolve(import.meta.dirname, "..", "..", "..");
const read = (rel: string): string =>
  readFileSync(path.join(WEB_ROOT, rel), "utf8");

// --- code -> English mapping ------------------------------------------------

test("fence-code table covers the known codes", () => {
  for (const [code, plain] of [
    ["E_DOWNSTREAM", "Downstream step failed"],
    ["E_CONTRACT", "Contract problem"],
    ["rework_exhausted", "Rework exhausted"],
    ["approval_wait", "Waiting for approval"],
    ["FileNotFoundError", "File not found"],
  ] as const) {
    assert.equal(
      ATTENTION_CODE_ENGLISH[code],
      plain,
      `${code} must map to "${plain}"`,
    );
  }
});

test("known codes render as plain words with the code in brackets", () => {
  assert.equal(
    describeBlockedReason("Downstream step failed (E_DOWNSTREAM)")?.display,
    "Downstream step failed (E_DOWNSTREAM)",
  );
  assert.equal(
    describeBlockedReason("stuck on approval_wait for owner")?.display,
    "Waiting for approval (approval_wait)",
  );
  assert.equal(
    describeBlockedReason("Build failed: FileNotFoundError: foo.py")?.display,
    "File not found (FileNotFoundError)",
  );
});

test("ledger-humanized fences map back to their code", () => {
  // The ledger replaces _ with space before serving attention, so the page
  // must reverse the normalization for table members.
  assert.equal(
    describeBlockedReason("rework exhausted (attempt 3)")?.display,
    "Rework exhausted (rework_exhausted, attempt 3)",
  );
  assert.equal(
    describeBlockedReason("E MOVED (attempt 1)")?.display,
    "Task moved (E_MOVED, attempt 1)",
  );
});

test("unknown reason text renders as-is with no code appended", () => {
  for (const text of [
    "waiting on owner approval",
    "fix the flaky test",
    "reason unavailable",
  ]) {
    const out = describeBlockedReason(text);
    assert.equal(out?.display, text, `"${text}" must pass through untouched`);
    assert.equal(out?.code, null, `"${text}" must carry no code`);
  }
  assert.equal(describeBlockedReason(""), null);
  assert.equal(describeBlockedReason(undefined), null);
});

test("attention derives from the latest escalation/reviewer evidence", () => {
  const rows = [
    { id: 1, kind: "step", body: JSON.stringify({ label: "build" }), created_at: "t" },
    {
      id: 2,
      kind: "escalation",
      body: JSON.stringify({
        fence: "escalate:attempt-3:rework_exhausted",
        payload: { attempt_id: "attempt-3", reason: "rework_exhausted" },
      }),
      created_at: "t",
    },
  ];
  // Mirrors the ledger fixture: humanized text with the attempt suffix.
  assert.equal(attentionFromEvidence(rows), "rework exhausted (attempt 3)");
  // An explicit ledger attention string wins over the derivation.
  assert.equal(
    attentionFromEvidence(rows, "waiting on owner approval"),
    "waiting on owner approval",
  );
  // Non-escalation kinds never explain attention.
  assert.equal(
    attentionFromEvidence([rows[0]]),
    null,
    "step evidence must not produce an attention reason",
  );
});

// --- why-line ----------------------------------------------------------------

test("why-line prefers the first acceptance criterion, then intent", () => {
  const contract = (body: unknown) => ({ body: JSON.stringify(body) });
  assert.equal(
    whyTaskExists({
      contracts: [contract({ acceptance_criteria: ["ship the slice", "tests pass"] })],
      intent: "do things",
    }),
    "ship the slice",
  );
  assert.equal(
    whyTaskExists({ contracts: [], intent: "do things\nmore detail" }),
    "do things",
  );
  assert.equal(
    whyTaskExists({ contracts: [], intent: "" }),
    "no contract recorded",
  );
  assert.equal(
    whyTaskExists({ contracts: [contract({ acceptance_criteria: [] })], intent: "" }),
    "no contract recorded",
  );
});

// --- knowledge refs -----------------------------------------------------------

test("knowledge refs promote linkage, deduped and order-stable", () => {
  const refs = collectKnowledgeRefs(
    [
      {
        body: JSON.stringify({
          acceptance_criteria: ["x cites knowledge:258@v1 for style"],
          category: "ops",
          plan_slug: "morning-report",
        }),
      },
      {
        body: JSON.stringify({
          acceptance_criteria: ["again knowledge:258@v1"],
          category: "ops",
          plan_slug: "morning-report",
        }),
      },
    ],
    [{ action: "chat:create", body: JSON.stringify({ plan_slug: "other" }) }],
  );
  assert.deepEqual(refs.planSlugs, ["morning-report", "other"]);
  assert.deepEqual(refs.categories, ["ops"]);
  assert.deepEqual(refs.citations, ["knowledge:258@v1"]);
});

// --- evidence labels ------------------------------------------------------------

test("evidence labels stay readable and never invent text", () => {
  assert.equal(
    evidenceLabel("reviewer", JSON.stringify({ verdict: "request_changes", reason: "fix the flaky test" })),
    "fix the flaky test (verdict: request_changes)",
  );
  assert.equal(evidenceLabel("note", "plain worker note"), "plain worker note");
  assert.equal(
    evidenceLabel("step", JSON.stringify({ done: 3, total: 5 })),
    JSON.stringify({ done: 3, total: 5 }),
  );
});

// --- page wiring ------------------------------------------------------------------

test("detail top renders the full strip plus a real-data why-line", () => {
  const page = read("src/app/workspace/tasks/[id]/page.tsx");
  assert.ok(page.includes('"full"') && page.includes("PipelineStrip"));
  assert.ok(page.includes("data-why"), "why-line must be marked with data-why");
  assert.ok(page.includes("whyTaskExists"), "why-line must derive from the contract");
  assert.ok(
    page.includes("Why this exists:"),
    "why-line must label itself as the task's reason to exist",
  );
});

test("detail blocked section maps the code and states the Retry contract", () => {
  const page = read("src/app/workspace/tasks/[id]/page.tsx");
  assert.ok(page.includes("data-blocked-reason"));
  assert.ok(page.includes("data-reason-display"));
  assert.ok(
    page.includes("describeBlockedReason") && page.includes("attentionFromEvidence"),
    "reason must derive from evidence and map codes to plain English",
  );
  assert.ok(
    page.includes("reason unavailable"),
    "missing reason must fall back to 'reason unavailable'",
  );
  assert.ok(page.includes("data-retry-note"));
  assert.ok(
    page.includes("Retry reopens it for a worker with the same contract."),
    "retry note must be exactly one line with the exact sentence",
  );
  assert.equal(
    (page.match(/Retry reopens it for a worker with the same contract\./g) ?? []).length,
    1,
    "retry explanation must appear exactly once",
  );
  assert.ok(
    page.includes("RETRYABLE.has(task.state)"),
    "retry note must gate on the same retryable states as the Retry button",
  );
});

test("detail knowledge badges come from the contract, titles only from the endpoint", () => {
  const page = read("src/app/workspace/tasks/[id]/page.tsx");
  assert.ok(page.includes("data-knowledge-badges"));
  assert.ok(page.includes("Knowledge applied"));
  assert.ok(page.includes("collectKnowledgeRefs"));
  assert.ok(page.includes("plan_slug:") && page.includes("category:"));
  assert.ok(
    page.includes("/api/knowledge/{key}"),
    "citation titles must resolve via the existing knowledge endpoint",
  );
  assert.ok(
    page.includes("citeTitles[citation]"),
    "citation text must render only from a fetched entry, never invented",
  );
});

test("detail evidence is a timeline with time, label and collapsed raw JSON", () => {
  const page = read("src/app/workspace/tasks/[id]/page.tsx");
  assert.ok(page.includes("Evidence timeline"));
  assert.ok(page.includes("<time") && page.includes("dateTime={e.created_at}"));
  assert.ok(page.includes("formatEvidenceTime"));
  assert.ok(page.includes("evidenceLabel(e.kind, e.body)"));
  assert.ok(page.includes("<details"), "raw JSON must sit behind a <details>");
  assert.ok(
    !page.includes("<details open"),
    "raw JSON details must be closed by default",
  );
});

test("detail renders duplicate evidence rows once each, keyed by id", () => {
  // #490 verdict: the ledger stores real duplicate rows (evidence POST has
  // no dedupe, unlike contracts); the page must render one row per id.
  const page = read("src/app/workspace/tasks/[id]/page.tsx");
  assert.equal(
    (page.match(/task\.evidence\.slice/g) ?? []).length,
    1,
    "evidence must be mapped exactly once",
  );
  assert.ok(page.includes("key={e.id}"), "evidence rows must key on the row id");
  const ledger = readFileSync(
    path.join(WEB_ROOT, "..", "ledger", "app.py"),
    "utf8",
  );
  assert.ok(
    ledger.includes("INSERT INTO evidence(task_id,kind,body,sha256,created_at)"),
    "ledger evidence POST inserts unconditionally (real duplicate rows possible)",
  );
  assert.ok(
    !/SELECT DISTINCT|SELECT.*DISTINCT.*FROM evidence/i.test(
      ledger.slice(
        ledger.indexOf("def get_task"),
        ledger.indexOf("def get_task") + 3000,
      ),
    ),
    "get_task returns every evidence row without dedupe",
  );
});

test("detail slice keeps theme tokens, no hard-coded colour", () => {
  for (const rel of [
    "src/app/workspace/tasks/[id]/page.tsx",
    "src/lib/task-detail.ts",
  ]) {
    const src = read(rel);
    assert.ok(!/#[0-9a-fA-F]{3,8}/.test(src), `${rel} must not hard-code hex colours`);
  }
  const page = read("src/app/workspace/tasks/[id]/page.tsx");
  assert.ok(
    page.includes("text-muted") && page.includes("text-cloud"),
    "detail must keep theme tokens",
  );
});
