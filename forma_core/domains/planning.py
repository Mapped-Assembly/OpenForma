"""Select domain types from an explicitly scoped product and expose missing inputs."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Literal

from pydantic import Field, StrictBool, model_validator

from .base import Domain, DomainModel, Text, unique
from .providers import ContextRequirement, DomainDependency, DomainRegistry, ProviderMetadata
from .registry import get_registry


class ProductScope(DomainModel):
    """Agent-authored scope decisions, independent of specification completeness.

    These describe requested work: a motor's presence alone does not request
    wiring design. The selector must respect explicit exclusions in the brief.
    """

    physical_structure: bool = Field(False, description="Design physical geometry, parts, mounting or an assembly.")
    mechanical_motion: bool = Field(False, description="Design moving mechanical parts or motion constraints.")
    wiring: bool = Field(False, description="Design electrical connections, circuits or wiring.")
    electrical_control: bool = Field(False, description="Include electrically controlled equipment and its electrical interfaces.")
    fluid_network: bool = Field(False, description="Design fluid routing, channels, piping, flow or pressure constraints.")
    biological_experiment: bool = Field(False, description="Include biological compartments, experiments, exposures or measurements.")
    domains: list[Domain] = Field(default_factory=list, description="Additional explicitly requested domains from the advertised registry, including external domains.")


class CompleteProductScope(ProductScope):
    """An agent must explicitly decide every capability using JSON booleans."""

    physical_structure: StrictBool = Field(description=ProductScope.model_fields["physical_structure"].description)
    mechanical_motion: StrictBool = Field(description=ProductScope.model_fields["mechanical_motion"].description)
    wiring: StrictBool = Field(description=ProductScope.model_fields["wiring"].description)
    electrical_control: StrictBool = Field(description=ProductScope.model_fields["electrical_control"].description)
    fluid_network: StrictBool = Field(description=ProductScope.model_fields["fluid_network"].description)
    biological_experiment: StrictBool = Field(description=ProductScope.model_fields["biological_experiment"].description)


class DomainTask(DomainModel):
    """One selected domain, its private context and independently tracked status."""

    domain: Domain
    reason: Text
    context: dict[str, Any] = Field(default_factory=dict)
    missing_inputs: list[str] = Field(default_factory=list)
    status: Literal["ready", "blocked", "generated", "invalid", "failed"] = "ready"
    errors: list[str] = Field(default_factory=list)
    provider: ProviderMetadata | None = None
    required_context: list[ContextRequirement] = Field(default_factory=list)
    dependencies: list[DomainDependency] = Field(default_factory=list)
    unresolved_dependencies: list[str] = Field(default_factory=list)


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


def context_errors(domain: Domain, context: dict[str, Any], *, registry: DomainRegistry | None = None) -> list[str]:
    """Validate only this domain's context and report missing or malformed inputs."""
    registry = get_registry(registry)
    metadata, provider = registry.metadata(domain), registry.provider(domain)
    if provider is not None:
        try:
            return provider.validate_context(deepcopy(context))
        except Exception as exc:
            return [f"Provider context validation unavailable: {exc}"]
    return [f"{item.name}: required context missing" for item in (metadata.required_context if metadata else [])
            if item.required and (item.name not in context or context[item.name] in (None, "", []))]


def select_domains(
    product_id: str,
    brief: str,
    *,
    scope: ProductScope | None = None,
    selector: Callable[[str], ProductScope] | None = None,
    contexts: dict[Domain, dict[str, Any]] | None = None,
    registry: DomainRegistry | None = None,
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
    registry = get_registry(registry)
    catalog = registry.catalog()
    contexts = contexts or {}
    unknown = (set(contexts) | set(scope.domains)) - set(catalog)
    if unknown:
        raise ValueError(f"Unknown context domains: {sorted(unknown)}")
    flags = scope.model_dump()
    selected = {domain for domain, info in catalog.items()
                if domain in scope.domains or any(flags.get(flag) is True for flag in info["any_scope"])}
    reasons = {domain: f"Requested scope requires {catalog[domain]['ir_type']}." for domain in selected}
    pending = [domain for domain in catalog if domain in selected]
    while pending:
        domain = pending.pop()
        for dependency in catalog[domain]["dependencies"]:
            target = dependency["domain"]
            if dependency["required"] and target in catalog and target not in selected:
                selected.add(target)
                reasons[target] = f"{domain} requires {target} through {dependency['relationship']}."
                pending.append(target)
    tasks = []
    for domain in catalog:
        if domain not in selected:
            continue
        metadata = registry.metadata(domain)
        assert metadata is not None
        context = contexts.get(domain, {})
        missing = context_errors(domain, context, registry=registry)
        unresolved = [f"Provider for '{domain}' is unavailable."] if registry.provider(domain) is None else []
        unresolved += [f"Required domain '{d.domain}' ({d.relationship}) is unavailable."
                       for d in metadata.dependencies if d.required and registry.provider(d.domain) is None]
        tasks.append(DomainTask(domain=domain, reason=reasons[domain], context=context,
            provider=metadata, required_context=metadata.required_context, dependencies=metadata.dependencies,
            unresolved_dependencies=unresolved, missing_inputs=missing,
            status="blocked" if missing or unresolved else "ready"))
    return ProductPlan(product_id=product_id, brief=brief, scope=scope, tasks=tasks)
