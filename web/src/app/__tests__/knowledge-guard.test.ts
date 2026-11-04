// P5 slice guard: knowledge page explains itself, opens with content,
// counts honestly, and never invents numbers.
//
// Run: `node --test src/app/__tests__/knowledge-guard.test.ts` (no extra
// deps; uses Node type stripping). Source assertions use paths relative to
// the web/ root; behavior assertions import the pure lib directly.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import {
  DEFAULT_QUERY,
  KIND_OPTIONS,
  citationId,
  citationVersion,
  countKinds,
  isVerified,
} from "../../lib/knowledge.ts";

const WEB_ROOT = path.resolve(import.meta.dirname, "..", "..", "..");
const read = (rel: string): string =>
  readFileSync(path.join(WEB_ROOT, rel), "utf8");

const PAGE = "src/app/workspace/knowledge/page.tsx";
const EMPTY_GUIDE =
  "created from approved decisions, task outcomes and chat — search above to browse";

// --- pure helpers -------------------------------------------------------------

test("default query carries a searchable word (blank queries are a 422)", () => {
  assert.ok(
    /\w+/.test(DEFAULT_QUERY),
    "DEFAULT_QUERY must contain a searchable word or the ledger rejects it",
  );
  assert.ok(
    DEFAULT_QUERY.trim().length > 0,
    "DEFAULT_QUERY must not be blank",
  );
});

test("citation id and version parse from the ledger citation, never invented", () => {
  assert.equal(citationId("knowledge:258@v3"), "258");
  assert.equal(citationVersion("knowledge:258@v3"), "v3");
  assert.equal(citationVersion("knowledge:258@v1"), "v1");
  assert.equal(citationVersion("not-a-citation"), null);
  assert.equal(isVerified({ verified: 1 }), true);
  assert.equal(isVerified({ verified: true }), true);
  assert.equal(isVerified({ verified: 0 }), false);
  assert.equal(isVerified({ verified: false }), false);
});

test("kind counts tally the loaded shortlist, ignoring foreign kinds", () => {
  const counts = countKinds([
    { kind: "note" },
    { kind: "note" },
    { kind: "rule" },
    { kind: "user_profile" },
  ]);
  assert.equal(counts.note, 2);
  assert.equal(counts.rule, 1);
  assert.equal(counts.decision, 0);
  assert.equal(counts.snapshot, 0);
  assert.equal(counts.skill, 0);
  assert.deepEqual(Object.keys(counts).sort(), [...KIND_OPTIONS].sort());
});

// --- explainer -----------------------------------------------------------------

test("header states what the system is, honestly", () => {
  const page = read(PAGE);
  const flat = page.replace(/\s+/g, " ");
  for (const phrase of [
    "Institutional memory",
    "Versioned project memory",
    "reusable memory for future tasks",
  ]) {
    assert.ok(flat.includes(phrase), `header must mention "${phrase}"`);
  }
});

// --- kind counts -----------------------------------------------------------------

test("folders aside tallies the loaded shortlist as showing N", () => {
  const page = read(PAGE);
  assert.ok(page.includes("countKinds"), "folders must tally the loaded hits");
  assert.ok(page.includes("showing"), "folders must label themselves as showing N");
  assert.ok(page.includes("match filters"), "folders must state the filter match");
  assert.deepEqual(
    [...KIND_OPTIONS].sort(),
    ["decision", "note", "rule", "skill", "snapshot"],
    "shared kind options must cover rules/decisions/notes/snapshots/skills",
  );
});

test("no ledger endpoint for totals; search/detail/history/usage only", () => {
  const page = read(PAGE);
  assert.ok(page.includes('"/api/knowledge/search"'));
  assert.ok(page.includes('"/api/knowledge/{key}"'));
  assert.ok(page.includes('"/api/knowledge/{key}/usage"'));
  assert.ok(
    !page.includes("/api/knowledge/counts"),
    "page must not fetch a counts endpoint",
  );
  assert.equal(
    (page.match(/ws\.GET\(/g) ?? []).length,
    4,
    "page fetches exactly four endpoints: search, detail, history, usage",
  );
  assert.ok(
    !page.includes("/v1/knowledge"),
    "page must go through the proxy, never the ledger directly",
  );
});

// --- empty state -------------------------------------------------------------------

test("guided empty state names where entries come from", () => {
  const page = read(PAGE);
  assert.ok(
    page.includes(EMPTY_GUIDE),
    "empty state must carry the guided sentence",
  );
});

// --- default view ---------------------------------------------------------------------

test("page opens with content: default search runs once on mount", () => {
  const page = read(PAGE);
  assert.ok(
    page.includes("DEFAULT_QUERY") && page.includes("useState(DEFAULT_QUERY)"),
    "search box must start on the broad default query",
  );
  assert.ok(
    page.includes("useEffect") && page.includes("defaultRan"),
    "default search must run once on mount with a run-once guard",
  );
  assert.ok(
    page.includes("Loading recent entries"),
    "first load must show a loading state, not a bare empty page",
  );
});

test("default search sends no project filter (the 0-entries trap)", () => {
  // Ledger proof (2026-11-03, :8221, stack token, read-only GETs):
  // q=the with no project returned items; q=the&project=demo returned
  // {"items":[]}; the demo DB holds 1096 entries under other projects.
  // Scoping to the session's first project hid them, so the page must not
  // send a project filter — tenant scoping stays server-side.
  const page = read(PAGE);
  const searchCall = page.slice(
    page.indexOf('"/api/knowledge/search"') - 200,
    page.indexOf('"/api/knowledge/search"') + 500,
  );
  assert.ok(!searchCall.includes("project"), "search must not send a project filter");
  assert.ok(
    !page.includes("sessionProject") && !page.includes("useSession"),
    "page must not scope to the session project at all",
  );
});

// --- per-entry version, no invented usage ----------------------------------------------

test("versions render from data; usage renders from the usage endpoint or em dash", () => {
  const page = read(PAGE);
  assert.ok(page.includes("data-version"), "entries must show a version");
  assert.ok(
    page.includes("citationVersion"),
    "list versions must parse from the ledger citation",
  );
  assert.ok(
    page.includes("v{selected.version}"),
    "detail version must render the entry's real version field",
  );
  assert.ok(
    page.includes("applied_to_tasks"),
    "usage line must read the ledger usage field",
  );
  assert.ok(
    page.includes("Usage loading") || page.includes("Usage —"),
    "unloaded usage must show a loading or em-dash state, never a number",
  );
  for (const invented of ["used by", "last applied", "lastApplied", "usedBy"]) {
    assert.ok(
      !page.includes(invented),
      `page must not claim "${invented}" outside the sourced usage line`,
    );
  }
});

// --- theme ------------------------------------------------------------------------------

test("knowledge slice keeps theme tokens, no hard-coded colour", () => {
  for (const rel of [PAGE, "src/lib/knowledge.ts"]) {
    const src = read(rel);
    assert.ok(!/#[0-9a-fA-F]{3,8}/.test(src), `${rel} must not hard-code hex colours`);
  }
  const page = read(PAGE);
  assert.ok(
    page.includes("text-dim") && page.includes("text-fog"),
    "knowledge must keep Figma theme tokens",
  );
});
