"""Deterministic firmware generator: hardware IR → FirmwareScaffold.

``generate_firmware`` is a pure function of the IR — same IR + target yields a
byte-identical scaffold every run, costing zero tokens. Control logic is never
generated; the scaffold is a compiling, correctly-wired starting point.
"""

from __future__ import annotations

import re

from forma_core.firmware.drivers import DriverStub, lookup_driver
from forma_core.firmware.models import FirmwareScaffold, FirmwareTarget
from forma_core.firmware.targets import (
    BoardMapping,
    UnknownBoardError,
    bus_render_order,
    get_board_mapping,
    get_mapping_for_target,
)
from forma_core.wiring import CONTROLLER_CATEGORIES
from forma_core.workspaces.projects.models import (
    BusConnection,
    ComponentInstance,
    ConnectionNet,
    HardwareIntermediateRepresentation,
    PinMappingEntry,
)

_PWM_PIN_TYPES = {"DIGITAL", "PWM"}
_INPUT_PIN_TYPES = {"ANALOG"}
_BUS_MANAGED_PIN_TYPES = {"I2C", "SPI", "UART"}
_SKIP_PIN_TYPES = {"POWER", "GROUND"}


class _PinDefine:
    """One generated ``#define`` line in include/pins.h."""

    def __init__(self, macro: str, pin: str, comment: str) -> None:
        self.macro = macro
        self.pin = pin
        self.comment = comment


class _PinModeLine:
    """One generated ``pinMode(...)`` line in src/main.cpp."""

    def __init__(self, macro: str, direction: str, comment: str) -> None:
        self.macro = macro
        self.direction = direction
        self.comment = comment


def generate_firmware(
    ir: HardwareIntermediateRepresentation,
    target: FirmwareTarget | None = None,
) -> FirmwareScaffold:
    """Deterministically generate a PlatformIO scaffold from the hardware IR.

    An explicit target wins; otherwise the IR's optional ``firmware`` field pins
    the board; otherwise the board is inferred from the single controller
    component (the same category set derive_pin_mappings uses). Raises
    UnknownBoardError if the part_number has no BoardMapping entry.
    """

    warnings: list[str] = []
    controllers = [
        component
        for component in ir.components
        if component.category.strip().lower() in CONTROLLER_CATEGORIES
    ]

    target = target or ir.firmware
    if target is None:
        if not controllers:
            warnings.append("no MCU in BOM — generated empty scaffold")
            return FirmwareScaffold(target=None, files={}, warnings=warnings)
        controller = _choose_controller(controllers, warnings)
        mapping = get_board_mapping(controller.part_number)  # UnknownBoardError on miss
        target = mapping.target
    else:
        mapping = get_mapping_for_target(target)
        controller = _choose_controller(controllers, warnings) if controllers else None
        if controller is None:
            warnings.append("no MCU in BOM — generated empty scaffold")

    defines, pinMode_lines, bus_inits = _pin_plan(ir, mapping, controller, warnings)
    driver_stubs = _driver_stubs(ir, controller)

    title = ir.overview.title if ir.overview is not None else "Untitled Project"
    context = _RenderContext(
        mapping=mapping,
        target=target,
        title=title,
        ir_version=ir.hardware_ir_version,
    )

    files = {
        "platformio.ini": _render_platformio_ini(context),
        "include/pins.h": _render_pins_h(context, defines),
        "src/main.cpp": _render_main_cpp(
            context, bus_inits, pinMode_lines, driver_stubs, has_pins=bool(defines)
        ),
        "README.md": _render_readme(context, defines, driver_stubs),
    }
    return FirmwareScaffold(target=target, files=files, warnings=warnings)


class _RenderContext:
    """Immutable render inputs shared by all file renderers."""

    def __init__(self, mapping: BoardMapping, target: FirmwareTarget, title: str, ir_version: str) -> None:
        self.mapping = mapping
        self.target = target
        self.title = title
        self.ir_version = ir_version

    @property
    def target_line(self) -> str:
        return f"{self.mapping.display_name} ({self.target.board_id}, {self.target.framework})"

    @property
    def source_line(self) -> str:
        return f"Source project: {self.title} · hardware-ir {self.ir_version}"


def _choose_controller(controllers: list[ComponentInstance], warnings: list[str]) -> ComponentInstance:
    """Pick the scaffold's controller deterministically (lowest ref_des)."""

    if len(controllers) == 1:
        return controllers[0]
    chosen = min(controllers, key=lambda component: component.ref_des)
    warnings.append(
        f"multiple controllers in BOM — generated scaffold for "
        f"{chosen.part_number} ({chosen.ref_des})"
    )
    return chosen


