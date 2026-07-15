from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Any, Iterable

from app.domain.control.models import AtoConfig, AtoTarget
from app.domain.control.services import ATOController
from app.domain.control.speed_profile import (
    OptimizedSpeedProfile,
    estimate_scheduled_run_time_s,
    optimize_speed_profile_dcdp,
)
from app.domain.vehicle.models import ControlCommand, TrainState, VehicleConfig
from app.domain.vehicle.services import BrakeBlendService, SimpleVehicleModel, TractionDriveModel

if TYPE_CHECKING:
    from app.domain.line.services import PathPlan


JsonDict = dict[str, Any]
STOP_EXPERIMENT_SCHEMA_VERSION = "stop-comfort-energy-v1"


@dataclass(frozen=True)
class StopExperimentScenario:
    scenario_id: str = "synthetic-200m-load700"
    target_position_m: float = 200.0
    permitted_speed_mps: float = 12.0
    onboard_pax: int = 700
    dt_s: float = 0.1
    control_period_s: float = 0.1
    max_time_s: float = 180.0
    train_id: str = "STOP-EXP-001"
    path_plan: PathPlan | None = None

    def __post_init__(self) -> None:
        if not self.scenario_id:
            raise ValueError("scenario_id must not be empty")
        if self.target_position_m <= 0 and self.path_plan is None:
            raise ValueError("target_position_m must be positive")
        if self.permitted_speed_mps <= 0:
            raise ValueError("permitted_speed_mps must be positive")
        if self.onboard_pax < 0:
            raise ValueError("onboard_pax must be non-negative")
        if self.dt_s <= 0:
            raise ValueError("dt_s must be positive")
        if self.control_period_s < self.dt_s:
            raise ValueError("control_period_s must be at least dt_s")
        control_ratio = self.control_period_s / self.dt_s
        if abs(control_ratio - round(control_ratio)) > 1e-9:
            raise ValueError("control_period_s must be an integer multiple of dt_s")
        if self.max_time_s <= 0:
            raise ValueError("max_time_s must be positive")
        if not self.train_id:
            raise ValueError("train_id must not be empty")

    @property
    def resolved_target_position_m(self) -> float:
        if self.path_plan is not None:
            return self.path_plan.total_length_m
        return self.target_position_m

    def to_dict(self) -> JsonDict:
        path_plan = self.path_plan
        return {
            "scenarioId": self.scenario_id,
            "targetPositionM": self.resolved_target_position_m,
            "permittedSpeedMps": self.permitted_speed_mps,
            "onboardPax": self.onboard_pax,
            "dtS": self.dt_s,
            "controlPeriodS": self.control_period_s,
            "maxTimeS": self.max_time_s,
            "trainId": self.train_id,
            "pathPlanCacheKey": list(path_plan.cache_key()) if path_plan is not None else None,
        }


@dataclass(frozen=True)
class StopExperimentResult:
    scenario: JsonDict
    ato_config: JsonDict
    vehicle_config: JsonDict
    config_fingerprint: str
    ok: bool
    status: str
    metrics: JsonDict
    violations: tuple[str, ...]
    history: tuple[JsonDict, ...]

    def to_dict(self, *, include_history: bool = False) -> JsonDict:
        payload: JsonDict = {
            "schemaVersion": STOP_EXPERIMENT_SCHEMA_VERSION,
            "scenario": self.scenario,
            "atoConfig": self.ato_config,
            "vehicleConfig": self.vehicle_config,
            "configFingerprint": self.config_fingerprint,
            "ok": self.ok,
            "status": self.status,
            "metrics": self.metrics,
            "violations": list(self.violations),
        }
        if include_history:
            payload["history"] = list(self.history)
        return payload


def baseline_ato_config() -> AtoConfig:
    """Return the main-engine ATO baseline used by this experiment."""
    return AtoConfig(
        target_cruise_speed_mps=12.0,
        expected_deceleration_mps2=0.6,
        profile_brake_timing_bias_s=-1.0,
        profile_position_step_m=10.0,
        profile_speed_step_mps=1.0,
        profile_max_states_per_stage=700,
    )


