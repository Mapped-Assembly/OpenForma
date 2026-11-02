"""Board-mapping table: seed-catalog part numbers → PlatformIO targets.

Hand-curated data that can rot silently when the catalog changes; tests
validate every mapping against the seed catalog pinouts (see
tests/firmware/test_generate.py::test_board_mapping_integrity).
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from forma_core.firmware.models import FirmwareTarget


class UnknownBoardError(LookupError):
    """Raised when a part number (or explicit target) has no board mapping."""


class BoardMapping(BaseModel):
    """Curated translation table for one supported MCU board."""

    part_number: str = Field(..., description="Seed-catalog part number, e.g. 'ESP32-WROOM-32D'")
    display_name: str = Field(..., description="Human-readable board name for generated headers")
    target: FirmwareTarget = Field(..., description="PlatformIO build target")
    # Catalog pin names → generated constants. GPIO names pass through, aliases
    # (D21 → 21, VP → 36) are translated explicitly.
    pin_translation: dict[str, str] = Field(default_factory=dict)
    # bus_type → (bus init expression, default pins for generated constants).
    bus_objects: dict[str, tuple[str, dict[str, str]]] = Field(default_factory=dict)
    monitor_speed: int = Field(115200, description="platformio.ini monitor_speed")

    def translate_pin(self, pin_id: str) -> str:
        """Translate a catalog pin id to its generated constant.

        Explicit aliases win; bare 'D<digits>' NodeMCU aliases strip the
        prefix (D26 → 26); everything else passes through unchanged.
        """

        if pin_id in self.pin_translation:
            return self.pin_translation[pin_id]
        if re.fullmatch(r"D\d+", pin_id):
            return pin_id[1:]
        return pin_id


BOARD_MAPPINGS: dict[str, BoardMapping] = {
    "ESP32-WROOM-32D": BoardMapping(
        part_number="ESP32-WROOM-32D",
        display_name="ESP32 NodeMCU",
        target=FirmwareTarget(board_id="esp32dev", platform="espressif32"),
        # Catalog pin names → generated constants; GPIO names pass through,
        # aliases (D21 → 21, VP → 36) are translated explicitly:
        pin_translation={"D21": "21", "D22": "22", "TXD": "TX", "RXD": "RX", "VP": "36"},
        bus_objects={
            # bus_type → (Wire/SPI/Serial init, default pins)
            "I2C": ("Wire.begin(PIN_SDA, PIN_SCL)", {"SDA": "21", "SCL": "22"}),
            "SPI": ("SPI.begin()", {"SCK": "18", "MOSI": "23", "MISO": "19"}),
            "UART": ("Serial.begin(115200)", {}),
        },
        monitor_speed=115200,
    ),
    "Arduino-Nano-V3": BoardMapping(
        part_number="Arduino-Nano-V3",
        display_name="Arduino Nano",
        target=FirmwareTarget(board_id="nanoatmega328", platform="atmelavr"),
        pin_translation={"A4": "A4", "A5": "A5"},  # I2C on ATmega328P
        bus_objects={
            "I2C": ("Wire.begin()", {"SDA": "A4", "SCL": "A5"}),
            "SPI": ("SPI.begin()", {"SS": "10", "MOSI": "11", "MISO": "12", "SCK": "13"}),
            "UART": ("Serial.begin(9600)", {}),
        },
        monitor_speed=9600,
    ),
}

_BUS_RENDER_ORDER = ("I2C", "SPI", "UART")


def get_board_mapping(part_number: str) -> BoardMapping:
    """Look up the board mapping for a seed-catalog part number.

    Raises UnknownBoardError if the part_number has no BoardMapping entry.
    """

    mapping = BOARD_MAPPINGS.get(part_number.strip())
    if mapping is None:
        known = ", ".join(sorted(BOARD_MAPPINGS))
        raise UnknownBoardError(
            f"no board mapping for part number '{part_number}' (supported: {known})"
        )
    return mapping


def get_mapping_for_target(target: FirmwareTarget) -> BoardMapping:
    """Look up the board mapping behind an explicit FirmwareTarget.

    Raises UnknownBoardError if the board_id has no BoardMapping entry.
    """

    for mapping in BOARD_MAPPINGS.values():
        if mapping.target == target:
            return mapping
    raise UnknownBoardError(f"no board mapping for target '{target.board_id}'")


def bus_render_order() -> tuple[str, ...]:
    """Deterministic bus render order for generated sources."""

    return _BUS_RENDER_ORDER
