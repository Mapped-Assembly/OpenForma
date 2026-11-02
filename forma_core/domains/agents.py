"""Provider-neutral agent adapters for scope selection and isolated domain authoring."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel

from .base import Domain, canonical_json
from .generation import DomainAuthor, GenerationRequest
from .planning import CompleteProductScope as InferredProductScope
from .planning import ProductPlan, ProductScope, select_domains
from .registry import domain_model
from .registry import get_registry
from .providers import DomainRegistry


class StructuredProvider(Protocol):
    """The existing Forma provider method used without the HardwareIR orchestrator."""

    def generate_structured(self, prompt: str, schema_class: type[BaseModel]) -> BaseModel:
        """Generate a typed response through the configured provider."""
        ...


def plan_with_agent(
    provider: StructuredProvider,
    product_id: str,
    brief: str,
    *,
    contexts: dict[Domain, dict[str, Any]] | None = None,
    registry: DomainRegistry | None = None,
) -> ProductPlan:
    """Ask the agent to interpret scope, then deterministically select domain types."""
    def selector(text: str) -> ProductScope:
        """Interpret only requested product work, preserving required but underspecified domains."""
        prompt = (
            "Identify the requested product scope. Explicitly decide all six capabilities. "
            "Set only capabilities requested by the brief; do not include every discipline merely "
            "because physical hardware has geometry or uses biological materials. "
            "Respect exclusions: a passive chip with external pumping excluded needs no electrical control; "
            "including wiring or electrically controlled pumping does. Missing specifications never remove "
            "a required capability. A software-only project can select no physical domains. "
            "Do not substitute workflow stages for engineering domains. "
            "Use domains for required registry domains beyond the six legacy capabilities, even if unavailable.\n"
            "Domain registry:\n" + canonical_json(get_registry(registry).catalog()) + "\nBrief:\n" + text
        )
        value = provider.generate_structured(prompt, InferredProductScope)
        payload = value.model_dump(exclude_unset=True) if isinstance(value, BaseModel) else value
        inferred = InferredProductScope.model_validate(payload)
        return ProductScope.model_validate(inferred.model_dump())
    return select_domains(product_id, brief, selector=selector, contexts=contexts, registry=registry)


def configured_selector(provider_name: str | None = None, model_name: str | None = None) -> StructuredProvider:
    """Resolve an explicitly requested internal provider, without simulation."""
    from forma_core.llm_providers import build_llm_provider, enforce_production_llm_preflight

    provider = build_llm_provider(provider_name=provider_name, model_name=model_name)
    if not provider.is_configured or provider.provider_name == "simulation":
        raise RuntimeError(
            "Internal provider selection requires a configured live LLM provider. "
            "Use the default external selection handoff with your calling agent, supply an explicit scope, "
            "or configure your existing Forma provider credentials. No inference was performed."
        )
    validation = enforce_production_llm_preflight(provider.validate_configured_model(raise_on_strict=True))
    if not validation.live_generation_enabled:
        raise RuntimeError("Configured provider is unavailable for domain selection; no inference was performed.")
    return provider


def provider_author(provider: StructuredProvider) -> DomainAuthor:
    """Wrap Forma's structured provider with a single-domain prompt boundary."""
    def author(request: GenerationRequest) -> BaseModel:
        """Author one revision from only relevant context and declared cross-domain inputs."""
        prompt = (
            "Author exactly the requested domain IR and preserve its requested artifact identity/revision. "
            "Use only supplied context and declared inputs. Never fabricate missing specifications or "
            "add unrelated domains. Preserve stable object IDs when updating; ref_des is a display label. "
            "Validity is limited to the schema and declared checks; do not claim physical validation.\n"
            + canonical_json(request)
        )
        return provider.generate_structured(prompt, domain_model(request.domain))
    return author