def evaluate_stop_scenario(
    scenario: StopExperimentScenario,
    *,
    ato_config: AtoConfig | None = None,
    vehicle_config: VehicleConfig | None = None,
    optimized_profile: OptimizedSpeedProfile | None = None,
) -> StopExperimentResult:
    requested_config = ato_config or baseline_ato_config()
    # The physics integration step is refined during convergence checks while
    # the ATO remains a fixed-rate sampled controller.  Its configured period
    # is also the correct first-sample slew interval.
    config = replace(requested_config, control_period_s=scenario.control_period_s)
    vehicle_cfg = vehicle_config or VehicleConfig.for_load(
        scenario.train_id,
        scenario.onboard_pax,
    )
    if vehicle_cfg.train_id != scenario.train_id:
        raise ValueError("vehicle_config.train_id must match scenario.train_id")

    target_position_m = scenario.resolved_target_position_m
    vehicle = SimpleVehicleModel(vehicle_cfg)
    drive = TractionDriveModel(vehicle_cfg)
    controller = ATOController(config)
    state = TrainState(
        scenario.train_id,
        position_m=0.0,
        speed_mps=0.0,
        acceleration_mps2=0.0,
        sim_time_s=0.0,
    )
    target = AtoTarget(
        target_position_m=target_position_m,
        permitted_speed_mps=scenario.permitted_speed_mps,
        path_plan=scenario.path_plan,
    )
    _install_candidate_profile(
        controller,
        state,
        target,
        config,
        vehicle_cfg,
        optimized_profile=optimized_profile,
    )

    history: list[JsonDict] = []
    status = "MAX_TIME"
    terminal_window_started = False
    previous_mode: str | None = None
    command_switches = 0
    previous_braking = False
    last_low_speed_release_s: float | None = None
    low_speed_brake_transitions = 0
    rapid_reapplications = 0
    minimum_reapplication_interval_s = math.inf
    emergency_interventions = 0
    emergency_active = False
    traction_brake_overlap_samples = 0
    traction_energy_kwh = 0.0
    regen_available_energy_kwh = 0.0
    auxiliary_energy_kwh = 0.0
    maximum_unconstrained_residual_n = 0.0
    maximum_speed_constraint_reaction_n = 0.0
    non_finite_samples = 0
    active_command: ControlCommand | None = None
    next_control_time_s = 0.0
    last_analysis_acceleration_mps2 = state.acceleration_mps2

    maximum_ticks = math.ceil(scenario.max_time_s / scenario.dt_s)
    for _ in range(maximum_ticks):
        if active_command is None or state.sim_time_s + 1e-9 >= next_control_time_s:
            active_command = controller.decide(state, target)
            next_control_time_s += scenario.control_period_s
        command = active_command
        mode = _command_mode(command)
        if previous_mode is not None and mode != previous_mode:
            command_switches += 1
        previous_mode = mode

        remaining_m = target_position_m - state.position_m
        braking_distance_m = state.speed_mps * state.speed_mps / (
            2.0 * config.expected_deceleration_mps2
        )
        if command.brake_percent > 0 and remaining_m <= (
            braking_distance_m + config.brake_margin_m + config.terminal_brake_guard_margin_m
        ):
            terminal_window_started = True

        demand = drive.demand(command, state.speed_mps)
        blend = BrakeBlendService.blend(demand, 1.0)
        traction_force_n = demand.traction_force_n
        brake_force_n = blend.total_brake_force_n
        electric_brake_force_n = blend.electric_brake_force_n
        grade_ratio = scenario.path_plan.grade_ratio_at(state.position_m) if scenario.path_plan is not None else 0.0
        gradient_force_n = vehicle_cfg.mass_kg * 9.80665 * grade_ratio
        resistance_force_n = vehicle.running_resistance_n(
            state.speed_mps,
            traction_force_n,
            brake_force_n,
        )
        net_force_n = traction_force_n - brake_force_n - resistance_force_n - gradient_force_n
        raw_acceleration_mps2 = net_force_n / vehicle_cfg.mass_kg
        raw_next_speed_mps = state.speed_mps + raw_acceleration_mps2 * scenario.dt_s

        next_state = vehicle.step(
            state,
            command,
            dt_s=scenario.dt_s,
            gradient_force_n=gradient_force_n,
        )
        average_speed_mps = (state.speed_mps + next_state.speed_mps) / 2.0
        traction_step_kwh = _traction_energy_kwh(
            drive,
            traction_force_n,
            average_speed_mps,
            scenario.dt_s,
        )
        regen_step_kwh = _regen_energy_kwh(
            drive,
            electric_brake_force_n,
            average_speed_mps,
            scenario.dt_s,
        )
        auxiliary_step_kwh = vehicle_cfg.auxiliary_power_kw * scenario.dt_s / 3600.0
        traction_energy_kwh += traction_step_kwh
        regen_available_energy_kwh += regen_step_kwh
        auxiliary_energy_kwh += auxiliary_step_kwh

        actual_net_force_n = next_state.acceleration_mps2 * vehicle_cfg.mass_kg
        force_residual_n = abs(net_force_n - actual_net_force_n)
        if 0.0 < raw_next_speed_mps < vehicle_cfg.max_speed_mps:
            maximum_unconstrained_residual_n = max(maximum_unconstrained_residual_n, force_residual_n)
            speed_constraint_reaction_n = 0.0
        else:
            speed_constraint_reaction_n = force_residual_n
            maximum_speed_constraint_reaction_n = max(
                maximum_speed_constraint_reaction_n,
                speed_constraint_reaction_n,
            )

        integration_jerk_mps3 = (
            next_state.acceleration_mps2 - state.acceleration_mps2
        ) / scenario.dt_s
        control_sample_index = round(next_state.sim_time_s / scenario.control_period_s)
        is_control_sample = abs(
            next_state.sim_time_s - control_sample_index * scenario.control_period_s
        ) <= 1e-8
        analysis_jerk_mps3: float | None = None
        if is_control_sample:
            analysis_jerk_mps3 = (
                next_state.acceleration_mps2 - last_analysis_acceleration_mps2
            ) / scenario.control_period_s
            last_analysis_acceleration_mps2 = next_state.acceleration_mps2
        braking = brake_force_n > 100.0
        traction = traction_force_n > 100.0
        if braking and traction:
            traction_brake_overlap_samples += 1
        if command.emergency_brake and not emergency_active:
            emergency_interventions += 1
        emergency_active = command.emergency_brake

        if braking != previous_braking and min(state.speed_mps, next_state.speed_mps) <= 2.0:
            low_speed_brake_transitions += 1
            if not braking:
                last_low_speed_release_s = next_state.sim_time_s
            elif last_low_speed_release_s is not None:
                interval_s = next_state.sim_time_s - last_low_speed_release_s
                minimum_reapplication_interval_s = min(minimum_reapplication_interval_s, interval_s)
                if interval_s <= 2.0:
                    rapid_reapplications += 1
        previous_braking = braking

        sample_values = (
            next_state.position_m,
            next_state.speed_mps,
            next_state.acceleration_mps2,
            integration_jerk_mps3,
            traction_step_kwh,
            regen_step_kwh,
            auxiliary_step_kwh,
        )
        if not all(math.isfinite(value) for value in sample_values):
            non_finite_samples += 1

        history.append({
            "simTimeS": next_state.sim_time_s,
            "positionM": next_state.position_m,
            "remainingDistanceM": target_position_m - next_state.position_m,
            "speedMps": next_state.speed_mps,
            "accelerationMps2": next_state.acceleration_mps2,
            "jerkMps3": analysis_jerk_mps3,
            "integrationJerkMps3": integration_jerk_mps3,
            "targetSpeedMps": controller.last_target_speed_mps,
            "speedErrorMps": controller.last_speed_error_mps,
            "profileMode": controller.last_profile_mode,
            "commandMode": mode,
            "tractionPercent": command.traction_percent,
            "brakePercent": command.brake_percent,
            "tractionForceN": traction_force_n,
            "brakeForceN": brake_force_n,
            "gradientForceN": gradient_force_n,
            "resistanceForceN": resistance_force_n,
            "forceBalanceResidualN": force_residual_n,
            "speedConstraintReactionN": speed_constraint_reaction_n,
            "terminalWindow": terminal_window_started,
            "tractionEnergyStepKwh": traction_step_kwh,
            "regenAvailableEnergyStepKwh": regen_step_kwh,
            "auxiliaryEnergyStepKwh": auxiliary_step_kwh,
        })
        state = next_state

        stop_error_m = state.position_m - target_position_m
        if state.speed_mps <= config.stop_speed_threshold_mps:
            if abs(stop_error_m) <= config.stop_tolerance_m:
                status = "STOPPED_AT_TARGET"
                break
            if stop_error_m > config.stop_tolerance_m:
                status = "OVERSHOT_STOPPED"
                break
        if non_finite_samples:
            status = "NON_FINITE_STATE"
            break

    terminal_jerks = [
        abs(float(sample["jerkMps3"]))
        for sample in history
        if sample["terminalWindow"] and sample["jerkMps3"] is not None
    ]
    all_jerks = [
        abs(float(sample["jerkMps3"]))
        for sample in history
        if sample["jerkMps3"] is not None
    ]
    integration_jerks = [
        abs(float(sample["integrationJerkMps3"]))
        for sample in history
    ]
    comfort_jerks = terminal_jerks or all_jerks
    accelerations = [float(sample["accelerationMps2"]) for sample in history]
    final_error_m = state.position_m - target_position_m
    regen_credited_kwh = regen_available_energy_kwh * vehicle_cfg.regen_efficiency
    net_energy_kwh = traction_energy_kwh + auxiliary_energy_kwh - regen_credited_kwh
    metrics: JsonDict = {
        "rawStopErrorM": final_error_m,
        "absoluteStopErrorM": abs(final_error_m),
        "finalPositionM": state.position_m,
        "arrivalSpeedMps": state.speed_mps,
        "runTimeSec": state.sim_time_s,
        "tickCount": len(history),
        "maximumSpeedMps": max((float(sample["speedMps"]) for sample in history), default=0.0),
        "maximumAccelerationMps2": max(accelerations, default=0.0),
        "maximumDecelerationMps2": abs(min(accelerations, default=0.0)),
        "p95TerminalAbsJerkMps3": _percentile(comfort_jerks, 0.95),
        "rmsTerminalJerkMps3": _rms(comfort_jerks),
        "maximumAbsJerkMps3": max(all_jerks, default=0.0),
        "maximumIntegrationAbsJerkMps3": max(integration_jerks, default=0.0),
        "jerkAnalysisSamplePeriodS": scenario.control_period_s,
        "terminalJerkSampleCount": len(terminal_jerks),
        "commandSwitchCount": command_switches,
        "lowSpeedBrakeTransitionCount": low_speed_brake_transitions,
        "rapidLowSpeedBrakeReapplicationCount": rapid_reapplications,
        "minimumLowSpeedBrakeReapplicationIntervalSec": (
            minimum_reapplication_interval_s if math.isfinite(minimum_reapplication_interval_s) else -1.0
        ),
        "rapidReapplicationThresholdSec": 2.0,
        "tractionBrakeOverlapSampleCount": traction_brake_overlap_samples,
        "emergencyBrakeInterventionCount": emergency_interventions,
        "tractionEnergyKwh": traction_energy_kwh,
        "auxiliaryEnergyKwh": auxiliary_energy_kwh,
        "regenAvailableEnergyKwh": regen_available_energy_kwh,
        "regenCreditedEnergyKwh": regen_credited_kwh,
        "netEnergyKwh": net_energy_kwh,
        "vehicleModelNetEnergyKwhExcludingAuxiliary": state.net_energy_kwh,
        "maximumUnconstrainedForceBalanceResidualN": maximum_unconstrained_residual_n,
        "maximumSpeedConstraintReactionN": maximum_speed_constraint_reaction_n,
        "nonFiniteSampleCount": non_finite_samples,
    }
    violations = _basic_violations(status, metrics, config)
    ato_payload = asdict(config)
    vehicle_payload = vehicle_cfg.to_dict()
    fingerprint = _config_fingerprint(scenario, ato_payload, vehicle_payload)
    return StopExperimentResult(
        scenario=scenario.to_dict(),
        ato_config=ato_payload,
        vehicle_config=vehicle_payload,
        config_fingerprint=fingerprint,
        ok=not violations,
        status=status,
        metrics=metrics,
        violations=tuple(violations),
        history=tuple(history),
    )


