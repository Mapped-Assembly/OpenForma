"""Conservative envelope budgets and revision-bound DC motoring estimates."""
from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import Field

from forma_core.domains.base import DomainModel, Text
from .contracts import Digest, Efficiency, Nonnegative, Positive

if TYPE_CHECKING:
    from .models import ElectricalIR


class Demand(DomainModel):
    """Unknown inputs propagate independently into peak and time-average watts."""

    peak_w: Nonnegative | None = None
    average_w: Nonnegative | None = None


def power_budget(ir: ElectricalIR) -> dict[str, Demand]:
    """Sum simultaneous loads and recursively account for converter losses.

    Average = duty * on + (1-duty) * idle. Peak = max(on, idle), without
    diversity credit. Converter quiescent power is on its input side.
    """
    # Reparse public inputs because Pydantic documents remain mutable.
    ir = type(ir).model_validate(ir.model_dump())
    domains = {d.domain_id: d for d in ir.power_domains}
    results: dict[str, Demand] = {}

    def total(values: list[float | None]) -> float | None:
        """Do not silently omit unknown consumers."""
        return None if any(v is None for v in values) else sum(values)

    def compute(domain_id: str) -> Demand:
        """Evaluate the already validated acyclic power graph bottom up."""
        if domain_id in results:
            return results[domain_id]
        domain = domains[domain_id]
        peaks, averages = [], []
        for load in domain.loads:
            peaks.append(None if load.on_power_w is None or load.idle_power_w is None
                         else max(load.on_power_w, load.idle_power_w))
            averages.append(None if any(v is None for v in (load.on_power_w, load.idle_power_w, load.duty_cycle))
                            else load.on_power_w * load.duty_cycle + load.idle_power_w * (1 - load.duty_cycle))
        for child in domains.values():
            if child.upstream_domain_id == domain_id:
                demand = compute(child.domain_id)
                for values, watts in ((peaks, demand.peak_w), (averages, demand.average_w)):
                    values.append(None if watts is None or child.conversion_efficiency is None or child.quiescent_power_w is None
                                  else watts / child.conversion_efficiency + child.quiescent_power_w)
        results[domain_id] = Demand(peak_w=total(peaks), average_w=total(averages))
        return results[domain_id]

    for domain_id in domains:
        compute(domain_id)
    return results


class MotionSample(DomainModel):
    """Interval-constant joint-output torque and speed in SI, including modelled load."""

    duration_s: Positive
    torque_nm: float
    speed_rad_s: float


class MotorHistory(DomainModel):
    """Normalized simulator output, not raw MuJoCo ctrl or actuator_force arrays."""

    motor_object_id: Text
    controller_object_id: Text
    design_sha256: Digest
    simulation_sha256: Digest
    joint_id: Text
    gear_ratio: Positive | None = None
    gear_efficiency: Efficiency | None = None
    bus_voltage_v: Positive | None = None
    samples: list[MotionSample] = Field(min_length=1)


class MotorEstimate(Demand):
    """Calculation results with limit findings; never a verification pass."""

    peak_motor_current_a: Nonnegative | None = None
    peak_bus_current_a: Nonnegative | None = None
    copper_loss_average_w: Nonnegative | None = None
    unknowns: list[str] = Field(default_factory=list)
    limit_violations: list[str] = Field(default_factory=list)


def estimate_motor_history(ir: ElectricalIR, history: MotorHistory) -> MotorEstimate:
    """Estimate brushed-DC motoring/stall demand with explicitly sourced constants.

    tau_motor = abs(tau_joint)/(ratio*gear_efficiency); omega_motor =
    abs(omega_joint)*ratio; I = tau_motor/Kt + I0; V = Ke*omega_motor + I*R;
    P_bus = V*I/controller_efficiency. Reverse motoring is symmetric.
    Negative torque*speed needs a regenerative model and remains unknown.
    """
    from .validation import design_digest

    ir = type(ir).model_validate(ir.model_dump())
    history = MotorHistory.model_validate(history.model_dump())
    if history.design_sha256 != design_digest(ir):
        raise ValueError("Motion history is pinned to a different electrical design.")
    objects = {c.object_id: c for c in ir.components}
    parts = {p.part_definition_id: p for p in ir.parts}
    if history.motor_object_id not in objects or history.controller_object_id not in objects:
        raise ValueError("History references unknown motor/controller object.")
    motor_part = parts[objects[history.motor_object_id].part_definition_id]
    controller_part = parts[objects[history.controller_object_id].part_definition_id]
    motor = motor_part.engineering.motor if motor_part.engineering else None
    controller = controller_part.engineering.controller if controller_part.engineering else None
    missing = []
    for name, model, fields in (
        ("motor", motor, ("source", "torque_constant_nm_per_a", "back_emf_v_per_rad_s", "resistance_ohm", "no_load_current_a")),
        ("controller", controller, ("source", "efficiency", "motor_current_limit_a", "bus_current_limit_a", "bus_voltage")),
        ("history", history, ("gear_ratio", "gear_efficiency", "bus_voltage_v")),
    ):
        missing.extend(f"{name}.{field}" for field in fields if model is None or getattr(model, field) is None)
    for name, part in (("motor", motor_part), ("controller", controller_part)):
        if part.engineering is None or part.engineering.revision is None:
            missing.append(f"{name}.revision")
    if any(s.torque_nm * s.speed_rad_s < 0 for s in history.samples):
        missing.append("regenerative/braking model")
    if missing:
        return MotorEstimate(unknowns=missing)
    powers, currents, losses = [], [], []
    violations: set[str] = set()
    bus = history.bus_voltage_v
    if not controller.bus_voltage.minimum <= bus <= controller.bus_voltage.maximum:
        violations.add("bus_voltage")
    for sample in history.samples:
        current = abs(sample.torque_nm) / (history.gear_ratio * history.gear_efficiency * motor.torque_constant_nm_per_a) + motor.no_load_current_a
        voltage = motor.back_emf_v_per_rad_s * abs(sample.speed_rad_s) * history.gear_ratio + current * motor.resistance_ohm
        power = voltage * current / controller.efficiency
        if voltage > bus:
            violations.add("required_motor_voltage")
        if current > controller.motor_current_limit_a:
            violations.add("motor_current")
        if power / bus > controller.bus_current_limit_a:
            violations.add("bus_current")
        currents.append(current)
        powers.append(power)
        losses.append(current * current * motor.resistance_ohm)
    duration = sum(s.duration_s for s in history.samples)
    return MotorEstimate(peak_w=max(powers), average_w=sum(p*s.duration_s for p, s in zip(powers, history.samples))/duration,
                         peak_motor_current_a=max(currents), peak_bus_current_a=max(powers)/bus,
                         copper_loss_average_w=sum(p*s.duration_s for p, s in zip(losses, history.samples))/duration,
                         limit_violations=sorted(violations))
