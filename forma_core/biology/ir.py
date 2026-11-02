"""Versioned domain envelope around the existing biological experiment contract."""

from typing import Literal

from pydantic import Field

from forma_core.domains.base import DomainDocument, DomainModel, Text
from .models import BiologicalExperiment


class BiologicalContext(DomainModel):
    """Biological authoring inputs; no claim of biological simulation."""

    scope: Text
    biological_question: Text
    experimental_conditions: list[Text] = Field(min_length=1)
    evidence_sources: list[Text] = Field(min_length=1)


class BiologicalIR(DomainDocument):
    """Version the envelope without changing BiologicalExperiment's wire format."""

    domain: Literal["biological"] = "biological"
    schema_version: Literal["1.0"] = "1.0"
    experiment: BiologicalExperiment