def _pin_plan(
    ir: HardwareIntermediateRepresentation,
    mapping: BoardMapping,
    controller: ComponentInstance | None,
    warnings: list[str],
) -> tuple[list[_PinDefine], list[_PinModeLine], list[tuple[str, str]]]:
    """Derive pin defines, pinMode lines, and bus init lines from the IR.

    Individual (non-bus) pin mappings become their own defines; bus pins are
    covered by the board's curated default bus pins. Returns
    (defines, pinMode_lines, bus_init[(expression, comment)]).
    """

    defines: list[_PinDefine] = []
    pinMode_lines: list[_PinModeLine] = []
    bus_inits: list[tuple[str, str]] = []
    if controller is None:
        return defines, pinMode_lines, bus_inits

    nets_by_id = {net.net_id: net for net in ir.nets}
    component_by_ref = {component.ref_des: component for component in ir.components}
    controller_pin_types = {pin.pin_id: pin.pin_type for pin in controller.pins}

    bus_by_type, bus_nets = _bus_index(ir, warnings)

    used_macros: set[str] = set()
    bus_devices: dict[str, list[str]] = {bus_type: [] for bus_type in bus_by_type}
    bus_net_pins: dict[str, list[tuple[PinMappingEntry, str]]] = {bus_type: [] for bus_type in bus_by_type}

    for entry in ir.pin_mappings:
        bus_type = bus_nets.get(entry.net_name)
        translated = mapping.translate_pin(entry.mcu_pin)
        pin_type = controller_pin_types.get(entry.mcu_pin, "").strip().upper()

        if pin_type in _SKIP_PIN_TYPES:
            warnings.append(
                f"net {entry.net_name} maps controller power/ground pin {entry.mcu_pin} — skipped"
            )
            continue

        if bus_type is not None:
            bus_net_pins[bus_type].append((entry, translated))
            if pin_type != bus_type.upper():
                warnings.append(
                    f"net {entry.net_name} mapped to non-{bus_type} pin {translated}"
                )
            peer = _peer_part_number(ir, nets_by_id, component_by_ref, controller.ref_des, entry)
            if peer is not None and peer not in bus_devices[bus_type]:
                bus_devices[bus_type].append(peer)
            continue

        macro, peer_label = _individual_macro(
            ir, nets_by_id, component_by_ref, controller.ref_des, entry, used_macros, warnings
        )
        defines.append(_PinDefine(macro, translated, f"net: {entry.net_name} → {peer_label}"))
        direction, direction_warning = _pin_direction(entry.mcu_pin, pin_type)
        if direction is not None:
            pinMode_lines.append(_PinModeLine(macro, direction, f"net: {entry.net_name}"))
        if direction_warning:
            warnings.append(direction_warning)

    for bus_type in bus_render_order():
        if bus_type not in bus_by_type or bus_type not in mapping.bus_objects:
            continue
        bus = bus_by_type[bus_type]
        init_expression, default_pins = mapping.bus_objects[bus_type]
        device_list = ", ".join(bus_devices[bus_type])
        label = f"{bus_type} bus ({bus.bus_id})" + (f": {device_list}" if device_list else "")
        for role, pin in default_pins.items():
            defines.append(_PinDefine(f"PIN_{role}", mapping.translate_pin(pin), label))
        for entry, translated in bus_net_pins[bus_type]:
            if translated not in default_pins.values():
                warnings.append(
                    f"net {entry.net_name} maps MCU pin {translated}, which is not a default "
                    f"{bus_type} pin on this board — scaffold initializes {bus_type} on "
                    f"default pins"
                )
        bus_inits.append((init_expression, f"{bus_type} bus: {bus.bus_id}"))

    return defines, pinMode_lines, bus_inits


def _bus_index(
    ir: HardwareIntermediateRepresentation,
    warnings: list[str],
) -> tuple[dict[str, BusConnection], dict[str, str]]:
    """Index buses by type (first wins per type) and net_id → bus_type."""

    bus_by_type: dict[str, BusConnection] = {}
    bus_nets: dict[str, str] = {}
    for bus in ir.buses:
        bus_type = bus.bus_type.strip().upper()
        if bus_type in bus_by_type:
            warnings.append(
                f"multiple {bus_type} buses declared — scaffold initializes {bus_by_type[bus_type].bus_id}"
            )
            continue
        bus_by_type[bus_type] = bus
        for net_id in bus.nets:
            bus_nets[net_id] = bus_type
    return bus_by_type, bus_nets


