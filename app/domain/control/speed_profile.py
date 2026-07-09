from __future__ import annotations

from dataclasses import dataclass
import math

from app.domain.vehicle.models import CommandSource, ControlCommand, TrainState, VehicleConfig
from app.domain.vehicle.services import SimpleVehicleModel


@dataclass(frozen=True)
class SpeedProfilePoint:
    sim_time_s: float
    position_m: float
    speed_mps: float
    mode: str
    traction_percent: float
    brake_percent: float
    energy_kwh: float


@dataclass(frozen=True)
class OptimizedSpeedProfile:
    points: tuple[SpeedProfilePoint, ...]
    target_position_m: float
    permitted_speed_mps: float
    scheduled_run_time_s: float
    terminal_score: float

    def speed_at_position_mps(self, position_m: float) -> float:
        if not self.points:
            return 0.0
        bounded_position_m = min(max(0.0, position_m), self.target_position_m)
        previous = self.points[0]
        if bounded_position_m <= previous.position_m:
            return previous.speed_mps
        for point in self.points[1:]:
            if bounded_position_m <= point.position_m:
                distance_m = point.position_m - previous.position_m
                if distance_m <= 1e-9:
                    return point.speed_mps
                ratio = (bounded_position_m - previous.position_m) / distance_m
                return previous.speed_mps + ratio * (point.speed_mps - previous.speed_mps)
            previous = point
        return self.points[-1].speed_mps

    def mode_at_position(self, position_m: float) -> str:
        if not self.points:
            return "UNKNOWN"
        bounded_position_m = min(max(0.0, position_m), self.target_position_m)
        for point in self.points[1:]:
            if bounded_position_m <= point.position_m:
                return point.mode
        return self.points[-1].mode


@dataclass(frozen=True)
class _SearchNode:
    state: TrainState
    cost_kwh: float
    path: tuple[SpeedProfilePoint, ...]


def stopping_target_speed_mps(
    position_m: float,
    target_position_m: float,
    permitted_speed_mps: float,
    cruise_speed_mps: float,
    expected_deceleration_mps2: float,
    stop_tolerance_m: float,
    approach_margin_m: float = 0.0,
) -> float:
    if permitted_speed_mps <= 0:
        raise ValueError("permitted_speed_mps must be positive")
    if cruise_speed_mps <= 0:
        raise ValueError("cruise_speed_mps must be positive")
    if expected_deceleration_mps2 <= 0:
        raise ValueError("expected_deceleration_mps2 must be positive")
    if stop_tolerance_m <= 0:
        raise ValueError("stop_tolerance_m must be positive")
    if approach_margin_m < 0:
        raise ValueError("approach_margin_m must be non-negative")

    distance_to_target_m = max(0.0, target_position_m - position_m)
    if distance_to_target_m <= stop_tolerance_m:
        return 0.0

    braking_curve_distance_m = max(0.0, distance_to_target_m - approach_margin_m)
    stop_curve_speed_mps = math.sqrt(2.0 * expected_deceleration_mps2 * braking_curve_distance_m)
    return min(permitted_speed_mps, cruise_speed_mps, stop_curve_speed_mps)


