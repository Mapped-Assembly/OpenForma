"""Schema-independent domain provider contracts and explicit in-process registration.

These are composition metadata, not a base schema for external IR documents.
A provider may be a local adapter or a proxy to a service. No transport, package
discovery, credential handling or external implementation is loaded here.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Annotated, Any, Literal, Protocol, TYPE_CHECKING

from pydantic import Field, model_validator

from .base import Domain, DomainModel, Finding, Text, unique

if TYPE_CHECKING:
    from .evaluation import EvaluationRequest, EvaluationReceipt

Revision = Annotated[int, Field(ge=1)] | Text
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
NATIVE_DOMAINS = frozenset({"mechanical", "electrical", "biological", "fluidic", "chemical", "process", "thermal", "human"})
NATIVE_PROVIDER = "openforma.native"


class SchemaSupport(DomainModel):
    """An independently owned schema identifier and its supported versions."""

    schema_id: Text
    versions: list[Text] = Field(min_length=1)


class ContextRequirement(DomainModel):
    """A discoverable context input; detailed validation belongs to the provider."""

    name: Text
    description: Text
    required: bool = True


class DomainDependency(DomainModel):
    """A declared domain relationship, optionally required by a provider."""

    domain: Domain
    relationship: Text
    required: bool = True


class ProviderMetadata(DomainModel):
    """Discoverable ownership and capabilities without importing a domain schema."""

    domain: Domain
    ir_type: Text
    owner: Text
    provider_id: Text
    provider_version: Text
    schemas: list[SchemaSupport] = Field(default_factory=list)
    authoring: bool = False
    validation: bool = False
    evaluation: bool = False
    resolution: bool = False
    discovery: bool = False
    required_context: list[ContextRequirement] = Field(default_factory=list)
    reference_types: list[Text] = Field(default_factory=list)
    any_scope: list[Text] = Field(default_factory=list)
    dependencies: list[DomainDependency] = Field(default_factory=list)
    fact_authorities: list[Text] = Field(default_factory=list)
    availability: Literal["available", "unavailable"] = "available"
    limitations: list[Text] = Field(default_factory=list)


class ArtifactIdentity(DomainModel):
    """Provider-extracted identity; external payloads need not use these field names."""

    artifact_id: Text
    domain: Domain
    schema_id: Text
    schema_version: Text
    revision: Revision | None = None


class ArtifactReference(ArtifactIdentity):
    """Pinned reference to an independently serialized artifact, optionally remote."""

    schema_id: Text = ""
    provider_id: Text = ""
    provider_version: Text = ""
    sha256: Digest
    locator: Text | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def legacy_native_identity(cls, value: Any) -> Any:
        """Read old native manifests; never infer identity for external schemas."""
        if isinstance(value, dict):
            value = dict(value)
            if value.get("domain") in NATIVE_DOMAINS and value.get("provider_id", NATIVE_PROVIDER) == NATIVE_PROVIDER:
                value.setdefault("schema_id", f"openforma.{value['domain']}")
                value.setdefault("provider_id", NATIVE_PROVIDER)
                value.setdefault("provider_version", "1.0")
        return value

    @model_validator(mode="after")
    def explicit_provider(self) -> ArtifactReference:
        """External references must carry provider and schema identity explicitly."""
        if not self.schema_id or not self.provider_id or not self.provider_version:
            raise ValueError("Artifact references require explicit schema and provider identity/version.")
        if self.provider_id == NATIVE_PROVIDER and self.revision is None:
            raise ValueError("Native artifact references require a revision.")
        return self


class ArtifactInput(DomainModel):
    """A standalone provider-owned payload paired with its exact artifact reference."""

    reference: ArtifactReference
    artifact: dict[str, Any]


class DomainProvider(Protocol):
    """Boundary a future package or remote-service adapter can implement.

