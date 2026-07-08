from __future__ import annotations

import math

from app.domain.control.models import AtoConfig, AtoTarget, DriverHandleMode, DriverInput, OperationMode
from app.domain.vehicle.models import CommandSource, ControlCommand, TrainState


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)


class ATOController:
    def __init__(self, config: AtoConfig | None = None) -> None:
        self.config = config or AtoConfig()

    def decide(self, state: TrainState, target: AtoTarget) -> ControlCommand:
        if target.emergency_brake_required:
            return ControlCommand(state.train_id, emergency_brake=True, source=CommandSource.ATO)

        distance_to_target_m = max(0.0, target.target_position_m - state.position_m)
        if distance_to_target_m <= self.config.stop_tolerance_m:
            if state.speed_mps <= self.config.stop_speed_threshold_mps:
                return ControlCommand(
                    state.train_id,
                    brake_level=self.config.hold_brake_level,
                    source=CommandSource.ATO,
                )
            return ControlCommand(
                state.train_id,
                brake_level=self.config.max_brake_level,
                source=CommandSource.ATO,
            )

        brake_distance_m = state.speed_mps * state.speed_mps / (2.0 * self.config.expected_deceleration_mps2)
        if state.speed_mps > 0 and distance_to_target_m <= brake_distance_m + self.config.brake_margin_m:
            return ControlCommand(
                state.train_id,
                brake_level=self._brake_level(state.speed_mps, distance_to_target_m),
                source=CommandSource.ATO,
            )

        target_speed_mps = min(self.config.target_cruise_speed_mps, target.permitted_speed_mps)
        if state.speed_mps < target_speed_mps:
            return ControlCommand(
                state.train_id,
                traction_level=self._traction_level(state.speed_mps, target_speed_mps),
                source=CommandSource.ATO,
            )
        return ControlCommand.coast(state.train_id, source=CommandSource.ATO)

    def _brake_level(self, speed_mps: float, distance_to_target_m: float) -> int:
        required_deceleration = speed_mps * speed_mps / (2.0 * max(distance_to_target_m, 0.1))
        ratio = _clamp(required_deceleration / self.config.expected_deceleration_mps2, 0.25, 1.0)
        return max(1, math.ceil(ratio * self.config.max_brake_level))

    def _traction_level(self, speed_mps: float, target_speed_mps: float) -> int:
        speed_gap = max(0.0, target_speed_mps - speed_mps)
        ratio = _clamp(speed_gap / target_speed_mps, 0.25, 1.0)
        return max(1, math.ceil(ratio * self.config.max_traction_level))


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
        max_traction_level: int = 5,
        max_brake_level: int = 5,
    ) -> ControlCommand:
        if max_traction_level <= 0:
            raise ValueError("max_traction_level must be positive")
        if max_brake_level <= 0:
            raise ValueError("max_brake_level must be positive")

        if driver_input.emergency_brake:
            return ControlCommand(
                driver_input.train_id,
                emergency_brake=True,
                source=driver_input.to_command_source(),
            )
        if driver_input.handle_mode == DriverHandleMode.TRACTION:
            return ControlCommand(
                driver_input.train_id,
                traction_level=self._percent_to_level(driver_input.traction_percent, max_traction_level),
                source=driver_input.to_command_source(),
            )
        if driver_input.handle_mode == DriverHandleMode.BRAKE:
            return ControlCommand(
                driver_input.train_id,
                brake_level=self._percent_to_level(driver_input.brake_percent, max_brake_level),
                source=driver_input.to_command_source(),
            )
        if driver_input.handle_mode == DriverHandleMode.FAST_BRAKE:
            return ControlCommand(
                driver_input.train_id,
                brake_level=max_brake_level,
                source=driver_input.to_command_source(),
            )
        return ControlCommand.coast(driver_input.train_id, source=driver_input.to_command_source())

    @staticmethod
    def _require_train_id(train_id: str, command: ControlCommand) -> None:
        if command.train_id != train_id:
            raise ValueError("command train_id must match requested train_id")

    @staticmethod
    def _percent_to_level(percent: float, max_level: int) -> int:
        if percent <= 0:
            return 0
        return max(1, min(max_level, math.ceil(percent / 100.0 * max_level)))
