from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.domain.control.models import AtoConfig, AtoTarget
from app.domain.control.services import ATOController
from app.domain.vehicle.models import ControlCommand, TrainState, VehicleConfig
from app.domain.vehicle.services import SimpleVehicleModel


JsonDict = dict[str, Any]


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


def _command_mode(command: ControlCommand) -> str:
    if command.emergency_brake:
        return "EMERGENCY_BRAKE"
    if command.traction_level > 0:
        return "TRACTION"
    if command.brake_level > 0:
        return "BRAKE"
    return "COAST"
