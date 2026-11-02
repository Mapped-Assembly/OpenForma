"""Warning-only firmware-target validation rules over the hardware IR.

These rules mirror the deterministic generator's expectations (see
``forma_core.firmware.generate``): they flag designs whose firmware scaffold
will be empty, mis-pinned, or wired to the wrong bus pins. Every finding is
``WARNING`` — a firmware problem degrades the generated scaffold, it never
makes the project electrically invalid, so it must neither trigger the
CRITICAL-gated wiring-repair loop nor block generation.
"""

from __future__ import annotations

from forma_core.wiring import CONTROLLER_CATEGORIES
from forma_core.workspaces.projects.models import (
    ComponentInstance,
    HardwareIntermediateRepresentation,
    PinDefinition,
    ValidationIssue,
)


def _missing_controller_issue() -> ValidationIssue:
    return ValidationIssue(
        severity="WARNING",
        category="Firmware: Missing Controller",
        description=(
            "The BOM has no controller component (microcontroller/MCU/SBC) but the design "
            "declares wiring or pin mappings, so firmware generation would produce an empty scaffold."
        ),
        troubleshooting=(
            "Add a catalog controller component (e.g. ESP32-WROOM-32D or Arduino-Nano-V3) "
            "so a firmware target can be resolved."
        ),
    )


def _unknown_pin_issue(mcu_pin: str, controller_ref: str, valid_pins: list[str]) -> ValidationIssue:
    return ValidationIssue(
        severity="WARNING",
        category="Firmware: Unknown Pin",
        description=(
            f"Pin mapping references '{mcu_pin}', which is not a catalog pin on controller "
            f"{controller_ref}. Known pins: {', '.join(valid_pins) if valid_pins else 'none'}."
        ),
        troubleshooting=(
            f"Replace '{mcu_pin}' with a pin declared in {controller_ref}'s pin definitions, "
            "or declare the pin on the part."
        ),
    )


def _bus_pin_mismatch_issue(mcu_pin: str, net_name: str, bus_type: str, pin_type: str) -> ValidationIssue:
    return ValidationIssue(
        severity="WARNING",
        category="Firmware: Bus Pin Mismatch",
        description=(
            f"Net '{net_name}' belongs to a {bus_type} bus but is mapped to controller pin "
            f"'{mcu_pin}' whose pin_type is '{pin_type}', not a {bus_type} bus pin."
        ),
        troubleshooting=(
            f"Map the {bus_type} net to the controller's {bus_type}-capable pins, "
            "or change the net's bus declaration."
        ),
    )


def _controller_pins(controller: ComponentInstance) -> dict[str, PinDefinition]:
    """Catalog pins of one controller, keyed by pin_id with name fallbacks."""

    pins: dict[str, PinDefinition] = {}
    for pin in controller.pins:
        pins.setdefault(pin.pin_id, pin)
        if pin.name and pin.name != pin.pin_id:
            pins.setdefault(pin.name, pin)
    return pins


def _bus_nets(ir: HardwareIntermediateRepresentation) -> dict[str, str]:
    """net_id → bus_type for the first bus declared per type (same as the generator)."""

    bus_nets: dict[str, str] = {}
    seen_types: set[str] = set()
    for bus in ir.buses:
        bus_type = bus.bus_type.strip().upper()
        if bus_type in seen_types:
            continue
        seen_types.add(bus_type)
        for net_id in bus.nets:
            bus_nets[net_id] = bus_type
    return bus_nets


def firmware_target_rules(ir: HardwareIntermediateRepresentation) -> list[ValidationIssue]:
    """Firmware-target rules: missing controller, unknown mcu_pin, bus/pin mismatch.

    Rules run against the controller the deterministic generator would pick
    (lowest ref_des) so the diagnostics describe the scaffold that will
    actually be rendered. Purely mechanical BOMs with no wiring intent are not
    flagged.
    """

    issues: list[ValidationIssue] = []
    controllers = [
        component
        for component in ir.components
        if component.category.strip().lower() in CONTROLLER_CATEGORIES
    ]
    if not controllers:
        if ir.pin_mappings or ir.buses or ir.nets:
            issues.append(_missing_controller_issue())
        return issues

    controller = min(controllers, key=lambda component: component.ref_des)
    controller_label = f"{controller.part_number} ({controller.ref_des})"
    pins = _controller_pins(controller)
    bus_nets = _bus_nets(ir)

    for entry in ir.pin_mappings:
        pin = pins.get(entry.mcu_pin.strip())
        if pin is None:
            issues.append(_unknown_pin_issue(
                entry.mcu_pin, controller_label, sorted(pins),
            ))
            continue
        bus_type = bus_nets.get(entry.net_name)
        if bus_type is not None and pin.pin_type.strip().upper() != bus_type:
            issues.append(_bus_pin_mismatch_issue(
                entry.mcu_pin, entry.net_name, bus_type, pin.pin_type,
            ))
    return issues
