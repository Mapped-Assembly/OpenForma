"""Reference integrity and engineering findings, separate from verification claims."""
from __future__ import annotations

from typing import TYPE_CHECKING

from forma_core.domains.base import Finding, fingerprint, unique
from .contracts import Discipline

if TYPE_CHECKING:
    from forma_core.control.models import MachineDefinition
    from .models import ElectricalIR


def validate_engineering_references(ir: ElectricalIR) -> None:
    """Reject dangling shared IDs, impossible harness endpoints and cyclic power flow."""
    objects = {c.object_id: c for c in ir.components}
    nets = {n.net_id: n for n in ir.nets}

    def require_object(object_id: str) -> None:
        """Resolve an object without substituting display reference designators."""
        if object_id not in objects:
            raise ValueError(f"Unknown electrical object '{object_id}'.")

    for part in ir.parts:
        if part.engineering:
            connectors = {c.connector_id: c for c in part.engineering.connectors}
            unique([c.connector_id for c in part.engineering.connectors], "connector ID")
            pins = {p.pin_id: p for p in part.pins}
            assigned: list[str] = []
            for connector in connectors.values():
                assigned.extend(connector.pin_ids)
                if any(p not in pins for p in connector.pin_ids):
                    raise ValueError("Connector references unknown pin.")
            unique(assigned, "connector pin assignment")
        else:
            connectors = {}
        for pin in part.pins:
            if pin.limits and pin.limits.connector_id:
                connector = connectors.get(pin.limits.connector_id)
                if connector is None or pin.pin_id not in connector.pin_ids:
                    raise ValueError("Pin connector does not contain this pin.")
    unique([h.harness_id for h in ir.harnesses], "harness ID")
    unique([w.wire_id for h in ir.harnesses for w in h.wires], "wire ID")
    for harness in ir.harnesses:
        unique(harness.mating_connector_object_ids, "mating connector ID")
        for object_id in harness.mating_connector_object_ids:
            require_object(object_id)
        for wire in harness.wires:
            if wire.net_id not in nets:
                raise ValueError("Wire references unknown net.")
            if wire.start == wire.end:
                raise ValueError("Wire endpoints must differ.")
            for endpoint in (wire.start, wire.end):
                require_object(endpoint.object_id)
                ref_des = objects[endpoint.object_id].ref_des
                if (ref_des, endpoint.pin_id) not in {(p.ref_des, p.pin_id) for p in nets[wire.net_id].pins}:
                    raise ValueError("Wire endpoint is not on its declared net.")
            if wire.protection_object_id is not None:
                require_object(wire.protection_object_id)
    unique([d.domain_id for d in ir.power_domains], "power domain ID")
    unique([d.net_id for d in ir.power_domains], "power domain net")
    domains = {d.domain_id: d for d in ir.power_domains}
    for domain in ir.power_domains:
        require_object(domain.source_object_id)
        if domain.net_id not in nets or nets[domain.net_id].net_type.lower() != "power":
            raise ValueError("Power domain requires a declared power net.")
        unique([load.object_id for load in domain.loads], "power load ID")
        net_voltage = nets[domain.net_id].voltage
        if net_voltage is not None and domain.voltage_v is not None and net_voltage != domain.voltage_v:
            raise ValueError("Power domain voltage conflicts with its net nominal voltage.")
        connected = {p.ref_des for p in nets[domain.net_id].pins}
        for object_id in [domain.source_object_id, *[load.object_id for load in domain.loads]]:
            require_object(object_id)
            if objects[object_id].ref_des not in connected:
                raise ValueError("Power source/load must be connected to the domain net.")
        if domain.source_object_id in {load.object_id for load in domain.loads}:
            raise ValueError("Power domain cannot load itself.")
        if domain.upstream_domain_id is not None:
            upstream = domains.get(domain.upstream_domain_id)
            if upstream is None:
                raise ValueError("Unknown upstream power domain.")
            if objects[domain.source_object_id].ref_des not in {p.ref_des for p in nets[upstream.net_id].pins}:
                raise ValueError("Converter must be connected to upstream net.")
            if domain.source_object_id in {load.object_id for load in upstream.loads}:
                raise ValueError("Converter demand is derived; do not also list it as an upstream load.")
        seen = {domain.domain_id}
        parent = domain.upstream_domain_id
        while parent is not None:
            if parent not in domains or parent in seen:
                raise ValueError("Power domains require an acyclic, resolved upstream graph.")
            seen.add(parent)
            parent = domains[parent].upstream_domain_id
    unique([b.object_id for b in ir.runtime_bindings], "runtime component binding")
    unique([f"{b.machine_id}/{b.axis_id}" for b in ir.runtime_bindings], "runtime axis binding")
    for binding in ir.runtime_bindings:
        require_object(binding.object_id)
    unique([e.evidence_id for e in ir.evidence], "evidence ID")
    for evidence in ir.evidence:
        for object_id in evidence.component_revisions:
            require_object(object_id)


