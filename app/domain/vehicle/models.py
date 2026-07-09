from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


JsonDict = dict[str, Any]


class CommandSource(str, Enum):
    MANUAL = "MANUAL"
    ATO = "ATO"
    ATP_OVERRIDE = "ATP_OVERRIDE"
    PLATFORM = "PLATFORM"
    MOCK = "MOCK"


def _require_non_empty(value: str, field_name: str) -> None:
    if not value:
        raise ValueError(f"{field_name} must not be empty")


def _require_non_negative(value: float, field_name: str) -> None:
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")


def _require_positive(value: float, field_name: str) -> None:
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")


@dataclass(frozen=True)
class VehicleConfig:
    train_id: str = "T001"
    mass_kg: float = 180_000.0
    max_speed_mps: float = 22.22
    max_traction_level: int = 5
    max_brake_level: int = 5
    traction_force_per_level_n: float = 20_000.0
    brake_force_per_level_n: float = 25_000.0
    emergency_brake_force_n: float = 180_000.0
    basic_resistance_n: float = 3_000.0
    stop_speed_threshold_mps: float = 0.05

    def __post_init__(self) -> None:
        _require_non_empty(self.train_id, "train_id")
        _require_positive(self.mass_kg, "mass_kg")
        _require_positive(self.max_speed_mps, "max_speed_mps")
        _require_positive(self.max_traction_level, "max_traction_level")
        _require_positive(self.max_brake_level, "max_brake_level")
        _require_positive(self.traction_force_per_level_n, "traction_force_per_level_n")
        _require_positive(self.brake_force_per_level_n, "brake_force_per_level_n")
        _require_positive(self.emergency_brake_force_n, "emergency_brake_force_n")
        _require_non_negative(self.basic_resistance_n, "basic_resistance_n")
        _require_positive(self.stop_speed_threshold_mps, "stop_speed_threshold_mps")

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class TrainState:
    train_id: str
    position_m: float
    speed_mps: float
    acceleration_mps2: float
    sim_time_s: float
    segment_id: int | None = None
    net_energy_kwh: float = 0.0

    def __post_init__(self) -> None:
        _require_non_empty(self.train_id, "train_id")
        _require_non_negative(self.position_m, "position_m")
        _require_non_negative(self.speed_mps, "speed_mps")
        _require_non_negative(self.sim_time_s, "sim_time_s")
        _require_non_negative(self.net_energy_kwh, "net_energy_kwh")
        if self.segment_id is not None and self.segment_id <= 0:
            raise ValueError("segment_id must be positive when provided")

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class ControlCommand:
    train_id: str
    traction_level: int = 0
    brake_level: int = 0
    emergency_brake: bool = False
    source: CommandSource = CommandSource.MANUAL

    def __post_init__(self) -> None:
        _require_non_empty(self.train_id, "train_id")
        _require_non_negative(self.traction_level, "traction_level")
        _require_non_negative(self.brake_level, "brake_level")
        if not isinstance(self.source, CommandSource):
            object.__setattr__(self, "source", CommandSource(str(self.source)))
        if self.traction_level > 0 and self.brake_level > 0:
            raise ValueError("traction_level and brake_level cannot both be active")
        if self.emergency_brake and self.traction_level > 0:
            raise ValueError("emergency_brake requires traction_level to be 0")

    @classmethod
    def coast(cls, train_id: str, source: CommandSource = CommandSource.MANUAL) -> ControlCommand:
        return cls(train_id=train_id, source=source)

    def to_dict(self) -> JsonDict:
        payload = asdict(self)
        payload["source"] = self.source.value
        return payload
