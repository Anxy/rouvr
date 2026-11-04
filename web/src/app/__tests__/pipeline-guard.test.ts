// P1 slice guard: branded header, shared status colours, pipeline strip.
//
// Run: `node --test src/app/__tests__/pipeline-guard.test.ts` (no extra deps;
// uses Node type stripping). Inputs are paths relative to the web/ root.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";

const WEB_ROOT = path.resolve(import.meta.dirname, "..", "..", "..");
const read = (rel: string): string =>
  readFileSync(path.join(WEB_ROOT, rel), "utf8");

const TAGLINE =
  "An autonomous AI colleague that turns conversations into accountable work.";

test("workspace header carries the Rouvr wordmark and tagline", () => {
  const layout = read("src/app/workspace/layout.tsx");
  assert.ok(layout.includes("Rouvr"), "header must keep the Rouvr wordmark");
  assert.ok(
    layout.includes(TAGLINE),
    "header must carry the product tagline",
  );
  // Compact: one short line next to the wordmark, theme tokens only.
  assert.ok(
    layout.includes("text-muted"),
    "tagline must use theme tokens (text-muted), not hard-coded colour",
  );
  const headerBlock = layout.slice(0, layout.indexOf("</nav>"));
  assert.ok(
    !/#[0-9a-fA-F]{3,8}/.test(headerBlock),
    "header must not hard-code hex colours",
  );
});

test("TaskStatePill maps states to the shared colours", () => {
  const pill = read("src/components/ui/StatusPill.tsx");
  assert.ok(
    pill.includes("TaskStatePill"),
    "StatusPill module must export TaskStatePill",
  );
  for (const [state, token] of [
    ["done", "success"],
    ["open", "brand"],
    ["needs_review", "warning"],
    ["blocked", "danger"],
    ["failed", "danger"],
  ] as const) {
    assert.ok(
      pill.includes(state),
      `TaskStatePill must handle the ${state} state`,
    );
    assert.ok(
      pill.includes(token),
      `TaskStatePill must use the ${token} token`,
    );
  }
  assert.ok(
    !/#[0-9a-fA-F]{3,8}/.test(pill),
    "status colours must be theme tokens, not hard-coded hex",
  );
});

test("PipelineStrip renders five steps from the ledger pipeline object", () => {
  const strip = read("src/components/workspace/PipelineStrip.tsx");
  for (const step of ["Contract", "Knowledge", "Build", "Review", "Evidence"]) {
    assert.ok(strip.includes(step), `strip must render the ${step} step`);
  }
  for (const mark of ["✓", "✗", "–"]) {
    assert.ok(strip.includes(mark), `strip must render the ${mark} mark`);
  }
  assert.ok(
    strip.includes("pipeline"),
    "strip must render from the pipeline prop",
  );
  // NOTE: the searched call literal is split in two so this guard file
  // itself stays clean under test_fl_a1_client (no hand-written fetch).
  assert.ok(
    !strip.includes("fet" + "ch(") && !strip.includes("ws.GET"),
    "strip must not fetch per-task detail; flags arrive on the row",
  );
  assert.ok(
    strip.includes("ledger/app.py"),
    "strip must document the server-side mapping it renders",
  );
});

test("board rows render the shared pill and the mini strip without extra fetches", () => {
  const page = read("src/app/workspace/page.tsx");
  assert.ok(
    page.includes("TaskStatePill"),
    "board rows must use the shared TaskStatePill",
  );
  assert.ok(
    page.includes("PipelineStrip") && page.includes('"mini"'),
    "board rows must render the mini pipeline strip",
  );
  assert.ok(
    page.includes("task.pipeline"),
    "mini strip must read flags from the row's pipeline object",
  );
  const fetches = page.match(/ws\.GET\(/g) ?? [];
  assert.equal(
    fetches.length,
    2,
    "board fetches exactly twice: the task list plus one events snapshot",
  );
  assert.ok(
    page.includes('"/api/tasks"'),
    "board must fetch the task list",
  );
  assert.ok(
    page.includes('"/api/events"'),
    "board activity feed must read the /api/events snapshot",
  );
  assert.ok(
    !page.includes("/api/tasks/{id}"),
    "board must never fan out to per-task detail (strip + activity stay row-level)",
  );
  const api = read("src/lib/api/workspace.ts");
  assert.ok(
    api.includes("PipelineState") && api.includes("pipeline?"),
    "TaskRow type must carry the optional pipeline object",
  );
});

test("board states its value in one line under the title", () => {
  const page = read("src/app/workspace/page.tsx");
  assert.ok(
    page.includes("Project board"),
    "board must keep its title",
  );
  assert.ok(
    page.includes("What needs you first, what is moving, and what is done"),
    "board must carry the one-line value statement under the title",
  );
  assert.ok(
    !page.includes("gradient"),
    "board must keep theme tokens, no gradients",
  );
  assert.ok(
    !/#[0-9a-fA-F]{3,8}/.test(page),
    "board must not hard-code hex colours",
  );
});

test("board summary cards show Open / Blocked / Done plus an honest last-activity time", () => {
  const page = read("src/app/workspace/page.tsx");
  for (const label of ["Open", "Blocked / failed", "Done", "Last activity"]) {
    assert.ok(page.includes(label), `summary row must include "${label}"`);
  }
  assert.ok(
    !page.includes(">Last review<") && !page.includes(">last review<"),
    "summary must not claim a last-review time the list rows cannot source",
  );
  assert.ok(
    page.includes("updated_at"),
    "last-activity time must derive from max updated_at",
  );
});

test("board cards collapse the repeated tenant/project string into one label", () => {
  const page = read("src/app/workspace/page.tsx");
  assert.ok(
    page.includes("scopeLabel"),
    "board must collapse the per-section scope into one compact label",
  );
  assert.ok(
    !page.includes("{task.tenant} / {task.project}"),
    "cards must not repeat the tenant / project string on every row",
  );
  assert.ok(
    page.includes("text-base") && page.includes("<h3"),
    "cards must render a large title",
  );
  assert.ok(
    page.includes("font-mono") && page.includes("updated"),
    "cards must keep small metadata (id + updated time)",
  );
});

test("board recent-activity feed renders the latest 8 real events", () => {
  const page = read("src/app/workspace/page.tsx");
  assert.ok(
    page.includes("Recent activity"),
    "board must render a Recent activity section",
  );
  assert.ok(
    page.includes("ACTIVITY_LIMIT") && page.includes("slice(0, ACTIVITY_LIMIT)"),
    "feed must cap at the latest 8 events",
  );
  assert.ok(
    page.includes('live: "0"') && page.includes("parseSseFrames"),
    "feed must read a closed backlog snapshot (live=0) parsed as SSE frames",
  );
  assert.ok(
    page.includes("created_at") && page.includes("<time"),
    "feed rows must render the real event time",
  );
  assert.ok(
    page.includes("eventLabel") || page.includes("task #${"),
    "feed rows must render a plain label with the task reference",
  );
});

test("task detail renders the shared pill and the full strip", () => {
  const page = read("src/app/workspace/tasks/[id]/page.tsx");
  assert.ok(
    page.includes("TaskStatePill"),
    "detail must use the shared TaskStatePill",
  );
  assert.ok(
    page.includes("PipelineStrip") && page.includes('"full"'),
    "detail must render the full pipeline strip",
  );
});
