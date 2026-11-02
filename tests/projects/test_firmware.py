"""Firmware-target validation rules composed into validate_project.

All firmware findings must be WARNING severity: they surface in diagnostics
without triggering the CRITICAL-gated wiring-repair loop or blocking
generation.
"""

from __future__ import annotations

import unittest

from forma_core.firmware import (
    esp32_example_ir,
    firmware_target_rules,
    nano_example_ir,
)
from forma_core.validation import build_validation_summary, validate_project

FIRMWARE_CATEGORIES = {
    "Firmware: Missing Controller",
    "Firmware: Unknown Pin",
    "Firmware: Bus Pin Mismatch",
}


def _without_controller(ir):
    """Copy of the IR with the microcontroller dropped from the BOM."""

    copy = ir.model_copy(deep=True)
    copy.components = [
        component
        for component in copy.components
        if component.category.strip().lower() != "microcontroller"
    ]
    return copy


class FirmwareTargetRulesTests(unittest.TestCase):
    """Each rule fires with WARNING severity; a clean project fires none."""

    def test_clean_examples_produce_no_issues(self) -> None:
        for ir in (esp32_example_ir(), nano_example_ir()):
            with self.subTest(example=ir.overview.title if ir.overview else ""):
                self.assertEqual(firmware_target_rules(ir), [])

    def test_missing_controller_warns(self) -> None:
        issues = firmware_target_rules(_without_controller(esp32_example_ir()))
        self.assertEqual([issue.category for issue in issues], ["Firmware: Missing Controller"])
        self.assertEqual([issue.severity for issue in issues], ["WARNING"])

    def test_purely_mechanical_bom_is_not_flagged(self) -> None:
        ir = esp32_example_ir().model_copy(deep=True)
        ir.components = []
        ir.nets = []
        ir.pin_mappings = []
        ir.buses = []
        self.assertEqual(firmware_target_rules(ir), [])

    def test_unknown_mcu_pin_warns(self) -> None:
        ir = esp32_example_ir().model_copy(deep=True)
        ir.pin_mappings[0].mcu_pin = "D99"
        issues = firmware_target_rules(ir)
        self.assertEqual([issue.category for issue in issues], ["Firmware: Unknown Pin"])
        self.assertEqual([issue.severity for issue in issues], ["WARNING"])
        self.assertIn("D99", issues[0].description)

    def test_bus_pin_type_mismatch_warns(self) -> None:
        ir = esp32_example_ir().model_copy(deep=True)
        # D26 is a Digital pin; i2c_sda belongs to the I2C bus.
        ir.pin_mappings[0].mcu_pin = "D26"
        ir.pin_mappings[0].net_name = "i2c_sda"
        issues = firmware_target_rules(ir)
        self.assertEqual([issue.category for issue in issues], ["Firmware: Bus Pin Mismatch"])
        self.assertEqual([issue.severity for issue in issues], ["WARNING"])
        self.assertIn("I2C", issues[0].description)

    def test_every_firmware_issue_is_warning(self) -> None:
        fixtures = [
            _without_controller(esp32_example_ir()),
            _without_controller(nano_example_ir()),
        ]
        for ir in (esp32_example_ir(), nano_example_ir()):
            unknown_pin = ir.model_copy(deep=True)
            unknown_pin.pin_mappings[0].mcu_pin = "D99"
            fixtures.append(unknown_pin)
            mismatch = ir.model_copy(deep=True)
            mismatch.pin_mappings[0].mcu_pin = "D26"
            mismatch.pin_mappings[0].net_name = (
                "i2c_sda" if mismatch.buses and mismatch.buses[0].bus_type.upper() == "I2C" else ""
            )
            if mismatch.pin_mappings[0].net_name:
                fixtures.append(mismatch)
        for ir in fixtures:
            for issue in firmware_target_rules(ir):
                with self.subTest(category=issue.category):
                    self.assertEqual(issue.severity, "WARNING")


class ValidateProjectCompositionTests(unittest.TestCase):
    """validate_project composes firmware rules beside the fluidics adapter."""

    def test_clean_project_surfaces_no_firmware_issues(self) -> None:
        issues = validate_project(esp32_example_ir())
        self.assertEqual(
            [issue for issue in issues if issue.category in FIRMWARE_CATEGORIES],
            [],
        )

    def test_firmware_issue_flows_through_validate_project_as_warning(self) -> None:
        ir = _without_controller(esp32_example_ir())
        issues = [issue for issue in validate_project(ir) if issue.category in FIRMWARE_CATEGORIES]
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "WARNING")

    def test_warning_issues_do_not_mark_project_invalid(self) -> None:
        # The generation gate and repair loop key on CRITICAL only; firmware
        # findings must land in the warning bucket of the summary.
        ir = _without_controller(esp32_example_ir())
        summary = build_validation_summary(validate_project(ir))
        firmware_issues = [issue for issue in summary.warning if issue.category in FIRMWARE_CATEGORIES]
        self.assertEqual(len(firmware_issues), 1)
        self.assertEqual(summary.critical, [issue for issue in summary.critical if issue.category not in FIRMWARE_CATEGORIES])


if __name__ == "__main__":
    unittest.main()
