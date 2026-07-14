from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from app.domain.vehicle.models import CommandSource

if TYPE_CHECKING:
    from app.domain.line.services import PathPlan


class OperationMode(str, Enum):
    MANUAL = "MANUAL"
    ATO = "ATO"
    ATP_SUPERVISED = "ATP_SUPERVISED"
    SH = "SH"


class DriverHandleMode(str, Enum):
    NEUTRAL = "NEUTRAL"
    TRACTION = "TRACTION"
    BRAKE = "BRAKE"
    FAST_BRAKE = "FAST_BRAKE"


def _require_percent(value: float, field_name: str) -> None:
    if value < 0 or value > 100:
        raise ValueError(f"{field_name} must be between 0 and 100")


@dataclass(frozen=True)
class DriverInput:
    train_id: str
    handle_mode: DriverHandleMode = DriverHandleMode.NEUTRAL
    traction_percent: float = 0.0
    brake_percent: float = 0.0
    emergency_brake: bool = False
    reported_speed_mps: float | None = None
    source: str = "PLC"

    def __post_init__(self) -> None:
        if not self.train_id:
            raise ValueError("train_id must not be empty")
        if not isinstance(self.handle_mode, DriverHandleMode):
            object.__setattr__(self, "handle_mode", DriverHandleMode(str(self.handle_mode)))
        _require_percent(self.traction_percent, "traction_percent")
        _require_percent(self.brake_percent, "brake_percent")
        if self.reported_speed_mps is not None and self.reported_speed_mps < 0:
            raise ValueError("reported_speed_mps must be non-negative")
        if not self.source:
            raise ValueError("source must not be empty")

    def to_command_source(self) -> CommandSource:
        return CommandSource.MANUAL


