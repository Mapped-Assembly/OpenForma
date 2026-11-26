import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";

// Synthetic transport fixtures only; never physical evidence.
const cases = ["water-mixing", "vinegar-bicarbonate-declarative"].map(id => ({ id,
  request: JSON.parse(readFileSync(path.resolve(__dirname, `../../../forma_core/experiments/${id}.json`), "utf8")),
}));
const digest = "a".repeat(64);
function declaredPlan(request: any) {
  return { scientific_sha256: "b".repeat(64), request,
    protocol_step_ids: request.process.steps.map((s: { step_id: string }) => s.step_id),
    input_requirements: request.required_inputs.map((input: any) => {
      let quantity = input.artifact_id ? request.artifacts.find((a: any) => a.reference.artifact_id === input.artifact_id).artifact : request.evaluations.find((e: any) => e.evaluation_id === input.evaluation_id).parameters;
      for (const key of input.path) quantity = quantity[key];
      return { ...input, value: String(quantity.value) };
    }), predictions: [{ status: "pass" }] };
}

test.use({ viewport: { width: 390, height: 844 } });
for (const fixture of cases) {
  test(`declarative phone workflow: ${fixture.id}`, async ({ page }) => {
    const plan = declaredPlan(fixture.request);
    let submitted: Record<string, any> | null = null;
    await page.route("**/*", async route => {
      const url = new URL(route.request().url());
      if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers: { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*" } });
      if (url.pathname === "/experiments/tools") {
        if (route.request().postDataJSON().action === "catalog") return route.fulfill({ json: { benchmarks: cases.map(c => ({ benchmark_id: c.id, objective: c.request.objective, declarative: true })) } });
        return route.fulfill({ json: fixture.request });
      }
      if (url.pathname === "/experiments/plans") {
        expect(route.request().postDataJSON().request).not.toHaveProperty("chemical");
        return route.fulfill({ json: { sha256: digest, record: { created_at: new Date().toISOString(), plan } } });
      }
      if (url.pathname === "/experiments/reports") {
        submitted = route.request().postDataJSON();
        return route.fulfill({ json: { sha256: "c".repeat(64), record: { report: { results: fixture.request.criteria.map((c: any) => ({ run_id: "synthetic-ui-test", criterion_id: c.criterion_id, status: "unknown", unit: c.unit, reference: null, observed: null, signed_residual: null, message: "Uncertainty was not supplied.", above_bound: false })), verification_coverage: 0, physical_status: "observations_supplied", runs: submitted!.runs } } } });
      }
      return route.continue();
    });
    await page.goto("/experiments");
    await expect(page.getByRole("button", { name: "Start a new independent physical trial" })).toHaveCount(0);
    await page.getByRole("button", { name: "Browse case studies" }).click();
    await page.getByRole("button", { name: fixture.request.objective, exact: true }).click();
    await page.getByRole("button", { name: "Freeze original prediction and protocol" }).click();
    await expect(page.getByText("Physical evidence: not run", { exact: false })).toBeVisible();
    await page.getByRole("button", { name: "Start a new independent physical trial" }).click();
    await page.locator('[name="operator"]').fill("Synthetic browser test");
    await page.locator('[name="sample"]').fill("test-only-1");
    const fields = [...plan.input_requirements.map((i: any) => [`input-${i.input_id}`, i.value]),
      ...fixture.request.conditions.map((c: any) => [`condition-${c.name}`, String((c.minimum + c.maximum) / 2)]),
      ...fixture.request.criteria.map((c: any) => [`criterion-${c.criterion_id}`, "30"])];
    for (const [prefix, value] of fields) {
      await page.locator(`[name="${prefix}-first"]`).fill(value);
      await page.locator(`[name="${prefix}-instrument"]`).fill("Synthetic instrument");
      await page.locator(`[name="${prefix}-resolution"]`).fill("0.01");
    }
    for (const c of fixture.request.criteria) if (c.method === "difference_proxy") await page.locator(`[name="criterion-${c.criterion_id}-second"]`).fill("29");
    await page.locator('[name="notes"]').fill("Synthetic transport test, not actual observations.");
    await page.getByRole("button", { name: "Save observations and compare" }).click();
    await expect(page.getByText(`${fixture.request.criteria[0].criterion_id}: unknown`, { exact: true })).toBeVisible();
    expect(submitted!.plan_record_sha256).toBe(digest);
    expect(submitted!.runs[0].observations[0].target).toEqual(fixture.request.criteria[0].target);
    expect(submitted!.runs[0].observations[0].readings[0].uncertainty).toBeNull();
    expect(submitted!.runs[0].inputs).toHaveProperty(plan.input_requirements[0].input_id);
    expect(submitted!.runs[0]).not.toHaveProperty("actual_inventory");
    await expect(page.getByRole("button", { name: "Download reproducible report" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}

test("failed persistence does not expose a start-trial control", async ({ page }) => {
  await page.route("**/experiments/plans", route => route.fulfill({ status: 503, json: { detail: "Record was not saved." } }));
  await page.goto("/experiments");
  await page.getByLabel("Experiment request").fill(JSON.stringify(cases[0].request));
  await page.getByRole("button", { name: "Freeze original prediction and protocol" }).click();
  await expect(page.locator("main").getByRole("alert")).toHaveText("Record was not saved.");
  await expect(page.getByRole("button", { name: "Start a new independent physical trial" })).toHaveCount(0);
});
