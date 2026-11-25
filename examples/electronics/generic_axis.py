"""Synthetic low-voltage axis fixture; numbers are illustrative, not vendor evidence.

Run with: python -m examples.electronics.generic_axis
"""
from __future__ import annotations

import json

from forma_core.control.models import AxisDefinition, MachineDefinition
from forma_core.domains.base import fingerprint
from forma_core.electronics.models import ElectricalIR
from forma_core.electronics.power import MotorHistory, estimate_motor_history, power_budget
from forma_core.electronics.projections import engineering_bom
from forma_core.electronics.validation import design_digest, evidence_status, validate_runtime_bindings


def example() -> tuple[ElectricalIR, MachineDefinition, MotorHistory]:
    """Build a generic supply/controller/motor graph sharing runtime component IDs."""
    source = {"uri": "fixture:synthetic-constants", "sha256": fingerprint("synthetic constants v1"), "revision": "fixture-v1"}

    def pin(name: str, kind: str, role: str) -> dict:
        """Declare a fixture pin with a typed envelope, distinct from nominal voltage."""
        return {"pin_id": name, "name": name, "pin_type": kind, "power_role": role,
                "limits": {"voltage_range": {"minimum": 0, "maximum": 5}, "current_limit_a": 2}}

    machine = MachineDefinition(machine_id="generic-axis", name="Synthetic axis", axes=[
        AxisDefinition(axis_id="shaft", label="Output shaft", unit="degree", minimum=-90.0,
                       maximum=90.0, neutral=0.0, max_rate=30.0, component_ref="M1")])
    ir = ElectricalIR.model_validate({
        "artifact_id": "generic-electronics",
        "parts": [
            {"part_definition_id": "supply", "part_number": "SYNTHETIC-SUPPLY", "category": "Power",
             "pins": [pin("+", "Power", "source"), pin("-", "Ground", "return")],
             "engineering": {"revision": "fixture-v1"}},
            {"part_definition_id": "controller", "part_number": "SYNTHETIC-CONTROLLER", "category": "Power",
             "pins": [pin("in", "Power", "input"), pin("gnd", "Ground", "return"),
                      pin("out", "Power", "regulated_output"), pin("return", "Ground", "return")],
             "engineering": {"revision": "fixture-v1", "controller": {"source": source,
                "efficiency": 0.8, "motor_current_limit_a": 2, "bus_current_limit_a": 2,
                "bus_voltage": {"minimum": 4, "maximum": 5}}}},
            {"part_definition_id": "motor", "part_number": "SYNTHETIC-MOTOR", "category": "Actuator",
             "pins": [pin("+", "Power", "input"), pin("-", "Ground", "return")],
             "engineering": {"revision": "fixture-v1", "motor": {"source": source,
                "torque_constant_nm_per_a": 0.1, "back_emf_v_per_rad_s": 0.1,
                "resistance_ohm": 1, "no_load_current_a": 0.1}}},
        ],
        "components": [{"object_id": obj, "ref_des": ref, "part_definition_id": part}
                       for obj, ref, part in [("source-1", "PS1", "supply"), ("controller-1", "C1", "controller"), ("motor-1", "M1", "motor")]],
        "nets": [{"net_id": name, "name": name, "net_type": kind,
                  "pins": [{"ref_des": ref, "pin_id": pin_id} for ref, pin_id in endpoints]}
                 for name, kind, endpoints in [
                     ("bus", "Power", [("PS1", "+"), ("C1", "in")]),
                     ("ground", "Ground", [("PS1", "-"), ("C1", "gnd")]),
                     ("motor-power", "Power", [("C1", "out"), ("M1", "+")]),
                     ("motor-return", "Ground", [("C1", "return"), ("M1", "-")])]],
        "harnesses": [{"harness_id": "supply-lead", "wires": [{"wire_id": "positive", "net_id": "bus",
            "start": {"object_id": "source-1", "pin_id": "+"}, "end": {"object_id": "controller-1", "pin_id": "in"},
            "length_m": 0.2, "current_limit_a": 2}]}],
        "power_domains": [{"domain_id": "input", "net_id": "bus", "source_object_id": "source-1",
            "voltage_v": 5, "current_limit_a": 2,
            "loads": [{"object_id": "controller-1", "on_power_w": 4, "idle_power_w": 0.2, "duty_cycle": 0.5}]}],
        "runtime_bindings": [{"object_id": "motor-1", "machine_id": machine.machine_id,
            "machine_sha256": fingerprint(machine), "axis_id": "shaft", "feedback": "simulated",
            "startup_state": "disabled", "fault_response": "disable"}],
    })
    history = MotorHistory(motor_object_id="motor-1", controller_object_id="controller-1",
        design_sha256=design_digest(ir), simulation_sha256=fingerprint("synthetic history v1"), joint_id="shaft",
        gear_ratio=2, gear_efficiency=0.5, bus_voltage_v=5,
        samples=[{"duration_s": 1, "torque_nm": 0.1, "speed_rad_s": 2},
                 {"duration_s": 3, "torque_nm": 0, "speed_rad_s": 0}])
    return ir, machine, history


def main() -> None:
    """Print estimates and explicitly unknown verification statuses."""
    ir, machine, history = example()
    print(json.dumps({"power": {key: value.model_dump() for key, value in power_budget(ir).items()},
                      "motor": estimate_motor_history(ir, history).model_dump(),
                      "bom": [row.model_dump() | {"quantity": row.quantity} for row in engineering_bom(ir)],
                      "runtime_findings": [f.model_dump() for f in validate_runtime_bindings(ir, machine)],
                      "verification": {discipline: evidence_status(ir, discipline) for discipline in
                                       ("geometry", "dynamics", "electrical", "thermal", "safety")}}, indent=2))


if __name__ == "__main__":
    main()
