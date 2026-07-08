from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.domain.control.models import AtoConfig, AtoTarget
from app.domain.control.services import ATOController
from app.domain.vehicle.models import CommandSource, ControlCommand, TrainState, VehicleConfig
from app.domain.vehicle.services import SimpleVehicleModel


JsonDict = dict[str, Any]


INTERACTIVE_COMMANDS = [
    "status",
    "ato",
    "traction <level>",
    "brake <level>",
    "coast",
    "eb",
    "reset",
    "help",
    "quit",
]

MAX_HANDLE_LEVEL = 5


@dataclass(frozen=True)
class StopDemoResult:
    ok: bool
    train_id: str
    target_position_m: float
    final_position_m: float
    stop_error_m: float
    final_speed_mps: float
    run_time_s: float
    max_speed_mps: float
    max_abs_acceleration_mps2: float
    command_switches: int
    net_energy_kwh: float
    ticks: int
    status: str
    history: list[JsonDict]

    def to_dict(self, include_history: bool = False) -> JsonDict:
        payload = asdict(self)
        if not include_history:
            payload.pop("history")
        return payload


def run_ato_stop_demo(
    target_position_m: float = 200.0,
    permitted_speed_mps: float = 12.0,
    dt_s: float = 1.0,
    max_ticks: int = 120,
    expected_deceleration_mps2: float = 0.6,
    stop_tolerance_m: float = 1.0,
    train_id: str = "T001",
) -> StopDemoResult:
    if target_position_m <= 0:
        raise ValueError("target_position_m must be positive")
    if permitted_speed_mps <= 0:
        raise ValueError("permitted_speed_mps must be positive")
    if dt_s <= 0:
        raise ValueError("dt_s must be positive")
    if max_ticks <= 0:
        raise ValueError("max_ticks must be positive")

    vehicle = SimpleVehicleModel(VehicleConfig(train_id=train_id))
    controller = ATOController(
        AtoConfig(
            expected_deceleration_mps2=expected_deceleration_mps2,
            stop_tolerance_m=stop_tolerance_m,
        )
    )
    state = TrainState(
        train_id,
        position_m=0.0,
        speed_mps=0.0,
        acceleration_mps2=0.0,
        sim_time_s=0.0,
    )
    target = AtoTarget(target_position_m=target_position_m, permitted_speed_mps=permitted_speed_mps)
    history: list[JsonDict] = []
    command_switches = 0
    last_mode: str | None = None
    status = "MAX_TICKS"

    for _ in range(max_ticks):
        command = controller.decide(state, target)
        mode = _command_mode(command)
        if last_mode is not None and mode != last_mode:
            command_switches += 1
        last_mode = mode

        state = vehicle.step(state, command, dt_s=dt_s)
        history.append(
            {
                "simTimeS": round(state.sim_time_s, 3),
                "positionM": round(state.position_m, 3),
                "speedMps": round(state.speed_mps, 3),
                "accelerationMps2": round(state.acceleration_mps2, 3),
                "mode": mode,
                "tractionLevel": command.traction_level,
                "brakeLevel": command.brake_level,
            }
        )
        if state.speed_mps <= vehicle.config.stop_speed_threshold_mps and abs(state.position_m - target_position_m) <= stop_tolerance_m:
            status = "STOPPED_AT_TARGET"
            break

    max_speed_mps = max((item["speedMps"] for item in history), default=0.0)
    max_abs_acceleration_mps2 = max((abs(item["accelerationMps2"]) for item in history), default=0.0)
    stop_error_m = state.position_m - target_position_m
    return StopDemoResult(
        ok=status == "STOPPED_AT_TARGET",
        train_id=train_id,
        target_position_m=round(target_position_m, 3),
        final_position_m=round(state.position_m, 3),
        stop_error_m=round(stop_error_m, 3),
        final_speed_mps=round(state.speed_mps, 3),
        run_time_s=round(state.sim_time_s, 3),
        max_speed_mps=round(max_speed_mps, 3),
        max_abs_acceleration_mps2=round(max_abs_acceleration_mps2, 3),
        command_switches=command_switches,
        net_energy_kwh=round(state.net_energy_kwh, 6),
        ticks=len(history),
        status=status,
        history=history,
    )


