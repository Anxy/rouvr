// Shell guard: /workspace* must render the app shell, never marketing chrome.
//
// The root layout renders no Intro/Header/Footer (see prior layout split); the
// (marketing) group layout owns Skip link + Intro + Header + main + Footer and
// the workspace group layout owns the app shell marked with `data-app-shell`.
// This test pins that composition at the source level so a future import can
// never leak marketing chrome into /workspace* HTML, and pins that `/` keeps
// the marketing markers.
//
// Run: `node --test src/app/__tests__/shell-guard.test.ts` (no extra deps;
// uses Node type stripping). Inputs are paths relative to the web/ root.

import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";

const WEB_ROOT = path.resolve(import.meta.dirname, "..", "..", "..");
const SRC = (...segs: string[]): string => path.join(WEB_ROOT, "src", ...segs);

const APP_SHELL_MARKER = "data-app-shell";
const MARKETING_TAGLINE = "Make it happen.";

function listSources(dir: string): string[] {
  const out: string[] = [];
  if (!existsSync(dir)) return out;
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...listSources(full));
    else if (/\.(tsx?|jsx?|css)$/.test(entry.name)) out.push(full);
  }
  return out.sort();
}

function read(rel: string): string {
  return readFileSync(path.join(WEB_ROOT, rel), "utf8");
}

/** Labels from src/content/nav.ts whose href is not the /workspace entry. */
function marketingNavLabels(): string[] {
  const nav = read("src/content/nav.ts");
  const labels: string[] = [];
  for (const m of nav.matchAll(/\{\s*label:\s*"([^"]+)"\s*,\s*href:\s*"([^"]+)"\s*\}/g)) {
    if (m[2] !== "/workspace") labels.push(m[1]);
  }
  return labels;
}

const MARKETING_COMPONENT_IMPORT =
  /@\/components\/layout\/(Header|Footer|Intro)|@\/content\/nav/;

test("workspace shell carries the app-shell marker", () => {
  const layout = read("src/app/workspace/layout.tsx");
  assert.ok(
    layout.includes(APP_SHELL_MARKER),
    `src/app/workspace/layout.tsx must render ${APP_SHELL_MARKER}`,
  );
});

test("workspace sources contain no marketing markers", () => {
  const files = [
    ...listSources(SRC("app", "workspace")),
    ...listSources(SRC("components", "workspace")),
  ];
  assert.ok(files.length > 0, "expected workspace sources to exist");
  const labels = marketingNavLabels();
  assert.ok(labels.length > 0, "expected marketing nav labels in nav.ts");
  const offenders: string[] = [];
  for (const file of files) {
    const text = readFileSync(file, "utf8");
    const rel = path.relative(WEB_ROOT, file);
    // The standalone login page (no app shell) carries the official
    // brand block by explicit user direction; everywhere else the
    // tagline is a leak.
    if (
      text.includes(MARKETING_TAGLINE) &&
      !(rel === "src/app/workspace/login/page.tsx" && text.includes("/rouvr-logo.svg"))
    )
      offenders.push(`${rel}: marketing tagline`);
    if (MARKETING_COMPONENT_IMPORT.test(text))
      offenders.push(`${rel}: marketing component/nav import`);
    // Marketing section anchors (/#meet, /#trust, ...) never belong in the app shell.
    const anchor = text.match(/"\/#[A-Za-z-]+"/);
    if (anchor) offenders.push(`${rel}: marketing anchor ${anchor[0]}`);
    for (const label of labels) {
      // Labels are asserted as rendered link text (>Label<). A bare substring
      // match would false-positive on domain copy such as the task detail
      // page's `Evidence ({task.evidence.length})` section heading.
      if (label === "Get in touch" ? text.includes(label) : text.includes(`>${label}<`))
        offenders.push(`${rel}: nav label "${label}"`);
    }
  }
  assert.deepEqual(offenders, [], "marketing markers leaked into workspace shell");
});

