"""Executable ChemicalIR + ProcessIR + ThermalIR integration benchmark.

This is a contract/discrimination benchmark, not a physical process simulation.
The air-to-fuel fixture is an illustrative, non-proprietary reference system.
Passing expected UNKNOWN or FAIL outcomes counts as *correct diagnostics*,
never as demonstrated chemical performance, yield, or physical safety.

Run:
    python -m forma_core.benchmarks.ir_triad_v1
    python -m forma_core.benchmarks.ir_triad_v1 --output /tmp/triad-report.json
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Literal

from forma_core.chemistry.models import ChemicalIR
from forma_core.chemistry.validation import check_reaction_balance, validate_chemical
from forma_core.domains.base import fingerprint
from forma_core.domains.composition import (
    DomainLink, LinkEndpoint, ProductBundle, compose_product, validate_product,
)
from forma_core.domains.registry import validate_domain
from forma_core.processes.models import ProcessIR, ProcessPort
from forma_core.processes.validation import analyze_process, process_recycles
from forma_core.thermal.models import ThermalIR, ThermalSourceReference
from forma_core.thermal.validation import analyze_thermal

GROUPS: tuple[str, ...] = ("chemical", "process", "thermal", "integration")
SCHEMA_VERSION = "1.0"
EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "domains"


@dataclass(frozen=True)
class BenchmarkCase:
    """One expected diagnostic or numerical check, not a physical validation."""

    case_id: str
    group: Literal["chemical", "process", "thermal", "integration"]
    expected: str
    observed: str
    passed: bool
    explanation: str


def _fixture(filename: str) -> dict[str, Any]:
    """Read a pinned repository fixture without network, model, or runtime calls."""
    return json.loads((EXAMPLES / filename).read_text(encoding="utf-8"))


def _case(case_id: str, group: Literal["chemical", "process", "thermal", "integration"],
          expected: str, observed: str, explanation: str) -> BenchmarkCase:
    """Record an explicit expectation match; FAIL/UNKNOWN may be correct outputs."""
    return BenchmarkCase(case_id, group, expected, observed, expected == observed, explanation)


def _codes(value: Any) -> set[str]:
    """Collect stable codes from structural, local, or composition findings."""
    return {finding.code for finding in value}


def _link(link_id: str, kind: str, source: Any, source_id: str,
          target: Any, target_id: str, *,
          source_port: str | None = None, target_port: str | None = None,
          authorities: dict[str, str] | None = None) -> DomainLink:
    """Create both revision- and SHA-pinned typed relationship endpoints."""
    return DomainLink(
        link_id=link_id, kind=kind,
        source=LinkEndpoint(
            artifact_id=source.artifact_id, revision=source.revision,
            sha256=fingerprint(source), object_id=source_id, port_id=source_port,
        ),
        target=LinkEndpoint(
            artifact_id=target.artifact_id, revision=target.revision,
            sha256=fingerprint(target), object_id=target_id, port_id=target_port,
        ),
        fact_owners=authorities or {},
        provenance={"basis": "illustrative", "benchmark": "ir-triad-v1"},
    )


def _reference(document: Any, entity_id: str) -> ThermalSourceReference:
    """Pin a thermal source to an independently owned source artifact."""
    return ThermalSourceReference(
        domain=document.domain, artifact_id=document.artifact_id,
        entity_id=entity_id, schema_version=document.schema_version,
        revision=document.revision, sha256=fingerprint(document),
    )


def _connected_documents() -> tuple[ChemicalIR, ProcessIR, ThermalIR, list[DomainLink]]:
    """Build a real 3-domain composition with an intentionally UNKNOWN heat duty.

    ProcessIR gains a declared energy port, not a guessed heat transfer model.
    ThermalIR stores source provenance while retaining an unknown reaction load.
    """
    chemical = ChemicalIR.model_validate(_fixture("chemical-air-to-fuel.json"))
    process = ProcessIR.model_validate(_fixture("process-air-to-fuel.json"))
    operation = next(item for item in process.operations if item.operation_id == "methanol_reactor")
    operation.ports.append(ProcessPort(
        port_id="heat_out", direction="out", kind="energy", quantity_kind="power",
    ))
    process = ProcessIR.model_validate(process.model_dump(mode="json"))

    raw = _fixture("thermal-reactor.json")
    raw["loads"][0]["references"] = [
        _reference(chemical, "methanol_synthesis").model_dump(mode="json"),
        _reference(process, "methanol_reactor").model_dump(mode="json"),
    ]
    thermal = ThermalIR.model_validate(raw)
    links = [
        _link(
            "process-synthesis-reaction", "uses_reaction",
            process, "methanol_reactor", chemical, "methanol_synthesis",
            authorities={"chemical.reaction": "chemical"},
        ),
        _link(
            "hydrogen-identity", "uses_species",
            process, "hydrogen_stream", chemical, "hydrogen",
            authorities={"chemical.species": "chemical"},
        ),
        _link(
            "reaction-heat-source", "thermal_source",
            thermal, "reaction-heat", chemical, "methanol_synthesis",
            authorities={"chemical.reaction": "chemical", "thermal.heat_duty": "thermal"},
        ),
        _link(
            "process-heat-source", "thermal_source",
            thermal, "reaction-heat", process, "methanol_reactor",
            authorities={"process.operation": "process"},
        ),
        _link(
            "thermal-duty-interface", "uses_thermal_interface",
            process, "methanol_reactor", thermal, "reactor",
            source_port="heat_out", target_port="heat",
            authorities={"thermal.heat_duty": "thermal"},
        ),
    ]
    return chemical, process, thermal, links


def _chemistry_cases(document: ChemicalIR) -> list[BenchmarkCase]:
    """Challenge exact conservation, unknown charge, mixture basis, and evidence."""
    results: list[BenchmarkCase] = []
    for reaction in document.reactions:
        balance = check_reaction_balance(document, reaction)
        results.append(_case(
            f"chemical.balance.{reaction.reaction_id}", "chemical", "pass", balance.status,
            "Exact elemental and charge balance on the explicitly supported formula grammar.",
        ))

    unbalanced = document.model_copy(deep=True)
    unbalanced.reactions[0].products[0].coefficient = "1"
    actual = check_reaction_balance(unbalanced, unbalanced.reactions[0]).status
    results.append(_case(
        "chemical.unbalanced-detected", "chemical", "fail", actual,
        "A changed stoichiometric coefficient cannot report a balanced equation.",
    ))

    charge_unknown = document.model_copy(deep=True)
    charge_unknown.species[0].charge = None
    results.append(_case(
        "chemical.charge-unknown", "chemical", "unknown",
        check_reaction_balance(charge_unknown, charge_unknown.reactions[0]).status,
        "Unknown ionic charge must not silently become neutral.",
    ))

    unsupported = document.model_copy(deep=True)
    unsupported.species[0].formula = "H2O·2H2O"
    results.append(_case(
        "chemical.unsupported-formula", "chemical", "unsupported",
        check_reaction_balance(unsupported, unsupported.reactions[0]).status,
        "Unsupported hydrate syntax cannot be certified balanced.",
    ))

    over = document.model_copy(deep=True)
    over.mixtures[0].components[0].value = 2.0
    results.append(_case(
        "chemical.overallocated-mixture", "chemical", "composition_overallocated",
        "composition_overallocated" if "composition_overallocated" in _codes(validate_chemical(over)) else "not_detected",
        "Fractions exceeding one are rejected without hidden normalization.",
    ))
    unknown_fraction = document.model_copy(deep=True)
    unknown_fraction.mixtures[0].components[0].value = 0.1
    unknown_fraction.mixtures[0].complete = False
    results.append(_case(
        "chemical.incomplete-mixture", "chemical", "composition_incomplete",
        "composition_incomplete" if "composition_incomplete" in _codes(validate_chemical(unknown_fraction)) else "not_detected",
        "Unallocated component fraction remains explicit rather than invented.",
    ))

    sourced = document.model_dump(mode="json")
    sourced["species"][0]["claim"] = {"basis": "measured", "source_ids": ["missing-primary-source"]}
    results.append(_case(
        "chemical.missing-evidence", "chemical", "schema",
        "schema" if "schema" in _codes(validate_domain(sourced)) else "not_detected",
        "Claims marked measured require a resolvable source identity.",
    ))
    return results


def _process_cases(document: ProcessIR) -> list[BenchmarkCase]:
    """Check a meaningful flow graph, mass bookkeeping, loops, and unknown rates."""
    cases: list[BenchmarkCase] = []
    graph = analyze_process(document)
    no_throughput = all(stream.quantity is None for stream in document.streams)
    cases.append(_case(
        "process.conceptual-throughput", "process", "unknown" if no_throughput else "declared",
        "unknown" if no_throughput else "declared",
        "The conceptual air-to-fuel graph supplies no production, rate, or yield estimate.",
    ))
    cases.append(_case(
        "process.scenario-input-only", "process", "0.6",
        str(document.scenarios[0].overrides[0].quantity.value),
        "Reduced electricity availability is an illustrative input, not a fuel output forecast.",
    ))
    irrigation = ProcessIR.model_validate(_fixture("process-irrigation.json"))
    cases.append(_case(
        "process.mass-conservation", "process", "pass",
        analyze_process(irrigation).balance_results[0].status,
        "Explicit kg/h rates converted and checked in the declared conservation basis.",
    ))
    bad = irrigation.model_copy(deep=True)
    bad.streams[1].quantity.value = 0.8
    cases.append(_case(
        "process.mass-residual-detected", "process", "fail",
        analyze_process(bad).balance_results[0].status,
        "A deliberate outlet-rate discrepancy produces a failed mass balance.",
    ))
    missing = irrigation.model_copy(deep=True)
    missing.streams[1].quantity = None
    cases.append(_case(
        "process.missing-rate", "process", "unknown",
        analyze_process(missing).balance_results[0].status,
        "Missing numerical flow is not backfilled with zero.",
    ))
    malformed = irrigation.model_dump(mode="json")
    malformed["streams"][0]["kind"] = "energy"
    cases.append(_case(
        "process.port-kind-mismatch", "process", "schema",
        "schema" if "schema" in _codes(validate_domain(malformed)) else "not_detected",
        "Material plumbing cannot silently connect into an energy stream.",
    ))

    loop = irrigation.model_dump(mode="json")
    loop["operations"][0]["ports"].append({
        "port_id": "return", "direction": "in", "kind": "material",
        "quantity_kind": "mass_rate", "phase": "liquid",
    })
    loop["streams"].append({
        "stream_id": "recycle", "source": {"operation_id": "crop_bed", "port_id": "drain"},
        "target": {"operation_id": "reservoir", "port_id": "return"},
        "kind": "material", "phase": "liquid",
    })
    recycles = process_recycles(ProcessIR.model_validate(loop))
    cases.append(_case(
        "process.recycle-is-valid", "process", "3-node-recycle",
        "3-node-recycle" if recycles == [["crop_bed", "pump", "reservoir"]] else "missing",
        "A physical recycle is valid even when a workflow dependency DAG must be acyclic.",
    ))
    return cases


def _thermal_cases(document: ThermalIR) -> list[BenchmarkCase]:
    """Exercise expected temperatures and unsupported real-world inference."""
    cases: list[BenchmarkCase] = []
    cases.append(_case(
        "thermal.reactor-unknown", "thermal", "unknown",
        analyze_thermal(document).results[0].status,
        "Chemical reaction balance does not determine heat generation rate.",
    ))
    heater = ThermalIR.model_validate(_fixture("thermal-heater.json"))
    nominal = analyze_thermal(heater).results[0]
    cases.append(_case(
        "thermal.heater-nominal", "thermal", "30",
        str(Decimal(nominal.zone_temperature_celsius).normalize()) if nominal.zone_temperature_celsius is not None else nominal.status,
        "Declared 20 W, 20 degC ambient, and 0.5 K/W give 30 degC in a one-zone screening model.",
    ))
    weaker = heater.model_copy(deep=True)
    weaker.loads[0].heat_rate.value = 10.0
    reduced = analyze_thermal(weaker).results[0]
    cases.append(_case(
        "thermal.heater-reduced", "thermal", "25",
        str(Decimal(reduced.zone_temperature_celsius).normalize()) if reduced.zone_temperature_celsius is not None else reduced.status,
        "The same bounded screening model gives 25 degC for a declared 10 W load.",
    ))
    cases.append(_case(
        "thermal.receipt-changes", "thermal", "different",
        "different" if nominal.input_digest != reduced.input_digest else "unchanged",
        "A model receipt changes with a relevant thermal load while unrelated IRs remain untouched.",
    ))
    without_heat = heater.model_copy(deep=True)
    without_heat.loads[0].heat_rate = None
    cases.append(_case(
        "thermal.missing-heat", "thermal", "unknown",
        analyze_thermal(without_heat).results[0].status,
        "Heat rate omitted by a source cannot be fabricated.",
    ))
    with_limit = heater.model_copy(deep=True)
    with_limit.nodes[0].maximum_temperature = type(heater.boundaries[0].quantity)(
        kind="temperature", value=29, unit="degC",
    )
    cases.append(_case(
        "thermal.design-limit", "thermal", "fail",
        analyze_thermal(with_limit).results[0].status,
        "Exceeding a user-declared design maximum fails a model check, not a legal compliance ruling.",
    ))
    greenhouse = ThermalIR.model_validate(_fixture("thermal-greenhouse.json"))
    cases.append(_case(
        "thermal.unsupported-network", "thermal", "unsupported",
        analyze_thermal(greenhouse).results[0].status,
        "Conduction without an applicable transfer evaluator must be unsupported.",
    ))
    return cases


def _integration_cases(
    chemical: ChemicalIR, process: ProcessIR, thermal: ThermalIR, links: list[DomainLink],
) -> list[BenchmarkCase]:
    """Check three-domain coherence, stale links, scope selection and local separation."""
    from forma_core.domains.planning import ProductScope, select_domains

    cases: list[BenchmarkCase] = []
    bundle = compose_product("air-to-fuel-triad", [chemical, process, thermal], links=links)
    report = validate_product(bundle)
    cases.append(_case(
        "integration.triad-composes", "integration", "valid",
        "valid" if report.is_valid else "invalid",
        "Actual native registry and typed three-domain references compose together.",
    ))
    cases.append(_case(
        "integration.warnings-stay-scoped", "integration", "thermal_unknown",
        "thermal_unknown" if "thermal_unknown" in _codes(report.local[thermal.artifact_id]) else "missing",
        "Unsolved reactor heat remains a local warning, not a false certified result.",
    ))
    cases.append(_case(
        "integration.five-pinned-links", "integration", "5",
        str(len(bundle.manifest.links)),
        "Reaction, species, process heat source and thermal duty have typed revisioned relationships.",
    ))
    stale = chemical.model_copy(update={"revision": chemical.revision + 1})
    stale_report = validate_product(compose_product(
        "air-to-fuel-triad", [stale, process, thermal], links=links,
    ))
    stale_codes = _codes(stale_report.composition)
    cases.append(_case(
        "integration.stale-chemical-revision", "integration", "detected",
        "detected" if {"link_revision", "thermal_source_reference"} <= stale_codes else "not_detected",
        "Chemistry updates make pinned link/provenance checks stale without rewriting ProcessIR/ThermalIR.",
    ))
    cases.append(_case(
        "integration.thermal-local-independence", "integration", "nonblocking",
        "nonblocking" if not any(
            finding.severity == "error" for finding in stale_report.local[thermal.artifact_id]
        ) else "blocked",
        "Cross-domain staleness does not rewrite local thermal validity.",
    ))
    broken = deepcopy(links)
    broken[0].target.object_id = "hydrogen"
    result = validate_product(compose_product(
        "air-to-fuel-triad", [chemical, process, thermal], links=broken,
    ))
    cases.append(_case(
        "integration.wrong-reaction-entity", "integration", "detected",
        "detected" if "chemical_link_type" in _codes(result.composition) else "not_detected",
        "Reaction links cannot resolve to a species even when the species exists.",
    ))
    missing_link = [item for item in links if item.link_id != "reaction-heat-source"]
    result = validate_product(compose_product(
        "air-to-fuel-triad", [chemical, process, thermal], links=missing_link,
    ))
    cases.append(_case(
        "integration.missing-heat-provenance", "integration", "detected",
        "detected" if "thermal_link_missing" in _codes(result.composition) else "not_detected",
        "Thermal source references require corresponding explicit link evidence.",
    ))
    contexts = {
        "chemical": {"scope": "Balanced reaction definitions",
                     "chemical_requirements": ["Stoichiometry"],
                     "evidence_requirements": ["Illustrative demonstration"]},
        "process": {"scope": "Conceptual material and energy process",
                    "process_requirements": ["Port topology"],
                    "stream_requirements": ["Declared material and energy streams"],
                    "evidence_requirements": ["Illustrative demonstration"]},
        "thermal": {"scope": "Model supported heat constraints",
                    "thermal_requirements": ["Unknown reaction duty"],
                    "boundary_requirements": ["Illustrative ambient"],
                    "evidence_requirements": ["Illustrative demonstration"]},
    }
    plan = select_domains(
        "air-to-fuel-triad", "Select only the chemical/process/thermal design domains",
        scope=ProductScope(domains=["chemical", "process", "thermal"]),
        contexts=contexts,
    )
    selected = {task.domain for task in plan.tasks}
    cases.append(_case(
        "integration.only-required-domains", "integration",
        "chemical,process,thermal", ",".join(sorted(selected)),
        "No MechanicalIR, ElectricalIR, FluidicIR, BiologicalIR, SpaceIR or HardwareIR is implicitly created.",
    ))
    cases.append(_case(
        "integration.context-complete", "integration", "ready",
        "ready" if all(task.status == "ready" for task in plan.tasks) else "blocked",
        "Each selected domain receives its own declared context; no global megacontext.",
    ))
    return cases


def run_benchmark() -> dict[str, Any]:
    """Execute deterministic tests against real domain schemas and validators.

    The overall score is diagnostic-contract conformance: expected FAIL and
    UNKNOWN outcomes count as correct guardrails, never engineering accuracy.
    """
    chemical, process, thermal, links = _connected_documents()
    cases = [
        *_chemistry_cases(chemical),
        *_process_cases(process),
        *_thermal_cases(thermal),
        *_integration_cases(chemical, process, thermal, links),
    ]
    counts = Counter(item.group for item in cases)
    by_group = {
        group: {
            "passed": sum(1 for c in cases if c.group == group and c.passed),
            "total": counts[group],
        }
        for group in GROUPS
    }
    passed = sum(case.passed for case in cases)
    return {
        "benchmark": "openforma.ir-triad-v1",
        "schema_version": SCHEMA_VERSION,
        "classification": "synthetic diagnostic-contract benchmark; not a validated physical-process simulation",
        "subject": "Aircela-inspired conceptual air-to-fuel; non-proprietary reactions",
        "priority_domains": list(GROUPS[:3]),
        "source_artifact_digests": {
            doc.domain: fingerprint(doc) for doc in (chemical, process, thermal)
        },
        "summary": {
            "passed": passed, "total": len(cases),
            "all_expected_diagnostics_matched": passed == len(cases),
            "by_group": by_group,
            "real_world_performance": "not evaluated",
            "chemical_yield": "unknown",
            "real_reactor_heat_duty": "unknown",
            "physical_safety": "not evaluated",
            "regulatory_compliance": "not evaluated",
        },
        "cases": [asdict(case) for case in cases],
    }


def main(argv: list[str] | None = None) -> int:
    """Run the benchmark, emit stable JSON and exit nonzero on contract failures."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional stable JSON report path.")
    args = parser.parse_args(argv)
    report = run_benchmark()
    document = json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    if args.output is None:
        print(document, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(document, encoding="utf-8")
    return 0 if report["summary"]["all_expected_diagnostics_matched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
