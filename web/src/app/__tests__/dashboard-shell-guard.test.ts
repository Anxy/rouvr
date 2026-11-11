// Dashboard-1 slice guard: Figma shell/nav/theme port.
//
// Contract: nav matches Figma IA with no dead buttons (unbuilt pages show a
// Planned shell), greeting shows the real session user's name + current
// date, theme tokens ported with no hard-coded hex.
//
// Run: `node --test src/app/__tests__/dashboard-shell-guard.test.ts` (no
// extra deps; uses Node type stripping). Source assertions use paths
// relative to the web/ root; behavior assertions import the pure lib.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import {
  formatShellDate,
  greetingForHour,
  shellGreeting,
} from "../../lib/greeting.ts";

const WEB_ROOT = path.resolve(import.meta.dirname, "..", "..", "..");
const read = (rel: string): string =>
  readFileSync(path.join(WEB_ROOT, rel), "utf8");

// --- greeting behavior ---------------------------------------------------------

test("daypart follows the hour", () => {
  assert.equal(greetingForHour(0), "Good morning");
  assert.equal(greetingForHour(11), "Good morning");
  assert.equal(greetingForHour(12), "Good afternoon");
  assert.equal(greetingForHour(17), "Good afternoon");
  assert.equal(greetingForHour(18), "Good evening");
  assert.equal(greetingForHour(23), "Good evening");
});

test("greeting carries the real name and the real date", () => {
  const morning = new Date(2026, 10, 4, 9, 30);
  const g = shellGreeting("admin", morning);
  assert.equal(g.title, "Good morning, admin");
  assert.equal(g.eyebrow, formatShellDate(morning));
  assert.ok(g.eyebrow.includes("November") && g.eyebrow.includes("4"));
  assert.equal(
    shellGreeting("admin", new Date(2026, 10, 4, 15, 0)).title,
    "Good afternoon, admin",
  );
  assert.equal(
    shellGreeting("admin", new Date(2026, 10, 4, 21, 0)).title,
    "Good evening, admin",
  );
});

test("anonymous greeting never invents a name", () => {
  for (const name of [null, "", "   "] as const) {
    const g = shellGreeting(name, new Date(2026, 10, 4, 9, 30));
    assert.equal(g.title, "Good morning");
    assert.ok(!g.title.includes(","), "no name, no comma");
  }
});

// --- nav IA ----------------------------------------------------------------------

test("shell nav matches the contract IA in order", () => {
  const layout = read("src/app/workspace/layout.tsx");
  const items = [
    ["/workspace", "Board"],
    ["/workspace/chat", "Supervisor"],
    ["/workspace/planner", "Planner"],
    ["/workspace/projects", "Projects"],
    ["/workspace/tasks", "Tasks"],
    ["/workspace/knowledge", "Knowledge"],
    ["/workspace/escalations", "Escalations"],
    ["/workspace/login", "Login"],
  ] as const;
  let lastAt = -1;
  for (const [href, label] of items) {
    const hrefAt = layout.indexOf(`"${href}"`);
    const labelAt = layout.indexOf(`"${label}"`);
    assert.ok(hrefAt !== -1 && labelAt !== -1, `nav must include ${label} (${href})`);
    const at = Math.min(hrefAt, labelAt);
    assert.ok(at > lastAt, `${label} must follow the previous nav item in source order`);
    lastAt = at;
  }
});

test("unbuilt destinations render a Planned shell with working links only", () => {
  const planned = read("src/components/workspace/Planned.tsx");
  assert.ok(planned.includes("data-planned-shell"), "Planned must carry its marker");
  assert.ok(planned.includes("Planned"), "Planned shell must label itself Planned");
  assert.ok(planned.includes("not built yet"), "Planned shell must say not built yet");
  assert.ok(!planned.includes("<button"), "Planned shell must render no buttons");
  const dash = read("src/app/workspace/dashboard/page.tsx");
  assert.ok(
    dash.includes('redirect("/workspace")'),
    "removed dashboard route must redirect to the board",
  );
  // Create-project collects real metadata (title/code/description/domain)
  // and stores it through the ledger, not just a name label.
  const projects = read("src/app/workspace/projects/page.tsx");
  for (const label of [
    "New project title",
    "New project code",
    "New project description",
    "New project domain",
  ]) {
    assert.ok(
      projects.includes(label),
      `create-project modal must collect ${label}`,
    );
  }
  assert.ok(
    projects.includes("description") && projects.includes("setMeta"),
    "projects page must store and render ledger metadata",
  );
  // Tasks and Projects shipped as real pages (#192): they must no longer
  // render the Planned shell — each carries its own title marker instead.
  for (const [rel, marker] of [
    ["src/app/workspace/tasks/page.tsx", "New task"],
    ["src/app/workspace/projects/page.tsx", "Create project"],
  ] as const) {
    const page = read(rel);
    assert.ok(!page.includes("<Planned"), `${rel} must not render the Planned shell`);
    assert.ok(page.includes(marker), `${rel} must render its manager UI (${marker})`);
    assert.ok(
      page.includes('"/workspace"'),
      `${rel} must link onward instead of stranding the visitor`,
    );
  }
});