def run_time_step_preflight(
    scenario: StopExperimentScenario,
    *,
    time_steps_s: Iterable[float] = (0.1, 0.05),
    ato_config: AtoConfig | None = None,
) -> JsonDict:
    steps = sorted({float(value) for value in time_steps_s}, reverse=True)
    if len(steps) < 2 or any(value <= 0 for value in steps):
        raise ValueError("time_steps_s must contain at least two positive values")
    config = ato_config or baseline_ato_config()
    results = [
        evaluate_stop_scenario(replace(scenario, dt_s=dt_s), ato_config=config)
        for dt_s in steps
    ]
    reference = results[-1]
    comparisons: list[JsonDict] = []
    for dt_s, result in zip(steps[:-1], results[:-1]):
        stop_error_delta_m = abs(
            float(result.metrics["rawStopErrorM"]) - float(reference.metrics["rawStopErrorM"])
        )
        energy_relative_delta = _relative_delta(
            float(result.metrics["netEnergyKwh"]),
            float(reference.metrics["netEnergyKwh"]),
        )
        jerk_relative_delta = _relative_delta(
            float(result.metrics["p95TerminalAbsJerkMps3"]),
            float(reference.metrics["p95TerminalAbsJerkMps3"]),
        )
        comparisons.append({
            "candidateDtS": dt_s,
            "referenceDtS": steps[-1],
            "stopErrorDeltaM": stop_error_delta_m,
            "netEnergyRelativeDelta": energy_relative_delta,
            "p95JerkRelativeDelta": jerk_relative_delta,
            "passed": (
                result.ok
                and reference.ok
                and stop_error_delta_m <= 0.10
                and energy_relative_delta <= 0.01
                and jerk_relative_delta <= 0.10
            ),
        })
    passed = all(result.ok for result in results) and all(item["passed"] for item in comparisons)
    return {
        "schemaVersion": STOP_EXPERIMENT_SCHEMA_VERSION,
        "stage": "preflight",
        "passed": passed,
        "criteria": {
            "maximumStopErrorDeltaM": 0.10,
            "maximumNetEnergyRelativeDelta": 0.01,
            "maximumP95JerkRelativeDelta": 0.10,
        },
        "runs": [result.to_dict() for result in results],
        "comparisons": comparisons,
    }