test("chat project defaults to the session tenant, never a hard-coded name", () => {
  const chat = read("src/components/workspace/ChatSurface.tsx");
  assert.ok(
    chat.includes("useSession"),
    "chat surface must read the active project from the session",
  );
  assert.ok(
    !chat.includes('useState("demo")') && !chat.includes("|| \"demo\""),
    "chat surface must not fall back to a hard-coded project name",
  );
});

test("board is an attention-first list, not kanban columns", () => {
  const page = read("src/app/workspace/page.tsx");
  assert.ok(
    !page.includes("grid-cols-5") && !page.includes("COLUMNS"),
    "board must not render a kanban column grid",
  );
  const attentionAt = page.indexOf('title="Needs attention"');
  const progressAt = page.indexOf('title="In progress"');
  const doneAt = page.indexOf('title="Done"');
  assert.ok(
    attentionAt !== -1 && progressAt !== -1 && doneAt !== -1,
    "board must render Needs attention, In progress and Done sections",
  );
  assert.ok(
    attentionAt < progressAt && progressAt < doneAt,
    "board sections must be ordered Needs attention, In progress, Done",
  );
});

test("board attention rows render the reason inline with a fallback", () => {
  const page = read("src/app/workspace/page.tsx");
  assert.ok(
    page.includes("attention") && page.includes("reason unavailable"),
    "attention rows must render the attention reason with a 'reason unavailable' fallback",
  );
});

test("board rows link to the task detail route", () => {
  const page = read("src/app/workspace/page.tsx");
  assert.ok(
    page.includes("/workspace/tasks/${"),
    "every board row must link to /workspace/tasks/[id]",
  );
  assert.ok(
    page.includes("StatusPill"),
    "board rows must keep the StatusPill",
  );
});

test("board has live counts, search, project filter and sort", () => {
  const page = read("src/app/workspace/page.tsx");
  for (const label of ["Open", "Claimed", "Needs review", "Blocked / failed", "Done"]) {
    assert.ok(page.includes(label), `board count strip must include "${label}"`);
  }
  assert.ok(
    page.includes('type="search"'),
    "board must have a live search box",
  );
  assert.ok(page.includes("Combobox"), "board must use the shared Combobox picker");
  const select = read("src/components/ui/Select.tsx");
  assert.ok(
    select.includes('role="combobox"') && select.includes("aria-activedescendant"),
    "Combobox must be an accessible combobox with keyboard nav",
  );
  assert.ok(
    select.includes('role="listbox"') && select.includes('role="option"'),
    "Combobox must extend the Select hand-rolled listbox pattern",
  );
  assert.ok(
    !page.includes("<datalist"),
    "board must not fall back to a native datalist",
  );
  assert.ok(
    page.includes("aria-pressed") && page.includes("setStateFilter"),
    "count chips must be toggle buttons filtering by state",
  );
  assert.ok(
    page.includes("useSession"),
    "board project dropdown must list the session projects",
  );
  assert.ok(
    page.includes("Newest first") && page.includes("Oldest first"),
    "board must have a newest/oldest sort toggle",
  );
});

test("board sections page client-side with prev/next and collapse", () => {
  const page = read("src/app/workspace/page.tsx");
  assert.ok(
    page.includes("PAGE_SIZE") && page.includes("slice(") && page.includes("updated_at"),
    "board sections must page client-side slices sorted by updated_at",
  );
  assert.ok(
    page.includes("Prev") && page.includes("Next"),
    "board sections must have working Prev/Next pagination",
  );
  assert.ok(
    page.includes("aria-expanded"),
    "board sections must be collapsible with an expand toggle",
  );
  assert.ok(
    page.includes("done: true"),
    "done section must start collapsed",
  );
  assert.ok(
    page.includes("overflow-y-auto"),
    "board lists must scroll internally",
  );
  assert.ok(
    !page.includes("<datalist"),
    "board must not fall back to a native datalist",
  );
});

