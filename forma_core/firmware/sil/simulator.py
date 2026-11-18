"""Bounded ctypes calls into compiled firmware with an explicit simulated clock."""
from __future__ import annotations

import ctypes as ct
import hashlib
import json
import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

from .build import source_hash


class DriveProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["servo", "stepper"]
    steps_per_unit: FiniteFloat = Field(default=80, gt=0, le=100000)
    pulse_min_us: int = Field(default=1000, ge=500, le=2499)
    pulse_max_us: int = Field(default=2000, ge=501, le=2500)


class FirmwareProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    format: Literal["forma.firmware-sil"] = "forma.firmware-sil"
    version: Literal[1] = 1
    watchdog_ms: int = Field(default=250, ge=20, le=10000)
    assumptions: str
    axes: dict[str, DriveProfile] = Field(min_length=1, max_length=8)


class AxisConfig(ct.Structure):
    _fields_ = [(name, ct.c_double) for name in ("minimum", "maximum", "neutral", "max_rate", "steps_per_unit", "pulse_min_us", "pulse_max_us")] + [("drive", ct.c_int)]


class AxisState(ct.Structure):
    _fields_ = [(name, ct.c_double) for name in ("simulated", "firmware_position", "target")] + [
        ("signed_steps", ct.c_int64), ("pulses", ct.c_uint64),
        *[(name, ct.c_int) for name in ("pulse_us", "direction", "minimum_limit", "maximum_limit", "stalled")]]


class Status(ct.Structure):
    _fields_ = [(name, ct.c_uint64) for name in ("accepted", "rejected", "sequence")] + [
        (name, ct.c_int) for name in ("enabled", "fault", "reject_reason")]


class NativeSimulator:
    """Owns one firmware instance. Callers serialize access, including close()."""
    def __init__(self, library: Path, axes, profile: FirmwareProfile):
        self.handle = None
        self.library = Path(library).resolve()
        self.manifest = json.loads(self.library.with_name("build-manifest.json").read_text(encoding="utf-8"))
        if self.manifest["abi"] != 1 or self.manifest["source_sha256"] != source_hash():
            raise ValueError("Firmware SIL source changed; rebuild the native library.")
        if hashlib.sha256(self.library.read_bytes()).hexdigest() != self.manifest["library_sha256"]:
            raise ValueError("Firmware SIL library does not match its build manifest.")
        self.axes = tuple(axes)
        if set(profile.axes) != {a.axis_id for a in self.axes}:
            raise ValueError("Firmware profile must cover exactly the machine axes.")
        configs = []
        for axis in self.axes:
            drive = profile.axes[axis.axis_id]
            if (drive.kind == "servo") != (axis.unit == "degree"):
                raise ValueError("Servo profiles use degrees and stepper profiles use mm.")
            if drive.kind == "stepper" and any(not math.isclose(v*drive.steps_per_unit, round(v*drive.steps_per_unit), abs_tol=1e-7) for v in (axis.minimum, axis.maximum, axis.neutral)):
                raise ValueError("Stepper bounds and startup fixture must align with the step grid.")
            configs.append(AxisConfig(axis.minimum, axis.maximum, axis.neutral, axis.max_rate,
                                      drive.steps_per_unit, drive.pulse_min_us, drive.pulse_max_us, int(drive.kind == "stepper")))
        self.dll = ct.CDLL(str(self.library))
        signatures = {
            "abi": (ct.c_int, []),
            "create": (ct.c_void_p, [ct.POINTER(AxisConfig), ct.c_size_t, ct.c_uint32]),
            "destroy": (None, [ct.c_void_p]),
            "feed": (None, [ct.c_void_p, ct.c_char_p, ct.c_size_t, ct.c_uint64]),
            "tick": (None, [ct.c_void_p, ct.c_uint64]),
            "status": (None, [ct.c_void_p, ct.POINTER(Status)]),
            "axis": (None, [ct.c_void_p, ct.c_size_t, ct.POINTER(AxisState)]),
            "inject": (None, [ct.c_void_p, ct.c_size_t, ct.c_int, ct.c_int, ct.c_int]),
        }
        for name, (result, args) in signatures.items():
            fn = getattr(self.dll, "forma_sil_"+name)
            fn.restype, fn.argtypes = result, args
        if self.dll.forma_sil_abi() != 1:
            raise ValueError("Unsupported firmware SIL ABI.")
        self.handle = self.dll.forma_sil_create((AxisConfig*len(configs))(*configs), len(configs), profile.watchdog_ms)
        if not self.handle:
            raise ValueError("Native firmware rejected the drive configuration.")

    def _open(self):
        if not self.handle:
            raise RuntimeError("Firmware simulator is closed.")

    @staticmethod
    def _time(now_ms):
        if type(now_ms) is not int or not 0 <= now_ms <= 2**64-1:
            raise ValueError("Simulation time must be a nonnegative uint64 millisecond count.")

    def feed(self, packet: bytes, now_ms: int):
        self._open(); self._time(now_ms)
        if len(packet) > 4096:
            raise ValueError("Feed at most 4096 bytes per bounded simulation call.")
        self.dll.forma_sil_feed(self.handle, packet, len(packet), now_ms)

    def tick(self, now_ms: int):
        self._open(); self._time(now_ms)
        self.dll.forma_sil_tick(self.handle, now_ms)

    def inject(self, axis: str, *, stalled=False, minimum=False, maximum=False):
        self._open()
        ids = [a.axis_id for a in self.axes]
        self.dll.forma_sil_inject(self.handle, ids.index(axis), int(stalled), int(minimum), int(maximum))

    def snapshot(self):
        self._open()
        status = Status()
        self.dll.forma_sil_status(self.handle, ct.byref(status))
        result = {key: getattr(status, key) for key, _ in status._fields_}
        result["fault"] = {0: None, 1: "firmware_watchdog", 2: "firmware_limit", 3: "firmware_clock"}[result["fault"]]
        result["axes"] = {}
        for i, axis in enumerate(self.axes):
            value = AxisState()
            self.dll.forma_sil_axis(self.handle, i, ct.byref(value))
            result["axes"][axis.axis_id] = {key: getattr(value, key) for key, _ in value._fields_}
        return result

    def close(self):
        if self.handle:
            self.dll.forma_sil_destroy(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __del__(self):
        self.close()
