from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OperationMode(str, Enum):
    MANUAL = "MANUAL"
    ATO = "ATO"
    ATP_SUPERVISED = "ATP_SUPERVISED"
    SH = "SH"


@dataclass(frozen=True)
class AtoConfig:
    target_cruise_speed_mps: float = 12.0
    expected_deceleration_mps2: float = 0.8
    brake_margin_m: float = 20.0
    stop_tolerance_m: float = 1.0
    hold_brake_level: int = 1
    max_traction_level: int = 4
    max_brake_level: int = 4
    stop_speed_threshold_mps: float = 0.05

    def __post_init__(self) -> None:
        if self.target_cruise_speed_mps <= 0:
            raise ValueError("target_cruise_speed_mps must be positive")
        if self.expected_deceleration_mps2 <= 0:
            raise ValueError("expected_deceleration_mps2 must be positive")
        if self.brake_margin_m < 0:
            raise ValueError("brake_margin_m must be non-negative")
        if self.stop_tolerance_m <= 0:
            raise ValueError("stop_tolerance_m must be positive")
        if self.hold_brake_level <= 0:
            raise ValueError("hold_brake_level must be positive")
        if self.max_traction_level <= 0:
            raise ValueError("max_traction_level must be positive")
        if self.max_brake_level <= 0:
            raise ValueError("max_brake_level must be positive")
        if self.stop_speed_threshold_mps <= 0:
            raise ValueError("stop_speed_threshold_mps must be positive")


@dataclass(frozen=True)
class AtoTarget:
    target_position_m: float
    permitted_speed_mps: float
    emergency_brake_required: bool = False

    def __post_init__(self) -> None:
        if self.target_position_m < 0:
            raise ValueError("target_position_m must be non-negative")
        if self.permitted_speed_mps <= 0:
            raise ValueError("permitted_speed_mps must be positive")
