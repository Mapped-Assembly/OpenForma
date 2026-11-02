"""Provider-neutral agent adapters for scope selection and isolated domain authoring."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel

from .base import Domain, canonical_json
from .generation import DomainAuthor, GenerationRequest
from .planning import ProductPlan, ProductScope, select_domains
from .registry import domain_model


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
) -> ProductPlan:
    """Ask the agent to interpret scope, then deterministically select domain types."""
    def selector(text: str) -> ProductScope:
        """Interpret only requested product work, preserving required but underspecified domains."""
        prompt = (
            "Identify the requested product scope. Set only capabilities requested by the brief. "
            "Respect exclusions: a passive chip with external pumping excluded needs no electrical control; "
            "including wiring or electrically controlled pumping does. Missing specifications never remove "
            "a required capability. Do not substitute workflow stages for engineering domains.\nBrief:\n" + text
        )
        value = provider.generate_structured(prompt, ProductScope)
        return ProductScope.model_validate(value.model_dump() if isinstance(value, BaseModel) else value)
    return select_domains(product_id, brief, selector=selector, contexts=contexts)


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
