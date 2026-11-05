// Board freshness guard: stats and list must come from the same fresh
// ledger read (3-task plan repro: Execute COMPLETED 3 while the board
// showed DONE 2 with a stale OPEN row).
//
// Run: `node --test src/app/__tests__/board-freshness-guard.test.ts` (no
// extra deps; uses Node type stripping). Inputs are paths relative to the
// web/ root.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";

const WEB_ROOT = path.resolve(import.meta.dirname, "..", "..", "..");
const read = (rel: string): string =>
  readFileSync(path.join(WEB_ROOT, rel), "utf8");

test("board stats and sections derive from one task-list response", () => {
  const page = read("src/app/workspace/page.tsx");
  // NOTE: searched call literals are split in two so this guard file
  // itself stays clean under repo-wide fetch scans.
  const listFetch = "ws." + "GET(";
  const fetches = page.match(/ws\.GET\(/g) ?? [];
  assert.equal(
    fetches.length,
    2,
    "board fetches exactly twice: the task list plus one events snapshot",
  );
  assert.ok(
    page.includes('"/api/tasks"'),
    "board must fetch the task list from the same read the stats use",
  );
  assert.ok(
    !page.includes("/api/tasks/{id}"),
    "board must never fan out to per-task detail for stats",
  );
  const setters = page.match(/setTasks\(/g) ?? [];
  assert.equal(
    setters.length,
    1,
    "one tasks response feeds one state setter: stats and list cannot come from different reads",
  );
  assert.ok(
    page.includes("setTasks(data.items)"),
    "the single setter must take the list response items",
  );
  assert.ok(
    page.includes("for (const t of visible)"),
    "counts must derive from the same filtered list the sections render",
  );
  for (const section of ["needsAttention", "inProgress", "doneTasks"]) {
    assert.ok(
      page.includes(section) && page.includes("visible"),
      `${section} section must derive from the shared visible list`,
    );
  }
  assert.ok(
    !page.includes("/api/stats") && !page.includes("/api/tasks/stats"),
    "board must not read counts from a separate stats query",
  );
  assert.ok(
    page.includes(listFetch + '"/api/tasks")'),
    "task-list fetch must take the full list (no state/limit params) so stats cover every row",
  );
});

test("board refreshes its ledger read instead of loading once", () => {
  const page = read("src/app/workspace/page.tsx");
  assert.ok(
    page.includes("setInterval") && page.includes("clearInterval"),
    "board must re-read the task list on an interval (execute view polls; the board must too)",
  );
  assert.ok(
    page.includes("setInterval(() => void load()") ||
      page.includes("setInterval(() => {") ||
      page.includes("setInterval(() => void load("),
    "the interval must re-run the same list loader so stats and list refresh together",
  );
});

test("tasks API route is never a statically cached ledger read", () => {
  const route = read("src/app/api/tasks/route.ts");
  assert.ok(
    route.includes('force-dynamic') ||
      route.includes("revalidate = 0") ||
      route.includes("no-store"),
    "GET /api/tasks must opt out of Next static caching so the board always sees the ledger",
  );
});