test("knowledge page searches, filters and shows detail with history", () => {
  const page = read("src/app/workspace/knowledge/page.tsx");
  assert.ok(
    page.includes('type="search"'),
    "knowledge page must have a search box",
  );
  assert.ok(
    page.includes("Verified only") && page.includes('type="checkbox"'),
    "knowledge page must have a verified-only toggle",
  );
  // P5: kind options live in the shared lib (single source); the page
  // renders them via KIND_OPTIONS.map, so assert the import, not literals.
  assert.ok(
    page.includes("KIND_OPTIONS"),
    "knowledge kind filter must render the shared kind options",
  );
  const lib = read("src/lib/knowledge.ts");
  for (const kind of ["rule", "decision", "note", "snapshot", "skill"]) {
    assert.ok(
      lib.includes(`"${kind}"`),
      `shared kind options must include "${kind}"`,
    );
  }
  assert.ok(
    page.includes("citation") && page.includes("verified"),
    "knowledge results must show title/kind/verified pill/citation",
  );
  // Detail moved to the inner page: the index links out, the inner page
  // shows the body plus version history.
  assert.ok(
    page.includes("/workspace/knowledge/${citationId(hit.citation)}"),
    "knowledge cards must link to the entry inner page",
  );
  const inner = read("src/app/workspace/knowledge/[key]/page.tsx");
  assert.ok(
    inner.includes("Version history") && inner.includes("/history"),
    "knowledge inner page must show the body plus version history",
  );
  // P5 contract change (ledger proof 2026-11-03: q=the with no project
  // returned items on :8221 while q=the&project=demo returned {"items":[]};
  // the 1096 demo-DB entries live under other projects). Scoping search to
  // the session's first project hid those entries, so the page must NOT
  // send a project filter — tenant scoping stays server-side.
  assert.ok(
    !page.includes("useSession") && !page.includes("sessionProject"),
    "knowledge search must not scope to the session project",
  );
  assert.ok(
    !page.includes("@/components/layout") && !page.includes("@/content/nav"),
    "knowledge page must not import marketing components",
  );
  assert.ok(
    page.includes("/workspace/login"),
    "knowledge page must redirect anonymous users to login",
  );
});

test("workspace sub-nav links Board, Supervisor and Knowledge", () => {
  const layout = read("src/app/workspace/layout.tsx");
  for (const [href, label] of [
    ["/workspace", "Board"],
    ["/workspace/chat", "Supervisor"],
    ["/workspace/knowledge", "Knowledge"],
  ] as const) {
    assert.ok(
      layout.includes(`"${href}"`) && layout.includes(`"${label}"`),
      `workspace sub-nav must link ${label} (${href})`,
    );
  }
  const boardAt = layout.indexOf('"Board"');
  const chatAt = layout.indexOf('"Supervisor"');
  const knowledgeAt = layout.indexOf('"Knowledge"');
  assert.ok(
    boardAt !== -1 && chatAt !== -1 && knowledgeAt !== -1 && boardAt < chatAt && chatAt < knowledgeAt,
    "workspace sub-nav must order Board, Supervisor, Knowledge",
  );
});

test("chat renders a conversation thread with user and assistant turns", () => {
  const chat = read("src/components/workspace/ChatSurface.tsx");
  assert.ok(
    chat.includes("data-chat-thread"),
    "chat surface must render a thread container marked with data-chat-thread",
  );
  assert.ok(
    chat.includes('data-turn="user"') || chat.includes("data-turn='user'"),
    "chat thread must mark user turns",
  );
  assert.ok(
    chat.includes('data-turn="assistant"') || chat.includes("data-turn='assistant'"),
    "chat thread must mark assistant turns",
  );
  assert.ok(
    chat.includes("justify-end") && chat.includes("justify-start"),
    "user turns must be right-aligned and assistant turns left-aligned",
  );
  assert.ok(
    chat.includes("/workspace/tasks/${"),
    "assistant turns must render task cards linking to /workspace/tasks/[id]",
  );
  assert.ok(
    chat.includes("clarifying question"),
    "chat must render the clarifying-question state as a distinct assistant turn",
  );
  assert.ok(
    chat.includes("Retry") && chat.includes("scrollIntoView"),
    "chat must auto-scroll to latest and offer inline retry on the failed turn",
  );
});

