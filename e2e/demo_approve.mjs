// Demo 1: owner approves genuine coder work in the web UI, screen-recorded.
// Approves ledger tasks 2,3,4 (slice #1) as admin. Requires contracts +
// passing results already recorded (approve guard enforces this).
// Env: E2E_BASE, E2E_LEDGER, E2E_TOKEN, E2E_ADMIN_PW, E2E_OUT.
import { chromium } from "playwright";
import { writeFileSync, renameSync } from "fs";

const BASE = process.env.E2E_BASE ?? "http://localhost:13002";
const LEDGER = process.env.E2E_LEDGER ?? "http://127.0.0.1:18234";
const TOKEN = process.env.E2E_TOKEN ?? "";
const ADMIN_PW = process.env.E2E_ADMIN_PW ?? "";
const OUT = process.env.E2E_OUT ?? "recordings";
const TIDS = (process.env.E2E_TIDS ?? "2,3,4").split(",").map(Number);

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

await page.goto(`${BASE}/workspace/login`);
await page.getByPlaceholder("admin").fill("admin");
await page.getByPlaceholder("••••••••••••").fill(ADMIN_PW);
await page.getByRole("button", { name: "Sign in" }).click();
await page.waitForURL("**/workspace", { timeout: 15000 });
stamp("owner logged in");

const receipts = {};
for (const tid of TIDS) {
  await page.goto(`${BASE}/workspace/tasks/${tid}`);
  await page.getByRole("button", { name: "Approve → done" }).click();
  await page.getByText("done", { exact: true, timeout: 15000 });
  const got = await api("GET", `/v1/tasks/${tid}`);
  receipts[tid] = {
    state: got.state,
    approve: got.receipts.filter((r) => r.action === "transition:done").map((r) => r.id),
  };
  stamp(`task ${tid} -> ${got.state} receipts=${JSON.stringify(receipts[tid].approve)}`);
}

const videoPath = await page.video().path();
await context.close();
await browser.close();
renameSync(videoPath, `${OUT}/demo-approve1.webm`);
const secs = ((Date.now() - t0) / 1000).toFixed(1);
console.log(`APPROVE DONE in ${secs}s`);
writeFileSync(`${OUT}/demo-approve1.json`, JSON.stringify({ seconds: Number(secs), receipts }, null, 1));
