from __future__ import annotations

from dataclasses import dataclass
import math
from typing import TYPE_CHECKING

from app.domain.vehicle.models import CommandSource, ControlCommand, TrainState, VehicleConfig
from app.domain.vehicle.services import (
    BrakeBlendService,
    SimpleVehicleModel,
    TractionDriveModel,
    VehicleForceDemand,
)

if TYPE_CHECKING:
    from app.domain.line.services import PathPlan


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
        return self.control_at_position(position_m).mode

    def control_at_position(self, position_m: float) -> SpeedProfilePoint:
        """Return the planned zero-order-hold control for a path position."""
        if not self.points:
            return SpeedProfilePoint(0.0, 0.0, 0.0, "UNKNOWN", 0.0, 0.0, 0.0)
        bounded_position_m = min(max(0.0, position_m), self.target_position_m)
        for point in self.points[1:]:
            if bounded_position_m <= point.position_m:
                return point
        return self.points[-1]


@dataclass
class _SearchNode:
    state: TrainState
    cost_kwh: float
    point: SpeedProfilePoint
    parent: _SearchNode | None = None
    control_penalty: float = 0.0
    mode_group: str = "NEUTRAL"
    mode_hold_steps: int = 0
    terminal_braking_started: bool = False
    ranking_score: float = math.inf


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
    path_plan: PathPlan | None = None,
) -> OptimizedSpeedProfile:
    """Simplified discrete-continuous DP speed profile optimizer."""

    effective_target_position_m = path_plan.total_length_m if path_plan is not None else target_position_m

    if effective_target_position_m <= 0:
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
    drive = TractionDriveModel(config)
    dp_deceleration_mps2 = max(
        0.1,
        min(0.6, (config.max_service_brake_force_n + config.basic_resistance_n) / config.mass_kg),
    )
    requested_stages = max(1, int(round(scheduled_run_time_s / dt_s)))
    stages = max(requested_stages, int(math.ceil(requested_stages * 2.2)))
    overshoot_tolerance_m = min(0.05, terminal_tolerance_m * 0.1)
    initial = TrainState(config.train_id, position_m=0.0, speed_mps=0.0, acceleration_mps2=0.0, sim_time_s=0.0)
    initial_point = SpeedProfilePoint(0.0, 0.0, 0.0, "START", 0.0, 0.0, 0.0)
    states: dict[tuple[object, ...], _SearchNode] = {
        (0, 0): _SearchNode(state=initial, cost_kwh=0.0, point=initial_point)
    }
    terminal_candidates: list[_SearchNode] = []
    reference_profile = _reference_braking_profile(
        effective_target_position_m,
        permitted_speed_mps,
        scheduled_run_time_s,
        path_plan,
    )

    for stage in range(stages):
        next_states: dict[tuple[object, ...], _SearchNode] = {}
        remaining_stages = stages - stage
        for node in states.values():
            gradient_force_n = _gradient_force_n(node.state.position_m, path_plan, config)
            traction_capacity_n = drive.traction_capacity_n(node.state.speed_mps)
            electric_brake_capacity_n = drive.electric_brake_capacity_n(node.state.speed_mps)
            for mode, command in _candidate_commands(
                config.train_id,
                node.state.speed_mps,
                config,
                gradient_force_n,
                vehicle=vehicle,
                traction_capacity_n=traction_capacity_n,
            ):
                mode_group = _command_group(command)
                if not _transition_allowed(node, mode_group):
                    continue
                demand = _demand_from_capacities(
                    command,
                    config,
                    traction_capacity_n,
                    electric_brake_capacity_n,
                )
                blend = BrakeBlendService.blend(demand, 1.0)
                resistance_force_n = vehicle.running_resistance_n(
                    node.state.speed_mps,
                    demand.traction_force_n,
                    blend.total_brake_force_n,
                )
                raw_acceleration_mps2 = (
                    demand.traction_force_n
                    - blend.total_brake_force_n
                    - resistance_force_n
                    - gradient_force_n
                ) / config.mass_kg
                raw_next_speed_mps = node.state.speed_mps + raw_acceleration_mps2 * dt_s
                raw_next_position_m = node.state.position_m + (
                    node.state.speed_mps + max(raw_next_speed_mps, 0.0)
                ) * 0.5 * dt_s
                local_speed_limit_mps = min(
                    config.max_speed_mps,
                    _speed_limit_at_position(raw_next_position_m, permitted_speed_mps, path_plan),
                )
                # SimpleVehicleModel clamps speed to the vehicle maximum. Reject
                # the unclamped overspeed first so a full-traction command at the
                # cap cannot masquerade as a physically valid cruise state.
                if raw_next_speed_mps > local_speed_limit_mps + 1e-6:
                    continue
                next_state = vehicle.step_with_forces(
                    node.state,
                    traction_force_n=demand.traction_force_n,
                    brake_force_n=blend.total_brake_force_n,
                    electric_brake_force_n=blend.electric_brake_force_n,
                    dt_s=dt_s,
                    gradient_force_n=gradient_force_n,
                )
                local_speed_limit_mps = _speed_limit_at_position(next_state.position_m, permitted_speed_mps, path_plan)
                if next_state.speed_mps > local_speed_limit_mps + 1e-6:
                    continue
                remaining_distance_m = effective_target_position_m - next_state.position_m
                if remaining_distance_m < -overshoot_tolerance_m:
                    continue
                if (
                    next_state.speed_mps <= config.stop_speed_threshold_mps
                    and next_state.position_m < effective_target_position_m - position_step_m
                    and remaining_stages > 1
                ):
                    continue
                if not _can_still_stop_at_target(
                    next_state,
                    effective_target_position_m,
                    dp_deceleration_mps2,
                    terminal_tolerance_m,
                    speed_step_mps,
                ):
                    continue
                if not _can_still_reach_target(
                    next_state,
                    effective_target_position_m,
                    max(0, stages - stage - 1) * dt_s,
                    local_speed_limit_mps,
                    config,
                    position_step_m,
                ):
                    continue

                terminal_braking_started = node.terminal_braking_started or (
                    mode_group == "BRAKE"
                    and _inside_terminal_braking_zone(
                        node.state,
                        effective_target_position_m,
                        dp_deceleration_mps2,
                        position_step_m * 2.0,
                    )
                )
                mode_hold_steps = node.mode_hold_steps + 1 if mode_group == node.mode_group else 1
                key = _discrete_key(next_state.position_m, next_state.speed_mps, position_step_m, speed_step_mps) + (
                    mode_group,
                    min(mode_hold_steps, 3),
                    terminal_braking_started,
                )
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
                    point=point,
                    parent=node,
                    control_penalty=node.control_penalty + _transition_penalty(node, next_state, mode_group),
                    mode_group=mode_group,
                    mode_hold_steps=mode_hold_steps,
                    terminal_braking_started=terminal_braking_started,
                )
                if _is_terminal_candidate(
                    candidate,
                    effective_target_position_m,
                    terminal_tolerance_m,
                    overshoot_tolerance_m,
                    config.stop_speed_threshold_mps,
                ):
                    terminal_candidates.append(candidate)
                    continue
                candidate.ranking_score = _state_score(
                    candidate,
                    effective_target_position_m,
                    stages,
                    stage + 1,
                    reference_profile,
                )
                current = next_states.get(key)
                if current is None or candidate.ranking_score < current.ranking_score:
                    next_states[key] = candidate

        if terminal_candidates and stage + 1 >= max(1, int(requested_stages * 0.75)):
            break
        if not next_states:
            break
        states = _prune_states(next_states, effective_target_position_m, stages, stage + 1, max_states_per_stage, reference_profile)

    if terminal_candidates:
        best = min(
            terminal_candidates,
            key=lambda node: _terminal_score(
                node,
                effective_target_position_m,
                config.stop_speed_threshold_mps,
                scheduled_run_time_s,
            ),
        )
        terminal_score = _terminal_score(
            best,
            effective_target_position_m,
            config.stop_speed_threshold_mps,
            scheduled_run_time_s,
        )
        return OptimizedSpeedProfile(
            points=_with_terminal_point(
                _reconstruct_path(best),
                effective_target_position_m,
                best.state.sim_time_s,
            ),
            target_position_m=effective_target_position_m,
            permitted_speed_mps=permitted_speed_mps,
            scheduled_run_time_s=round(best.state.sim_time_s, 6),
            terminal_score=round(terminal_score, 9),
        )

    if not states:
        raise RuntimeError("DCDP search failed: no reachable states and no terminal stop candidate")

    best = min(
        states.values(),
        key=lambda node: _incomplete_terminal_score(
            node,
            effective_target_position_m,
            config.stop_speed_threshold_mps,
            scheduled_run_time_s,
        ),
    )
    raise RuntimeError(
        "DCDP search failed: no stop candidate within terminal tolerance "
        f"(best_position={best.state.position_m:.3f}, target={effective_target_position_m:.3f}, "
        f"best_speed={best.state.speed_mps:.3f}, best_time={best.state.sim_time_s:.3f})"
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
    gradient_force_n: float = 0.0,
    *,
    vehicle: SimpleVehicleModel | None = None,
    traction_capacity_n: float | None = None,
) -> tuple[tuple[str, ControlCommand], ...]:
    cruise_traction_percent = 0.0
    cruise_brake_percent = 0.0
    if speed_mps > config.stop_speed_threshold_mps:
        vehicle = vehicle or SimpleVehicleModel(config)
        traction_capacity_n = (
            traction_capacity_n
            if traction_capacity_n is not None
            else TractionDriveModel(config).traction_capacity_n(speed_mps)
        )
        hold_force_n = vehicle.running_resistance_n(speed_mps) + gradient_force_n
        if hold_force_n >= 0.0:
            cruise_traction_percent = min(
                100.0,
                hold_force_n / max(traction_capacity_n, 1.0) * 100.0,
            )
        else:
            cruise_brake_percent = min(
                100.0,
                -hold_force_n / max(config.max_service_brake_force_n, 1.0) * 100.0,
            )
    return (
        ("MAX_TRACTION", ControlCommand(train_id, traction_percent=100.0, source=CommandSource.ATO)),
        ("TRACTION_50", ControlCommand(train_id, traction_percent=50.0, source=CommandSource.ATO)),
        ("TRACTION_20", ControlCommand(train_id, traction_percent=20.0, source=CommandSource.ATO)),
        ("TRACTION_10", ControlCommand(train_id, traction_percent=10.0, source=CommandSource.ATO)),
        ("CRUISE", ControlCommand(
            train_id,
            traction_percent=cruise_traction_percent,
            brake_percent=cruise_brake_percent,
            source=CommandSource.ATO,
        )),
        ("COAST", ControlCommand.coast(train_id, source=CommandSource.ATO)),
        ("BRAKE_15", ControlCommand(train_id, brake_percent=15.0, source=CommandSource.ATO)),
        ("BRAKE_40", ControlCommand(train_id, brake_percent=40.0, source=CommandSource.ATO)),
        ("BRAKE_70", ControlCommand(train_id, brake_percent=70.0, source=CommandSource.ATO)),
        ("MAX_BRAKE", ControlCommand(train_id, brake_percent=100.0, source=CommandSource.ATO)),
    )


