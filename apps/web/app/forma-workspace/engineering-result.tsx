"use client";

import React from "react";

export type EngineeringResult = {
  format: "forma.engineering-result";
  status: string;
  product: {
    manifest: { product_id: string; artifacts: { artifact_id: string; domain: string; revision: number | string; sha256: string }[] };
    artifacts: Record<string, unknown>;
    plan: { brief: string; tasks: { domain: string; reason: string; status: string; missing_inputs: string[]; errors: string[] }[] } | null;
  };
  validation: unknown;
  evaluations: { input: { domain: string }; results: { status: string; message: string }[] }[];
  assumptions: Record<string, string[]>;
  unresolved: string[];
  provenance: unknown;
};

/** Show independently owned artifacts and scoped checks without requiring a hardware view. */
export function EngineeringResultView({ result, onBack }: { result: EngineeringResult; onBack: () => void }) {
  const download = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "engineering-result.json";
    link.click();
    URL.revokeObjectURL(url);
  };
  return (
    <main className="mx-auto max-w-5xl space-y-6 overflow-y-auto p-6 text-slate-100" aria-label="Engineering result">
      <div className="flex flex-wrap justify-between gap-3">
        <button type="button" onClick={onBack} className="rounded border px-4 py-2">Back to workspace</button>
        <button type="button" onClick={download} className="rounded bg-blue-600 px-4 py-2">Download engineering artifacts</button>
      </div>
      <h1 className="text-2xl font-semibold">Engineering result: {result.status}</h1>
      <p>{result.product.plan?.brief}</p>
      <p className="text-sm text-slate-400">These are design artifacts and scoped checks. They do not establish experimental performance or physical verification.</p>
      <section aria-label="Selected domains" className="space-y-3">
        {result.product.plan?.tasks.map((task) => (
          <article key={task.domain} className="rounded border border-slate-700 p-4">
            <h2 className="font-semibold">{task.domain} · {task.status}</h2>
            <p>{task.reason}</p>
            {[...task.missing_inputs, ...task.errors].map((item, i) => <p key={i} className="text-amber-300">{item}</p>)}
          </article>
        ))}
      </section>
      <section aria-label="Evaluation findings" className="space-y-2">
        <h2 className="text-xl">Evaluation findings</h2>
        {result.evaluations.flatMap((evaluation, i) => evaluation.results.map((finding, j) => (
          <p key={`${i}-${j}`}><strong>{evaluation.input.domain} · {finding.status}</strong>: {finding.message}</p>
        )))}
      </section>
      {result.unresolved.length > 0 && <section><h2 className="text-xl">Unresolved information</h2><ul className="list-disc pl-5">{result.unresolved.map((item, i) => <li key={i}>{item}</li>)}</ul></section>}
      {Object.entries(result.assumptions).map(([domain, items]) => items.length > 0 && <section key={domain}><h2 className="text-xl">{domain} assumptions</h2><ul className="list-disc pl-5">{items.map((item, i) => <li key={i}>{item}</li>)}</ul></section>)}
      {Object.entries(result.product.artifacts).map(([id, artifact]) => (
        <details key={id} className="rounded border border-slate-700 p-3"><summary>{id}</summary><pre className="overflow-x-auto whitespace-pre-wrap text-xs">{JSON.stringify(artifact, null, 2)}</pre></details>
      ))}
      <details><summary>Validation, revisions and provenance</summary><pre className="overflow-x-auto whitespace-pre-wrap text-xs">{JSON.stringify({ validation: result.validation, manifest: result.product.manifest, provenance: result.provenance }, null, 2)}</pre></details>
    </main>
  );
}
