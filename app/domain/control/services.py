from __future__ import annotations

from app.domain.control.models import AtoConfig, AtoTarget, DriverHandleMode, DriverInput, OperationMode
from app.domain.control.speed_profile import (
    OptimizedSpeedProfile,
    estimate_scheduled_run_time_s,
    optimize_speed_profile_dcdp,
    stopping_target_speed_mps,
)
from app.domain.vehicle.models import CommandSource, ControlCommand, TrainState, VehicleConfig


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)


class ATOController:
    def __init__(self, config: AtoConfig | None = None) -> None:
        self.config = config or AtoConfig()
        self._last_train_id: str | None = None
        self._last_sim_time_s: float | None = None
        self._last_error_mps: float | None = None
        self._integral_error: float = 0.0
        self._filtered_derivative: float = 0.0
        self._profile_cache_key: tuple[object, ...] | None = None
        self._profile_cache: OptimizedSpeedProfile | None = None
        self.last_target_speed_mps: float = 0.0
        self.last_speed_error_mps: float = 0.0
        self.last_pid_output_percent: float = 0.0
        self.last_profile_mode: str = "NONE"

    def decide(self, state: TrainState, target: AtoTarget) -> ControlCommand:
        if target.emergency_brake_required:
            self.reset()
            return ControlCommand(state.train_id, emergency_brake=True, source=CommandSource.ATO)

        target_position_m = self._target_position_m(target)
        distance_to_target_m = max(0.0, target_position_m - state.position_m)
        if distance_to_target_m <= self.config.stop_tolerance_m:
            self.reset()
            self.last_target_speed_mps = 0.0
            self.last_speed_error_mps = -state.speed_mps
            self.last_pid_output_percent = 0.0
            if state.speed_mps <= self.config.stop_speed_threshold_mps:
                return ControlCommand(
                    state.train_id,
                    brake_percent=self.config.hold_brake_percent,
                    source=CommandSource.ATO,
                )
            return ControlCommand(
                state.train_id,
                brake_percent=self.config.max_brake_percent,
                source=CommandSource.ATO,
            )

        target_speed_mps = self.target_speed_mps(state, target)
        brake_distance_m = state.speed_mps * state.speed_mps / (2.0 * self.config.expected_deceleration_mps2)
        if (
            state.speed_mps > target_speed_mps + self.config.pid_deadband_mps
            and distance_to_target_m <= brake_distance_m + self.config.brake_margin_m
        ):
            brake_percent = self._brake_percent(state.speed_mps, distance_to_target_m)
            self.last_target_speed_mps = target_speed_mps
            self.last_speed_error_mps = target_speed_mps - state.speed_mps
            self.last_pid_output_percent = -brake_percent
            return ControlCommand(
                state.train_id,
                brake_percent=brake_percent,
                source=CommandSource.ATO,
            )

        pid_output_percent = self._pid_output_percent(state, target_speed_mps)
        pid_output_percent = self._apply_profile_feedforward(state, target, target_speed_mps, pid_output_percent)
        if pid_output_percent > 0:
            return ControlCommand(
                state.train_id,
                traction_percent=pid_output_percent,
                source=CommandSource.ATO,
            )
        if pid_output_percent < 0:
            return ControlCommand(
                state.train_id,
                brake_percent=abs(pid_output_percent),
                source=CommandSource.ATO,
            )
        return ControlCommand.coast(state.train_id, source=CommandSource.ATO)

    def reset(self) -> None:
        self._last_train_id = None
        self._last_sim_time_s = None
        self._last_error_mps = None
        self._integral_error = 0.0
        self._filtered_derivative = 0.0
        self._profile_cache_key = None
        self._profile_cache = None
        self.last_profile_mode = "NONE"

    @property
    def current_profile(self) -> OptimizedSpeedProfile | None:
        return self._profile_cache

    def target_speed_mps(self, state: TrainState, target: AtoTarget) -> float:
        profile = self._profile_for(state, target)
        if profile is not None:
            lookup_position_m = self._profile_lookup_position_m(state, target)
            self.last_profile_mode = profile.mode_at_position(lookup_position_m)
            profile_speed_mps = profile.speed_at_position_mps(lookup_position_m)
            return min(
                self._permitted_speed_mps_at(target, lookup_position_m),
                self.config.target_cruise_speed_mps,
                profile_speed_mps,
            )
        self.last_profile_mode = "BRAKING_CURVE"
        return self._braking_curve_target_speed_mps(state, target)

    def _braking_curve_target_speed_mps(
        self,
        state: TrainState,
        target: AtoTarget,
        approach_margin_m: float | None = None,
    ) -> float:
        lookup_position_m = self._profile_lookup_position_m(state, target)
        return stopping_target_speed_mps(
            position_m=state.position_m,
            target_position_m=self._target_position_m(target),
            permitted_speed_mps=self._permitted_speed_mps_at(target, lookup_position_m),
            cruise_speed_mps=self.config.target_cruise_speed_mps,
            expected_deceleration_mps2=self.config.expected_deceleration_mps2,
            stop_tolerance_m=self.config.stop_tolerance_m,
            approach_margin_m=self.config.brake_margin_m if approach_margin_m is None else approach_margin_m,
        )

    def _profile_for(self, state: TrainState, target: AtoTarget) -> OptimizedSpeedProfile | None:
        if not self.config.use_dynamic_programming_profile:
            return None
        target_position_m = self._target_position_m(target)
        if target_position_m <= 0:
            return None

        vehicle_config = VehicleConfig(train_id=state.train_id)
        acceleration_mps2 = max(
            0.05,
            (vehicle_config.max_traction_force_n - vehicle_config.basic_resistance_n) / vehicle_config.mass_kg,
        )
        scheduled_run_time_s = self.config.profile_run_time_s or estimate_scheduled_run_time_s(
            target_position_m=target_position_m,
            permitted_speed_mps=min(target.permitted_speed_mps, self.config.target_cruise_speed_mps),
            acceleration_mps2=acceleration_mps2,
            deceleration_mps2=self.config.expected_deceleration_mps2,
            runtime_margin_ratio=self.config.profile_runtime_margin_ratio,
        )
        cache_key = (
            state.train_id,
            round(target_position_m, 3),
            round(target.permitted_speed_mps, 3),
            round(self.config.target_cruise_speed_mps, 3),
            round(scheduled_run_time_s, 3),
            round(self.config.profile_time_step_s, 3),
            round(self.config.profile_position_step_m, 3),
            round(self.config.profile_speed_step_mps, 3),
            self.config.profile_max_states_per_stage,
            target.path_plan.cache_key() if target.path_plan is not None else None,
        )
        if self._profile_cache_key == cache_key and self._profile_cache is not None:
            return self._profile_cache

        self._profile_cache = optimize_speed_profile_dcdp(
            target_position_m=target_position_m,
            permitted_speed_mps=min(target.permitted_speed_mps, self.config.target_cruise_speed_mps),
            scheduled_run_time_s=scheduled_run_time_s,
            vehicle_config=vehicle_config,
            dt_s=self.config.profile_time_step_s,
            position_step_m=self.config.profile_position_step_m,
            speed_step_mps=self.config.profile_speed_step_mps,
            terminal_tolerance_m=self.config.stop_tolerance_m,
            max_states_per_stage=self.config.profile_max_states_per_stage,
            path_plan=target.path_plan,
        )
        self._profile_cache_key = cache_key
        return self._profile_cache

    def _profile_lookup_position_m(self, state: TrainState, target: AtoTarget) -> float:
        target_position_m = self._target_position_m(target)
        remaining_distance_m = max(0.0, target_position_m - state.position_m)
        lookahead_m = min(self.config.profile_lookahead_m, remaining_distance_m * 0.35)
        return min(target_position_m, state.position_m + lookahead_m)

    def _apply_profile_feedforward(
        self,
        state: TrainState,
        target: AtoTarget,
        target_speed_mps: float,
        pid_output_percent: float,
    ) -> float:
        profile = self._profile_for(state, target)
        if profile is None:
            return pid_output_percent

        mode = profile.mode_at_position(self._profile_lookup_position_m(state, target))
        self.last_profile_mode = mode
        speed_gap_mps = target_speed_mps - state.speed_mps
        if mode == "MAX_TRACTION" and speed_gap_mps > self.config.pid_deadband_mps:
            return max(pid_output_percent, self.config.max_traction_percent)
        if mode == "MAX_BRAKE" and speed_gap_mps < -self.config.pid_deadband_mps:
            return min(pid_output_percent, -self.config.max_brake_percent * 0.8)
        if mode == "COAST" and abs(pid_output_percent) < 12.0:
            return 0.0
        return pid_output_percent

    def _pid_output_percent(self, state: TrainState, target_speed_mps: float) -> float:
        dt_s = self._control_period_s(state)
        speed_error_mps = target_speed_mps - state.speed_mps
        if self._last_error_mps is None:
            raw_derivative = 0.0
        else:
            raw_derivative = (speed_error_mps - self._last_error_mps) / dt_s
        alpha = self.config.pid_derivative_filter_ratio
        self._filtered_derivative = alpha * self._filtered_derivative + (1.0 - alpha) * raw_derivative
        self._integral_error = _clamp(
            self._integral_error + speed_error_mps * dt_s,
            -self.config.pid_integral_limit,
            self.config.pid_integral_limit,
        )
        pid_output = (
            self.config.pid_kp * speed_error_mps
            + self.config.pid_ki * self._integral_error
            + self.config.pid_kd * self._filtered_derivative
        )
        if abs(speed_error_mps) <= self.config.pid_deadband_mps:
            pid_output_percent = 0.0
        else:
            pid_output_percent = _clamp(
                pid_output * self.config.pid_output_percent_per_unit,
                -self.config.max_brake_percent,
                self.config.max_traction_percent,
            )
            if speed_error_mps > 0:
                pid_output_percent = max(0.0, pid_output_percent)
            elif speed_error_mps < 0:
                pid_output_percent = min(0.0, pid_output_percent)
        self._last_train_id = state.train_id
        self._last_sim_time_s = state.sim_time_s
        self._last_error_mps = speed_error_mps
        self.last_target_speed_mps = target_speed_mps
        self.last_speed_error_mps = speed_error_mps
        self.last_pid_output_percent = pid_output_percent
        return pid_output_percent

    def _control_period_s(self, state: TrainState) -> float:
        if self._last_train_id != state.train_id or self._last_sim_time_s is None:
            return self.config.control_period_s
        elapsed_s = state.sim_time_s - self._last_sim_time_s
        if elapsed_s <= 0:
            return self.config.control_period_s
        return elapsed_s

    def _brake_percent(self, speed_mps: float, distance_to_target_m: float) -> float:
        required_deceleration = speed_mps * speed_mps / (2.0 * max(distance_to_target_m, 0.1))
        ratio = _clamp(required_deceleration / self.config.expected_deceleration_mps2, 0.25, 1.0)
        return _clamp(ratio * self.config.max_brake_percent, 1.0, self.config.max_brake_percent)

    def _traction_percent(self, speed_mps: float, target_speed_mps: float) -> float:
        speed_gap = max(0.0, target_speed_mps - speed_mps)
        ratio = _clamp(speed_gap / target_speed_mps, 0.25, 1.0)
        return _clamp(ratio * self.config.max_traction_percent, 1.0, self.config.max_traction_percent)

    @staticmethod
    def _target_position_m(target: AtoTarget) -> float:
        if target.path_plan is not None:
            return target.path_plan.total_length_m
        return target.target_position_m

    @staticmethod
    def _permitted_speed_mps_at(target: AtoTarget, position_m: float) -> float:
        if target.path_plan is None:
            return target.permitted_speed_mps
        return target.path_plan.speed_limit_at(position_m, target.permitted_speed_mps)


