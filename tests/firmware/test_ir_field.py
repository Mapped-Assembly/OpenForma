"""Optional ``firmware`` IR field: additive pinning of the PlatformIO target.

The locked spec decision (art_3sR5210k, IR schema row): an optional
``HardwareIntermediateRepresentation.firmware: FirmwareTarget | None`` lets a
project pin a board; when absent the generator's default inference from the
controller part number is unchanged.
"""

from __future__ import annotations

import unittest

from forma_core.firmware import (
    FirmwareTarget,
    esp32_example_ir,
    generate_firmware,
    nano_example_ir,
)
from forma_core.workspaces.projects.models import HardwareIntermediateRepresentation

ESP32_TARGET = FirmwareTarget(board_id="esp32dev", platform="espressif32")


class FirmwareFieldRoundTripTests(unittest.TestCase):
    """IR round-trip with the optional field set and unset."""

    def test_field_defaults_to_none(self) -> None:
        for ir in (esp32_example_ir(), nano_example_ir(), HardwareIntermediateRepresentation()):
            self.assertIsNone(ir.firmware)

    def test_unset_field_round_trips_without_appearing_required(self) -> None:
        ir = esp32_example_ir()
        restored = HardwareIntermediateRepresentation.model_validate_json(ir.model_dump_json())
        self.assertIsNone(restored.firmware)
        self.assertEqual(ir, restored)
        schema = HardwareIntermediateRepresentation.model_json_schema()
        self.assertNotIn("firmware", schema.get("required", []))

    def test_pinned_target_round_trips(self) -> None:
        ir = esp32_example_ir()
        ir.firmware = ESP32_TARGET
        restored = HardwareIntermediateRepresentation.model_validate_json(ir.model_dump_json())
        self.assertEqual(ESP32_TARGET, restored.firmware)
        self.assertEqual(ir, restored)

    def test_pinned_target_drives_generation_without_changing_defaults(self) -> None:
        pinned = esp32_example_ir()
        pinned.firmware = ESP32_TARGET
        self.assertEqual(
            generate_firmware(pinned).files,
            generate_firmware(esp32_example_ir(), target=ESP32_TARGET).files,
        )
        # Inference for an unpinned IR still resolves the same board from the
        # controller part number — the field only adds a pin, never a default.
        self.assertEqual(
            generate_firmware(esp32_example_ir()).target,
            ESP32_TARGET,
        )

    def test_pinned_target_overrides_part_number_inference(self) -> None:
        ir = nano_example_ir()
        ir.firmware = ESP32_TARGET
        scaffold = generate_firmware(ir)
        self.assertEqual(scaffold.target, ESP32_TARGET)
        self.assertIn("espressif32", scaffold.files["platformio.ini"])


if __name__ == "__main__":
    unittest.main()
