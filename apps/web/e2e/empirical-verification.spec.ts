import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";

// Browser transport fixture only: no physical experiment is performed.
const request = JSON.parse(readFileSync(path.resolve(__dirname, "../../../forma_core/experiments/vinegar-bicarbonate.json"), "utf8"));
const digest = "a".repeat(64);
const plan = { scientific_sha256: "b".repeat(64), request,
  protocol_step_ids: request.process.steps.map((s: { step_id: string }) => s.step_id),
  prediction: { status: "pass", amounts: [] } };

test.use({ viewport: { width: 390, height: 844 } });
test("phone workflow freezes before entry and submits user supplied readings", async ({ page }) => {
  let submitted: Record<string, any> | null = null;
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers: { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*" } });
    if (url.pathname === "/experiments/tools") return route.fulfill({ json: request });
    if (url.pathname === "/experiments/plans") return route.fulfill({ json: { sha256: digest, record: { created_at: new Date().toISOString(), plan } } });
    if (url.pathname === "/experiments/reports") {
      submitted = route.request().postDataJSON();
      return route.fulfill({ json: { sha256: "c".repeat(64), record: { report: { results: [{ run_id: "synthetic-ui-test", criterion_id: "co2-mass-proxy", status: "unknown", unit: "g", reference: "440095/420033", observed: "1", signed_residual: "-20062/420033", message: "Uncertainty was not supplied.", above_bound: false }], verification_coverage: 0, physical_status: "observations_supplied", runs: submitted!.runs } } } });
    }
    return route.continue();
  });
  await page.goto("/experiments");
  await expect(page.getByRole("button", { name: "Start a new independent physical trial" })).toHaveCount(0);
  await page.getByRole("button", { name: "Load vinegar and baking soda benchmark" }).click();
  await page.getByRole("button", { name: "Freeze original prediction and protocol" }).click();
  await expect(page.getByText("Physical evidence: not run", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Start a new independent physical trial" }).click();
  await page.locator('[name="operator"]').fill("Synthetic browser test");
  await page.locator('[name="sample"]').fill("test-only-1");
  for (const [prefix, value, resolution] of [["feed-bicarbonate", "2", "0.01"], ["feed-acid", "2.5", "0.01"], ["ambient_temperature", "22", "0.1"], ["co2-mass-proxy", "100", "0.01"]]) {
    await page.locator(`[name="${prefix}-first"]`).fill(value);
    await page.locator(`[name="${prefix}-instrument"]`).fill("Synthetic instrument");
    await page.locator(`[name="${prefix}-resolution"]`).fill(resolution);
  }
  await page.locator('[name="co2-mass-proxy-second"]').fill("99");
  await page.locator('[name="notes"]').fill("Synthetic transport test, not actual observations.");
  await page.getByRole("button", { name: "Save observations and compare" }).click();
  await expect(page.getByText("co2-mass-proxy: unknown", { exact: true })).toBeVisible();
  expect(submitted!.plan_record_sha256).toBe(digest);
  expect(submitted!.runs[0].observations[0].readings[0].value).toBe(100);
  expect(submitted!.runs[0].observations[0].readings[0].uncertainty).toBeNull();
  expect(submitted!.runs[0].observations[0].assumptions_confirmed).toBe(false);
  await expect(page.getByRole("button", { name: "Download reproducible report" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("failed persistence does not expose a start-trial control", async ({ page }) => {
  await page.route("**/experiments/tools", route => route.fulfill({ json: request }));
  await page.route("**/experiments/plans", route => route.fulfill({ status: 503, json: { detail: "Record was not saved." } }));
  await page.goto("/experiments");
  await page.getByRole("button", { name: "Load vinegar and baking soda benchmark" }).click();
  await page.getByRole("button", { name: "Freeze original prediction and protocol" }).click();
  await expect(page.locator("main").getByRole("alert")).toHaveText("Record was not saved.");
  await expect(page.getByRole("button", { name: "Start a new independent physical trial" })).toHaveCount(0);
});
