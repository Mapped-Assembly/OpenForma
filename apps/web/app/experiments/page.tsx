"use client";

import { useState } from "react";
import { useFormaAuth } from "@/lib/forma-auth";
import { webConfig } from "@/lib/config/environment";

type Criterion = { criterion_id: string; species_id: string; basis: string; unit: string; method: string; absolute_tolerance: number; assumptions: string[] };
type Plan = { scientific_sha256: string; protocol_step_ids: string[]; request: { objective: string; calculation: { inventory: { species_id: string; value: number; unit: string }[] }; inventory_tolerances: Record<string, number>; criteria: Criterion[]; conditions: { name: string; unit: string; minimum: number; maximum: number }[]; assumptions: string[]; limitations: string[]; process: { steps: { step_id: string; description: string; preconditions: string[] }[] } }; prediction: { status: string; amounts: { species_id: string; formed_mass_g: string | null; formed: string }[] } };
type SavedPlan = { sha256: string; record: { created_at: string; plan: Plan } };
type Trial = Record<string, unknown>;
type Result = { run_id: string; criterion_id: string; status: string; reference: string | null; observed: string | null; signed_residual: string | null; unit: string; message: string; above_bound: boolean };
type SavedReport = { sha256: string; record: { report: { results: Result[]; verification_coverage: number; physical_status: string; runs: Trial[] } } };