def _peer_part_number(
    ir: HardwareIntermediateRepresentation,
    nets_by_id: dict[str, ConnectionNet],
    component_by_ref: dict[str, ComponentInstance],
    controller_ref: str,
    entry: PinMappingEntry,
) -> str | None:
    """Part number of the first non-controller peer on the mapped net."""

    net = nets_by_id.get(entry.net_name)
    if net is None:
        return None
    for pin_ref in net.pins:
        if pin_ref.ref_des == controller_ref:
            continue
        component = component_by_ref.get(pin_ref.ref_des)
        if component is not None and component.category.strip().lower() not in CONTROLLER_CATEGORIES:
            return component.part_number
    return None


def _individual_macro(
    ir: HardwareIntermediateRepresentation,
    nets_by_id: dict[str, ConnectionNet],
    component_by_ref: dict[str, ComponentInstance],
    controller_ref: str,
    entry: PinMappingEntry,
    used_macros: set[str],
    warnings: list[str],
) -> tuple[str, str]:
    """Macro name and comment label for one non-bus pin mapping.

    Named after the first peer's part_number + pin_id; falls back to the
    connected_to text, then the net name. Deterministic dedupe: first mapping
    keeps the plain name, later collisions switch to the net name, then
    numeric suffixes.
    """

    peer_label = entry.connected_to
    base: str | None = None
    net = nets_by_id.get(entry.net_name)
    if net is not None:
        for pin_ref in net.pins:
            if pin_ref.ref_des == controller_ref:
                continue
            component = component_by_ref.get(pin_ref.ref_des)
            if (
                component is not None
                and component.category.strip().lower() not in CONTROLLER_CATEGORIES
            ):
                base = _sanitize(f"{component.part_number}_{pin_ref.pin_id}")
                peer_label = f"{pin_ref.ref_des}.{pin_ref.pin_id}"
                break
            peer_label = f"{pin_ref.ref_des}.{pin_ref.pin_id}"
    if not base:
        base = _sanitize(entry.connected_to) or _sanitize(entry.net_name)
    if not base:  # connected_to and net_name both blank
        base = "PIN"
        warnings.append(f"pin mapping for net {entry.net_name!r} has no usable name")

    macro = f"PIN_{base}"
    if macro not in used_macros:
        used_macros.add(macro)
        return macro, peer_label
    net_macro = f"PIN_{_sanitize(entry.net_name)}"
    if net_macro not in used_macros:
        used_macros.add(net_macro)
        return net_macro, peer_label
    suffix = 2
    while f"{net_macro}_{suffix}" in used_macros:
        suffix += 1
    macro = f"{net_macro}_{suffix}"
    used_macros.add(macro)
    return macro, peer_label


def _pin_direction(mcu_pin: str, pin_type: str) -> tuple[str | None, str | None]:
    """pinMode direction from pin_type metadata.

    Digital/PWM pins drive → OUTPUT; Analog pins sense → INPUT; bus-managed
    pins are configured by bus init and get no pinMode. Returns
    (direction, warning).
    """

    normalized = pin_type.strip().upper()
    if normalized in _PWM_PIN_TYPES:
        return "OUTPUT", None
    if normalized in _INPUT_PIN_TYPES:
        return "INPUT", None
    if normalized in _BUS_MANAGED_PIN_TYPES:
        return None, None
    return (
        "INPUT",
        f"pin {mcu_pin} has unknown pin_type '{pin_type}' — defaulted to INPUT",
    )


def _driver_stubs(
    ir: HardwareIntermediateRepresentation,
    controller: ComponentInstance | None,
) -> list[tuple[DriverStub, ComponentInstance]]:
    """Driver stubs for every non-controller component with a known family."""

    stubs: list[tuple[DriverStub, ComponentInstance]] = []
    for component in ir.components:
        if controller is not None and component.ref_des == controller.ref_des:
            continue
        stub = lookup_driver(component.part_number)
        if stub is not None:
            stubs.append((stub, component))
    return stubs


def _render_platformio_ini(context: _RenderContext) -> str:
    target = context.target
    mapping = context.mapping
    return (
        f"; Generated by Forma — target: {context.target_line}\n"
        f"; {context.source_line}\n"
        "\n"
        f"[env:{target.board_id}]\n"
        f"platform = {target.platform}\n"
        f"board = {target.board_id}\n"
        f"framework = {target.framework}\n"
        f"monitor_speed = {mapping.monitor_speed}\n"
    )