def optimize_speed_profile_dcdp(
    target_position_m: float,
    permitted_speed_mps: float,
    scheduled_run_time_s: float,
    vehicle_config: VehicleConfig | None = None,
    dt_s: float = 1.0,
    position_step_m: float = 5.0,
    speed_step_mps: float = 0.5,
    terminal_tolerance_m: float = 1.0,
    max_states_per_stage: int = 1800,
) -> OptimizedSpeedProfile:
    """Simplified discrete-continuous DP speed profile optimizer."""

    if target_position_m <= 0:
        raise ValueError("target_position_m must be positive")
    if permitted_speed_mps <= 0:
        raise ValueError("permitted_speed_mps must be positive")
    if scheduled_run_time_s <= 0:
        raise ValueError("scheduled_run_time_s must be positive")
    if dt_s <= 0:
        raise ValueError("dt_s must be positive")
    if position_step_m <= 0:
        raise ValueError("position_step_m must be positive")
    if speed_step_mps <= 0:
        raise ValueError("speed_step_mps must be positive")
    if terminal_tolerance_m <= 0:
        raise ValueError("terminal_tolerance_m must be positive")
    if max_states_per_stage <= 0:
        raise ValueError("max_states_per_stage must be positive")

    config = vehicle_config or VehicleConfig()
    vehicle = SimpleVehicleModel(config)
    stages = max(1, int(round(scheduled_run_time_s / dt_s)))
    initial = TrainState(config.train_id, position_m=0.0, speed_mps=0.0, acceleration_mps2=0.0, sim_time_s=0.0)
    initial_point = SpeedProfilePoint(0.0, 0.0, 0.0, "START", 0.0, 0.0, 0.0)
    states: dict[tuple[int, int], _SearchNode] = {
        (0, 0): _SearchNode(state=initial, cost_kwh=0.0, path=(initial_point,))
    }

    for stage in range(stages):
        next_states: dict[tuple[int, int], _SearchNode] = {}
        remaining_stages = stages - stage
        for node in states.values():
            for mode, command in _candidate_commands(config.train_id, node.state.speed_mps, config):
                next_state = vehicle.step(node.state, command, dt_s=dt_s)
                if next_state.speed_mps > permitted_speed_mps + 1e-6:
                    continue
                if next_state.position_m > target_position_m + terminal_tolerance_m:
                    continue
                if (
                    next_state.speed_mps <= config.stop_speed_threshold_mps
                    and next_state.position_m < target_position_m - position_step_m
                    and remaining_stages > 1
                ):
                    continue

                key = _discrete_key(next_state.position_m, next_state.speed_mps, position_step_m, speed_step_mps)
                point = SpeedProfilePoint(
                    sim_time_s=round(next_state.sim_time_s, 6),
                    position_m=round(next_state.position_m, 6),
                    speed_mps=round(next_state.speed_mps, 6),
                    mode=mode,
                    traction_percent=command.traction_percent,
                    brake_percent=command.brake_percent,
                    energy_kwh=round(next_state.net_energy_kwh, 9),
                )
                candidate = _SearchNode(
                    state=next_state,
                    cost_kwh=next_state.net_energy_kwh,
                    path=node.path + (point,),
                )
                current = next_states.get(key)
                if current is None or _state_score(candidate, target_position_m, stages, stage + 1) < _state_score(
                    current,
                    target_position_m,
                    stages,
                    stage + 1,
                ):
                    next_states[key] = candidate

        if not next_states:
            break
        states = _prune_states(next_states, target_position_m, stages, stage + 1, max_states_per_stage)

    if not states:
        return _fallback_profile(target_position_m, permitted_speed_mps, scheduled_run_time_s)

    best = min(states.values(), key=lambda node: _terminal_score(node, target_position_m, config.stop_speed_threshold_mps))
    terminal_score = _terminal_score(best, target_position_m, config.stop_speed_threshold_mps)
    return OptimizedSpeedProfile(
        points=_with_terminal_point(best.path, target_position_m, scheduled_run_time_s),
        target_position_m=target_position_m,
        permitted_speed_mps=permitted_speed_mps,
        scheduled_run_time_s=scheduled_run_time_s,
        terminal_score=round(terminal_score, 9),
    )


def estimate_scheduled_run_time_s(
    target_position_m: float,
    permitted_speed_mps: float,
    acceleration_mps2: float,
    deceleration_mps2: float,
    runtime_margin_ratio: float = 1.18,
) -> float:
    if target_position_m <= 0:
        raise ValueError("target_position_m must be positive")
    if permitted_speed_mps <= 0:
        raise ValueError("permitted_speed_mps must be positive")
    if acceleration_mps2 <= 0:
        raise ValueError("acceleration_mps2 must be positive")
    if deceleration_mps2 <= 0:
        raise ValueError("deceleration_mps2 must be positive")
    if runtime_margin_ratio <= 0:
        raise ValueError("runtime_margin_ratio must be positive")

    accel_distance_m = permitted_speed_mps * permitted_speed_mps / (2.0 * acceleration_mps2)
    brake_distance_m = permitted_speed_mps * permitted_speed_mps / (2.0 * deceleration_mps2)
    if accel_distance_m + brake_distance_m <= target_position_m:
        cruise_distance_m = target_position_m - accel_distance_m - brake_distance_m
        minimum_time_s = permitted_speed_mps / acceleration_mps2
        minimum_time_s += cruise_distance_m / permitted_speed_mps
        minimum_time_s += permitted_speed_mps / deceleration_mps2
    else:
        peak_speed_mps = math.sqrt(
            target_position_m / (1.0 / (2.0 * acceleration_mps2) + 1.0 / (2.0 * deceleration_mps2))
        )
        minimum_time_s = peak_speed_mps / acceleration_mps2 + peak_speed_mps / deceleration_mps2
    return minimum_time_s * runtime_margin_ratio


