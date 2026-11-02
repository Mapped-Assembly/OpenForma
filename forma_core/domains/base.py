"""Small shared vocabulary for independent, versioned domain documents."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Domain = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]*$")]
Text = Annotated[str, Field(min_length=1)]


class DomainModel(BaseModel):
    """Reject unknown fields and non-finite numbers at new domain boundaries."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class DomainDocument(DomainModel):
    """Identity and revision metadata, with no other domain's semantics."""

    artifact_id: Text
    revision: int = Field(1, ge=1)


class ComponentView(DomainModel):
    """A domain view of a physical object; object_id survives refdes changes."""

    object_id: Text
    ref_des: Text
    part_definition_id: Text


class Finding(DomainModel):
    """A scoped diagnostic; local validity and composition validity are separate."""

    code: str
    location: str
    message: str
    severity: Literal["error", "warning"] = "error"


def canonical_json(value: Any) -> str:
    """Encode JSON deterministically, rejecting NaN and infinite values."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def fingerprint(value: Any) -> str:
    """Return the SHA-256 digest of the canonical JSON representation."""
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def unique(values: list[str], label: str) -> None:
    """Reject duplicate IDs rather than collapsing them into a lookup table."""
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {label}.")