Optional operations are advertised in metadata. Payloads remain provider-owned
JSON, with no requirement to subclass an OpenForma or Pydantic IR model.
"""

    metadata: ProviderMetadata

    def validate_context(self, context: dict[str, Any]) -> list[str]:
        """Return missing/malformed context inputs for this provider only."""
        ...

    def validate(self, payload: dict[str, Any], reference: ArtifactReference) -> list[Finding]:
        """Validate local semantics only; never resolve cross-domain dependencies."""
        ...

    def describe(self, payload: dict[str, Any], reference: ArtifactReference) -> ArtifactIdentity:
        """Extract independently owned artifact identity from the native wire format."""
        ...

    def entities(self, payload: dict[str, Any], reference: ArtifactReference) -> dict[str, set[str]]:
        """Return stable entity and port identifiers for relationship checks."""
        ...

    def resolve(self, reference: ArtifactReference) -> dict[str, Any] | None:
        """Load one pinned artifact; composition never fetches arbitrary URLs itself."""
        ...

    def discover(self, query: dict[str, Any]) -> list[ArtifactReference]:
        """Advertise discoverable artifact references when supported."""
        ...

    def author(self, request: dict[str, Any]) -> ArtifactReference:
        """Author through the owning implementation and return a pinned reference."""
        ...

    def evaluate(self, payload: dict[str, Any], reference: ArtifactReference) -> list[dict[str, Any]]:
        """Return supported deterministic analyses when metadata advertises evaluation."""
        ...


class DomainRegistry:
    """One explicitly selected provider per domain; no global plugin discovery."""

    def __init__(self) -> None:
        """Create an isolated registry with no imported provider implementations."""
        self._metadata: dict[str, ProviderMetadata] = {}
        self._providers: dict[str, DomainProvider] = {}

    def register(self, metadata: ProviderMetadata, provider: DomainProvider | None = None, *, replace: bool = False) -> None:
        """Register capabilities, including advertisements for unavailable providers."""
        metadata = ProviderMetadata.model_validate(metadata.model_dump())
        if metadata.domain in self._metadata and not replace:
            raise ValueError(f"Domain '{metadata.domain}' already has a provider.")
        if provider is not None and provider.metadata != metadata:
            raise ValueError("Provider implementation and advertised metadata differ.")
        if provider is None and metadata.availability == "available":
            raise ValueError("Available providers require an implementation adapter.")
        if metadata.validation and not metadata.schemas:
            raise ValueError("Validation capability requires advertised schema identifiers and versions.")
        unique([s.schema_id for s in metadata.schemas], "provider schema")
        unique([c.name for c in metadata.required_context], "context requirement")
        self._metadata[metadata.domain] = metadata.model_copy(deep=True)
        self._providers.pop(metadata.domain, None)
        if provider is not None:
            self._providers[metadata.domain] = provider

    def catalog(self) -> dict[str, dict[str, Any]]:
        """Return serializable capability snapshots without loading any schemas."""
        return {domain: metadata.model_dump(mode="json") for domain, metadata in self._metadata.items()}

    def metadata(self, domain: str) -> ProviderMetadata | None:
        """Return a defensive snapshot of one domain's declared capabilities."""
        metadata = self._metadata.get(domain)
        return metadata.model_copy(deep=True) if metadata is not None else None

    def provider(self, domain: str) -> DomainProvider | None:
        """Resolve an available adapter without importing external repositories."""
        metadata = self._metadata.get(domain)
        return self._providers.get(domain) if metadata and metadata.availability == "available" else None

    def reference_errors(self, reference: ArtifactReference) -> list[Finding]:
        """Diagnose provider/schema resolution separately from artifact validity."""
        metadata = self.metadata(reference.domain)
        code, message = "", ""
        if metadata is None or self.provider(reference.domain) is None:
            code, message = "provider_unavailable", f"Provider for '{reference.domain}' is unavailable."
        elif (reference.provider_id, reference.provider_version) != (metadata.provider_id, metadata.provider_version):
            code, message = "provider_version", "Pinned provider identity/version differs from the registered adapter."
        elif not any(s.schema_id == reference.schema_id and reference.schema_version in s.versions for s in metadata.schemas):
            code, message = "provider_schema", "Registered provider does not support the pinned schema identifier/version."
        elif not metadata.validation:
            code, message = "provider_capability", "Provider cannot validate this artifact."
        return [Finding(code=code, location=reference.artifact_id, message=message)] if code else []

    def validate_artifact(self, payload: dict[str, Any], reference: ArtifactReference) -> list[Finding]:
        """Run standalone local validation via the declared provider, on a copy."""
        errors = self.reference_errors(reference)
        if errors:
            return errors
        provider = self.provider(reference.domain)
        assert provider is not None
        return provider.validate(deepcopy(payload), reference.model_copy(deep=True))

    def evaluator_catalog(self) -> dict[str, list[dict[str, Any]]]:
        """Discover optional named evaluators from the already registered domain providers."""
        from .evaluation import EvaluationSpec
        catalog = {}
        for domain in self._metadata:
            provider = self.provider(domain)
            discover = getattr(provider, "evaluation_specs", None)
            if callable(discover) and self._metadata[domain].evaluation:
                specs = [EvaluationSpec.model_validate(s) for s in discover()]
                unique([s.evaluator_id + ":" + s.version for s in specs], "named evaluator")
                catalog[domain] = [s.model_dump(mode="json") for s in specs]
        return catalog

    def evaluate_named(self, payload: dict[str, Any], reference: ArtifactReference,
                       request: EvaluationRequest) -> EvaluationReceipt:
        """Check exact artifact/model pins before invoking a provider-owned evaluator."""
        from .base import fingerprint
        from .evaluation import EvaluationReceipt, EvaluationRequest
        request = EvaluationRequest.model_validate(request.model_dump(mode="json"))
        receipt = EvaluationReceipt(request=request, artifact=reference, status="unknown",
            input_digest=fingerprint({"artifact": payload, "reference": reference.model_dump(mode="json"),
                                      "request": request.model_dump(mode="json")}))
        if request.artifact_id != reference.artifact_id or fingerprint(payload) != reference.sha256:
            receipt.status, receipt.message = "fail", "Artifact identity or content digest mismatch."
            return receipt
        errors = self.reference_errors(reference)
        if errors:
            receipt.message = "; ".join(e.message for e in errors)
            return receipt
        provider = self.provider(reference.domain)
        assert provider is not None
        identity = provider.describe(deepcopy(payload), reference.model_copy(deep=True))
        if any(getattr(identity, key) != getattr(reference, key) for key in
               ("artifact_id", "domain", "schema_id", "schema_version", "revision")):
            receipt.status, receipt.message = "fail", "Artifact revision/schema identity mismatch."
            return receipt
        catalog = self.evaluator_catalog().get(reference.domain, [])
        if not any(s["evaluator_id"] == request.evaluator_id and s["version"] == request.evaluator_version for s in catalog):
            receipt.status, receipt.message = "unsupported", "Named evaluator/version is not registered for this domain."
            return receipt
        method = getattr(provider, "evaluate_named", None)
        if not callable(method):
            receipt.message = "Provider has no named evaluation implementation."
            return receipt
        try:
            result = method(deepcopy(payload), reference.model_copy(deep=True), request.model_copy(deep=True))
            if set(result) - {"status", "outputs", "raw", "message"}:
                raise ValueError("Evaluator attempted to replace authoritative invocation metadata.")
            return EvaluationReceipt(**{**receipt.model_dump(mode="json"), **result})
        except ValueError as exc:
            receipt.status, receipt.message = "fail", str(exc)
        except Exception as exc:
            receipt.message = f"Evaluator unavailable: {type(exc).__name__}"
        return receipt
