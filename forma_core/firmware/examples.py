"""Example hardware IRs for ESP32 and Arduino Nano, built from seed pinouts.

Pin data mirrors ``apps/api/seed_db.py`` (forma_core must not import apps —
the core package ships standalone). tests/firmware/test_generate.py asserts
the example MCU pinouts stay byte-equal to the seed catalog so this copy
cannot rot silently.
"""

from __future__ import annotations

from forma_core.workspaces.projects.models import (
    BusConnection,
    ComponentInstance,
    ConnectionNet,
    FunctionalRequirements,
    HardwareIntermediateRepresentation,
    PinDefinition,
    PinMappingEntry,
    PinReference,
    ProjectOverview,
)

# --- Seed-catalog pinouts (source: apps/api/seed_db.py SEED_COMPONENTS) ---


def _pin(pin_id: str, name: str, pin_type: str, voltage: float | None = None) -> PinDefinition:
    return PinDefinition(pin_id=pin_id, name=name, pin_type=pin_type, voltage=voltage)


def _esp32_pins() -> list[PinDefinition]:
    return [
        _pin("3V3", "3.3V Power Out", "Power", 3.3),
        _pin("GND", "Ground", "Ground", 0.0),
        _pin("EN", "Enable / Reset", "Passive", 3.3),
        _pin("VP", "GPIO36 / ADC_CH0", "Analog", 3.3),
        _pin("VN", "GPIO39 / ADC_CH3", "Analog", 3.3),
        _pin("D34", "GPIO34 / ADC_CH6", "Analog", 3.3),
        _pin("D35", "GPIO35 / ADC_CH7", "Analog", 3.3),
        _pin("D32", "GPIO32 / ADC_CH4", "Digital", 3.3),
        _pin("D33", "GPIO33 / ADC_CH5", "Digital", 3.3),
        _pin("D25", "GPIO25 / DAC_CH1", "Digital", 3.3),
        _pin("D26", "GPIO26 / DAC_CH2", "Digital", 3.3),
        _pin("D27", "GPIO27 / ADC_CH17", "Digital", 3.3),
        _pin("D14", "GPIO14 / SPI_CLK", "SPI", 3.3),
        _pin("D12", "GPIO12 / SPI_MISO", "SPI", 3.3),
        _pin("D13", "GPIO13 / SPI_MOSI", "SPI", 3.3),
        _pin("D23", "GPIO23 / MOSI", "SPI", 3.3),
        _pin("D22", "GPIO22 / I2C_SCL", "I2C", 3.3),
        _pin("D21", "GPIO21 / I2C_SDA", "I2C", 3.3),
        _pin("TXD", "GPIO1 / UART_TX", "UART", 3.3),
        _pin("RXD", "GPIO3 / UART_RX", "UART", 3.3),
        _pin("D19", "GPIO19 / MISO", "SPI", 3.3),
        _pin("D18", "GPIO18 / SCK", "SPI", 3.3),
        _pin("D5", "GPIO5 / SS", "SPI", 3.3),
        _pin("VIN", "External Power In", "Power", 5.0),
    ]


def _nano_pins() -> list[PinDefinition]:
    return [
        _pin("5V", "5V Power Out", "Power", 5.0),
        _pin("3V3", "3.3V Power Out", "Power", 3.3),
        _pin("GND", "Ground", "Ground", 0.0),
        _pin("VIN", "Voltage Input", "Power", 12.0),
        _pin("A0", "Analog 0", "Analog", 5.0),
        _pin("A1", "Analog 1", "Analog", 5.0),
        _pin("A2", "Analog 2", "Analog", 5.0),
        _pin("A3", "Analog 3", "Analog", 5.0),
        _pin("A4", "Analog 4 / I2C_SDA", "I2C", 5.0),
        _pin("A5", "Analog 5 / I2C_SCL", "I2C", 5.0),
        _pin("D2", "Digital 2 / INT0", "Digital", 5.0),
        _pin("D3", "Digital 3 / PWM", "PWM", 5.0),
        _pin("D4", "Digital 4", "Digital", 5.0),
        _pin("D5", "Digital 5 / PWM", "PWM", 5.0),
        _pin("D6", "Digital 6 / PWM", "PWM", 5.0),
        _pin("D7", "Digital 7", "Digital", 5.0),
        _pin("D8", "Digital 8", "Digital", 5.0),
        _pin("D9", "Digital 9 / PWM", "PWM", 5.0),
        _pin("D10", "Digital 10 / SPI_SS", "SPI", 5.0),
        _pin("D11", "Digital 11 / SPI_MOSI", "SPI", 5.0),
        _pin("D12", "Digital 12 / SPI_MISO", "SPI", 5.0),
        _pin("D13", "Digital 13 / Built-in LED", "Digital", 5.0),
    ]