def _render_pins_h(context: _RenderContext, defines: list[_PinDefine]) -> str:
    lines = [
        "// Generated by Forma — deterministic pin map; regenerate instead of editing by hand.",
        f"// Target: {context.target_line}",
        f"// {context.source_line}",
        "",
        "#ifndef FORMA_PINS_H",
        "#define FORMA_PINS_H",
        "",
    ]
    for define in defines:
        lines.append(f"// {define.comment}")
        lines.append(f"#define {define.macro} {define.pin}")
        lines.append("")
    lines.append("#endif  // FORMA_PINS_H")
    return "\n".join(lines) + "\n"


def _render_main_cpp(
    context: _RenderContext,
    bus_inits: list[tuple[str, str]],
    pinMode_lines: list[_PinModeLine],
    driver_stubs: list[tuple[DriverStub, ComponentInstance]],
    has_pins: bool,
) -> str:
    lines = [
        "// Generated by Forma — deterministic firmware scaffold; control logic is not generated.",
        f"// Target: {context.target_line}",
        f"// {context.source_line}",
        "",
        # A .cpp entry file must include Arduino.h explicitly: pinMode, OUTPUT,
        # and friends are not declared by the bus headers alone.
        "#include <Arduino.h>",
    ]
    bus_types = {comment.split(":")[0] for _, comment in bus_inits}
    if "I2C bus" in bus_types:
        lines.append("#include <Wire.h>")
    if "SPI bus" in bus_types:
        lines.append("#include <SPI.h>")
    if has_pins:
        lines.append('#include "pins.h"')
    lines.append("")

    for stub, component in driver_stubs:
        function_name = _c_name(f"{stub.function_prefix}_{component.ref_des}")
        lines.append(
            f"// {component.part_number} ({component.ref_des}): {stub.summary} — "
            "TODO: implement driver logic"
        )
        lines.append(f"static {stub.return_type} {function_name}({stub.params}) {{")
        if stub.return_type == "float":
            lines.append("  return 0.0f;")
        elif stub.return_type == "int":
            lines.append("  return 0;")
        elif stub.return_type == "long":
            lines.append("  return 0L;")
        elif stub.param_name:
            lines.append(f"  (void){stub.param_name};")
        lines.append("}")
        lines.append("")

    lines.append("void setup() {")
    for expression, comment in bus_inits:
        lines.append(f"  {expression};  // {comment}")
    for line in pinMode_lines:
        lines.append(f"  pinMode({line.macro}, {line.direction});  // {line.comment}")
    lines.append("}")
    lines.append("")
    lines.append("void loop() {")
    lines.append("  // TODO: control logic (not generated — scaffold only)")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _render_readme(
    context: _RenderContext,
    defines: list[_PinDefine],
    driver_stubs: list[tuple[DriverStub, ComponentInstance]],
) -> str:
    target = context.target
    lines = [
        f"# Firmware Scaffold — {context.title}",
        "",
        f"Generated by Forma's deterministic firmware generator from hardware-ir "
        f"{context.ir_version}.",
        f"Target: {context.mapping.display_name} (`{target.board_id}`, `{target.platform}`, `{target.framework}`)",
        "",
        "Control logic is intentionally not generated — this is a compiling starting",
        "point matched to the project wiring. Implement the TODO regions to add behavior.",
        "",
        "## Files",
        "",
        "- `platformio.ini` — PlatformIO build configuration",
        "- `include/pins.h` — pin constants derived from the project netlist",
        "- `src/main.cpp` — stubbed setup()/loop() with bus initialization and driver stubs",
        "",
        "## Pin map",
        "",
        "| Macro | Pin | Connection |",
        "| --- | --- | --- |",
    ]
    for define in defines:
        lines.append(f"| `{define.macro}` | {define.pin} | {define.comment} |")
    lines.extend(
        [
            "",
            "## Driver stubs",
            "",
            "| Function | Family | Notes |",
            "| --- | --- | --- |",
        ]
    )
    for stub, component in driver_stubs:
        function_name = _c_name(f"{stub.function_prefix}_{component.ref_des}")
        lines.append(
            f"| `{function_name}` | {stub.family} | {component.part_number} "
            f"({component.ref_des}): {stub.summary} |"
        )
    lines.extend(
        [
            "",
            "## Build",
            "",
            "```",
            f"pio run -e {target.board_id}",
            "```",
            "",
            "Pin modes in `src/main.cpp` are derived from pin_type metadata and may need",
            "adjustment for bidirectional signals.",
            "",
        ]
    )
    return "\n".join(lines)


def _sanitize(value: str) -> str:
    """Uppercase macro fragment: collapse non-alphanumerics to single underscores."""

    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").upper()


def _c_name(value: str) -> str:
    """Lowercase C identifier: collapse non-alphanumerics to single underscores."""

    name = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    if not name:
        return "_"
    if name[0].isdigit():
        return f"_{name}"
    return name
