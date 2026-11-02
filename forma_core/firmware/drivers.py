"""Deterministic driver-stub lookup keyed on seed-catalog part families.

Stubs are scaffolding only — no external library dependencies, no behavior.
Each entry maps a part-number family to a stub C function the generator
emits into ``src/main.cpp``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DriverStub:
    """One deterministic driver-stub template for a part family."""

    family: str
    matchers: tuple[str, ...]  # case-insensitive part-number substrings
    function_prefix: str  # C function name prefix; ref des is appended
    return_type: str  # C return type of the stub
    params: str  # C parameter list, "" for sensors
    param_name: str  # name of the single parameter, "" for sensors
    summary: str  # one-line human description used in stubs and README


DRIVER_STUBS: tuple[DriverStub, ...] = (
    DriverStub(
        family="DHT22",
        matchers=("DHT22", "DHT11"),
        function_prefix="read_dht22",
        return_type="float",
        params="",
        param_name="",
        summary="temperature & humidity (single-wire digital)",
    ),
    DriverStub(
        family="BME280",
        matchers=("BME280",),
        function_prefix="read_bme280",
        return_type="float",
        params="",
        param_name="",
        summary="temperature, humidity & pressure over I2C/SPI",
    ),
    DriverStub(
        family="BMP280",
        matchers=("BMP280",),
        function_prefix="read_bmp280",
        return_type="float",
        params="",
        param_name="",
        summary="barometric pressure & temperature over I2C/SPI",
    ),
    DriverStub(
        family="HC-SR04",
        matchers=("HC-SR04", "HCSR04"),
        function_prefix="read_hc_sr04",
        return_type="long",
        params="",
        param_name="",
        summary="ultrasonic distance in centimeters",
    ),
    DriverStub(
        family="MPU6050",
        matchers=("MPU6050",),
        function_prefix="read_mpu6050",
        return_type="float",
        params="",
        param_name="",
        summary="6-axis accelerometer & gyroscope over I2C",
    ),
    DriverStub(
        family="SSD1306",
        matchers=("SSD1306",),
        function_prefix="draw_ssd1306",
        return_type="void",
        params="",
        param_name="",
        summary="128x64 OLED display over I2C",
    ),
    DriverStub(
        family="LDR",
        matchers=("LDR", "PHOTORESISTOR"),
        function_prefix="read_ldr",
        return_type="int",
        params="",
        param_name="",
        summary="analog light level (ADC counts)",
    ),
    DriverStub(
        family="Servo",
        matchers=("SG90", "SERVO"),
        function_prefix="set_servo_angle",
        return_type="void",
        params="int degrees",
        param_name="degrees",
        summary="PWM hobby-servo angle (0-180)",
    ),
    DriverStub(
        family="Relay",
        matchers=("RELAY",),
        function_prefix="set_relay",
        return_type="void",
        params="bool state",
        param_name="state",
        summary="relay coil on/off control",
    ),
    DriverStub(
        family="LED",
        matchers=("LED",),
        function_prefix="set_led",
        return_type="void",
        params="bool state",
        param_name="state",
        summary="digital indicator LED on/off",
    ),
    DriverStub(
        family="Buzzer",
        matchers=("BUZZER",),
        function_prefix="set_buzzer",
        return_type="void",
        params="bool state",
        param_name="state",
        summary="piezo buzzer on/off",
    ),
)


def lookup_driver(part_number: str) -> DriverStub | None:
    """Return the driver stub for a part number, or None for unknown families.

    Deterministic: families are matched in DRIVER_STUBS declaration order and
    the first substring hit wins.
    """

    candidate = part_number.upper()
    for stub in DRIVER_STUBS:
        if any(matcher in candidate for matcher in stub.matchers):
            return stub
    return None
