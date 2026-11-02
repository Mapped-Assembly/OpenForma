"""Pure data models for deterministic firmware scaffolds — no I/O."""

from __future__ import annotations

from pydantic import BaseModel, Field


class FirmwareTarget(BaseModel):
    """PlatformIO build target for a generated scaffold."""

    board_id: str = Field(..., description='PlatformIO board id, e.g. "esp32dev" or "nanoatmega328"')
    framework: str = Field("arduino", description='Framework, e.g. "arduino"')
    platform: str = Field(..., description='PlatformIO platform, e.g. "espressif32" or "atmelavr"')


class FirmwareScaffold(BaseModel):
    """A deterministic, board-specific firmware scaffold.

    Data model first, rendered second: files is path → content so future
    renderers (or an LLM-authored sketch mode) compose over the same object.
    """

    target: FirmwareTarget | None = Field(
        None,
        description="Resolved build target; None only for an empty no-MCU scaffold",
    )
    files: dict[str, str] = Field(
        default_factory=dict,
        description="path → content, e.g. 'platformio.ini', 'include/pins.h', 'src/main.cpp', 'README.md'",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-blocking generation notes, e.g. 'no MCU in BOM — generated empty scaffold'",
    )