def _discrete_key(position_m: float, speed_mps: float, position_step_m: float, speed_step_mps: float) -> tuple[int, int]:
    return (int(round(position_m / position_step_m)), int(round(speed_mps / speed_step_mps)))


def _command_group(command: ControlCommand) -> str:
    if command.brake_percent > 0 or command.emergency_brake:
        return "BRAKE"
    if command.traction_percent > 0:
        return "TRACTION"
    return "NEUTRAL"


def _transition_allowed(node: _SearchNode, next_group: str) -> bool:
    if node.terminal_braking_started and next_group == "TRACTION":
        return False
    if {node.mode_group, next_group} == {"TRACTION", "BRAKE"}:
        return False
    if (
        node.mode_group not in {"NEUTRAL", next_group}
        and next_group != "BRAKE"
        and node.mode_hold_steps < 2
    ):
        return False
    return True


def _inside_terminal_braking_zone(
    state: TrainState,
    target_position_m: float,
    deceleration_mps2: float,
    guard_margin_m: float,
) -> bool:
    remaining_distance_m = max(0.0, target_position_m - state.position_m)
    stopping_distance_m = state.speed_mps * state.speed_mps / (2.0 * max(deceleration_mps2, 0.1))
    return remaining_distance_m <= stopping_distance_m + guard_margin_m


