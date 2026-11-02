"""Deterministic firmware-scaffold generation from the hardware IR.

Exports are lazy (PEP 562) so ``forma_core.workspaces.projects.models`` can
import ``FirmwareTarget`` from ``forma_core.firmware.models`` without an
import cycle: eager re-exports would pull ``generate``/``examples``/``validation``
— which import the hardware-IR module — back into the module that defines it.
"""

from __future__ import annotations

import importlib

from forma_core.firmware.models import FirmwareScaffold, FirmwareTarget

__all__ = [
    "BOARD_MAPPINGS",
    "DRIVER_STUBS",
    "BoardMapping",
    "DriverStub",
    "FirmwareScaffold",
    "FirmwareTarget",
    "UnknownBoardError",
    "esp32_example_ir",
    "firmware_target_rules",
    "generate_firmware",
    "get_board_mapping",
    "get_mapping_for_target",
    "lookup_driver",
    "nano_example_ir",
]

# Only models.py is imported eagerly: it depends on nothing but pydantic, so
# it is safe for the hardware-IR module to import FirmwareTarget from here.
_EXPORT_MODULES = {
    "BOARD_MAPPINGS": "forma_core.firmware.targets",
    "DRIVER_STUBS": "forma_core.firmware.drivers",
    "BoardMapping": "forma_core.firmware.targets",
    "DriverStub": "forma_core.firmware.drivers",
    "FirmwareScaffold": "forma_core.firmware.models",
    "FirmwareTarget": "forma_core.firmware.models",
    "UnknownBoardError": "forma_core.firmware.targets",
    "esp32_example_ir": "forma_core.firmware.examples",
    "firmware_target_rules": "forma_core.firmware.validation",
    "generate_firmware": "forma_core.firmware.generate",
    "get_board_mapping": "forma_core.firmware.targets",
    "get_mapping_for_target": "forma_core.firmware.targets",
    "lookup_driver": "forma_core.firmware.drivers",
    "nano_example_ir": "forma_core.firmware.examples",
}


def __getattr__(name: str):
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module_name), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