/** Phone-friendly access to frozen protocols and manually supplied measurements. */
export default function ExperimentsPage() {
  const auth = useFormaAuth();
  const [draft, setDraft] = useState("");
  const [saved, setSaved] = useState<SavedPlan | null>(null);
  const [report, setReport] = useState<SavedReport | null>(null);
  const [runs, setRuns] = useState<Trial[]>([]);
  const [started, setStarted] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [restore, setRestore] = useState("");
  const [photos, setPhotos] = useState<File[]>([]);
  const plan = saved?.record.plan;

  async function request(path: string, body?: unknown, file?: File): Promise<any> {
    const token = await auth.getToken();
    const response = await fetch(`${webConfig.apiBaseUrl}/experiments${path}`, {
      method: body !== undefined || file ? "POST" : "GET",
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(file ? { "Content-Type": file.type } : body !== undefined ? { "Content-Type": "application/json" } : {}) },
      body: file || (body !== undefined ? JSON.stringify(body) : undefined),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : JSON.stringify(result.detail));
    return result;
  }

  async function act(work: () => Promise<void>) {
    setBusy(true); setError("");
    try { await work(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  function quantity(value: string | null): string {
    if (value === null) return "unknown";
    const parts = value.split("/").map(Number);
    const number = parts.length === 2 ? parts[0] / parts[1] : parts[0];
    return Number.isFinite(number) ? number.toLocaleString(undefined, { maximumSignificantDigits: 7 }) : value;
  }

  function download(value: unknown, name: string) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
    const a = document.createElement("a"); a.href = url; a.download = name; a.click(); URL.revokeObjectURL(url);
  }

  async function submit(form: HTMLFormElement) {
    if (!saved || !plan || !started) return;
    const data = new FormData(form);
    const number = (key: string): number => {
      const raw = String(data.get(key) ?? "").trim();
      if (!raw || !Number.isFinite(Number(raw))) throw new Error(`Enter a number for ${key}.`);
      return Number(raw);
    };
    const optional = (key: string) => String(data.get(key) ?? "").trim() ? number(key) : null;
    const now = new Date().toISOString();
    const sourceId = crypto.randomUUID();
    const attachments = [];
    for (const file of photos) attachments.push({ ...(await request("/evidence", undefined, file)), source_id: sourceId });
    // Upload size is transport metadata, not part of the evidence contract.
    const refs = attachments.map(({ sha256, media_type, source_id }) => ({ sha256, media_type, source_id }));
    const reading = (prefix: string, unit: string, valueKey: string) => ({ value: number(valueKey), unit, instrument: String(data.get(`${prefix}-instrument`)), resolution: number(`${prefix}-resolution`), uncertainty: optional(`${prefix}-uncertainty`) });
    const observations = plan.request.criteria.map(c => ({
      observation_id: crypto.randomUUID(), criterion_id: c.criterion_id, species_id: c.species_id, basis: c.basis,
      readings: [reading(c.criterion_id, c.unit, `${c.criterion_id}-first`), ...(c.method === "difference_proxy" ? [reading(c.criterion_id, c.unit, `${c.criterion_id}-second`)] : [])],
      evidence_ids: [sourceId], assumptions_confirmed: data.get(`${c.criterion_id}-confirmed`) === "on",
    }));
    const actual_inventory = Object.fromEntries(plan.request.calculation.inventory.map(f => [f.species_id, reading(`feed-${f.species_id}`, f.unit, `feed-${f.species_id}-first`)]));
    const conditions = Object.fromEntries(plan.request.conditions.map(c => [c.name, reading(c.name, c.unit, `${c.name}-first`)]));
    const run = { run_id: crypto.randomUUID(), sample_id: String(data.get("sample")), plan_sha256: plan.scientific_sha256,
      kind: "physical", started_at: started, completed_at: now, operator: String(data.get("operator")), actual_inventory, conditions, observations,
      evidence: [{ source_id: sourceId, basis: "measured", title: "Operator experimental record", publisher: String(data.get("operator")), locator: `sample:${String(data.get("sample"))}`, retrieved_at: now, published_at: now, applicability: String(data.get("notes")) }], attachments: refs,
      cost: optional("cost"), currency: String(data.get("currency") || "").trim() || null };
    const nextRuns = [...runs, run];
    const next = await request("/reports", { plan_record_sha256: saved.sha256, runs: nextRuns });
    setReport(next); setRuns(nextRuns); setStarted(null); setPhotos([]); form.reset();
  }

  const inputClass = "block w-full rounded border border-gray-500 bg-transparent p-2 my-1";
  function instrumentFields(prefix: string, unit: string) {
    return <><label>Instrument / method<input required name={`${prefix}-instrument`} className={inputClass} /></label>
      <label>Resolution ({unit})<input required type="number" step="any" min="0.000000001" name={`${prefix}-resolution`} className={inputClass} /></label>
      <label>Absolute uncertainty bound ({unit}, leave blank if unknown)<input type="number" step="any" min="0" name={`${prefix}-uncertainty`} className={inputClass} /></label></>;
  }
  return <main className="mx-auto max-w-3xl p-5 space-y-6">
    <h1 className="text-2xl font-semibold">Physical process verification</h1>
    <p>Freeze a prediction and protocol, perform an independent trial, then record what happened. Calculations and photographs alone do not establish physical success.</p>
    {!auth.hasIdentity && <button onClick={() => auth.openSignIn()}>Sign in to save experiments</button>}
    {error && <p role="alert" className="border border-red-500 p-3">{error}</p>}
    <fieldset disabled={busy || !auth.hasIdentity} className="space-y-3 disabled:opacity-60">
      {!saved && <>
        <button className={inputClass} onClick={() => act(async () => setDraft(JSON.stringify(await request("/tools", { action: "benchmark" }), null, 2)))}>Load vinegar and baking soda benchmark</button>
        <p>Or paste an experiment request prepared from Forma’s domain-generation tools. Review the nominal feeds, concentration basis, protocol and criteria before freezing.</p>
        <label>Experiment request<textarea rows={12} value={draft} onChange={e => setDraft(e.target.value)} className={inputClass} /></label>
        <button className={inputClass} disabled={!draft} onClick={() => act(async () => { const next = await request("/plans", { request: JSON.parse(draft) }); setSaved(next); })}>Freeze original prediction and protocol</button>
        <label>Restore saved plan or report digest<input value={restore} onChange={e => setRestore(e.target.value.trim())} className={inputClass} /></label>
        <div className="flex gap-3">{["plans", "reports"].map(kind => <button key={kind} onClick={() => act(async () => {
          if (!/^[a-f0-9]{64}$/.test(restore)) throw new Error("Enter a saved 64-character digest.");
          const record = await request(`/records/${kind}/${restore}`);
          if (kind === "plans") setSaved({ sha256: restore, record });
          else { const original = await request(`/records/plans/${record.plan_record_sha256}`); setSaved({ sha256: record.plan_record_sha256, record: original }); setReport({ sha256: restore, record }); setRuns(record.report.runs); }
        })}>Restore {kind === "plans" ? "plan" : "report"}</button>)}</div>
      </>}
      {saved && plan && <>
        <h2 className="text-xl">{plan.request.objective}</h2>
        <p>Prediction calculation: {plan.prediction.status}. Physical evidence: {report?.record.report.physical_status || "not run"}.</p>
        <p className="break-all">Saved plan: {saved.sha256}</p>
        <button className={inputClass} onClick={() => download(saved, "experiment-plan.json")}>Download frozen plan (keep for recovery)</button>
        <ul className="list-disc pl-5">{plan.request.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul>
        <h2 className="text-xl">Protocol</h2>
        <ol className="list-decimal pl-6 space-y-3">{plan.protocol_step_ids.map(id => { const s = plan.request.process.steps.find(s => s.step_id === id); return <li key={id}>{s?.description}{s?.preconditions?.map((p,i) => <p key={i}>Prerequisite: {p}</p>)}</li>; })}</ol>
        <h2 className="text-xl">Acceptance criteria</h2>
        {plan.request.criteria.map(c => <p key={c.criterion_id}>{c.species_id}: {c.basis}, {c.method}; absolute tolerance {c.absolute_tolerance} {c.unit}. {c.assumptions.join(" ")}</p>)}
        <details><summary>Predicted quantities and limitations</summary><pre className="whitespace-pre-wrap break-all text-sm">{JSON.stringify(plan.prediction, null, 2)}</pre>{plan.request.limitations.map((l, i) => <p key={i}>{l}</p>)}</details>
        {!started && <button className={inputClass} onClick={() => setStarted(new Date().toISOString())}>Start a new independent physical trial</button>}
        {started && <form className="space-y-4" onSubmit={e => { e.preventDefault(); const form = e.currentTarget; void act(() => submit(form)); }}>
          <p>Started {new Date(started).toLocaleString()}. Keep this page open until saved. No measurements are filled in automatically.</p>
          <label>Operator<input required name="operator" className={inputClass} /></label>
          <label>Unique sample / batch identifier<input required name="sample" className={inputClass} /></label>
          {plan.request.calculation.inventory.map(f => <fieldset className="border p-3" key={f.species_id}><legend>Actual feed: {f.species_id} (nominal {f.value} {f.unit}, tolerance {plan.request.inventory_tolerances[f.species_id] || 0} {f.unit})</legend><p>For a mixture, use the confirmed concentration basis to record this species amount; record the calculation in your notes.</p><label>Actual amount<input required type="number" step="any" name={`feed-${f.species_id}-first`} className={inputClass} /></label>{instrumentFields(`feed-${f.species_id}`, f.unit)}</fieldset>)}
          {plan.request.conditions.map(c => <fieldset className="border p-3" key={c.name}><legend>{c.name} ({c.minimum}–{c.maximum} {c.unit})</legend><label>Observed value<input required type="number" step="any" name={`${c.name}-first`} className={inputClass} /></label>{instrumentFields(c.name, c.unit)}</fieldset>)}
          {plan.request.criteria.map(c => <fieldset className="border p-3" key={c.criterion_id}><legend>{c.species_id} ({c.unit})</legend>
            <label>{c.method === "difference_proxy" ? "Initial reading" : "Observed quantity"}<input required type="number" step="any" name={`${c.criterion_id}-first`} className={inputClass} /></label>
            {c.method === "difference_proxy" && <label>Final reading<input required type="number" step="any" name={`${c.criterion_id}-second`} className={inputClass} /></label>}
            {instrumentFields(c.criterion_id, c.unit)}
            <label><input type="checkbox" name={`${c.criterion_id}-confirmed`} /> I can support the stated measurement assumptions for this trial.</label>
          </fieldset>)}
          <label>Observations, input quantities and deviations<textarea required name="notes" className={inputClass} /></label>
          <label>Photographs or raw evidence (maximum 8 MiB each)<input type="file" multiple accept="image/jpeg,image/png,image/webp,text/plain,application/json" onChange={e => setPhotos(Array.from(e.target.files || []))} className={inputClass} /></label>
          <label>Trial cost (optional)<input type="number" step="any" min="0" name="cost" className={inputClass} /></label>
          <label>Currency (required with cost)<input name="currency" className={inputClass} /></label>
          <button className={inputClass} type="submit">Save observations and compare</button>
        </form>}
        {report && <section className="space-y-3"><h2 className="text-xl">Verification report</h2><p>Physical criteria coverage: {report.record.report.verification_coverage * 100}%</p>
          {report.record.report.results.map(r => <div className="border p-3" key={`${r.run_id}-${r.criterion_id}`}><strong>{r.criterion_id}: {r.status}</strong><p>Reference: {quantity(r.reference)}; observed: {quantity(r.observed)}; discrepancy: {quantity(r.signed_residual)} {r.unit}</p><p>{r.message}</p>{r.above_bound && <p>Above the conditional bound: investigate inputs, proxy biases and measurement error.</p>}</div>)}
          <p className="break-all">Saved report: {report.sha256}</p><button className={inputClass} onClick={() => download(report, "experiment-report.json")}>Download reproducible report</button>
        </section>}
      </>}
    </fieldset>
  </main>;
}