def _transition_penalty(node: _SearchNode, next_state: TrainState, next_group: str) -> float:
    group_switch_penalty = 0.0 if next_group == node.mode_group else 12.0
    acceleration_change = abs(next_state.acceleration_mps2 - node.state.acceleration_mps2)
    short_hold_penalty = 8.0 if next_group != node.mode_group and node.mode_hold_steps < 2 else 0.0
    return group_switch_penalty + short_hold_penalty + acceleration_change * 6.0


def _state_score(
    node: _SearchNode,
    target_position_m: float,
    stages: int,
    stage: int,
    reference_profile: OptimizedSpeedProfile,
) -> float:
    progress = stage / max(stages, 1)
    expected_position_m = _reference_position_at_time(reference_profile, node.state.sim_time_s)
    expected_speed_mps = reference_profile.speed_at_position_mps(expected_position_m)
    remaining_distance_m = max(0.0, target_position_m - node.state.position_m)
    stopping_distance_m = node.state.speed_mps * node.state.speed_mps / (2.0 * 0.6)
    overshoot_risk_m = max(0.0, stopping_distance_m - remaining_distance_m)
    position_penalty = abs(node.state.position_m - expected_position_m) * 4.0
    speed_penalty = abs(node.state.speed_mps - expected_speed_mps) * (1.0 if progress < 0.8 else 5.0)
    overshoot_penalty = overshoot_risk_m * 120.0
    terminal_speed_penalty = max(0.0, progress - 0.75) * node.state.speed_mps * 10.0
    energy_tiebreaker = node.cost_kwh * 0.0001
    return (
        position_penalty
        + speed_penalty
        + overshoot_penalty
        + terminal_speed_penalty
        + node.control_penalty
        + energy_tiebreaker
    )


