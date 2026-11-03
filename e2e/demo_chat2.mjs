// Demo: genuine end-to-end through the web UI (live model), recorded.
// Usage: E2E_* env + node demo_chat.mjs <tag>
// Logs in, sends ONE natural-language message, waits for either inline task
// cards (plan) or the clarifying question, prints the outcome as JSON.
import { chromium } from "playwright";
import { renameSync } from "fs";

const BASE = process.env.E2E_BASE ?? "http://localhost:13002";
const ADMIN_PW = process.env.E2E_ADMIN_PW ?? "";
const OUT = process.env.E2E_OUT ?? "recordings";
const TAG = process.argv[2] ?? "demo";
const MESSAGE =
  process.argv[3] ??
  "Extend GET /v1/version with a `build` field reporting the short git HEAD sha (fallback dev), identical body on both prefixes, test extended.";

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: 1280, height: 720 },
  recordVideo: { dir: OUT, size: { width: 1280, height: 720 } },
});
const page = await context.newPage();
const t0 = Date.now();
const stamp = (m) => console.log(`[+${((Date.now() - t0) / 1000).toFixed(1)}s] ${m}`);

await page.goto(`${BASE}/workspace/login`);
await page.getByPlaceholder("admin").fill("admin");
await page.getByPlaceholder("••••••••••••").fill(ADMIN_PW);
await page.getByRole("button", { name: "Sign in" }).click();
await page.waitForURL("**/workspace", { timeout: 15000 });
stamp("logged in");

await page.goto(`${BASE}/workspace/chat`);
await page.locator('input[value="demo"]').fill("demo");
await page.locator("textarea").fill(MESSAGE);
await page.getByRole("button", { name: "Send" }).click();

const box = page.locator("div.mt-6.space-y-4");
await box.waitFor({ timeout: 180000 });
const cards = box.locator('a[href^="/workspace/tasks/"]');
const n = await cards.count();
stamp(`turn resolved: ${n > 0 ? "plan" : "question"}`);
if (n > 0) {
  const href = await cards.first().getAttribute("href");
  console.log(JSON.stringify({ outcome: "plan", slice: href.split("/").pop() }));
} else {
  console.log(JSON.stringify({ outcome: "question", text: (await box.innerText()).slice(0, 300) }));
}
await page.waitForTimeout(2000); // let the footage settle on the result
const videoPath = await page.video().path();
await context.close();
await browser.close();
renameSync(videoPath, `${OUT}/${TAG}.webm`);
const secs = ((Date.now() - t0) / 1000).toFixed(1);
console.log(`DONE in ${secs}s`);