def design_digest(ir: ElectricalIR) -> str:
    """Hash every design fact except evidence, avoiding self-referential evidence hashes."""
    return fingerprint(ir.model_dump(mode="json", exclude={"evidence"}))


def evidence_status(
    ir: ElectricalIR, discipline: Discipline, *, model_sha256: dict[str, str] | None = None,
) -> str:
    """Return scoped claims; external models require caller-supplied current digests."""
    ir = type(ir).model_validate(ir.model_dump())
    parts = {p.part_definition_id: p for p in ir.parts}
    revisions = {c.object_id: (parts[c.part_definition_id].engineering.revision
                 if parts[c.part_definition_id].engineering else None) for c in ir.components}
    records = [e for e in ir.evidence if e.discipline == discipline]
    valid = [e for e in records if e.design_sha256 == design_digest(ir)
             and e.component_revisions == revisions
             and all((model_sha256 or {}).get(key) == value for key, value in e.model_sha256.items())]
    if any(e.status == "fail" for e in valid):
        return "fail"
    if not records or len(valid) != len(records) or any(e.status != "pass" for e in valid):
        return "unknown"
    return "pass"


def validate_runtime_bindings(ir: ElectricalIR, machine: MachineDefinition) -> list[Finding]:
    """Check pinned runtime axes; no firmware is generated or hardware actuated."""
    ir = type(ir).model_validate(ir.model_dump())
    machine = type(machine).model_validate(machine.model_dump())
    findings: list[Finding] = []
    objects = {c.object_id: c for c in ir.components}
    axes = {a.axis_id: a for a in machine.axes}
    for binding in ir.runtime_bindings:
        axis = axes.get(binding.axis_id)
        if (binding.machine_id != machine.machine_id or binding.machine_sha256 != fingerprint(machine)
                or axis is None or axis.component_ref != objects[binding.object_id].ref_des):
            findings.append(Finding(code="runtime_binding", location=binding.object_id,
                                    message="Runtime identity, digest or component axis does not match."))
        if binding.startup_state == "unknown" or binding.fault_response == "unknown" or binding.feedback == "unknown":
            findings.append(Finding(code="runtime_unknown", location=binding.object_id, severity="warning",
                                    message="Startup, fault response or feedback contract is unknown."))
        if (binding.feedback == "measured" and machine.backend == "virtual") or (
                binding.feedback == "simulated" and machine.backend == "physical"):
            findings.append(Finding(code="feedback_provenance", location=binding.object_id,
                                    message="Feedback provenance conflicts with runtime backend."))
    return findings


