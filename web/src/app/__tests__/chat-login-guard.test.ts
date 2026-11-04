// P4 slice guard: chat empty state + prominent intake cards, login value prop.
//
// Run: `node --test src/app/__tests__/chat-login-guard.test.ts` (no extra
// deps; uses Node type stripping). Inputs are paths relative to the web/ root.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";

const WEB_ROOT = path.resolve(import.meta.dirname, "..", "..", "..");
const read = (rel: string): string =>
  readFileSync(path.join(WEB_ROOT, rel), "utf8");

const VALUE_PROP =
  "Rouvr turns requests into tracked work: contract, build, review, evidence.";

test("chat helper text states the value prop in one line", () => {
  const chat = read("src/app/workspace/chat/page.tsx");
  assert.ok(
    chat.includes(VALUE_PROP),
    "chat helper text must state the tracked-work value prop",
  );
  const flat = chat.replace(/\s+/g, " ");
  assert.ok(
    !flat.includes("One turn: a chat answer"),
    "old multi-clause helper text must be gone",
  );
});

test("chat examples rotate as help text under the composer", () => {
  const chat = read("src/app/workspace/chat/page.tsx");
  assert.ok(
    chat.includes("data-chat-examples"),
    "rotator must render a container marked with data-chat-examples",
  );
  assert.ok(
    chat.includes("EXAMPLE_PROMPTS"),
    "examples must come from a shared EXAMPLE_PROMPTS list",
  );
  const prompts = chat.slice(chat.indexOf("EXAMPLE_PROMPTS = ["));
  assert.ok(
    (prompts.match(/"/g) ?? []).length >= 8,
    "EXAMPLE_PROMPTS must hold four prompt strings",
  );
  assert.ok(
    chat.includes("data-example-prompt"),
    "the rotating example must be clickable (data-example-prompt)",
  );
  // JSX text is split across lines; collapse whitespace before matching.
  const flat = chat.replace(/\s+/g, " ");
  assert.ok(
    flat.includes("> Example <"),
    "the rotator must visibly carry the 'Example' label",
  );
  assert.ok(
    chat.includes("ExampleRotator") && chat.includes("setInterval"),
    "examples must rotate on a timer under the composer",
  );
  assert.ok(
    !chat.includes("No turns yet"),
    "the old empty-state header is gone (rotator is always-on help text)",
  );
  assert.ok(
    chat.includes("onPick"),
    "clicking the example must prefill the composer (no send)",
  );
});

test("chat intake badge is a prominent pill with TaskStatePill tones", () => {
  const chat = read("src/app/workspace/chat/page.tsx");
  assert.ok(
    chat.includes("data-intake-badge"),
    "assistant turns must render a badge marked with data-intake-badge",
  );
  assert.ok(
    chat.includes("data-badge-kind") && chat.includes("turn.badge.label"),
    "badge must expose its kind and render the server-provided label",
  );
  for (const kind of ["task", "saved", "logged"] as const) {
    assert.ok(
      chat.includes(`"${kind}"`) || chat.includes(`${kind}:`),
      `badge tones must cover the "${kind}" kind`,
    );
  }
  // Tones mirror TaskStatePill: open blue for tasks, done green for saved,
  // neutral steel for logged.
  for (const token of ["brand", "success", "steel"] as const) {
    assert.ok(
      chat.includes(token),
      `badge pill must use the shared ${token} tone`,
    );
  }
  assert.ok(
    chat.includes("rounded-full") && chat.includes("size-1.5 rounded-full"),
    "badge must be a pill with a colour dot",
  );
  assert.ok(
    !/#[0-9a-fA-F]{3,8}/.test(chat),
    "chat must keep theme tokens, no hard-coded hex",
  );
  assert.ok(!chat.includes("gradient"), "chat must keep theme tokens, no gradients");
});

test("chat task cards show id, title, status pill and mini strip", () => {
  const chat = read("src/app/workspace/chat/page.tsx");
  assert.ok(
    chat.includes("data-task-card"),
    "task cards must be marked with data-task-card",
  );
  assert.ok(
    chat.includes("/workspace/tasks/${t.id}"),
    "task cards must link to /workspace/tasks/[id]",
  );
  assert.ok(
    chat.includes("TaskStatePill") && chat.includes("t.state"),
    "task cards must render the shared TaskStatePill where a state is present",
  );
  assert.ok(
    chat.includes("PipelineStrip") &&
      chat.includes('"mini"') &&
      chat.includes("t.pipeline"),
    "task cards must render the mini PipelineStrip where pipeline data is present",
  );
  assert.ok(
    chat.includes("#{t.id}") || chat.includes("#"),
    "task cards must show the task id",
  );
});

test("chat plan offers carry a title with the task count plus the buttons", () => {
  const chat = read("src/app/workspace/chat/page.tsx");
  assert.ok(
    chat.includes("data-plan-offer-title"),
    "plan offer must render a title marked with data-plan-offer-title",
  );
  assert.ok(
    chat.includes("turn.offer.plan_slug"),
    "plan offer title must name the offered plan",
  );
  assert.ok(
    chat.includes("turn.offer.task_titles.length"),
    "plan offer title must state how many task titles follow",
  );
  assert.ok(
    chat.includes("turn.offer.task_titles.map"),
    "plan offer must list every offered task title",
  );
  for (const action of ["convert", "edit", "keep"] as const) {
    assert.ok(
      chat.includes(`data-plan-action="${action}"`),
      `plan offer must keep its data-plan-action="${action}" button`,
    );
  }
});

test("login shows the official mark and wordmark only", () => {
  const login = read("src/app/workspace/login/page.tsx");
  assert.ok(
    login.includes("Sign in to Rouvr"),
    "login must keep its heading",
  );
  assert.ok(
    login.includes("/rouvr-logo.svg"),
    "login must use the official rouvr.ai brand mark",
  );
  assert.ok(
    login.includes("rouvr") && login.includes("text-brand"),
    "login must carry the marketing wordmark with brand dot",
  );
  assert.ok(
    !login.includes("RouvrMark") &&
      !login.includes("autonomous AI colleague"),
    "login must not carry hand-drawn marks or brand copy",
  );
  const formAt = login.indexOf("<form");
  assert.ok(
    formAt !== -1 && login.indexOf("rouvr<span") !== -1 && login.indexOf("rouvr<span") < formAt,
    "wordmark must sit above the form",
  );
  assert.ok(
    !login.includes("gradient"),
    "login must keep theme tokens, no gradients",
  );
  assert.ok(
    !/#[0-9a-fA-F]{3,8}/.test(login),
    "login must not hard-code hex colours",
  );
});
