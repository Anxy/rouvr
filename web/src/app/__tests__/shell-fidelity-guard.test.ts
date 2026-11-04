// Round-2 shell-fidelity guard (#183): Figma logo, nav hover, theme toggle,
// project heading, real user.
//
// Contract: RouvrMark + wordmark top-left ported from Figma, nav hover
// matches Figma, sun/moon toggle switches dark/light and persists, header
// shows the active project heading, bottom-left shows the real logged-in
// username.
//
// Run: `node --test src/app/__tests__/shell-fidelity-guard.test.ts` (no
// extra deps; uses Node type stripping). Source assertions use paths
// relative to the web/ root.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";

const WEB_ROOT = path.resolve(import.meta.dirname, "..", "..", "..");
const read = (rel: string): string =>
  readFileSync(path.join(WEB_ROOT, rel), "utf8");

const hexes = (src: string): string[] => src.match(/#[0-9a-fA-F]{3,8}/g) ?? [];

// --- RouvrMark logo + wordmark -----------------------------------------------------

test("RouvrMark is ported verbatim from the Figma source", () => {
  const mark = read("src/components/workspace/RouvrMark.tsx");
  assert.ok(mark.includes('viewBox="0 0 40 40"'), "mark must keep the Figma 40-grid");
  assert.ok(
    mark.includes("M10 13.5 14.5 8l3 5h5l3-5 4.5 5.5"),
    "mark must keep the Figma dog path",
  );
  assert.ok(
    mark.includes("Rouvr blue dog mascot"),
    "mark must keep the Figma aria-label",
  );
  assert.deepEqual(
    [...new Set(hexes(mark))].sort(),
    ["#061522", "#08A9F0", "#EAF9FF"].sort(),
    "mark may only carry the three Figma brand fills (fixed asset, not UI theme)",
  );
});

test("shell brand lockup renders the mark plus the Figma wordmark", () => {
  const layout = read("src/app/workspace/layout.tsx");
  assert.ok(
    layout.includes("@/components/workspace/RouvrMark"),
    "shell must import the ported RouvrMark",
  );
  assert.ok(layout.includes("<RouvrMark"), "shell must render the mark top-left");
  assert.ok(layout.includes("Rouvr home"), "brand link must label itself for AT");
  assert.ok(
    layout.includes("rouvr") && layout.includes("text-[24px]"),
    "shell must render the lowercase wordmark enlarged at 24px",
  );
  assert.ok(
    layout.includes("tracking-[-0.5px]"),
    "wordmark must keep the Figma -0.5px tracking",
  );
});

// --- nav hover ---------------------------------------------------------------------

test("nav rows match the Figma refined metrics and hover", () => {
  const layout = read("src/app/workspace/layout.tsx");
  for (const cls of [
    "h-10",
    "gap-3",
    "px-[11px]",
    "text-xs",
    "duration-[160ms]",
    "hover:bg-panel2",
    "hover:text-fog",
    "motion-safe:hover:translate-x-0.5",
    "opacity-[0.78]",
    "group-hover:opacity-100",
  ] as const) {
    assert.ok(layout.includes(cls), `nav row must carry ${cls}`);
  }
  assert.ok(
    layout.includes("nav-item-active"),
    "active nav row must use the inset-bar utility, not a shifting border",
  );
  const css = read("src/app/globals.css");
  assert.ok(css.includes(".nav-item-active"), "globals must define the active utility");
  assert.ok(
    css.includes("inset 2px 0 0 var(--color-brand)"),
    "active utility must be an inset 2px brand bar from the token",
  );
});

// --- theme toggle --------------------------------------------------------------------

test("workspace sun/moon toggle switches theme and persists", () => {
  const toggle = read("src/components/workspace/ThemeToggle.tsx");
  assert.equal(
    (toggle.match(/<button/g) ?? []).length,
    1,
    "toggle must render exactly one button",
  );
  assert.ok(toggle.includes("data-theme-toggle"), "toggle must carry its marker");
  assert.ok(
    toggle.includes('aria-label="Toggle color theme"'),
    "toggle must label itself for AT",
  );
  // Figma sun verbatim (12-grid circle + 8 rays), same stroke style both icons.
  assert.ok(
    toggle.includes('cx="12" cy="12" r="4"') &&
      toggle.includes("M12 2v2M12 20v2M4.9 4.9l1.4 1.4"),
    "sun must be the Figma icon verbatim",
  );
  assert.ok(toggle.includes("M20 13.5A8 8 0 0 1 10.5 4"), "moon must render a crescent");
  assert.equal(
    (toggle.match(/strokeWidth="1\.8"/g) ?? []).length,
    2,
    "both icons must share the Figma 1.8 stroke",
  );
  assert.ok(
    toggle.includes("theme-icon-sun") && toggle.includes("theme-icon-moon"),
    "icons must use the CSS-switched visibility classes (no hydration flash)",
  );
  // Same storage contract as the root themeBoot script: key "theme".
  assert.ok(
    toggle.includes('"theme"') && toggle.includes("localStorage"),
    "toggle must persist under the shared theme key",
  );
  assert.ok(
    toggle.includes('classList.contains("light")') &&
      toggle.includes('classList.remove("light", "dark")'),
    "toggle must flip the <html> light/dark class the tokens key off",
  );
  assert.ok(
    toggle.includes('setAttribute("data-theme"'),
    "toggle must mirror data-theme like the boot script",
  );
  assert.deepEqual(
    [...new Set(hexes(toggle))].sort(),
    ["#040B13", "#F3F6FA"].sort(),
    "toggle hex may only be the theme-color meta values",
  );
  const layout = read("src/app/workspace/layout.tsx");
  assert.ok(layout.includes("<ThemeToggle"), "shell header must render the toggle");
  const root = read("src/app/layout.tsx");
  assert.ok(
    root.includes('localStorage.getItem(key)') && root.includes('var key="theme"'),
    "root layout must keep the pre-paint boot that restores the stored theme",
  );
});

// --- project heading + real user -------------------------------------------------------

test("header shows the active project heading", () => {
  const layout = read("src/app/workspace/layout.tsx");
  assert.ok(layout.includes("data-project-heading"), "header must mark the heading");
  assert.ok(layout.includes("Active project"), "heading must label itself");
  assert.ok(
    layout.includes('project ?? "—"'),
    "heading must show the session project, em dash when missing",
  );
});

test("bottom-left profile shows the real logged-in username", () => {
  const layout = read("src/app/workspace/layout.tsx");
  assert.ok(layout.includes("data-profile-username"), "profile name must carry its marker");
  assert.ok(
    layout.includes('{username ?? "unknown user"}'),
    "profile must render the session username, never a placeholder",
  );
});

// --- rails ---------------------------------------------------------------------------------

test("shell fidelity keeps tokens, no hand fetch, button set exact", () => {
  for (const rel of [
    "src/app/workspace/layout.tsx",
    "src/components/workspace/ThemeToggle.tsx",
    "src/components/workspace/RouvrMark.tsx",
  ]) {
    const src = read(rel);
    assert.ok(!src.includes("gradient"), `${rel} must not use gradients`);
    // NOTE: searched literals are split in two so this guard file itself
    // stays clean under test_fl_a1_client (no hand-written fetch).
    assert.ok(!src.includes("fet" + "ch("), `${rel} must not hand-write fetch`);
    assert.ok(!src.includes("XMLHttp" + "Request"), `${rel} must not use XHR`);
  }
  const layout = read("src/app/workspace/layout.tsx");
  assert.equal(
    (layout.match(/<button/g) ?? []).length,
    1,
    "shell keeps exactly one literal button (Sign out); the toggle is a component",
  );
  assert.ok(
    layout.includes("void logout()"),
    "Sign out must still call the session logout",
  );
});
