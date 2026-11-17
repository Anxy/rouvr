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
  const card = read("src/components/workspace/BoardTaskCard.tsx");
  assert.ok(board.includes("PlanDeliveryPanel"));
  assert.ok(board.includes("BoardTaskCard"));
  assert.ok(card.includes("data-task-resume"));
  assert.ok(card.includes("data-task-actions"));
  assert.ok(board.includes("/api/tasks/{id}/start"));
});

test("Board plan delivery starts delivery for the whole plan only", () => {
  const page = read("src/components/workspace/PlanDeliveryPanel.tsx");
  assert.ok(page.includes("DeliveryStartDialog"));
  assert.ok(page.includes("Start delivery"));
  assert.ok(page.includes("Restart from build"));
  assert.ok(page.includes("data-plan-restart"));
  assert.ok(page.includes("data-plan-start") || page.includes("openPlanStart"));
  assert.ok(page.includes("/api/plans/{slug}/start") || page.includes("plan={startPlan}"));
  assert.ok(page.includes("GitGrantPanel"));
  assert.ok(page.includes("will resume at") || page.includes("resume_at"));
  assert.ok(page.includes("data-task-resume"));
  assert.ok(page.includes("/api/tasks/{id}/start"));
  assert.ok(page.includes("Resume at") || page.includes("resumeTask"));
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

test("supervisor chat renders catalog op chips and dialog handoffs", () => {
  const chat = read("src/components/workspace/ChatSurface.tsx");
  assert.ok(chat.includes("data-chat-actions"));
  assert.ok(chat.includes("data-chat-action"));
  assert.ok(chat.includes("actOp"));
  assert.ok(chat.includes("op_args") || chat.includes("op_args:"));
  assert.ok(chat.includes("needs_dialog"));
  assert.ok(chat.includes("GitGrantPanel"));
  assert.ok(chat.includes("delivery_start") || chat.includes("plan.start.dialog"));
  const workspace = read("src/lib/api/workspace.ts");
  assert.ok(workspace.includes("op?: string"));
  assert.ok(workspace.includes("op_args?"));
  const ops = read("../ledger/chat_ops.py");
  assert.ok(ops.includes('"plan.approve"') || ops.includes("plan.approve"));
  assert.ok(ops.includes("needs_dialog"));
  assert.ok(ops.includes("OP_CONFIRM"));
});