def _install_candidate_profile(
    controller: ATOController,
    state: TrainState,
    target: AtoTarget,
    config: AtoConfig,
    vehicle_config: VehicleConfig,
    *,
    optimized_profile: OptimizedSpeedProfile | None = None,
) -> None:
    if not config.use_dynamic_programming_profile:
        return
    profile = optimized_profile or build_candidate_profile(
        target,
        config,
        vehicle_config,
    )
    controller.install_profile(state, target, profile)
    controller.allow_profile_compute = False


def build_candidate_profile(
    target: AtoTarget,
    config: AtoConfig,
    vehicle_config: VehicleConfig,
) -> OptimizedSpeedProfile:
    """Build the DCDP profile whose full inputs are tracked by an experiment candidate."""
    acceleration_mps2 = max(
        0.05,
        (vehicle_config.max_traction_force_n - vehicle_config.basic_resistance_n) / vehicle_config.mass_kg,
    )
    scheduled_run_time_s = config.profile_run_time_s or estimate_scheduled_run_time_s(
        target_position_m=target.target_position_m,
        permitted_speed_mps=min(target.permitted_speed_mps, config.target_cruise_speed_mps),
        acceleration_mps2=acceleration_mps2,
        deceleration_mps2=config.expected_deceleration_mps2,
        runtime_margin_ratio=config.profile_runtime_margin_ratio,
    )
    return optimize_speed_profile_dcdp(
        target_position_m=target.target_position_m,
        permitted_speed_mps=min(target.permitted_speed_mps, config.target_cruise_speed_mps),
        scheduled_run_time_s=scheduled_run_time_s,
        vehicle_config=vehicle_config,
        dt_s=config.profile_time_step_s,
        position_step_m=config.profile_position_step_m,
        speed_step_mps=config.profile_speed_step_mps,
        terminal_tolerance_m=config.stop_tolerance_m,
        max_states_per_stage=config.profile_max_states_per_stage,
        path_plan=target.path_plan,
    )


