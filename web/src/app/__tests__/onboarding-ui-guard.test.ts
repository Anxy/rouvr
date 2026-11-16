import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";

const WEB_ROOT = path.resolve(import.meta.dirname, "..", "..", "..");
const read = (rel: string): string =>
  readFileSync(path.join(WEB_ROOT, rel), "utf8");

test("onboarding route exists with three steps", () => {
  const page = read("src/app/workspace/onboarding/page.tsx");
  assert.ok(page.includes("Set up your workspace"));
  assert.ok(page.includes("exec-spec"));
  assert.ok(page.includes("git-grant"));
  assert.ok(page.includes("Skip for now"));
  assert.ok(page.includes("/workspace/chat?onboarded=1"));
  assert.ok(
    page.includes("wizardStarted"),
    "must keep wizard-in-progress so project create does not bounce to Board",
  );
});

test("workspace empty states point at Supervisor", () => {
  const board = read("src/app/workspace/page.tsx");
  assert.ok(board.includes("Talk to Supervisor") || board.includes("talk to the Supervisor"));
  assert.ok(board.includes("New task"), "Board must offer New task");
  assert.ok(
    board.includes("talk to the Supervisor") || board.includes("Talk to Supervisor"),
    "Board empty / create copy must point at Supervisor",
  );
  const panel = read("src/components/workspace/PlanDeliveryPanel.tsx");
  assert.ok(panel.includes("Start delivery"));
  const execute = read("src/app/workspace/execute/page.tsx");
  assert.ok(
    execute.includes("router.replace") && execute.includes("/workspace"),
    "legacy Execute must redirect to Board",
  );
});

test("login redirects empty workspace to onboarding", () => {
  const login = read("src/app/workspace/login/page.tsx");
  assert.ok(login.includes("/workspace/onboarding"));
  assert.ok(login.includes("GET(\"/api/projects\")") || login.includes('GET("/api/projects")'));
});

test("session gates zero-project users to onboarding", () => {
  const session = read("src/components/workspace/session.tsx");
  assert.ok(session.includes('router.replace("/workspace/onboarding")'));
  assert.ok(session.includes("authTenant"));
});

test("layout treats onboarding as shell-less", () => {
  const layout = read("src/app/workspace/layout.tsx");
  assert.ok(layout.includes("/workspace/onboarding"));
});
