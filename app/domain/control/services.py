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
    def __init__(
        self,
        config: AtoConfig | None = None,
        *,
        enable_synchronous_profile_optimization: bool = True,
    ) -> None:
        self.config = config or AtoConfig()
        self.enable_synchronous_profile_optimization = enable_synchronous_profile_optimization
        self._last_train_id: str | None = None
        self._last_sim_time_s: float | None = None
        self._last_error_mps: float | None = None
        self._integral_error: float = 0.0
        self._filtered_derivative: float = 0.0
        self._profile_cache_key: tuple[object, ...] | None = None
        self._profile_cache: OptimizedSpeedProfile | None = None
        # Profile generation is asynchronous in the engine; this switch remains
        # available for standalone controller use and is enabled for runtime ATO.
        self.allow_profile_compute: bool = True
        self.last_target_speed_mps: float = 0.0
        self.last_speed_error_mps: float = 0.0
        self.last_pid_output_percent: float = 0.0
        self.last_profile_mode: str = "NONE"
        self._last_command: ControlCommand | None = None
        self._last_command_sim_time_s: float | None = None
        self._terminal_braking_latched = False
        self._terminal_braking_target_position_m: float | None = None
        self._service_brake_active = False
        self._creep_release_in_progress = False
        self._creep_neutral_since_s: float | None = None
        self._creep_mode_active = False

    def decide(self, state: TrainState, target: AtoTarget) -> ControlCommand:
        if target.emergency_brake_required:
            self.reset()
            return ControlCommand(state.train_id, emergency_brake=True, source=CommandSource.ATO)

        target_position_m = self._target_position_m(target)
        distance_to_target_m = max(0.0, target_position_m - state.position_m)
        if distance_to_target_m > self.config.creep_distance_m:
            self._reset_creep_transition()
        if distance_to_target_m <= self.config.stop_tolerance_m:
            self.reset()
            self.last_target_speed_mps = 0.0
            self.last_speed_error_mps = -state.speed_mps
            self.last_pid_output_percent = 0.0
            if state.speed_mps <= self.config.stop_speed_threshold_mps:
                command = ControlCommand(
                    state.train_id,
                    brake_percent=self.config.hold_brake_percent,
                    source=CommandSource.ATO,
                )
                self._last_command = command
                self._last_command_sim_time_s = state.sim_time_s
                return command
            return self._stabilize_command(state, target, ControlCommand(
                state.train_id,
                brake_percent=self.config.max_brake_percent,
                source=CommandSource.ATO,
            ))

        creep_entry_allowed = (
            distance_to_target_m <= self.config.creep_distance_m
            and state.speed_mps <= self.config.creep_speed_threshold_mps
        )
        if self._creep_mode_active or creep_entry_allowed:
            return self._creep_command(state, target)

        target_speed_mps = self.target_speed_mps(state, target)
        brake_distance_m = state.speed_mps * state.speed_mps / (2.0 * self.config.expected_deceleration_mps2)
        if (
            state.speed_mps
            > target_speed_mps
            + max(self.config.pid_deadband_mps, self.config.service_brake_trigger_margin_mps)
            and distance_to_target_m <= brake_distance_m + self.config.brake_margin_m
        ):
            brake_percent = self._brake_percent(state.speed_mps, distance_to_target_m)
            self.last_target_speed_mps = target_speed_mps
            self.last_speed_error_mps = target_speed_mps - state.speed_mps
            self.last_pid_output_percent = -brake_percent
            self._update_brake_hysteresis(self.last_speed_error_mps, brake_requested=True)
            return self._stabilize_command(state, target, ControlCommand(
                state.train_id,
                brake_percent=brake_percent,
                source=CommandSource.ATO,
            ))

        pid_output_percent = self._pid_output_percent(state, target_speed_mps)
        pid_output_percent = self._apply_profile_feedforward(state, target, target_speed_mps, pid_output_percent)
        self._update_brake_hysteresis(
            self.last_speed_error_mps,
            brake_requested=pid_output_percent < 0,
        )
        if self._service_brake_active:
            pid_output_percent = min(
                pid_output_percent,
                -self.config.brake_hysteresis_hold_percent,
            )
        if pid_output_percent > 0:
            return self._stabilize_command(state, target, ControlCommand(
                state.train_id,
                traction_percent=pid_output_percent,
                source=CommandSource.ATO,
            ))
        if pid_output_percent < 0:
            return self._stabilize_command(state, target, ControlCommand(
                state.train_id,
                brake_percent=abs(pid_output_percent),
                source=CommandSource.ATO,
            ))
        return self._stabilize_command(
            state,
            target,
            ControlCommand.coast(state.train_id, source=CommandSource.ATO),
        )

    def reset(self) -> None:
        self._last_train_id = None
        self._last_sim_time_s = None
        self._last_error_mps = None
        self._integral_error = 0.0
        self._filtered_derivative = 0.0
        self._profile_cache_key = None
        self._profile_cache = None
        self.last_profile_mode = "NONE"
        self._last_command = None
        self._last_command_sim_time_s = None
        self._terminal_braking_latched = False
        self._terminal_braking_target_position_m = None
        self._service_brake_active = False
        self._reset_creep_transition()

    @property
    def current_profile(self) -> OptimizedSpeedProfile | None:
        return self._profile_cache

    def install_profile(
        self,
        state: TrainState,
        target: AtoTarget,
        profile: OptimizedSpeedProfile,
    ) -> None:
        """Install a profile produced outside the real-time control thread."""
        self._profile_cache_key = self._make_profile_cache_key(state, target)
        self._profile_cache = profile

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
            # The fallback curve must hand over directly to creep control. Using
            # brake_margin_m here creates a dead zone between creep_distance_m
            # and brake_margin_m where the target speed is already zero.
            approach_margin_m=self.config.creep_distance_m if approach_margin_m is None else approach_margin_m,
        )

    def fallback_target_speed_mps(self, state: TrainState, target: AtoTarget) -> float:
        """Return the safe lightweight target used while optimization is pending."""
        return self._braking_curve_target_speed_mps(state, target)

    def _profile_for(self, state: TrainState, target: AtoTarget) -> OptimizedSpeedProfile | None:
        if not self.config.use_dynamic_programming_profile:
            return None
        # During fast-forward, never construct a new cache key or solve DCDP.
        # Path changes call reset(), so an existing cache is still the profile
        # for the active interval.
        if not self.allow_profile_compute:
            return self._profile_cache
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
        cache_key = self._make_profile_cache_key(
            state,
            target,
            scheduled_run_time_s=scheduled_run_time_s,
        )
        if self._profile_cache_key == cache_key and self._profile_cache is not None:
            return self._profile_cache
        if not self.enable_synchronous_profile_optimization:
            return None

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

    def _make_profile_cache_key(
        self,
        state: TrainState,
        target: AtoTarget,
        *,
        scheduled_run_time_s: float | None = None,
    ) -> tuple[object, ...]:
        target_position_m = self._target_position_m(target)
        if scheduled_run_time_s is None:
            vehicle_config = VehicleConfig(train_id=state.train_id)
            acceleration_mps2 = max(
                0.05,
                (vehicle_config.max_traction_force_n - vehicle_config.basic_resistance_n)
                / vehicle_config.mass_kg,
            )
            scheduled_run_time_s = self.config.profile_run_time_s or estimate_scheduled_run_time_s(
                target_position_m=target_position_m,
                permitted_speed_mps=min(target.permitted_speed_mps, self.config.target_cruise_speed_mps),
                acceleration_mps2=acceleration_mps2,
                deceleration_mps2=self.config.expected_deceleration_mps2,
                runtime_margin_ratio=self.config.profile_runtime_margin_ratio,
            )
        return (
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

    def _stabilize_command(
        self,
        state: TrainState,
        target: AtoTarget,
        requested: ControlCommand,
    ) -> ControlCommand:
        """Apply terminal-brake hysteresis and actuator-realistic slew limits."""
        if self._last_command_sim_time_s is None:
            dt_s = self.config.control_period_s
        else:
            dt_s = max(state.sim_time_s - self._last_command_sim_time_s, 1e-6)
        target_position_m = self._target_position_m(target)
        remaining_distance_m = max(0.0, target_position_m - state.position_m)
        # A route-end movement authority is a temporary stopping target.  Once
        # CI extends that authority, the old terminal-brake latch must not keep
        # suppressing traction for the rest of the station interval.
        if (
            self._terminal_braking_latched
            and self._terminal_braking_target_position_m is not None
            and target_position_m
            > self._terminal_braking_target_position_m + self.config.stop_tolerance_m
        ):
            self._terminal_braking_latched = False
            self._terminal_braking_target_position_m = None
        braking_distance_m = state.speed_mps * state.speed_mps / (
            2.0 * self.config.expected_deceleration_mps2
        )
        in_terminal_braking_zone = (
            remaining_distance_m
            <= braking_distance_m + self.config.brake_margin_m + self.config.terminal_brake_guard_margin_m
        )
        if requested.brake_percent > 0 and in_terminal_braking_zone:
            self._terminal_braking_latched = True
            self._terminal_braking_target_position_m = target_position_m

        creep_allowed = (
            remaining_distance_m <= self.config.creep_distance_m
            and state.speed_mps <= self.config.creep_speed_threshold_mps
        )
        desired_traction = requested.traction_percent
        desired_brake = requested.brake_percent
        if self._terminal_braking_latched and desired_traction > 0 and not creep_allowed:
            desired_traction = 0.0
            desired_brake = 0.0
        terminal_floor_active = (
            self._terminal_braking_latched
            and not self._creep_release_in_progress
            and not self._creep_mode_active
            and remaining_distance_m <= self.config.creep_distance_m
            and state.speed_mps <= self.config.terminal_brake_floor_speed_mps
        )
        if terminal_floor_active:
            desired_traction = 0.0
            desired_brake = max(desired_brake, self.config.terminal_brake_floor_percent)

        previous_traction = self._last_command.traction_percent if self._last_command is not None else 0.0
        previous_brake = self._last_command.brake_percent if self._last_command is not None else 0.0
        traction_step = self.config.traction_slew_rate_percent_per_s * dt_s
        brake_apply_step = self.config.brake_apply_slew_rate_percent_per_s * dt_s
        brake_release_step = self.config.brake_release_slew_rate_percent_per_s * dt_s

        if desired_brake > 0:
            traction = 0.0
            if desired_brake >= previous_brake:
                brake = min(desired_brake, previous_brake + brake_apply_step)
            else:
                brake = max(desired_brake, previous_brake - brake_release_step)
        elif desired_traction > 0 and previous_brake > 0:
            traction = 0.0
            brake = max(0.0, previous_brake - brake_release_step)
        elif desired_traction > 0:
            brake = 0.0
            if desired_traction >= previous_traction:
                traction = min(desired_traction, previous_traction + traction_step)
            else:
                traction = max(desired_traction, previous_traction - traction_step)
        elif previous_brake > 0:
            traction = 0.0
            brake = max(0.0, previous_brake - brake_release_step)
        else:
            brake = 0.0
            traction = max(0.0, previous_traction - traction_step)

        # Outside the explicit creep-release sequence, do not let a small
        # terminal brake command cross directly to zero at low speed. The next
        # control sample may request braking again, which appears as a brief
        # release/reapplication pulse in the force trace. Holding the existing
        # hysteresis floor preserves the normal 18 %/s actuator release rate
        # everywhere else and still permits a full release before creep.
        if (
            brake <= 0.0
            and desired_brake <= 0.0
            and previous_brake > 0.0
            and self._terminal_braking_latched
            and not creep_allowed
            and state.speed_mps <= self.config.low_speed_brake_guard_speed_mps
        ):
            brake = min(previous_brake, self.config.brake_hysteresis_hold_percent)

        stabilized = ControlCommand(
            state.train_id,
            traction_percent=traction,
            brake_percent=brake,
            source=requested.source,
        )
        self._last_command = stabilized
        self._last_command_sim_time_s = state.sim_time_s
        return stabilized

    def _update_brake_hysteresis(self, speed_error_mps: float, *, brake_requested: bool) -> None:
        if self._service_brake_active:
            if speed_error_mps >= self.config.brake_release_error_mps:
                self._service_brake_active = False
            return
        if brake_requested or speed_error_mps <= -self.config.brake_engage_error_mps:
            self._service_brake_active = True

    def _creep_command(self, state: TrainState, target: AtoTarget) -> ControlCommand:
        """Release brake fully, dwell in neutral, then enter traction-only creep."""
        self.last_target_speed_mps = self.config.creep_speed_threshold_mps
        self.last_speed_error_mps = self.config.creep_speed_threshold_mps - state.speed_mps
        self.last_profile_mode = "CREEP" if self._creep_mode_active else "CREEP_RELEASE"

        if not self._creep_mode_active:
            self._creep_release_in_progress = True
            previous_brake = self._last_command.brake_percent if self._last_command is not None else 0.0
            if previous_brake > 0:
                self._creep_neutral_since_s = None
                self.last_pid_output_percent = 0.0
                return self._stabilize_command(
                    state,
                    target,
                    ControlCommand.coast(state.train_id, source=CommandSource.ATO),
                )

            if self._creep_neutral_since_s is None:
                self._creep_neutral_since_s = state.sim_time_s
            neutral_elapsed_s = max(0.0, state.sim_time_s - self._creep_neutral_since_s)
            if neutral_elapsed_s < self.config.creep_neutral_time_s:
                self.last_pid_output_percent = 0.0
                return self._stabilize_command(
                    state,
                    target,
                    ControlCommand.coast(state.train_id, source=CommandSource.ATO),
                )

            self._creep_mode_active = True
            self._creep_release_in_progress = False
            self._service_brake_active = False
            self.last_profile_mode = "CREEP"

        if state.speed_mps < self.config.creep_speed_threshold_mps:
            self.last_pid_output_percent = self.config.creep_traction_percent
            requested = ControlCommand(
                state.train_id,
                traction_percent=self.config.creep_traction_percent,
                source=CommandSource.ATO,
            )
        else:
            self.last_pid_output_percent = 0.0
            requested = ControlCommand.coast(state.train_id, source=CommandSource.ATO)
        return self._stabilize_command(state, target, requested)

    def _reset_creep_transition(self) -> None:
        self._creep_release_in_progress = False
        self._creep_neutral_since_s = None
        self._creep_mode_active = False

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
            return min(target.target_position_m, target.path_plan.total_length_m)
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