def _basic_violations(status: str, metrics: JsonDict, config: AtoConfig) -> list[str]:
    violations: list[str] = []
    if status != "STOPPED_AT_TARGET":
        violations.append(status)
    if float(metrics["absoluteStopErrorM"]) > config.stop_tolerance_m:
        violations.append("STOP_ERROR_LIMIT")
    if float(metrics["arrivalSpeedMps"]) > config.stop_speed_threshold_mps:
        violations.append("ARRIVAL_SPEED_LIMIT")
    if float(metrics["maximumDecelerationMps2"]) > 1.4:
        violations.append("SERVICE_DECELERATION_LIMIT")
    if int(metrics["rapidLowSpeedBrakeReapplicationCount"]) > 0:
        violations.append("RAPID_LOW_SPEED_BRAKE_REAPPLICATION")
    if int(metrics["tractionBrakeOverlapSampleCount"]) > 0:
        violations.append("TRACTION_BRAKE_OVERLAP")
    if int(metrics["emergencyBrakeInterventionCount"]) > 0:
        violations.append("EMERGENCY_BRAKE_INTERVENTION")
    if int(metrics["nonFiniteSampleCount"]) > 0:
        violations.append("NON_FINITE_SAMPLE")
    if float(metrics["maximumUnconstrainedForceBalanceResidualN"]) > 1e-6:
        violations.append("DYNAMICS_FORCE_BALANCE")
    return violations