class CabControlService:
    def compose(
        self,
        train_id: str,
        mode: OperationMode,
        manual_command: ControlCommand | None = None,
        ato_command: ControlCommand | None = None,
        atp_emergency_brake: bool = False,
    ) -> ControlCommand:
        if atp_emergency_brake:
            return ControlCommand(
                train_id,
                emergency_brake=True,
                source=CommandSource.ATP_OVERRIDE,
            )
        if mode == OperationMode.ATO:
            if ato_command is None:
                raise ValueError("ato_command is required in ATO mode")
            self._require_train_id(train_id, ato_command)
            return ato_command
        if manual_command is None:
            return ControlCommand.coast(train_id, source=CommandSource.MANUAL)
        self._require_train_id(train_id, manual_command)
        return manual_command

    def command_from_driver_input(
        self,
        driver_input: DriverInput,
    ) -> ControlCommand:
        if driver_input.emergency_brake:
            return ControlCommand(
                driver_input.train_id,
                emergency_brake=True,
                source=driver_input.to_command_source(),
            )
        if driver_input.handle_mode == DriverHandleMode.TRACTION:
            return ControlCommand(
                driver_input.train_id,
                traction_percent=driver_input.traction_percent,
                source=driver_input.to_command_source(),
            )
        if driver_input.handle_mode == DriverHandleMode.BRAKE:
            return ControlCommand(
                driver_input.train_id,
                brake_percent=driver_input.brake_percent,
                source=driver_input.to_command_source(),
            )
        if driver_input.handle_mode == DriverHandleMode.FAST_BRAKE:
            return ControlCommand(
                driver_input.train_id,
                brake_percent=100.0,
                source=driver_input.to_command_source(),
            )
        return ControlCommand.coast(driver_input.train_id, source=driver_input.to_command_source())

    @staticmethod
    def _require_train_id(train_id: str, command: ControlCommand) -> None:
        if command.train_id != train_id:
            raise ValueError("command train_id must match requested train_id")