def _candidate_commands(
    train_id: str,
    speed_mps: float,
    config: VehicleConfig,
) -> tuple[tuple[str, ControlCommand], ...]:
    cruise_percent = 0.0
    if speed_mps > config.stop_speed_threshold_mps:
        cruise_percent = min(100.0, config.basic_resistance_n / config.max_traction_force_n * 100.0)
    return (
        ("MAX_TRACTION", ControlCommand(train_id, traction_percent=100.0, source=CommandSource.ATO)),
        ("CRUISE", ControlCommand(train_id, traction_percent=cruise_percent, source=CommandSource.ATO)),
        ("COAST", ControlCommand.coast(train_id, source=CommandSource.ATO)),
        ("MAX_BRAKE", ControlCommand(train_id, brake_percent=100.0, source=CommandSource.ATO)),
    )


def _discrete_key(position_m: float, speed_mps: float, position_step_m: float, speed_step_mps: float) -> tuple[int, int]:
    return (int(round(position_m / position_step_m)), int(round(speed_mps / speed_step_mps)))


def _state_score(node: _SearchNode, target_position_m: float, stages: int, stage: int) -> float:
    progress = stage / max(stages, 1)
    expected_position_m = target_position_m * progress
    schedule_penalty = abs(node.state.position_m - expected_position_m) * 0.0005
    return node.cost_kwh + schedule_penalty


def _terminal_score(node: _SearchNode, target_position_m: float, stop_speed_threshold_mps: float) -> float:
    position_error_m = abs(node.state.position_m - target_position_m)
    speed_error_mps = max(0.0, node.state.speed_mps - stop_speed_threshold_mps)
    return node.cost_kwh + position_error_m * 0.12 + speed_error_mps * 1.4


def _prune_states(
    states: dict[tuple[int, int], _SearchNode],
    target_position_m: float,
    stages: int,
    stage: int,
    max_states: int,
) -> dict[tuple[int, int], _SearchNode]:
    if len(states) <= max_states:
        return states
    ordered = sorted(states.items(), key=lambda item: _state_score(item[1], target_position_m, stages, stage))
    return dict(ordered[:max_states])


def _with_terminal_point(
    points: tuple[SpeedProfilePoint, ...],
    target_position_m: float,
    scheduled_run_time_s: float,
) -> tuple[SpeedProfilePoint, ...]:
    if not points:
        return points
    last = points[-1]
    if abs(last.position_m - target_position_m) <= 1e-6 and last.speed_mps <= 1e-6:
        return points
    terminal = SpeedProfilePoint(
        sim_time_s=round(max(last.sim_time_s, scheduled_run_time_s), 6),
        position_m=round(target_position_m, 6),
        speed_mps=0.0,
        mode="STOP",
        traction_percent=0.0,
        brake_percent=0.0,
        energy_kwh=last.energy_kwh,
    )
    return points + (terminal,)


def _fallback_profile(
    target_position_m: float,
    permitted_speed_mps: float,
    scheduled_run_time_s: float,
) -> OptimizedSpeedProfile:
    points = (
        SpeedProfilePoint(0.0, 0.0, 0.0, "START", 0.0, 0.0, 0.0),
        SpeedProfilePoint(
            scheduled_run_time_s / 2.0,
            target_position_m / 2.0,
            permitted_speed_mps,
            "CRUISE",
            0.0,
            0.0,
            0.0,
        ),
        SpeedProfilePoint(scheduled_run_time_s, target_position_m, 0.0, "STOP", 0.0, 0.0, 0.0),
    )
    return OptimizedSpeedProfile(points, target_position_m, permitted_speed_mps, scheduled_run_time_s, terminal_score=math.inf)