def _config_fingerprint(
    scenario: StopExperimentScenario,
    ato_config: JsonDict,
    vehicle_config: JsonDict,
) -> str:
    payload = {
        "schemaVersion": STOP_EXPERIMENT_SCHEMA_VERSION,
        "scenario": scenario.to_dict(),
        "atoConfig": ato_config,
        "vehicleConfig": vehicle_config,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _traction_energy_kwh(
    drive: TractionDriveModel,
    traction_force_n: float,
    average_speed_mps: float,
    dt_s: float,
) -> float:
    if traction_force_n <= 0:
        return 0.0
    capacity_n = max(drive.traction_capacity_n(average_speed_mps), 1.0)
    factor = min(traction_force_n / capacity_n, 1.0)
    return drive.traction_energy_rate_kw(average_speed_mps) * factor * dt_s / 3600.0


def _regen_energy_kwh(
    drive: TractionDriveModel,
    electric_brake_force_n: float,
    average_speed_mps: float,
    dt_s: float,
) -> float:
    if electric_brake_force_n <= 0:
        return 0.0
    capacity_n = drive.electric_brake_capacity_n(average_speed_mps)
    factor = min(electric_brake_force_n / max(capacity_n, 1.0), 1.0) if capacity_n > 0 else 0.0
    return drive.brake_energy_rate_kw(average_speed_mps) * factor * dt_s / 3600.0


def _command_mode(command: Any) -> str:
    if command.emergency_brake:
        return "EMERGENCY_BRAKE"
    if command.traction_percent > 0:
        return "TRACTION"
    if command.brake_percent > 0:
        return "BRAKE"
    return "COAST"


def _percentile(values: list[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    ratio = position - lower
    return ordered[lower] * (1.0 - ratio) + ordered[upper] * ratio


def _rms(values: list[float]) -> float:
    if not values:
        return 0.0
    return math.sqrt(sum(value * value for value in values) / len(values))


def _relative_delta(candidate: float, reference: float) -> float:
    return abs(candidate - reference) / max(abs(reference), 1e-12)