def _demand_from_capacities(
    command: ControlCommand,
    config: VehicleConfig,
    traction_capacity_n: float,
    electric_brake_capacity_n: float,
) -> VehicleForceDemand:
    """Match TractionDriveModel.demand while sharing capacity interpolation per node."""
    if command.emergency_brake:
        return VehicleForceDemand(0.0, config.emergency_brake_force_n, 0.0)
    traction_force_n = traction_capacity_n * command.traction_percent / 100.0
    total_brake_force_n = config.max_service_brake_force_n * command.brake_percent / 100.0
    candidate_electric_brake_force_n = min(
        total_brake_force_n,
        electric_brake_capacity_n * command.brake_percent / 100.0,
    )
    return VehicleForceDemand(
        traction_force_n,
        total_brake_force_n,
        candidate_electric_brake_force_n,
    )


def _reconstruct_path(node: _SearchNode) -> tuple[SpeedProfilePoint, ...]:
    """Materialize the winning path once instead of copying it for every DP candidate."""
    reversed_points: list[SpeedProfilePoint] = []
    current: _SearchNode | None = node
    while current is not None:
        reversed_points.append(current.point)
        current = current.parent
    reversed_points.reverse()
    return tuple(reversed_points)


def _terminal_score(
    node: _SearchNode,
    target_position_m: float,
    stop_speed_threshold_mps: float,
    scheduled_run_time_s: float,
) -> float:
    position_error_m = abs(node.state.position_m - target_position_m)
    speed_error_mps = max(0.0, node.state.speed_mps - stop_speed_threshold_mps)
    overshoot_m = max(0.0, node.state.position_m - target_position_m)
    time_error_s = abs(node.state.sim_time_s - scheduled_run_time_s)
    return (
        overshoot_m * 50_000.0
        + position_error_m * 5_000.0
        + speed_error_mps * 5_000.0
        + time_error_s * 2.0
        + node.control_penalty
        + node.cost_kwh * 0.001
    )


