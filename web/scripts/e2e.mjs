// End-to-end check of the demo story in real Chrome, under the vercel.json CSP.
//   node scripts/e2e.mjs [screenshot_dir]      (expects scripts/serve.mjs on :4317)
import { chromium } from "playwright-core";

const BASE = process.env.E2E_BASE ?? "http://localhost:4317";
const shots = process.argv[2];
const problems = [];
const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1360, height: 900 } });
page.on("console", (m) => {
  if (m.type() === "error") problems.push(`console: ${m.text()}`);
});
page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));

const check = (cond, msg) => {
  if (!cond) problems.push(msg);
  console.log(`${cond ? "ok  " : "FAIL"} ${msg}`);
};
const stamp = async () => (await page.locator(".stamp-word").first().textContent())?.trim();
const shot = async (name) => shots && (await page.screenshot({ path: `${shots}/${name}.png`, fullPage: true }));

for (const path of ["/", "/benchmark/", "/about/", "/runs/checkout-weakened/", "/runs/checkout-legit/"]) {
  const res = await page.goto(BASE + path, { waitUntil: "networkidle" });
  check(res.status() === 200, `${path} returns 200`);
  await shot(path.replaceAll("/", "_") || "home");
}
await page.goto(BASE + "/runs/checkout-weakened/");
check((await stamp()) === "FAILED", "recorded weakened run shows FAILED");
await page.goto(BASE + "/runs/checkout-legit/");
check((await stamp()) === "VERIFIED", "recorded legit run shows VERIFIED");

await page.goto(BASE + "/live/", { waitUntil: "networkidle" });
check((await stamp()) === "FAILED", "precomputed weakened preset shows FAILED before loading Pyodide");
const status = page.getByRole("status");
const t0 = Date.now();
await page.getByRole("button", { name: "Run it live" }).click();
await status.filter({ hasText: /Analyzed in|unavailable/ }).waitFor({ timeout: 120_000 });
const loadText = await status.textContent();
console.log(`     first live run: ${loadText} (wall ${((Date.now() - t0) / 1000).toFixed(1)} s)`);
check(/Analyzed in/.test(loadText), "live analysis loads and runs");
check((await stamp()) === "FAILED", "live weakened result is FAILED");
check((await page.locator(".src-live").count()) > 0, "live result is labeled Live");
await shot("live_weakened");

const area = page.locator("#head");
await area.fill((await area.inputValue()).replace(">= 0", "== 80.00"));
await page.getByRole("button", { name: "Run it live" }).click();
await status.filter({ hasText: /Analyzed in \d+ ms\.$/ }).waitFor({ timeout: 10_000 });
check((await stamp()) === "INCONCLUSIVE", "restoring the assertion gives INCONCLUSIVE (no execution live)");

await area.fill("def test_discount_is_capped():\n    assert f(" + "(".repeat(400) + ")".repeat(400) + ") == 1\n");
await page.getByRole("button", { name: "Run it live" }).click();
await page.waitForTimeout(1500);
check((await page.getByText("Input is over the size or nesting limit").count()) > 0, "pathological nesting becomes an input-limit gap");
check((await stamp()) === "INCONCLUSIVE", "pathological input is INCONCLUSIVE, never a pass");

await page.getByText("The cap is raised to 25% under an approved contract").click();
await page.getByRole("button", { name: "Run it live" }).click();
await status.filter({ hasText: /Analyzed in \d+ ms\.$/ }).waitFor({ timeout: 10_000 });
check((await stamp()) === "INCONCLUSIVE", "legit preset live is INCONCLUSIVE");
check((await page.getByText("Covered by the approved contract change").count()) > 0, "legit finding links the approved contract change");
await shot("live_legit");

const mobile = await browser.newPage({ viewport: { width: 390, height: 844 } });
await mobile.goto(BASE + "/", { waitUntil: "networkidle" });
await mobile.waitForTimeout(1500); // let the stamp animation finish
const overflow = await mobile.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
check(overflow <= 0, `no horizontal scroll at phone width (overflow ${overflow}px)`);
if (shots) await mobile.screenshot({ path: `${shots}/mobile_home.png`, fullPage: true });

await browser.close();
const real = problems.filter((p) => !p.startsWith("ok"));
if (real.length) {
  console.log("\nProblems:\n" + real.join("\n"));
  process.exit(1);
}
console.log("\nall checks passed");