def _dht22_pins() -> list[PinDefinition]:
    return [
        _pin("VCC", "VCC Power", "Power", 3.3),
        _pin("DATA", "Signal Out", "Digital", 3.3),
        _pin("NC", "No Connection", "Passive", 0.0),
        _pin("GND", "Ground", "Ground", 0.0),
    ]


def _hcsr04_pins() -> list[PinDefinition]:
    return [
        _pin("VCC", "5V Power Supply", "Power", 5.0),
        _pin("TRIG", "Trigger Input", "Digital", 5.0),
        _pin("ECHO", "Echo Output", "Digital", 5.0),
        _pin("GND", "Ground", "Ground", 0.0),
    ]


def _mpu6050_pins() -> list[PinDefinition]:
    return [
        _pin("VCC", "VCC Power (3.3V or 5V)", "Power", 3.3),
        _pin("GND", "Ground", "Ground", 0.0),
        _pin("SCL", "I2C Serial Clock", "I2C", 3.3),
        _pin("SDA", "I2C Serial Data", "I2C", 3.3),
        _pin("XDA", "Aux I2C Data", "I2C", 3.3),
        _pin("XCL", "Aux I2C Clock", "I2C", 3.3),
        _pin("AD0", "I2C Address Select", "Digital", 3.3),
        _pin("INT", "Interrupt Out", "Digital", 3.3),
    ]


def _ssd1306_pins() -> list[PinDefinition]:
    return [
        _pin("VCC", "Power VCC", "Power", 3.3),
        _pin("GND", "Ground", "Ground", 0.0),
        _pin("SCL", "I2C Serial Clock", "I2C", 3.3),
        _pin("SDA", "I2C Serial Data", "I2C", 3.3),
    ]


def _servo_pins() -> list[PinDefinition]:
    return [
        _pin("5V", "Power VCC (Red)", "Power", 5.0),
        _pin("GND", "Ground (Brown)", "Ground", 0.0),
        _pin("PWM", "Control Signal (Orange)", "PWM", 5.0),
    ]


def _relay_pins() -> list[PinDefinition]:
    return [
        _pin("VCC", "Module Power (5V)", "Power", 5.0),
        _pin("GND", "Module Ground", "Ground", 0.0),
        _pin("IN", "Signal Input", "Digital", 5.0),
        _pin("COM", "Switch Common Terminal", "Passive", 250.0),
        _pin("NO", "Switch Normally Open Terminal", "Passive", 250.0),
        _pin("NC", "Switch Normally Closed Terminal", "Passive", 250.0),
    ]


def _led_pins() -> list[PinDefinition]:
    return [
        _pin("ANODE", "Anode (+) Long Lead", "Passive", 2.0),
        _pin("CATHODE", "Cathode (-) Flat Lead", "Ground", 0.0),
    ]


def _usb_plug_pins() -> list[PinDefinition]:
    return [
        _pin("5V", "5V Power Line", "Power", 5.0),
        _pin("GND", "Ground", "Ground", 0.0),
    ]


def _component(
    ref_des: str,
    part_number: str,
    name: str,
    category: str,
    unit_price: float,
    pins: list[PinDefinition],
    rationale: str,
) -> ComponentInstance:
    return ComponentInstance(
        ref_des=ref_des,
        part_number=part_number,
        name=name,
        category=category,
        unit_price=unit_price,
        pins=pins,
        rationale=rationale,
    )


def _net(net_id: str, net_type: str, voltage: float | None, *pins: tuple[str, str]) -> ConnectionNet:
    return ConnectionNet(
        net_id=net_id,
        name=net_id,
        net_type=net_type,
        voltage=voltage,
        pins=[PinReference(ref_des=ref_des, pin_id=pin_id) for ref_des, pin_id in pins],
    )