@dataclass(frozen=True)
class AtoConfig:
    target_cruise_speed_mps: float = 12.0
    expected_deceleration_mps2: float = 0.8
    brake_margin_m: float = 20.0
    stop_tolerance_m: float = 1.0
    hold_brake_percent: float = 20.0
    max_traction_percent: float = 100.0
    max_brake_percent: float = 100.0
    stop_speed_threshold_mps: float = 0.05
    control_period_s: float = 1.0
    pid_kp: float = 0.55
    pid_ki: float = 0.005
    pid_kd: float = 0.08
    pid_output_percent_per_unit: float = 25.0
    pid_integral_limit: float = 25.0
    pid_derivative_filter_ratio: float = 0.85
    pid_deadband_mps: float = 0.06
    brake_engage_error_mps: float = 0.12
    brake_release_error_mps: float = 0.03
    brake_hysteresis_hold_percent: float = 3.0
    service_brake_trigger_margin_mps: float = 0.35
    traction_slew_rate_percent_per_s: float = 30.0
    brake_apply_slew_rate_percent_per_s: float = 40.0
    brake_release_slew_rate_percent_per_s: float = 18.0
    low_speed_brake_guard_speed_mps: float = 2.0
    terminal_brake_guard_margin_m: float = 35.0
    terminal_brake_floor_percent: float = 8.0
    terminal_brake_floor_speed_mps: float = 1.0
    creep_distance_m: float = 5.0
    creep_speed_threshold_mps: float = 0.15
    creep_traction_percent: float = 3.0
    creep_neutral_time_s: float = 0.5
    use_dynamic_programming_profile: bool = True
    profile_run_time_s: float | None = None
    profile_runtime_margin_ratio: float = 1.18
    profile_time_step_s: float = 1.0
    profile_position_step_m: float = 5.0
    profile_speed_step_mps: float = 0.5
    profile_lookahead_m: float = 5.0
    profile_feedforward_full_error_mps: float = 0.35
    profile_traction_timing_bias_s: float = 0.0
    profile_brake_timing_bias_s: float = 0.0
    profile_max_states_per_stage: int = 1800

    def __post_init__(self) -> None:
        if self.target_cruise_speed_mps <= 0:
            raise ValueError("target_cruise_speed_mps must be positive")
        if self.expected_deceleration_mps2 <= 0:
            raise ValueError("expected_deceleration_mps2 must be positive")
        if self.brake_margin_m < 0:
            raise ValueError("brake_margin_m must be non-negative")
        if self.stop_tolerance_m <= 0:
            raise ValueError("stop_tolerance_m must be positive")
        _require_percent(self.hold_brake_percent, "hold_brake_percent")
        _require_percent(self.max_traction_percent, "max_traction_percent")
        _require_percent(self.max_brake_percent, "max_brake_percent")
        if self.hold_brake_percent <= 0:
            raise ValueError("hold_brake_percent must be positive")
        if self.max_traction_percent <= 0:
            raise ValueError("max_traction_percent must be positive")
        if self.max_brake_percent <= 0:
            raise ValueError("max_brake_percent must be positive")
        if self.stop_speed_threshold_mps <= 0:
            raise ValueError("stop_speed_threshold_mps must be positive")
        if self.control_period_s <= 0:
            raise ValueError("control_period_s must be positive")
        if self.pid_output_percent_per_unit <= 0:
            raise ValueError("pid_output_percent_per_unit must be positive")
        if self.pid_integral_limit <= 0:
            raise ValueError("pid_integral_limit must be positive")
        if self.pid_derivative_filter_ratio < 0 or self.pid_derivative_filter_ratio >= 1:
            raise ValueError("pid_derivative_filter_ratio must be in [0, 1)")
        if self.pid_deadband_mps < 0:
            raise ValueError("pid_deadband_mps must be non-negative")
        if self.brake_engage_error_mps <= 0:
            raise ValueError("brake_engage_error_mps must be positive")
        if self.brake_release_error_mps < 0:
            raise ValueError("brake_release_error_mps must be non-negative")
        if self.brake_release_error_mps >= self.brake_engage_error_mps:
            raise ValueError("brake_release_error_mps must be less than brake_engage_error_mps")
        _require_percent(self.brake_hysteresis_hold_percent, "brake_hysteresis_hold_percent")
        if self.brake_hysteresis_hold_percent <= 0:
            raise ValueError("brake_hysteresis_hold_percent must be positive")
        if self.service_brake_trigger_margin_mps < 0:
            raise ValueError("service_brake_trigger_margin_mps must be non-negative")
        if self.traction_slew_rate_percent_per_s <= 0:
            raise ValueError("traction_slew_rate_percent_per_s must be positive")
        if self.brake_apply_slew_rate_percent_per_s <= 0:
            raise ValueError("brake_apply_slew_rate_percent_per_s must be positive")
        if self.brake_release_slew_rate_percent_per_s <= 0:
            raise ValueError("brake_release_slew_rate_percent_per_s must be positive")
        if self.low_speed_brake_guard_speed_mps <= self.creep_speed_threshold_mps:
            raise ValueError("low_speed_brake_guard_speed_mps must exceed creep_speed_threshold_mps")
        if self.terminal_brake_guard_margin_m < 0:
            raise ValueError("terminal_brake_guard_margin_m must be non-negative")
        _require_percent(self.terminal_brake_floor_percent, "terminal_brake_floor_percent")
        if self.terminal_brake_floor_percent <= 0:
            raise ValueError("terminal_brake_floor_percent must be positive")
        if self.terminal_brake_floor_speed_mps <= self.stop_speed_threshold_mps:
            raise ValueError("terminal_brake_floor_speed_mps must exceed stop_speed_threshold_mps")
        if self.creep_distance_m < self.stop_tolerance_m:
            raise ValueError("creep_distance_m must be at least stop_tolerance_m")
        if self.creep_speed_threshold_mps <= self.stop_speed_threshold_mps:
            raise ValueError("creep_speed_threshold_mps must exceed stop_speed_threshold_mps")
        _require_percent(self.creep_traction_percent, "creep_traction_percent")
        if self.creep_neutral_time_s < 0:
            raise ValueError("creep_neutral_time_s must be non-negative")
        if self.profile_run_time_s is not None and self.profile_run_time_s <= 0:
            raise ValueError("profile_run_time_s must be positive when provided")
        if self.profile_runtime_margin_ratio <= 0:
            raise ValueError("profile_runtime_margin_ratio must be positive")
        if self.profile_time_step_s <= 0:
            raise ValueError("profile_time_step_s must be positive")
        if self.profile_position_step_m <= 0:
            raise ValueError("profile_position_step_m must be positive")
        if self.profile_speed_step_mps <= 0:
            raise ValueError("profile_speed_step_mps must be positive")
        if self.profile_lookahead_m < 0:
            raise ValueError("profile_lookahead_m must be non-negative")
        if self.profile_feedforward_full_error_mps <= self.pid_deadband_mps:
            raise ValueError(
                "profile_feedforward_full_error_mps must exceed pid_deadband_mps"
            )
        if abs(self.profile_traction_timing_bias_s) > 5.0:
            raise ValueError("profile_traction_timing_bias_s must be within +/- 5 seconds")
        if abs(self.profile_brake_timing_bias_s) > 5.0:
            raise ValueError("profile_brake_timing_bias_s must be within +/- 5 seconds")
        if self.profile_max_states_per_stage <= 0:
            raise ValueError("profile_max_states_per_stage must be positive")


@dataclass(frozen=True)
class AtoTarget:
    target_position_m: float
    permitted_speed_mps: float
    emergency_brake_required: bool = False
    path_plan: PathPlan | None = None

    def __post_init__(self) -> None:
        if self.target_position_m < 0:
            raise ValueError("target_position_m must be non-negative")
        if self.permitted_speed_mps <= 0:
            raise ValueError("permitted_speed_mps must be positive")