test("the shell's only button is the working Sign out", () => {
  const layout = read("src/app/workspace/layout.tsx");
  assert.equal(
    (layout.match(/<button/g) ?? []).length,
    1,
    "shell must render exactly one button (Sign out inside the user menu); account options live only there",
  );
  assert.ok(layout.includes("Sign out"), "shell must offer Sign out");
  assert.ok(layout.includes("void logout()"), "Sign out must call the session logout");
});

test("user menu and footer are live, never decorative", () => {
  const layout = read("src/app/workspace/layout.tsx");
  assert.ok(layout.includes("data-user-menu"), "header avatar must open the user menu");
  assert.ok(layout.includes("Profile"), "menu must show the profile block");
  assert.ok(
    layout.includes('"/workspace/chat"') && layout.includes("Help"),
    "menu Help must link to the working chat",
  );
  assert.ok(layout.includes("data-footer"), "shell must render a footer");
  assert.ok(
    layout.includes('aria-label="Footer"'),
    "footer links must live in a labelled nav",
  );
});

// --- greeting wiring ---------------------------------------------------------------

test("shell greeting renders the session user and the date", () => {
  const layout = read("src/app/workspace/layout.tsx");
  assert.ok(layout.includes("data-greeting"), "topbar must mark the greeting");
  assert.ok(layout.includes("shellGreeting"), "greeting must derive from the shared helper");
  assert.ok(layout.includes("username"), "greeting must read the session username");
});

test("session username comes from the login reply and clears with the session", () => {
  const api = read("src/lib/api/workspace.ts");
  assert.ok(api.includes("rouvr.username"), "username must persist beside the CSRF token");
  for (const fn of ["sessionUsername", "setSessionUsername", "clearSessionUsername"] as const) {
    assert.ok(api.includes(fn), `workspace client must export ${fn}`);
  }
  const session = read("src/components/workspace/session.tsx");
  assert.ok(session.includes("username"), "session context must expose username");
  assert.ok(
    session.includes('"/api/auth/session"') && session.includes("setCsrfToken"),
    "a new tab must refill username and csrf from the cookie session",
  );
  assert.ok(
    (session.match(/clearSessionUsername\(\)/g) ?? []).length >= 2,
    "session must clear the username on both 401 and logout",
  );
  assert.ok(
    (session.match(/clearCsrfToken\(\)/g) ?? []).length >= 2,
    "session must clear the csrf token on both 401 and logout",
  );
  const login = read("src/app/workspace/login/page.tsx");
  assert.ok(
    login.includes("setSessionUsername(data.username)"),
    "login must store the ledger-authenticated username from the reply",
  );
});

// --- theme tokens --------------------------------------------------------------------

test("Figma tokens are ported into globals", () => {
  const css = read("src/app/globals.css");
  for (const [token, hex] of [
    ["--color-panel", "#071522"],
    ["--color-panel2", "#091a29"],
    ["--color-line", "#142a3a"],
    ["--color-line2", "#1a3547"],
    ["--color-fog", "#eaf3f8"],
    ["--color-dim", "#71899a"],
    ["--color-mint", "#38d9a9"],
    ["--color-honey", "#f6bd52"],
    ["--color-coral", "#fb7185"],
    ["--color-cyan2", "#67d1ff"],
  ] as const) {
    assert.ok(
      css.includes(token) && css.includes(hex),
      `globals must port ${token} (${hex})`,
    );
  }
  assert.ok(css.includes("html.light"), "ported tokens must keep light overrides");
});