def esp32_example_ir() -> HardwareIntermediateRepresentation:
    """Greenhouse monitor: ESP32 + DHT22 + relay + I2C sensors + status LED."""

    return HardwareIntermediateRepresentation(
        overview=ProjectOverview(
            title="greenhouse-monitor",
            description="Greenhouse temperature, humidity, and irrigation monitoring with a pump relay and status LED.",
            difficulty="Beginner",
            estimated_cost=14.80,
            category="IoT",
        ),
        requirements=FunctionalRequirements(
            requirements=[
                "Read temperature and humidity from a DHT22",
                "Display motion and environment status on a 0.96 inch OLED",
                "Switch an irrigation pump with a 5V relay",
            ],
            power_needs="5V USB",
            operating_voltage=3.3,
        ),
        components=[
            _component(
                "U1",
                "ESP32-WROOM-32D",
                "ESP32 NodeMCU Development Board",
                "Microcontroller",
                4.50,
                _esp32_pins(),
                "WiFi-connected controller for the greenhouse monitor.",
            ),
            _component(
                "SEN1",
                "DHT22",
                "DHT22 Temperature & Humidity Sensor",
                "Sensor",
                2.80,
                _dht22_pins(),
                "Measures greenhouse air temperature and humidity.",
            ),
            _component(
                "MPU1",
                "MPU6050",
                "MPU-6050 6-Axis Accelerometer & Gyroscope",
                "Sensor",
                2.20,
                _mpu6050_pins(),
                "Detects enclosure tamper or shelf vibration.",
            ),
            _component(
                "OLED1",
                "SSD1306-I2C",
                "0.96 inch OLED Display (I2C)",
                "Display",
                2.50,
                _ssd1306_pins(),
                "Local status display for readings.",
            ),
            _component(
                "K1",
                "Relay-5V-1Ch",
                "5V 1-Channel Optocoupled Relay Module",
                "Actuator",
                1.20,
                _relay_pins(),
                "Switches the irrigation pump.",
            ),
            _component(
                "LED1",
                "LED-Red-Generic",
                "Standard Red LED (5mm)",
                "Passives",
                0.10,
                _led_pins(),
                "Status indicator for pump activity.",
            ),
            _component(
                "PWR1",
                "USB-5V-Plug",
                "5V USB Wall Power Supply",
                "Power",
                1.50,
                _usb_plug_pins(),
                "Mains power for the controller and relay.",
            ),
        ],
        nets=[
            _net("rail_5v", "Power", 5.0, ("PWR1", "5V"), ("U1", "VIN"), ("K1", "VCC")),
            _net(
                "rail_3v3",
                "Power",
                3.3,
                ("U1", "3V3"),
                ("SEN1", "VCC"),
                ("MPU1", "VCC"),
                ("OLED1", "VCC"),
            ),
            _net(
                "gnd",
                "Ground",
                0.0,
                ("PWR1", "GND"),
                ("U1", "GND"),
                ("SEN1", "GND"),
                ("MPU1", "GND"),
                ("OLED1", "GND"),
                ("K1", "GND"),
                ("LED1", "CATHODE"),
            ),
            _net("sensor_data", "Digital", 3.3, ("U1", "D26"), ("SEN1", "DATA")),
            _net("pump_ctrl", "Digital", 3.3, ("U1", "D25"), ("K1", "IN")),
            _net("status_led", "Digital", 3.3, ("U1", "D27"), ("LED1", "ANODE")),
            _net(
                "i2c_sda",
                "I2C",
                3.3,
                ("U1", "D21"),
                ("MPU1", "SDA"),
                ("OLED1", "SDA"),
            ),
            _net(
                "i2c_scl",
                "I2C",
                3.3,
                ("U1", "D22"),
                ("MPU1", "SCL"),
                ("OLED1", "SCL"),
            ),
            _net("debug_tx", "UART", 3.3, ("U1", "TXD")),
            _net("debug_rx", "UART", 3.3, ("U1", "RXD")),
        ],
        buses=[
            BusConnection(
                bus_id="BUS_I2C_1",
                bus_type="I2C",
                clock_frequency_hz=100000.0,
                nets=["i2c_sda", "i2c_scl"],
            ),
            BusConnection(bus_id="BUS_UART_1", bus_type="UART", nets=["debug_tx", "debug_rx"]),
        ],
        pin_mappings=[
            PinMappingEntry(mcu_pin="D26", connected_to="DHT22 (SEN1).DATA", net_name="sensor_data"),
            PinMappingEntry(
                mcu_pin="D25",
                connected_to="5V 1-Channel Optocoupled Relay Module (K1).IN",
                net_name="pump_ctrl",
            ),
            PinMappingEntry(
                mcu_pin="D27",
                connected_to="Standard Red LED (5mm) (LED1).ANODE",
                net_name="status_led",
            ),
            PinMappingEntry(
                mcu_pin="D21",
                connected_to=(
                    "MPU-6050 (MPU1).SDA, 0.96 inch OLED Display (I2C) (OLED1).SDA"
                ),
                net_name="i2c_sda",
            ),
            PinMappingEntry(
                mcu_pin="D22",
                connected_to=(
                    "MPU-6050 (MPU1).SCL, 0.96 inch OLED Display (I2C) (OLED1).SCL"
                ),
                net_name="i2c_scl",
            ),
        ],
    )