test("chat replies carry an intake badge (Logged / Saved / Task)", () => {
  const chat = read("src/components/workspace/ChatSurface.tsx");
  assert.ok(
    chat.includes("data-intake-badge"),
    "assistant turns must render a small intake badge marked with data-intake-badge",
  );
  assert.ok(
    chat.includes("data-badge-kind") && chat.includes("turn.badge.label"),
    "badge must expose its kind and render the server-provided label",
  );
  // The three label shapes produced by the ledger intake router, pinned
  // where the UI documents the badge contract (JSX collapses whitespace).
  const flat = chat.replace(/\s+/g, " ");
  for (const marker of ["Logged", "Saved:", "Task #"] as const) {
    assert.ok(
      flat.includes(marker),
      `badge contract must cover the "${marker}" case`,
    );
  }
});

test("chat plan offers carry Convert/Edit/Just-keep-it buttons", () => {
  const chat = read("src/components/workspace/ChatSurface.tsx");
  assert.ok(
    chat.includes("data-plan-offer"),
    "plan offers must render a container marked with data-plan-offer",
  );
  for (const action of ["convert", "edit", "keep"] as const) {
    assert.ok(
      chat.includes(`data-plan-action="${action}"`),
      `plan offer must have a button with data-plan-action="${action}"`,
    );
  }
  // JSX text is split across lines; collapse whitespace before matching.
  const flat = chat.replace(/\s+/g, " ");
  for (const label of ["Convert", "Edit", "Just keep it"] as const) {
    assert.ok(
      flat.includes(`> ${label} <`),
      `plan offer must render a "${label}" button`,
    );
  }
  assert.ok(
    !chat.includes("data-autonomy"),
    "chat carries no autonomy picker (autonomy belongs to task execution, server default applies)",
  );
});

test("chat uses the header project only — no duplicate picker above the thread", () => {
  const page = read("src/app/workspace/chat/page.tsx");
  const chat = read("src/components/workspace/ChatSurface.tsx");
  const layout = read("src/app/workspace/layout.tsx");
  assert.ok(
    !page.includes("Combobox") && !chat.includes("Combobox"),
    "chat must not render a second project Combobox above the thread",
  );
  assert.ok(
    layout.includes('data-project-heading') && layout.includes('aria-label="Active project"'),
    "active project lives once in the shell header",
  );
  assert.ok(
    chat.includes("data-chat-history") && chat.includes("/api/chat/turns"),
    "chat must load and show previous turns for the active project",
  );
  assert.ok(
    !chat.includes('"demo"') && !chat.includes("'demo'"),
    "chat surface must not hard-code a 'demo' project default anywhere",
  );
});

test("marketing route keeps its markers", () => {
  const layout = read("src/app/(marketing)/layout.tsx");
  for (const name of ["Intro", "Header", "Footer"]) {
    assert.ok(
      layout.includes(name),
      `marketing layout must render <${name}/>`,
    );
  }
  const footer = read("src/components/layout/Footer.tsx");
  assert.ok(
    footer.includes(MARKETING_TAGLINE),
    "marketing footer must contain the tagline",
  );
  const header = read("src/components/layout/Header.tsx");
  assert.ok(
    /navLinks/.test(header),
    "marketing header must render the marketing nav links",
  );
  const home = read("src/app/(marketing)/page.tsx");
  assert.ok(home.length > 0, "marketing home page (/) must exist");
});
