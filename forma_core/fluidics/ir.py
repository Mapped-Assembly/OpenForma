"""Versioned domain envelope around the existing reusable fluid network."""

from typing import Literal

from pydantic import Field

from forma_core.domains.base import DomainDocument, DomainModel, Text
from .models import FluidicSystem


class FluidicContext(DomainModel):
    """Fluid-specific authoring inputs, with explicit operating requirements."""

    scope: Text
    fluid_properties: list[Text] = Field(min_length=1)
    operating_conditions: list[Text] = Field(min_length=1)
    flow_requirements: list[Text] = Field(min_length=1)


class FluidicIR(DomainDocument):
    """Version the envelope while retaining FluidicSystem and its validators."""

    domain: Literal["fluidic"] = "fluidic"
    schema_version: Literal["1.0"] = "1.0"
    system: FluidicSystem
