"""Existing domain value types, re-exported by the legacy project model."""

from typing import Any, List, Literal, Mapping, Optional
from pydantic import BaseModel, Field, field_validator


class PinDefinition(BaseModel):
    pin_id: str = Field(..., description="Unique pin identifier, e.g., '1', 'GND', 'D13'")
    name: str = Field(..., description="Pin functional name, e.g., 'VCC', 'TX', 'GPIO4'")
    pin_type: str = Field(..., description="Type of pin: Power, Ground, Digital, Analog, I2C, SPI, UART, PWM, Passive")
    voltage: Optional[float] = Field(None, description="Operating voltage of the pin in Volts, e.g., 3.3 or 5.0")
    description: Optional[str] = Field(None, description="Detailed description of the pin function")
    direction: Optional[str] = Field(None, description="Signal direction such as input, output, or bidirectional")
    power_role: Optional[str] = Field(None, description="Power role such as input, source, regulated_output, or return")
    interface: Optional[str] = Field(None, description="Logical interface such as I2C, SPI, UART, PWM, or analog")

class PinReference(BaseModel):
    ref_des: str = Field(..., description="Component reference designator, e.g., 'U1'")
    pin_id: str = Field(..., description="Pin ID on the target component, e.g., 'GND' or '12'")

class ConnectionNet(BaseModel):
    net_id: str = Field(..., description="Unique ID for the electrical net, e.g., 'NET_VCC', 'NET_I2C_SDA'")
    name: str = Field(..., description="Friendly net name, e.g., '3.3V Power Rail', 'I2C Data'")
    net_type: str = Field(..., description="Net type: Power, Ground, Analog, Digital, I2C, SPI, UART, PWM")
    voltage: Optional[float] = Field(None, description="Expected voltage of this net, e.g., 3.3")
    pins: List[PinReference] = Field(default_factory=list, description="All component pins tied to this net")

class PinMappingEntry(BaseModel):
    mcu_pin: str = Field(..., description="MCU pin identifier, e.g., 'GPIO23'")
    connected_to: str = Field(..., description="Name of the sensor pin/function connected, e.g., 'DHT22 Data'")
    net_name: str = Field(..., description="Electrical net name, e.g., 'DHT_SDA_NET'")

class BusConnection(BaseModel):
    bus_id: str = Field(..., description="Unique ID for the digital communication bus, e.g., 'BUS_I2C_1'")
    bus_type: str = Field(..., description="Bus type: I2C, SPI, UART, CAN")
    clock_frequency_hz: Optional[float] = Field(None, description="Operating bus speed if applicable")
    nets: List[str] = Field(default_factory=list, description="Electrical net IDs associated with this bus")

class PowerRail(BaseModel):
    rail_id: str = Field(..., description="Unique ID for power rail, e.g., 'RAIL_3V3'")
    voltage: float = Field(..., description="Nominal operating voltage in Volts")
    max_current_capacity_ma: float = Field(..., description="Maximum continuous current capacity in mA")
    source_component: str = Field(..., description="Reference designator of the power source component")