class VehicleInteractiveSession:
    def __init__(
        self,
        target_position_m: float = 200.0,
        permitted_speed_mps: float = 12.0,
        dt_s: float = 1.0,
        expected_deceleration_mps2: float = 0.6,
        stop_tolerance_m: float = 1.0,
        train_id: str = "T001",
    ) -> None:
        if target_position_m <= 0:
            raise ValueError("target_position_m must be positive")
        if permitted_speed_mps <= 0:
            raise ValueError("permitted_speed_mps must be positive")
        if dt_s <= 0:
            raise ValueError("dt_s must be positive")

        self.vehicle = SimpleVehicleModel(VehicleConfig(train_id=train_id))
        self.controller = ATOController(
            AtoConfig(
                expected_deceleration_mps2=expected_deceleration_mps2,
                stop_tolerance_m=stop_tolerance_m,
            )
        )
        self.target = AtoTarget(target_position_m=target_position_m, permitted_speed_mps=permitted_speed_mps)
        self.dt_s = dt_s
        self.train_id = train_id
        self.ticks = 0
        self.command_switches = 0
        self.last_mode: str | None = None
        self.state = self._initial_state()

    def apply_command(self, line: str) -> JsonDict:
        parts = line.strip().split()
        if not parts:
            return self.status_payload(message="empty command")

        name = parts[0].lower()
        if name in {"help", "?"}:
            return {
                "ok": True,
                "commands": INTERACTIVE_COMMANDS,
                "state": self.status_payload(),
            }
        if name == "status":
            return self.status_payload()
        if name == "reset":
            self.state = self._initial_state()
            self.ticks = 0
            self.command_switches = 0
            self.last_mode = None
            return self.status_payload(message="reset")

        command = self._command_from_parts(name, parts)
        return self._step(command)

    def apply_handle_level(self, handle_level: int) -> JsonDict:
        return self._step(self.command_from_handle_level(handle_level))

    def command_from_handle_level(self, handle_level: int) -> ControlCommand:
        if handle_level > MAX_HANDLE_LEVEL or handle_level < -MAX_HANDLE_LEVEL:
            raise ValueError(f"handle_level must be between {-MAX_HANDLE_LEVEL} and {MAX_HANDLE_LEVEL}")
        if handle_level > 0:
            return ControlCommand(self.train_id, traction_level=handle_level, source=CommandSource.MANUAL)
        if handle_level < 0:
            return ControlCommand(self.train_id, brake_level=abs(handle_level), source=CommandSource.MANUAL)
        return ControlCommand.coast(self.train_id, source=CommandSource.MANUAL)

    def status_payload(self, message: str | None = None) -> JsonDict:
        stop_error_m = self.state.position_m - self.target.target_position_m
        payload: JsonDict = {
            "ok": True,
            "trainId": self.train_id,
            "targetPositionM": round(self.target.target_position_m, 3),
            "positionM": round(self.state.position_m, 3),
            "stopErrorM": round(stop_error_m, 3),
            "speedMps": round(self.state.speed_mps, 3),
            "accelerationMps2": round(self.state.acceleration_mps2, 3),
            "simTimeS": round(self.state.sim_time_s, 3),
            "netEnergyKwh": round(self.state.net_energy_kwh, 6),
            "ticks": self.ticks,
            "commandSwitches": self.command_switches,
            "status": self._status(stop_error_m),
        }
        if message:
            payload["message"] = message
        return payload

    def _command_from_parts(self, name: str, parts: list[str]) -> ControlCommand:
        if name in {"ato", "a"}:
            return self.controller.decide(self.state, self.target)
        if name in {"traction", "t"}:
            level = self._parse_level(parts, "traction")
            return ControlCommand(self.train_id, traction_level=level, source=CommandSource.MANUAL)
        if name in {"brake", "b"}:
            level = self._parse_level(parts, "brake")
            return ControlCommand(self.train_id, brake_level=level, source=CommandSource.MANUAL)
        if name in {"coast", "c"}:
            return ControlCommand.coast(self.train_id, source=CommandSource.MANUAL)
        if name in {"eb", "emergency", "emergency-brake"}:
            return ControlCommand(self.train_id, emergency_brake=True, source=CommandSource.MANUAL)
        raise ValueError(f"unknown command: {name}")

    def _step(self, command: ControlCommand) -> JsonDict:
        mode = _command_mode(command)
        if self.last_mode is not None and mode != self.last_mode:
            self.command_switches += 1
        self.last_mode = mode
        self.state = self.vehicle.step(self.state, command, dt_s=self.dt_s)
        self.ticks += 1
        payload = self.status_payload()
        payload["command"] = {
            "mode": mode,
            "tractionLevel": command.traction_level,
            "brakeLevel": command.brake_level,
            "emergencyBrake": command.emergency_brake,
            "source": command.source.value,
        }
        return payload

    def _status(self, stop_error_m: float) -> str:
        if (
            self.state.speed_mps <= self.vehicle.config.stop_speed_threshold_mps
            and abs(stop_error_m) <= self.controller.config.stop_tolerance_m
        ):
            return "STOPPED_AT_TARGET"
        return "RUNNING"

    @staticmethod
    def _parse_level(parts: list[str], command_name: str) -> int:
        if len(parts) != 2:
            raise ValueError(f"{command_name} requires a level argument")
        return int(parts[1])

    def _initial_state(self) -> TrainState:
        return TrainState(
            self.train_id,
            position_m=0.0,
            speed_mps=0.0,
            acceleration_mps2=0.0,
            sim_time_s=0.0,
        )


def _command_mode(command: ControlCommand) -> str:
    if command.emergency_brake:
        return "EMERGENCY_BRAKE"
    if command.traction_level > 0:
        return "TRACTION"
    if command.brake_level > 0:
        return "BRAKE"
    return "COAST"