def nano_example_ir() -> HardwareIntermediateRepresentation:
    """Plant-watering timer: Nano + HC-SR04 + servo + I2C OLED + status LED."""

    return HardwareIntermediateRepresentation(
        overview=ProjectOverview(
            title="plant-watering-timer",
            description="Timed plant watering with a reservoir level check, servo valve, and status LED.",
            difficulty="Beginner",
            estimated_cost=10.80,
            category="Automation",
        ),
        requirements=FunctionalRequirements(
            requirements=[
                "Check water reservoir distance with an HC-SR04",
                "Display timer status on a 0.96 inch OLED",
                "Actuate the watering valve with an SG90 servo",
            ],
            power_needs="5V USB",
            operating_voltage=5.0,
        ),
        components=[
            _component(
                "U1",
                "Arduino-Nano-V3",
                "Arduino Nano v3.0",
                "Microcontroller",
                3.20,
                _nano_pins(),
                "Compact controller for the watering timer.",
            ),
            _component(
                "SEN1",
                "HC-SR04",
                "Ultrasonic Distance Sensor HC-SR04",
                "Sensor",
                1.50,
                _hcsr04_pins(),
                "Measures reservoir water level.",
            ),
            _component(
                "OLED1",
                "SSD1306-I2C",
                "0.96 inch OLED Display (I2C)",
                "Display",
                2.50,
                _ssd1306_pins(),
                "Shows timer countdown and level readings.",
            ),
            _component(
                "SG1",
                "SG90-Servo",
                "SG90 Micro Servo Motor",
                "Actuator",
                2.00,
                _servo_pins(),
                "Opens and closes the watering valve.",
            ),
            _component(
                "LED1",
                "LED-Red-Generic",
                "Standard Red LED (5mm)",
                "Passives",
                0.10,
                _led_pins(),
                "Blinks while a watering cycle runs.",
            ),
            _component(
                "PWR1",
                "USB-5V-Plug",
                "5V USB Wall Power Supply",
                "Power",
                1.50,
                _usb_plug_pins(),
                "Mains power for the timer.",
            ),
        ],
        nets=[
            _net(
                "rail_5v",
                "Power",
                5.0,
                ("PWR1", "5V"),
                ("U1", "5V"),
                ("SEN1", "VCC"),
                ("OLED1", "VCC"),
                ("SG1", "5V"),
            ),
            _net(
                "gnd",
                "Ground",
                0.0,
                ("PWR1", "GND"),
                ("U1", "GND"),
                ("SEN1", "GND"),
                ("OLED1", "GND"),
                ("SG1", "GND"),
                ("LED1", "CATHODE"),
            ),
            _net("sonar_trig", "Digital", 5.0, ("U1", "D2"), ("SEN1", "TRIG")),
            _net("sonar_echo", "Digital", 5.0, ("U1", "D3"), ("SEN1", "ECHO")),
            _net("servo_pwm", "PWM", 5.0, ("U1", "D9"), ("SG1", "PWM")),
            _net("status_led", "Digital", 5.0, ("U1", "D13"), ("LED1", "ANODE")),
            _net("i2c_sda", "I2C", 5.0, ("U1", "A4"), ("OLED1", "SDA")),
            _net("i2c_scl", "I2C", 5.0, ("U1", "A5"), ("OLED1", "SCL")),
        ],
        buses=[
            BusConnection(
                bus_id="BUS_I2C_1",
                bus_type="I2C",
                clock_frequency_hz=100000.0,
                nets=["i2c_sda", "i2c_scl"],
            ),
        ],
        pin_mappings=[
            PinMappingEntry(
                mcu_pin="D2",
                connected_to="Ultrasonic Distance Sensor HC-SR04 (SEN1).TRIG",
                net_name="sonar_trig",
            ),
            PinMappingEntry(
                mcu_pin="D3",
                connected_to="Ultrasonic Distance Sensor HC-SR04 (SEN1).ECHO",
                net_name="sonar_echo",
            ),
            PinMappingEntry(
                mcu_pin="D9",
                connected_to="SG90 Micro Servo Motor (SG1).PWM",
                net_name="servo_pwm",
            ),
            PinMappingEntry(
                mcu_pin="D13",
                connected_to="Standard Red LED (5mm) (LED1).ANODE",
                net_name="status_led",
            ),
            PinMappingEntry(
                mcu_pin="A4",
                connected_to="0.96 inch OLED Display (I2C) (OLED1).SDA",
                net_name="i2c_sda",
            ),
            PinMappingEntry(
                mcu_pin="A5",
                connected_to="0.96 inch OLED Display (I2C) (OLED1).SCL",
                net_name="i2c_scl",
            ),
        ],
    )