def _incomplete_terminal_score(
    node: _SearchNode,
    target_position_m: float,
    stop_speed_threshold_mps: float,
    scheduled_run_time_s: float,
) -> float:
    position_error_m = abs(node.state.position_m - target_position_m)
    speed_error_mps = max(0.0, node.state.speed_mps - stop_speed_threshold_mps)
    overshoot_m = max(0.0, node.state.position_m - target_position_m)
    time_error_s = abs(node.state.sim_time_s - scheduled_run_time_s)
    return (
        overshoot_m * 50_000.0
        + position_error_m * 5_000.0
        + speed_error_mps * 5_000.0
        + time_error_s
        + node.control_penalty
    )


def _is_terminal_candidate(
    node: _SearchNode,
    target_position_m: float,
    terminal_tolerance_m: float,
    overshoot_tolerance_m: float,
    stop_speed_threshold_mps: float,
) -> bool:
    position_error_m = target_position_m - node.state.position_m
    return (
        -overshoot_tolerance_m <= position_error_m <= terminal_tolerance_m
        and node.state.speed_mps <= stop_speed_threshold_mps
    )


def _can_still_stop_at_target(
    state: TrainState,
    target_position_m: float,
    deceleration_mps2: float,
    terminal_tolerance_m: float,
    speed_step_mps: float,
) -> bool:
    remaining_distance_m = target_position_m - state.position_m
    if remaining_distance_m < -terminal_tolerance_m:
        return False
    if remaining_distance_m <= terminal_tolerance_m:
        return state.speed_mps <= speed_step_mps + 0.2
    allowed_speed_mps = math.sqrt(2.0 * deceleration_mps2 * max(remaining_distance_m + terminal_tolerance_m, 0.0))
    return state.speed_mps <= allowed_speed_mps + speed_step_mps + 0.2


def _can_still_reach_target(
    state: TrainState,
    target_position_m: float,
    remaining_time_s: float,
    local_speed_limit_mps: float,
    config: VehicleConfig,
    position_step_m: float,
) -> bool:
    remaining_distance_m = target_position_m - state.position_m
    if remaining_distance_m <= position_step_m:
        return True
    if remaining_time_s <= 0:
        return False
    acceleration_mps2 = max(
        0.05,
        (config.max_traction_force_n - config.basic_resistance_n) / config.mass_kg,
    )
    speed_limit_mps = min(config.max_speed_mps, local_speed_limit_mps)
    time_to_limit_s = max(0.0, speed_limit_mps - state.speed_mps) / acceleration_mps2
    accel_time_s = min(remaining_time_s, time_to_limit_s)
    accel_distance_m = state.speed_mps * accel_time_s + 0.5 * acceleration_mps2 * accel_time_s * accel_time_s
    cruise_time_s = max(0.0, remaining_time_s - accel_time_s)
    max_distance_m = accel_distance_m + speed_limit_mps * cruise_time_s
    return max_distance_m + position_step_m * 2.0 >= remaining_distance_m


def _prune_states(
    states: dict[tuple[object, ...], _SearchNode],
    target_position_m: float,
    stages: int,
    stage: int,
    max_states: int,
    reference_profile: OptimizedSpeedProfile,
) -> dict[tuple[object, ...], _SearchNode]:
    if len(states) <= max_states:
        return states
    ordered = sorted(
        states.items(),
        key=lambda item: _state_score(item[1], target_position_m, stages, stage, reference_profile),
    )
    return dict(ordered[:max_states])


