// Step 7: timed web-UI e2e runs, screen-recorded.
// Usage: node run.mjs <run#>  (1=happy path, 2=failure+recovery, 3=question+plan)
// Env: E2E_BASE (web origin), E2E_LEDGER (ledger origin), E2E_TOKEN (stack),
//      E2E_ADMIN_PW, E2E_OUT (recordings dir).
import { chromium } from "playwright";
import { writeFileSync } from "fs";

const BASE = process.env.E2E_BASE ?? "http://localhost:13002";
const LEDGER = process.env.E2E_LEDGER ?? "http://127.0.0.1:18234";
const TOKEN = process.env.E2E_TOKEN ?? "";
const ADMIN_PW = process.env.E2E_ADMIN_PW ?? "";
const OUT = process.env.E2E_OUT ?? "recordings";
const RUN = process.argv[2] ?? "1";

const H = { "x-rouvr-token": TOKEN, "Content-Type": "application/json" };
async function api(method, path, body) {
  const r = await fetch(LEDGER + path, {
    method,
    headers: H,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`${method} ${path} -> ${r.status}: ${await r.text()}`);
  return r.json();
}
const t0 = Date.now();
const stamp = (m) => console.log(`[+${((Date.now() - t0) / 1000).toFixed(1)}s] ${m}`);

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: 1280, height: 720 },
  recordVideo: { dir: OUT, size: { width: 1280, height: 720 } },
});
const page = await context.newPage();

// login
await page.goto(`${BASE}/workspace/login`);
await page.getByPlaceholder("admin").fill("admin");
await page.getByPlaceholder("••••••••••••").fill(ADMIN_PW);
await page.getByRole("button", { name: "Sign in" }).click();
await page.waitForURL("**/workspace", { timeout: 15000 });
stamp("logged in, board visible");

const receipts = { chat: [], transitions: [] };
async function chatTurn(message, tag, expectCards = true) {
  await page.goto(`${BASE}/workspace/chat`);
  await page.locator('input[value="demo"]').fill("demo");
  await page.locator("textarea").fill(message);
  await page.getByRole("button", { name: "Send" }).click();
  if (expectCards) {
    await page.locator('a[href^="/workspace/tasks/"]').first().waitFor({ timeout: 60000 });
  } else {
    await page.getByText("clarifying question", { exact: false, timeout: 60000 });
  }
  const replyText = await page.locator("div.mt-6.space-y-4").last().innerText().catch(() => "(no result box)");
  stamp(`${tag}: reply with cards :: ${replyText.slice(0, 160).replace(/\n/g, " | ")}`);
}

const plan = (title, chores) => JSON.stringify({
  question: null,
  slice: { title, intent: `e2e run ${RUN}: ${title}`, acceptance_criteria: [`run ${RUN} passes`] },
  tasks: chores.map((c) => ({ title: c, intent: `do ${c} in run ${RUN}` })),
});

if (RUN === "1" || RUN === "2") {
  await chatTurn(plan(`E2E slice R${RUN}`, [`Chore A R${RUN}`, `Chore B R${RUN}`]), "plan turn");
  const cards = await page.locator('a[href^="/workspace/tasks/"]').all();
  const href = await cards[0].getAttribute("href"); // cards[0] is the slice
  const tid = href.split("/").pop();
  stamp(`slice task id ${tid}`);

  // worker flow via API (headless worker role): claim -> attempt -> verdict.
  // Run 2 injects the failure: move to needs_review BEFORE any verify, so
  // the first UI approve hits the guard (409); the verdict lands after.
  const claim = await api("POST", `/v1/tasks/${tid}/assignments/claim`,
    { key: `e2e-${RUN}-claim`, principal: "e2e-worker", attempt_no: 1 });
  const epoch = claim.lease_epoch;
  const att = await api("POST", `/v1/tasks/${tid}/attempts`,
    { key: `e2e-${RUN}-att`, attempt_no: 1, lease_epoch: epoch });
  const attemptId = att.attempt.id; // claim_assignment already moved open -> claimed
  await api("POST", `/v1/tasks/${tid}/transition`, { to: "needs_review", idempotency_key: `e2e-${RUN}-r` });
  if (RUN === "1") {
    await api("POST", `/v1/tasks/${tid}/results`,
      { key: `e2e-${RUN}-res`, attempt_id: attemptId, lease_epoch: epoch, verdict: "pass", result: {} });
  }
  stamp("task needs_review (run 2 has no verify yet: failure armed)");

  // task page: live events + approve
  await page.goto(`${BASE}/workspace/tasks/${tid}`);
  await page.getByRole("button", { name: "Approve → done" }).click();
  await page.waitForTimeout(1000);
  if (RUN === "2") {
    await page.getByText("passing verify", { timeout: 15000 });
    stamp("FAILURE VISIBLE: approve refused without verify (409)");
    await page.waitForTimeout(1500); // let the footage show the banner
    await api("POST", `/v1/tasks/${tid}/results`,
      { key: `e2e-${RUN}-res`, attempt_id: attemptId, lease_epoch: epoch, verdict: "pass", result: {} });
    stamp("recovery: passing verify recorded");
    await page.getByRole("button", { name: "Approve → done" }).click();
  }
  await page.getByText("done", { exact: true, timeout: 15000 });
  stamp("approved -> done");
  const got = await api("GET", `/v1/tasks/${tid}`);
  receipts.chat = got.receipts.filter((r) => r.action === "chat:create").map((r) => r.id);
  receipts.transitions = got.receipts.filter((r) => r.action.startsWith("transition:")).map((r) => `${r.id}:${r.action}`);
  stamp(`receipts chat=${JSON.stringify(receipts.chat)} transitions=${JSON.stringify(receipts.transitions)}`);
}

if (RUN === "3") {
  // question turn creates nothing (restraint), then a plan turn creates a slice
  const q = JSON.stringify({ question: "Which project should this go in?", slice: null, tasks: [] });
  await chatTurn(q, "question turn", false);
  stamp("question shown, nothing created");
  await chatTurn(plan("E2E slice R3", ["Chore A R3"]), "plan turn");
  const cards3 = await page.locator('a[href^="/workspace/tasks/"]').all();
  const href3 = await cards3[0].getAttribute("href");
  const tid = href3.split("/").pop();
  const got = await api("GET", `/v1/tasks/${tid}`);
  receipts.chat = got.receipts.filter((r) => r.action === "chat:create").map((r) => r.id);
  stamp(`slice ${tid} receipts=${JSON.stringify(receipts.chat)}`);
}

await context.close();
await browser.close();
const secs = ((Date.now() - t0) / 1000).toFixed(1);
console.log(`RUN ${RUN} DONE in ${secs}s`);
writeFileSync(`${OUT}/run${RUN}.json`, JSON.stringify({ run: RUN, seconds: Number(secs), receipts }, null, 1));
