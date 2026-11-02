"""Select domain types from an explicitly scoped product and expose missing inputs."""

from __future__ import annotations

from typing import Any, Callable, Literal

from pydantic import Field, ValidationError, model_validator

from .base import Domain, DomainModel, Text, unique
from .registry import CONTRACTS, domain_model


class ProductScope(DomainModel):
    """Agent-authored scope decisions, independent of specification completeness.

    These describe requested work: a motor's presence alone does not request
    wiring design. The selector must respect explicit exclusions in the brief.
    """

    physical_structure: bool = False
    mechanical_motion: bool = False
    wiring: bool = False
    electrical_control: bool = False
    fluid_network: bool = False
    biological_experiment: bool = False


class DomainTask(DomainModel):
    """One selected domain, its private context and independently tracked status."""

    domain: Domain
    reason: Text
    context: dict[str, Any] = Field(default_factory=dict)
    missing_inputs: list[str] = Field(default_factory=list)
    status: Literal["ready", "blocked", "generated", "invalid", "failed"] = "ready"
    errors: list[str] = Field(default_factory=list)


class ProductPlan(DomainModel):
    """The selected subset of IR types, not a collection of workflow stages."""

    schema_version: Literal["1.0"] = "1.0"
    product_id: Text
    brief: Text
    scope: ProductScope
    tasks: list[DomainTask] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_domains(self) -> ProductPlan:
        """A plan selects each domain at most once."""
        unique([task.domain for task in self.tasks], "selected domain")
        return self


def context_errors(domain: Domain, context: dict[str, Any]) -> list[str]:
    """Validate only this domain's context and report missing or malformed inputs."""
    try:
        domain_model(domain, context=True).model_validate(context)
    except ValidationError as exc:
        return [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
    return []


def select_domains(
    product_id: str,
    brief: str,
    *,
    scope: ProductScope | None = None,
    selector: Callable[[str], ProductScope] | None = None,
    contexts: dict[Domain, dict[str, Any]] | None = None,
) -> ProductPlan:
    """Compile an agent's scope into S(P), preserving required but blocked domains.

    Supply an explicit typed scope for deterministic/offline use or a selector
    that reads the product brief and returns ProductScope. Context availability
    never changes selection. No domain is silently inferred from missing inputs.
    """
    if scope is None:
        if selector is None:
            raise ValueError("Supply a ProductScope or an agent selector for the product brief.")
        scope = selector(brief)
    scope = ProductScope.model_validate(scope)
    contexts = contexts or {}
    unknown = set(contexts) - set(CONTRACTS)
    if unknown:
        raise ValueError(f"Unknown context domains: {sorted(unknown)}")
    reasons = {
        "mechanical": (scope.physical_structure or scope.mechanical_motion,
                       "Requested physical structure or mechanical motion requires MechanicalIR."),
        "electrical": (scope.wiring or scope.electrical_control,
                       "Requested wiring or electrical control requires ElectricalIR."),
        "biological": (scope.biological_experiment,
                       "Requested biological experiment requires BiologicalIR."),
        "fluidic": (scope.fluid_network,
                    "Requested fluid network requires FluidicIR."),
    }
    tasks = []
    for domain, (required, reason) in reasons.items():
        if not required:
            continue
        context = contexts.get(domain, {})
        missing = context_errors(domain, context)
        tasks.append(DomainTask(domain=domain, reason=reason, context=context,
                                missing_inputs=missing, status="blocked" if missing else "ready"))
    return ProductPlan(product_id=product_id, brief=brief, scope=scope, tasks=tasks)