def _reference_position_at_time(profile: OptimizedSpeedProfile, sim_time_s: float) -> float:
    if not profile.points:
        return 0.0
    previous = profile.points[0]
    if sim_time_s <= previous.sim_time_s:
        return previous.position_m
    for point in profile.points[1:]:
        if sim_time_s <= point.sim_time_s:
            time_delta_s = point.sim_time_s - previous.sim_time_s
            if time_delta_s <= 1e-9:
                return point.position_m
            ratio = (sim_time_s - previous.sim_time_s) / time_delta_s
            return previous.position_m + ratio * (point.position_m - previous.position_m)
        previous = point
    return profile.points[-1].position_m


def _speed_limit_at_position(
    position_m: float,
    permitted_speed_mps: float,
    path_plan: PathPlan | None,
) -> float:
    if path_plan is None:
        return permitted_speed_mps
    return min(permitted_speed_mps, path_plan.speed_limit_at(position_m, permitted_speed_mps))


def _gradient_force_n(
    position_m: float,
    path_plan: PathPlan | None,
    config: VehicleConfig,
) -> float:
    if path_plan is None:
        return 0.0
    return config.mass_kg * 9.80665 * path_plan.grade_ratio_at(position_m)


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


def _reference_braking_profile(
    target_position_m: float,
    permitted_speed_mps: float,
    scheduled_run_time_s: float,
    path_plan: PathPlan | None = None,
) -> OptimizedSpeedProfile:
    acceleration_mps2 = 0.55
    deceleration_mps2 = 0.6
    step_m = min(20.0, max(5.0, target_position_m / 80.0))

    positions = [0.0]
    position_m = step_m
    while position_m < target_position_m:
        positions.append(position_m)
        position_m += step_m
    if positions[-1] < target_position_m - 1e-6:
        positions.append(target_position_m)
    else:
        positions[-1] = target_position_m

    reference_points: list[SpeedProfilePoint] = []
    elapsed_s = 0.0
    previous_position_m = 0.0
    previous_speed_mps = 0.0
    for index, position_m in enumerate(positions):
        if index == 0:
            speed_mps = 0.0
            mode = "START"
        elif position_m >= target_position_m - 1e-9:
            speed_mps = 0.0
            mode = "STOP"
        else:
            remaining_m = max(0.0, target_position_m - position_m)
            local_limit_mps = _speed_limit_at_position(position_m, permitted_speed_mps, path_plan)
            accel_limit_mps = math.sqrt(2.0 * acceleration_mps2 * max(position_m, 0.0))
            brake_limit_mps = math.sqrt(2.0 * deceleration_mps2 * remaining_m)
            speed_mps = min(permitted_speed_mps, local_limit_mps, accel_limit_mps, brake_limit_mps)
            if speed_mps >= local_limit_mps - 0.2:
                mode = "CRUISE"
            elif brake_limit_mps <= min(permitted_speed_mps, local_limit_mps, accel_limit_mps) + 1e-9:
                mode = "MAX_BRAKE"
            elif speed_mps > previous_speed_mps + 0.2:
                mode = "MAX_TRACTION"
            else:
                mode = "COAST"

        distance_m = position_m - previous_position_m
        average_speed_mps = (previous_speed_mps + speed_mps) / 2.0
        if index > 0:
            if average_speed_mps > 1e-6:
                elapsed_s += distance_m / average_speed_mps
            else:
                elapsed_s += 1.0
        reference_points.append(
            SpeedProfilePoint(
                sim_time_s=round(elapsed_s, 6),
                position_m=round(position_m, 6),
                speed_mps=round(speed_mps, 6),
                mode=mode,
                traction_percent=0.0,
                brake_percent=0.0,
                energy_kwh=0.0,
            )
        )
        previous_position_m = position_m
        previous_speed_mps = speed_mps

    points = tuple(reference_points)
    return OptimizedSpeedProfile(points, target_position_m, permitted_speed_mps, scheduled_run_time_s, terminal_score=math.inf)
