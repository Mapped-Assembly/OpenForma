"""Deterministic ProcessIR topology, recycle detection and conservative balances."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import Field

from forma_core.domains.base import DomainModel, Finding
from .models import ProcessBalanceSpec, ProcessIR, ProcessQuantity


UNIT_TO_BASE: dict[str, Decimal] = {
    "kg/s": Decimal("1"), "g/s": Decimal("0.001"), "kg/h": Decimal(1) / Decimal(3600),
    "mol/s": Decimal("1"), "mol/h": Decimal(1) / Decimal(3600),
    "m3/s": Decimal("1"), "L/s": Decimal("0.001"), "L/min": Decimal(1) / Decimal(60000),
    "W": Decimal("1"), "kW": Decimal("1000"), "1": Decimal("1"),
}


class BalanceResult(DomainModel):
    """Conservation result, pinned to the named balance and its stated inputs."""

    balance_id: str
    status: Literal["pass", "fail", "unknown", "unsupported"]
    residual_base_units: str | None = None
    base_unit: str
    message: str


class ProcessAnalysis(DomainModel):
    """Pure topology/balance report; no runtime state, ledger or solver side effects."""

    recycle_components: list[list[str]] = Field(default_factory=list)
    balance_results: list[BalanceResult] = Field(default_factory=list)


def canonical_quantity(quantity: ProcessQuantity) -> Decimal:
    """Convert one already-validated quantity into its dimension's SI base."""
    return Decimal(str(quantity.value)) * UNIT_TO_BASE[quantity.unit]


def process_recycles(document: ProcessIR) -> list[list[str]]:
    """Return deterministic strongly connected components representing recycle loops."""
    adj = {op.operation_id: set() for op in document.operations}
    for stream in document.streams:
        adj[stream.source.operation_id].add(stream.target.operation_id)
    index = 0
    ids: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    present: set[str] = set()
    result: list[list[str]] = []

    def visit(node: str) -> None:
        """Tarjan traversal; graph cycles are legal process designs."""
        nonlocal index
        ids[node] = low[node] = index
        index += 1
        stack.append(node)
        present.add(node)
        for child in sorted(adj[node]):
            if child not in ids:
                visit(child)
                low[node] = min(low[node], low[child])
            elif child in present:
                low[node] = min(low[node], ids[child])
        if low[node] == ids[node]:
            component: list[str] = []
            while True:
                child = stack.pop()
                present.remove(child)
                component.append(child)
                if child == node:
                    break
            if len(component) > 1 or node in adj[node]:
                result.append(sorted(component))

    for node in sorted(adj):
        if node not in ids:
            visit(node)
    return sorted(result)


def evaluate_balance(document: ProcessIR, spec: ProcessBalanceSpec) -> BalanceResult:
    """Evaluate explicit inlet/outlet and generation/consumption bookkeeping.

    All incident material stream rates and all three auxiliary terms must be
    explicitly supplied. Values in a different basis are unsupported until an
    independently justified conversion is supplied; neither 0 nor density
    is inferred. This is a declared accounting check, not a reaction model.
    """
    base_unit = "kg/s" if spec.quantity_kind == "mass_rate" else "mol/s"
    streams = [stream for stream in document.streams
               if stream.kind == "material" and
               (stream.source.operation_id == spec.operation_id or
                stream.target.operation_id == spec.operation_id)]
    if not streams:
        return BalanceResult(balance_id=spec.balance_id, status="unknown", base_unit=base_unit,
                             message="No incident material streams were declared.")
    if any(stream.quantity is not None and stream.quantity.kind != spec.quantity_kind for stream in streams):
        return BalanceResult(balance_id=spec.balance_id, status="unsupported", base_unit=base_unit,
                             message="Incident material streams use incompatible rate dimensions.")
    if (any(stream.quantity is None for stream in streams)
            or any(term is None for term in (spec.accumulation, spec.generation, spec.consumption))):
        return BalanceResult(balance_id=spec.balance_id, status="unknown", base_unit=base_unit,
                             message="Required rate, accumulation, generation or consumption is unknown.")
    incoming = sum((canonical_quantity(s.quantity) for s in streams
                    if s.target.operation_id == spec.operation_id), Decimal(0))
    outgoing = sum((canonical_quantity(s.quantity) for s in streams
                    if s.source.operation_id == spec.operation_id), Decimal(0))
    assert spec.accumulation and spec.generation and spec.consumption
    residual = (incoming - outgoing - canonical_quantity(spec.accumulation)
                - canonical_quantity(spec.generation) + canonical_quantity(spec.consumption))
    result = "pass" if abs(residual) <= Decimal("1e-9") else "fail"
    return BalanceResult(balance_id=spec.balance_id, status=result, base_unit=base_unit,
                         residual_base_units=str(residual),
                         message="Declared conservation accounting balances." if result == "pass"
                         else "Declared conservation accounting does not balance.")


def analyze_process(document: ProcessIR) -> ProcessAnalysis:
    """Describe legal recycle topology and only explicitly requested balances."""
    return ProcessAnalysis(
        recycle_components=process_recycles(document),
        balance_results=[evaluate_balance(document, item) for item in document.balances],
    )


def validate_process(document: ProcessIR) -> list[Finding]:
    """Keep structural validity independent of unsupported analysis coverage."""
    findings: list[Finding] = []
    for result in analyze_process(document).balance_results:
        if result.status != "pass":
            findings.append(Finding(
                code=f"process_balance_{result.status}", location=result.balance_id,
                severity="error" if result.status == "fail" else "warning",
                message=result.message,
            ))
    return findings
