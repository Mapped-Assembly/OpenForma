"""Offline tests for deterministic firmware-scaffold generation.

Golden files (tests/firmware/golden/<board_id>/) are compared byte-for-byte
against generate_firmware output for the example IRs. Regenerate goldens with
scripts (see test README) whenever an intentional rendering change lands.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from apps.api.seed_db import SEED_COMPONENTS
from forma_core.firmware import (
    BOARD_MAPPINGS,
    FirmwareTarget,
    UnknownBoardError,
    esp32_example_ir,
    generate_firmware,
    nano_example_ir,
)
from forma_core.wiring import CONTROLLER_CATEGORIES

GOLDEN_ROOT = Path(__file__).parent / "golden"
SCAFFOLD_PATHS = ("platformio.ini", "include/pins.h", "src/main.cpp", "README.md")
GOLDEN_FILENAMES = {
    "platformio.ini": "platformio.ini",
    "include/pins.h": "pins.h",
    "src/main.cpp": "main.cpp",
    "README.md": "README.md",
}


def _example_irs() -> dict[str, object]:
    return {
        "esp32dev": esp32_example_ir(),
        "nanoatmega328": nano_example_ir(),
    }


class GoldenFileTests(unittest.TestCase):
    """Golden files — byte-for-byte compare against generate_firmware output."""

    def test_esp32_example_matches_golden(self) -> None:
        scaffold = generate_firmware(esp32_example_ir())
        self.assertEqual(scaffold.warnings, [])
        for path in SCAFFOLD_PATHS:
            with self.subTest(path=path):
                expected = (GOLDEN_ROOT / "esp32dev" / GOLDEN_FILENAMES[path]).read_text()
                self.assertEqual(scaffold.files[path], expected)

    def test_nano_example_matches_golden(self) -> None:
        scaffold = generate_firmware(nano_example_ir())
        self.assertEqual(scaffold.warnings, [])
        for path in SCAFFOLD_PATHS:
            with self.subTest(path=path):
                expected = (GOLDEN_ROOT / "nanoatmega328" / GOLDEN_FILENAMES[path]).read_text()
                self.assertEqual(scaffold.files[path], expected)

    def test_scaffold_contains_all_scaffold_files(self) -> None:
        for ir in _example_irs().values():
            scaffold = generate_firmware(ir)
            self.assertEqual(set(scaffold.files), set(SCAFFOLD_PATHS))


class DeterminismTests(unittest.TestCase):
    """Regenerating twice must be identical — byte-for-byte, every run."""

    def test_double_run_is_byte_identical(self) -> None:
        for name, ir in _example_irs().items():
            with self.subTest(example=name):
                first = generate_firmware(ir)
                second = generate_firmware(ir)
                self.assertEqual(first, second)
                self.assertEqual(first.model_dump(), second.model_dump())

    def test_double_run_serializes_identically(self) -> None:
        for name, ir in _example_irs().items():
            with self.subTest(example=name):
                first = generate_firmware(ir).model_dump_json()
                second = generate_firmware(ir).model_dump_json()
                self.assertEqual(first, second)


class PropertyTests(unittest.TestCase):
    """Toolchain-free property assertions over generated scaffolds."""

    def test_every_pin_mapping_appears_in_pins_h(self) -> None:
        for name, ir in _example_irs().items():
            scaffold = generate_firmware(ir)
            pins_h = scaffold.files["include/pins.h"]
            defined = dict(
                (macro, value)
                for macro, value in re.findall(r"^#define (\w+) (\S+)", pins_h, re.MULTILINE)
            )
            for mapping in ir.pin_mappings:
                with self.subTest(example=name, mcu_pin=mapping.mcu_pin):
                    self.assertIn(
                        _translated_pin(scaffold.target.board_id, mapping.mcu_pin),
                        defined.values(),
                        f"pin mapping {mapping.mcu_pin} missing from generated pins.h",
                    )

    def test_no_invented_nets(self) -> None:
        for name, ir in _example_irs().items():
            scaffold = generate_firmware(ir)
            pins_h = scaffold.files["include/pins.h"]
            net_ids = {net.net_id for net in ir.nets}
            referenced = set(re.findall(r"net: (\S+) →", pins_h))
            self.assertTrue(referenced, "expected net references in pins.h")
            self.assertLessEqual(referenced, net_ids, f"invented nets in {name} scaffold")

    def test_bus_nets_map_to_correct_bus_init(self) -> None:
        esp32 = generate_firmware(esp32_example_ir()).files["src/main.cpp"]
        self.assertIn("Wire.begin(PIN_SDA, PIN_SCL);", esp32)
        self.assertIn("Serial.begin(115200);", esp32)
        self.assertNotIn("SPI.begin()", esp32)

        nano = generate_firmware(nano_example_ir()).files["src/main.cpp"]
        self.assertIn("Wire.begin();", nano)
        self.assertNotIn("Serial.begin(", nano)
        self.assertNotIn("SPI.begin()", nano)

    def test_pin_modes_derived_from_pin_type(self) -> None:
        esp32 = generate_firmware(esp32_example_ir()).files["src/main.cpp"]
        self.assertIn("pinMode(PIN_DHT22_DATA, OUTPUT);", esp32)
        self.assertNotIn("pinMode(PIN_SDA,", esp32)  # bus pins are bus-managed


class UnknownBoardTests(unittest.TestCase):
    """Unknown part numbers and unknown explicit targets must fail loudly."""

    def test_unknown_part_number_raises(self) -> None:
        ir = esp32_example_ir()
        ir.components[0].part_number = "ESP32-UNKNOWN-VARIANT"
        with self.assertRaises(UnknownBoardError):
            generate_firmware(ir)

    def test_unknown_explicit_target_raises(self) -> None:
        with self.assertRaises(UnknownBoardError):
            generate_firmware(
                esp32_example_ir(),
                target=FirmwareTarget(board_id="does_not_exist", platform="espressif32"),
            )

    def test_no_mcu_produces_empty_scaffold(self) -> None:
        ir = esp32_example_ir()
        del ir.components[0]
        del ir.buses[:]
        ir.nets = []
        ir.pin_mappings = []
        scaffold = generate_firmware(ir)
        self.assertIsNone(scaffold.target)
        self.assertEqual(scaffold.files, {})
        self.assertIn("no MCU in BOM — generated empty scaffold", scaffold.warnings)


class BoardMappingIntegrityTests(unittest.TestCase):
    """Every mapping must stay valid against the seed catalog pinouts."""

    def _seed_template(self, part_number: str) -> dict:
        for template in SEED_COMPONENTS:
            if template["part_number"] == part_number:
                return template
        self.fail(f"part number {part_number} missing from seed catalog")

    def test_mappings_reference_seed_catalog_microcontrollers(self) -> None:
        for part_number in BOARD_MAPPINGS:
            template = self._seed_template(part_number)
            self.assertEqual(template["category"].strip().lower(), "microcontroller")

    def test_every_mapped_pin_exists_in_seed_catalog(self) -> None:
        for part_number, mapping in BOARD_MAPPINGS.items():
            catalog_pins = {pin["pin_id"] for pin in self._seed_template(part_number)["pins"]}
            translated_catalog_pins = {mapping.translate_pin(pin) for pin in catalog_pins}
            with self.subTest(part_number=part_number):
                for alias in mapping.pin_translation:
                    self.assertIn(alias, catalog_pins)
                referenced = set(mapping.pin_translation.values())
                for pins in mapping.bus_objects.values():
                    referenced.update(pins[1].values())
                for pin in referenced:
                    self.assertIn(
                        pin,
                        catalog_pins | translated_catalog_pins,
                        f"mapped pin {pin!r} not in {part_number} catalog pinout",
                    )

    def test_board_targets_match_spec(self) -> None:
        self.assertEqual(
            BOARD_MAPPINGS["ESP32-WROOM-32D"].target,
            FirmwareTarget(board_id="esp32dev", framework="arduino", platform="espressif32"),
        )
        self.assertEqual(
            BOARD_MAPPINGS["Arduino-Nano-V3"].target,
            FirmwareTarget(board_id="nanoatmega328", framework="arduino", platform="atmelavr"),
        )


class ExampleCatalogSyncTests(unittest.TestCase):
    """Example pinouts must stay byte-equal to the seed catalog they mirror."""

    _EXAMPLE_PARTS = {
        "esp32dev": {
            "U1": "ESP32-WROOM-32D",
            "SEN1": "DHT22",
            "MPU1": "MPU6050",
            "OLED1": "SSD1306-I2C",
            "K1": "Relay-5V-1Ch",
            "LED1": "LED-Red-Generic",
            "PWR1": "USB-5V-Plug",
        },
        "nanoatmega328": {
            "U1": "Arduino-Nano-V3",
            "SEN1": "HC-SR04",
            "OLED1": "SSD1306-I2C",
            "SG1": "SG90-Servo",
            "LED1": "LED-Red-Generic",
            "PWR1": "USB-5V-Plug",
        },
    }

    def test_example_pinouts_match_seed_catalog(self) -> None:
        for board, parts in self._EXAMPLE_PARTS.items():
            ir = _example_irs()[board]
            for component in ir.components:
                if component.ref_des not in parts:
                    continue
                template = next(
                    item
                    for item in SEED_COMPONENTS
                    if item["part_number"] == parts[component.ref_des]
                )
                with self.subTest(board=board, ref_des=component.ref_des):
                    self.assertEqual(component.part_number, template["part_number"])
                    self.assertEqual(
                        [(pin.pin_id, pin.name, pin.pin_type, pin.voltage) for pin in component.pins],
                        [
                            (pin["pin_id"], pin["name"], pin["pin_type"], pin.get("voltage"))
                            for pin in template["pins"]
                        ],
                    )

    def test_example_controllers_use_wiring_controller_categories(self) -> None:
        for ir in _example_irs().values():
            controllers = [
                component
                for component in ir.components
                if component.category.strip().lower() in CONTROLLER_CATEGORIES
            ]
            self.assertEqual(len(controllers), 1)


def _translated_pin(board_id: str, mcu_pin: str) -> str:
    mapping = next(
        board for board in BOARD_MAPPINGS.values() if board.target.board_id == board_id
    )
    return mapping.translate_pin(mcu_pin)


if __name__ == "__main__":
    unittest.main()
