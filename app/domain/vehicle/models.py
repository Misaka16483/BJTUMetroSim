from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from app.domain.signal.models import TrainState  # canonical B/C/D train state

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


def _require_percent(value: float, field_name: str) -> None:
    if value < 0 or value > 100:
        raise ValueError(f"{field_name} must be between 0 and 100")


@dataclass(frozen=True)
class VehicleConfig:
    train_id: str = "T001"
    mass_kg: float = 180_000.0
    max_speed_mps: float = 22.22
    max_traction_force_n: float = 100_000.0
    max_service_brake_force_n: float = 125_000.0
    emergency_brake_force_n: float = 180_000.0
    basic_resistance_n: float = 3_000.0
    stop_speed_threshold_mps: float = 0.05

    def __post_init__(self) -> None:
        _require_non_empty(self.train_id, "train_id")
        _require_positive(self.mass_kg, "mass_kg")
        _require_positive(self.max_speed_mps, "max_speed_mps")
        _require_positive(self.max_traction_force_n, "max_traction_force_n")
        _require_positive(self.max_service_brake_force_n, "max_service_brake_force_n")
        _require_positive(self.emergency_brake_force_n, "emergency_brake_force_n")
        _require_non_negative(self.basic_resistance_n, "basic_resistance_n")
        _require_positive(self.stop_speed_threshold_mps, "stop_speed_threshold_mps")

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class ControlCommand:
    train_id: str
    traction_percent: float = 0.0
    brake_percent: float = 0.0
    emergency_brake: bool = False
    source: CommandSource = CommandSource.MANUAL

    def __post_init__(self) -> None:
        _require_non_empty(self.train_id, "train_id")
        _require_percent(self.traction_percent, "traction_percent")
        _require_percent(self.brake_percent, "brake_percent")
        if not isinstance(self.source, CommandSource):
            object.__setattr__(self, "source", CommandSource(str(self.source)))
        if self.traction_percent > 0 and self.brake_percent > 0:
            raise ValueError("traction_percent and brake_percent cannot both be active")
        if self.emergency_brake and self.traction_percent > 0:
            raise ValueError("emergency_brake requires traction_percent to be 0")

    @classmethod
    def coast(cls, train_id: str, source: CommandSource = CommandSource.MANUAL) -> ControlCommand:
        return cls(train_id=train_id, source=source)

    def to_dict(self) -> JsonDict:
        payload = asdict(self)
        payload["source"] = self.source.value
        return payload