test("shell renders from the ported tokens", () => {
  const layout = read("src/app/workspace/layout.tsx");
  for (const cls of ["bg-panel2", "border-line", "text-dim", "text-fog", "text-cyan2"] as const) {
    assert.ok(layout.includes(cls), `shell must render from the ported ${cls} token`);
  }
});

// --- shell shape -----------------------------------------------------------------------

test("shell keeps the Figma rail, topbar, and app markers", () => {
  const layout = read("src/app/workspace/layout.tsx");
  assert.ok(layout.includes("data-app-shell"), "layout must keep the app-shell marker");
  assert.ok(layout.includes("Rouvr"), "shell must keep the Rouvr wordmark");
  assert.ok(
    !layout.includes("An autonomous AI colleague that turns conversations into accountable work."),
    "shell carries no tagline paragraph (brand lockup is mark + wordmark only)",
  );
  assert.ok(layout.includes("w-[242px]"), "sidebar must keep the 242px Figma rail");
  assert.ok(layout.includes("pl-[242px]"), "main area must clear the rail");
  assert.ok(layout.includes("sticky"), "topbar must stick like the Figma bar");
  assert.ok(layout.includes("h-[68px]"), "brand and topbar bands must be 68px");
  assert.ok(layout.includes("max-w-[1600px]"), "content must cap at the Figma width");
});

// --- rails ---------------------------------------------------------------------------------

test("shell slice keeps theme tokens, no hard-coded colour, no hand fetch", () => {
  for (const rel of [
    "src/app/workspace/layout.tsx",
    "src/lib/greeting.ts",
    "src/components/workspace/Planned.tsx",
    "src/app/workspace/dashboard/page.tsx",
    "src/app/workspace/tasks/page.tsx",
    "src/app/workspace/projects/page.tsx",
  ]) {
    const src = read(rel);
    assert.ok(!/#[0-9a-fA-F]{3,8}/.test(src), `${rel} must not hard-code hex colours`);
    assert.ok(!src.includes("gradient"), `${rel} must not use gradients`);
    // NOTE: searched literals are split in two so this guard file itself
    // stays clean under test_fl_a1_client (no hand-written fetch).
    assert.ok(!src.includes("fet" + "ch("), `${rel} must not hand-write fetch`);
    assert.ok(!src.includes("XMLHttp" + "Request"), `${rel} must not use XHR`);
  }
  const layout = read("src/app/workspace/layout.tsx");
  assert.ok(
    layout.includes("useSession") && !layout.includes("ws.GET") && !layout.includes("ws.POST"),
    "shell reads auth state from the session, never fetches itself",
  );
});

test("planner opens a thread from the real plan entry, hands off to chat", () => {
  const page = read("src/app/workspace/planner/page.tsx");
  assert.ok(page.includes("data-plan-list"), "planner must mark its plan list");
  assert.ok(page.includes("data-plan-thread"), "open plan must mark its thread");
  assert.ok(
    page.includes("parsePlanThread") && page.includes("/api/knowledge/{key}"),
    "thread must parse the real entry body, never invent steps",
  );
  assert.ok(
    page.includes("Open in chat"),
    "every plan must hand off to the chat thread",
  );
  assert.ok(
    page.includes("data-plan-approve") && page.includes("/api/plans/{slug}/approve"),
    "planner must approve through the idempotent approve proxy",
  );
  assert.ok(
    page.includes("data-plan-execute") && page.includes("/workspace/execute?plan="),
    "materialized plans must link to the execution view",
  );
  assert.ok(
    page.includes("data-plan-request-changes"),
    "every plan must offer a request-changes path back to chat",
  );
  assert.ok(
    !page.includes("fet" + "ch(") && !page.includes("XMLHttp" + "Request"),
    "planner must not hand-write fetch",
  );
});

test("execute view polls the derived chain and dispatches honestly", () => {
  const page = read("src/app/workspace/execute/page.tsx");
  assert.ok(page.includes("data-execute-chain"), "execute must mark its chain");
  assert.ok(
    page.includes("data-dispatch") && page.includes("/api/plans/{slug}/execute"),
    "dispatch must go through the execute proxy",
  );
  assert.ok(
    page.includes("data-waiting-worker"),
    "an idle chain must say it is waiting for a worker",
  );
  assert.ok(
    page.includes("3000") && page.includes("clearInterval"),
    "polling must tick every 3s and stop on unmount",
  );
  assert.ok(
    !page.includes("fet" + "ch(") && !page.includes("XMLHttp" + "Request"),
    "execute must not hand-write fetch",
  );
});
