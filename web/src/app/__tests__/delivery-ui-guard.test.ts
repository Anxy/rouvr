import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";

const WEB_ROOT = path.resolve(import.meta.dirname, "..", "..", "..");
const read = (rel: string): string =>
  readFileSync(path.join(WEB_ROOT, rel), "utf8");

test("Execute is not a peer nav item; delivery lives on Board", () => {
  const layout = read("src/app/workspace/layout.tsx");
  assert.ok(!layout.includes("/workspace/execute"));
  assert.ok(!layout.includes('"Execute"'));
  const board = read("src/app/workspace/page.tsx");
  assert.ok(board.includes("PlanDeliveryPanel"));
});

test("Board plan delivery starts delivery for the whole plan only", () => {
  const page = read("src/components/workspace/PlanDeliveryPanel.tsx");
  assert.ok(page.includes("DeliveryStartDialog"));
  assert.ok(page.includes("Start delivery"));
  assert.ok(page.includes("data-plan-start") || page.includes("openPlanStart"));
  assert.ok(page.includes("/api/plans/{slug}/start") || page.includes("plan={startPlan}"));
  assert.ok(page.includes("GitGrantPanel"));
  assert.ok(
    !page.includes("onStart={(task)"),
    "plan delivery must not Start delivery on individual task cards",
  );
});

test("task detail does not start a single-task delivery", () => {
  const page = read("src/app/workspace/tasks/[id]/page.tsx");
  assert.ok(!page.includes("DeliveryStartDialog"));
  assert.ok(page.includes("Start delivery on plan") || page.includes("/workspace/chat"));
  assert.ok(page.includes("GitGrantPanel"));
  assert.ok(page.includes("data-pr-url"));
});

test("escalations inbox can grant GitHub access", () => {
  const page = read("src/app/workspace/escalations/page.tsx");
  assert.ok(page.includes("GitGrantPanel"));
  assert.ok(page.includes("waiting_git_grant"));
});

test("supervisor chat starts delivery on the plan, not per-task cards", () => {
  const chat = read("src/components/workspace/ChatSurface.tsx");
  assert.ok(chat.includes("DeliveryStartDialog"));
  assert.ok(chat.includes('data-start-delivery="plan"'));
  assert.ok(!chat.includes("data-start-delivery={t.id}"));
});