def validate_engineering(ir: ElectricalIR) -> list[Finding]:
    """Report typed net incompatibilities and incomplete data without certifying hardware."""
    from .power import power_budget

    findings: list[Finding] = []
    parts = {p.part_definition_id: p for p in ir.parts}
    components = {c.ref_des: parts[c.part_definition_id] for c in ir.components}
    engineering_enabled = bool(ir.harnesses or ir.power_domains or ir.runtime_bindings or ir.evidence
                               or any(p.engineering or any(pin.limits for pin in p.pins) for p in ir.parts))
    membership: dict[tuple[str, str], str] = {}
    for net in ir.nets:
        ranges = []
        protocols = set()
        for endpoint in net.pins:
            key = (endpoint.ref_des, endpoint.pin_id)
            if key in membership and membership[key] != net.net_id:
                findings.append(Finding(code="multiple_nets", location=net.net_id,
                                        message="A physical terminal cannot belong to distinct logical nets."))
            membership[key] = net.net_id
            pin = next(p for p in components[endpoint.ref_des].pins if p.pin_id == endpoint.pin_id)
            if pin.interface:
                protocols.add(pin.interface.casefold())
            limits = pin.limits
            if limits and limits.voltage_range:
                ranges.append(limits.voltage_range)
                if net.voltage is not None and not limits.voltage_range.minimum <= net.voltage <= limits.voltage_range.maximum:
                    findings.append(Finding(code="voltage_range", location=net.net_id,
                                            message=f"Net voltage exceeds {endpoint.ref_des}.{endpoint.pin_id} envelope."))
            elif engineering_enabled:
                findings.append(Finding(code="unknown_pin_rating", location=f"{endpoint.ref_des}.{endpoint.pin_id}",
                                        message="Pin voltage envelope is unknown.", severity="warning"))
        if ranges and max(r.minimum for r in ranges) > min(r.maximum for r in ranges):
            findings.append(Finding(code="voltage_range", location=net.net_id, message="Pin voltage envelopes do not overlap."))
        if len(protocols) > 1:
            findings.append(Finding(code="protocol_mismatch", location=net.net_id, message="Pin protocols differ."))
    for harness in ir.harnesses:
        for wire in harness.wires:
            if any(value is None for value in (wire.length_m, wire.current_limit_a, wire.route_ref, wire.protection_object_id)):
                findings.append(Finding(code="harness_incomplete", location=wire.wire_id, severity="warning",
                                        message="Wire length, rating, route or protection is unknown."))
    for domain_id, demand in power_budget(ir).items():
        domain = next(d for d in ir.power_domains if d.domain_id == domain_id)
        if demand.peak_w is None or demand.average_w is None or domain.current_limit_a is None or domain.voltage_v is None:
            findings.append(Finding(code="power_unknown", location=domain_id, severity="warning",
                                    message="Power demand or source capacity is unknown."))
        if (demand.peak_w is not None and domain.voltage_v is not None and domain.current_limit_a is not None
                and demand.peak_w > domain.voltage_v * domain.current_limit_a):
            findings.append(Finding(code="power_overload", location=domain_id, message="Simultaneous peak demand exceeds source capacity."))
        if demand.peak_w is not None and domain.voltage_v is not None:
            source = next(c for c in ir.components if c.object_id == domain.source_object_id)
            net = next(n for n in ir.nets if n.net_id == domain.net_id)
            source_pins = [p for p in net.pins if p.ref_des == source.ref_des]
            # Multiple feed terminals need explicit current-sharing analysis.
            if len(source_pins) == 1:
                pin = next(p for p in components[source.ref_des].pins if p.pin_id == source_pins[0].pin_id)
                limit = pin.limits.current_limit_a if pin.limits else None
                if limit is not None and demand.peak_w / domain.voltage_v > limit:
                    findings.append(Finding(code="source_pin_overload", location=domain_id,
                                            message="Peak demand exceeds the single source terminal current rating."))
            else:
                findings.append(Finding(code="current_sharing_unknown", location=domain_id, severity="warning",
                                        message="Multiple source terminals require current-sharing analysis."))
    return findings
