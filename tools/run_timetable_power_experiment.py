from __future__ import annotations

import argparse
from collections import defaultdict, deque
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.engine import SimulationEngine
from app.domain.control.models import AtoTarget
from app.domain.power.joint_optimization import (
    BASELINE_CANDIDATE,
    VARIABLE_BOUNDS,
    JointExperimentConfig,
    JointPowerEvaluator,
    Nsga2JointOptimizer,
    relative_utility,
    run_random_search,
    summarize_repeats,
)
from app.domain.power.trajectory import (
    EngineSnapshotTrajectoryAdapter,
    JsonlTrajectoryProvider,
    JsonlTrajectoryRecorder,
    TrajectoryFrame,
    validate_trajectory_frames,
)
from app.domain.vehicle.models import TrainState


DEFAULT_SCENARIO = ROOT / "data" / "scenarios" / "line9_timetable_operation.json"
TOPOLOGY = ROOT / "data" / "scenarios" / "line9_power_topology.json"
DEFAULT_TRAJECTORY = ROOT / "outputs" / "timetable-power-trajectory-v1.jsonl"
DEFAULT_OUTPUT = ROOT / "outputs" / "timetable-power-experiment-v1.json"
LIVE_STORAGE_BASELINE_CANDIDATE = {
    **BASELINE_CANDIDATE,
    # A zero-throughput reference is SOC-neutral and keeps the comparison
    # feasible even when the captured window is not a periodic duty cycle.
    "storageChargeLimitKw": 0.0,
    "storageDischargeLimitKw": 0.0,
    "storageTriggerKw": 1000.0,
}
LIVE_STORAGE_VARIABLE_BOUNDS = {
    **VARIABLE_BOUNDS,
    # The original V2 proxy studied a denser 12-train load and therefore used
    # a 3.4-3.8 MW discharge threshold. Four live timetable duties do not
    # reliably create that local demand at TS-0905, so search from disabled to
    # the installed engineering-estimate ratings and topology default trigger.
    "storageChargeLimitKw": (0.0, 2000.0),
    "storageDischargeLimitKw": (0.0, 2000.0),
    "storageTriggerKw": (0.0, 2500.0),
}
STORAGE_REMEDIABLE_CONSTRAINTS = frozenset({
    "minimumVoltage",
    "substationCapacity",
    "terminalSoc",
})


def _non_storage_constraints_pass(evaluation: Mapping[str, Any]) -> bool:
    """Return whether a replay is valid apart from storage-remediable limits."""
    constraints = evaluation.get("constraints", {})
    return bool(constraints) and all(
        bool(passed)
        for name, passed in constraints.items()
        if name not in STORAGE_REMEDIABLE_CONSTRAINTS
    )


def configure_engine_timing_candidate(
    engine: SimulationEngine,
    candidate: Mapping[str, float] | None,
) -> dict[str, Any]:
    """Apply experiment timing variables before the operation plan is built."""
    values = dict(candidate or {})
    departure_spread_sec = float(values.get("departureSpreadSec", 0.0))
    traction_timing_sec = float(values.get("tractionTimingSec", 0.0))
    brake_timing_sec = float(values.get("brakeTimingSec", 0.0))
    if not 0.0 <= departure_spread_sec <= 30.0:
        raise ValueError("departureSpreadSec must be within [0, 30]")
    if abs(traction_timing_sec) > 5.0 or abs(brake_timing_sec) > 5.0:
        raise ValueError("tractionTimingSec and brakeTimingSec must be within +/- 5")

    engine._ato_config = replace(
        engine._ato_config,
        profile_traction_timing_bias_s=traction_timing_sec,
        profile_brake_timing_bias_s=brake_timing_sec,
    )
    headway = engine.timetable_service.headway_config
    adjusted_periods = {
        name: float(seconds) + departure_spread_sec
        for name, seconds in headway.period_headway_sec.items()
    }
    engine.timetable_service.headway_config = replace(
        headway,
        period_headway_sec=adjusted_periods,
    )
    return {
        "candidate": {
            "departureSpreadSec": departure_spread_sec,
            "tractionTimingSec": traction_timing_sec,
            "brakeTimingSec": brake_timing_sec,
        },
        "semantics": {
            "departureSpreadSec": "ADDED_TO_PERIOD_HEADWAY",
            "tractionTimingSec": "DCDP_TRACTION_PHASE_DELAY_POSITIVE",
            "brakeTimingSec": "DCDP_BRAKE_PHASE_DELAY_POSITIVE",
        },
        "periodHeadwaySec": adjusted_periods,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Capture a fixed-roster timetable trajectory from the main engine and "
            "run a replay-safe supercapacitor-control experiment."
        )
    )
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--max-duties", type=int, default=4)
    parser.add_argument("--capture-seconds", type=int, default=240)
    parser.add_argument("--tick-seconds", type=float, default=0.25)
    parser.add_argument("--evaluation-step-seconds", type=float, default=1.0)
    parser.add_argument("--seeds", default="20260714,20260717,20260719")
    parser.add_argument("--population", type=int, default=8)
    parser.add_argument("--generations", type=int, default=2)
    parser.add_argument("--wall-timeout-sec", type=float, default=300.0)
    parser.add_argument(
        "--profile-prewarm-timeout-sec",
        type=float,
        default=600.0,
        help="Wait for every operation-plan DCDP profile before advancing simulation time.",
    )
    parser.add_argument(
        "--profile-cache-dir",
        type=Path,
        default=None,
        help="Optional DCDP cache directory, useful for cold-cache reproducibility checks.",
    )
    parser.add_argument("--trajectory", type=Path, default=DEFAULT_TRAJECTORY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_revision() -> dict[str, Any]:
    def run(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    return {
        "branch": run("branch", "--show-current"),
        "commit": run("rev-parse", "HEAD"),
        "dirty": bool(run("status", "--porcelain")),
    }


def _operation_tracking_metrics(engine: SimulationEngine, snapshots: list[Any]) -> dict[str, float]:
    speed_error_squared = 0.0
    speed_sample_count = 0
    maximum_speed_mps = 0.0
    for snapshot in snapshots:
        for train in snapshot.trains:
            speed_mps = float(train.get("speedMps", 0.0))
            maximum_speed_mps = max(maximum_speed_mps, speed_mps)
            if train.get("lifecycleState") not in {"IN_SERVICE", "TURNBACK"}:
                continue
            target_speed_mps = float(train.get("targetSpeedMps", speed_mps))
            # ATO target speed is a position/look-ahead ceiling, not a demand
            # for the vehicle to jump to that value within the current tick.
            # Underspeed is covered by the independently measured runtime KPI;
            # this metric therefore measures only speed-profile exceedance.
            speed_error_squared += max(speed_mps - target_speed_mps, 0.0) ** 2
            speed_sample_count += 1

    events = list(getattr(engine, "_operation_events", ()))
    departure_events = [
        event for event in events
        if event.get("event") == "DEPARTURE" and event.get("deviationSec") is not None
    ]
    departure_deviations = [abs(float(event["deviationSec"])) for event in departure_events]
    first_departure_by_service: dict[str, dict[str, Any]] = {}
    for event in departure_events:
        service_id = str(event.get("serviceId", ""))
        if service_id and service_id not in first_departure_by_service:
            first_departure_by_service[service_id] = event

    runtime_deviations: list[float] = []
    for event in events:
        if event.get("event") != "ARRIVAL":
            continue
        departure = first_departure_by_service.get(str(event.get("serviceId", "")))
        if departure is None:
            continue
        actual_departure_ms = departure.get("actualTimeMs")
        planned_departure_ms = departure.get("plannedTimeMs")
        actual_arrival_ms = event.get("actualTimeMs")
        planned_arrival_ms = event.get("plannedTimeMs")
        if None in {
            actual_departure_ms,
            planned_departure_ms,
            actual_arrival_ms,
            planned_arrival_ms,
        }:
            continue
        actual_runtime_sec = (int(actual_arrival_ms) - int(actual_departure_ms)) / 1000.0
        planned_runtime_sec = (int(planned_arrival_ms) - int(planned_departure_ms)) / 1000.0
        runtime_deviations.append(abs(actual_runtime_sec - planned_runtime_sec))

    return {
        "speedTrackingRmseMps": math.sqrt(
            speed_error_squared / max(speed_sample_count, 1)
        ),
        "meanDepartureDeviationSec": (
            sum(departure_deviations) / len(departure_deviations)
            if departure_deviations else 0.0
        ),
        "maxDepartureDeviationSec": max(departure_deviations, default=0.0),
        "runtimeDeviationSec": max(runtime_deviations, default=0.0),
        # PathPlan completion anchors the stopped train head at the selected
        # platform reference. This is a reference-consistency metric, not a
        # claim about field balise measurement accuracy.
        "stopPositionErrorM": 0.0,
        "maximumSpeedMps": maximum_speed_mps,
    }


def _control_quality(frames: list[TrajectoryFrame]) -> dict[str, Any]:
    previous: dict[str, tuple[bool, float, bool]] = {}
    last_low_speed_release_ms: dict[str, int] = {}
    low_speed_transitions = 0
    rapid_reapplications = 0
    emergency_brake_interventions = 0
    traction_brake_overlaps = 0
    minimum_reapplication_interval_sec = float("inf")
    rapid_reapplication_events: list[dict[str, Any]] = []
    emergency_brake_events: list[dict[str, Any]] = []
    for frame in frames:
        for sample in frame.samples:
            braking = sample.total_brake_force_n > 100.0
            traction = sample.traction_force_n > 100.0
            if braking and traction:
                traction_brake_overlaps += 1
            prior = previous.get(sample.train_id)
            previous[sample.train_id] = (
                braking,
                sample.speed_mps,
                sample.emergency_brake_active,
            )
            if sample.emergency_brake_active and (prior is None or not prior[2]):
                emergency_brake_interventions += 1
                emergency_brake_events.append({
                    "trainId": sample.train_id,
                    "appliedAtMs": frame.sim_time_ms,
                    "speedMps": sample.speed_mps,
                    "phase": sample.phase,
                    "currentStationCode": sample.current_station_code,
                    "nextStationCode": sample.next_station_code,
                    "commandSource": sample.command_source,
                })
            if prior is None or braking == prior[0]:
                continue
            low_speed = min(prior[1], sample.speed_mps) <= 2.0
            if not low_speed:
                continue
            low_speed_transitions += 1
            if not braking:
                last_low_speed_release_ms[sample.train_id] = frame.sim_time_ms
                continue
            released_ms = last_low_speed_release_ms.get(sample.train_id)
            if released_ms is None:
                continue
            interval_sec = (frame.sim_time_ms - released_ms) / 1000.0
            minimum_reapplication_interval_sec = min(
                minimum_reapplication_interval_sec,
                interval_sec,
            )
            if interval_sec <= 2.0:
                rapid_reapplications += 1
                rapid_reapplication_events.append({
                    "trainId": sample.train_id,
                    "releasedAtMs": released_ms,
                    "reappliedAtMs": frame.sim_time_ms,
                    "intervalSec": interval_sec,
                    "speedMps": sample.speed_mps,
                    "phase": sample.phase,
                    "currentStationCode": sample.current_station_code,
                    "nextStationCode": sample.next_station_code,
                })
    return {
        "lowSpeedBrakeTransitionCount": low_speed_transitions,
        "rapidLowSpeedBrakeReapplicationCount": rapid_reapplications,
        "rapidLowSpeedBrakeReapplications": rapid_reapplication_events,
        "minimumLowSpeedBrakeReapplicationIntervalSec": (
            minimum_reapplication_interval_sec
            if math.isfinite(minimum_reapplication_interval_sec) else -1.0
        ),
        "tractionBrakeOverlapSampleCount": traction_brake_overlaps,
        "emergencyBrakeInterventionCount": emergency_brake_interventions,
        "emergencyBrakeInterventions": emergency_brake_events,
        "rapidReapplicationThresholdSec": 2.0,
    }


def _compact_train_diagnostic(train: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "trainId",
        "dutyId",
        "serviceId",
        "lifecycleState",
        "phase",
        "direction",
        "currentStationCode",
        "nextStationCode",
        "speedMps",
        "pathPositionM",
        "pathTotalLengthM",
        "distanceToNextM",
        "tractionPercent",
        "brakePercent",
        "emergencyBrakeActive",
        "commandSource",
        "departureAuthorized",
        "interlockingHoldReason",
        "activeRouteIds",
        "turnbackCount",
        "turnbackState",
        "movementAuthorityEndM",
        "movementAuthorityReason",
        "movementAuthoritySpeedMps",
        "scheduleDeviationSec",
    )
    return {name: train.get(name) for name in fields}


def _wait_for_operation_profile_prewarm(
    engine: SimulationEngine,
    timeout_sec: float,
) -> dict[str, Any]:
    """Make batch trajectories independent of persistent DCDP cache state."""
    profile_keys = tuple(engine._operation_profile_requests)
    status = engine.speed_profile_service.wait_for(
        profile_keys,
        max(0.0, timeout_sec),
    )
    engine._refresh_operation_profile_warmup()
    if status["failedProfileCount"]:
        raise RuntimeError(f"DCDP_PROFILE_PREWARM_FAILED:{status['errors']}")
    if not status["ready"]:
        raise TimeoutError(
            f"DCDP_PROFILE_PREWARM_TIMEOUT:{status['pendingCacheKeys']}"
        )
    return status


def capture_timetable_trajectory(
    *,
    scenario_path: Path,
    max_duties: int,
    capture_seconds: int,
    tick_seconds: float,
    wall_timeout_sec: float,
    timing_candidate: Mapping[str, float] | None = None,
    profile_prewarm_timeout_sec: float = 600.0,
    profile_cache_dir: Path | None = None,
) -> tuple[list[TrajectoryFrame], dict[str, Any]]:
    engine = SimulationEngine.load_from_files(
        scenario_path=scenario_path,
        line_map_path=ROOT / "data" / "cache" / "line_map.json",
        stations_csv_path=ROOT / "data" / "line9" / "stations.csv",
    )
    if profile_cache_dir is not None:
        engine.speed_profile_service.cache_dir = profile_cache_dir
    operation_plan = replace(
        engine.scenario.operation_plan,
        max_duties=max(2, max_duties),
    )
    engine.scenario = replace(
        engine.scenario,
        tick_seconds=max(0.05, tick_seconds),
        operation_plan=operation_plan,
    )
    engine.clock.tick_seconds = engine.scenario.tick_seconds
    engine._snapshot_interval_ticks = 1
    timing_control = configure_engine_timing_candidate(engine, timing_candidate)

    frames: list[TrajectoryFrame] = []
    captured_snapshots: list[Any] = []
    recent_control_states: dict[str, deque[dict[str, Any]]] = defaultdict(
        lambda: deque(maxlen=24)
    )
    control_state_history: dict[str, list[dict[str, Any]]] = defaultdict(list)
    previous_local_limit: dict[str, float] = {}
    speed_limit_transitions: list[dict[str, Any]] = []
    adapter = EngineSnapshotTrajectoryAdapter()
    capture_start_ms: int | None = None
    deadline = float("inf")
    next_progress_at = float("inf")
    last_diagnostic: dict[str, Any] = {}
    profile_warmup_before_clock_start: dict[str, Any] = {}
    try:
        load_started_at = time.monotonic()
        print("[capture] ENGINE_LOAD_STARTED", file=sys.stderr, flush=True)
        engine.load()
        load_elapsed_sec = time.monotonic() - load_started_at
        print(
            f"[capture] ENGINE_LOAD_COMPLETED elapsedSec={load_elapsed_sec:.3f}",
            file=sys.stderr,
            flush=True,
        )
        profile_warmup_before_clock_start = _wait_for_operation_profile_prewarm(
            engine,
            profile_prewarm_timeout_sec,
        )
        print(
            "[capture] "
            + json.dumps({
                "event": "PROFILE_PREWARM_COMPLETED",
                **profile_warmup_before_clock_start,
            }, ensure_ascii=False),
            file=sys.stderr,
            flush=True,
        )
        deadline = time.monotonic() + max(1.0, wall_timeout_sec)
        next_progress_at = time.monotonic() + 60.0
        engine.clock.start()
        while time.monotonic() < deadline:
            engine._tick()
            snapshot = engine.snapshot()
            if snapshot is None:
                continue
            frame = adapter.frame_from_snapshot(snapshot)
            state = engine.operation_plan_state()
            acceptance = state["acceptance"]
            profile_warmup = state["profileWarmup"]
            last_diagnostic = {
                "simTimeMs": frame.sim_time_ms,
                "captureStarted": capture_start_ms is not None,
                "acceptance": acceptance,
                "profileWarmup": profile_warmup,
            }
            if time.monotonic() >= next_progress_at:
                print(
                    "[capture] " + json.dumps(last_diagnostic, ensure_ascii=False),
                    file=sys.stderr,
                    flush=True,
                )
                next_progress_at = time.monotonic() + 60.0
            ready = (
                acceptance["startedServiceCount"] >= acceptance["totalDutyCount"]
                and acceptance["stuckTrainCount"] == 0
                and bool(profile_warmup.get("allProfilesReady", profile_warmup.get("ready", False)))
            )
            if capture_start_ms is None and ready:
                capture_start_ms = frame.sim_time_ms
            if capture_start_ms is None:
                continue
            frames.append(frame)
            captured_snapshots.append(snapshot)
            sim_trains = {item.train_id: item for item in engine.trains}
            for train_state in snapshot.trains:
                train_id = str(train_state.get("trainId", ""))
                local_limit = float(train_state.get("localSpeedLimitMps", 0.0))
                controller = engine._ato_for_train(train_id)
                profile = controller.current_profile
                sim_train = sim_trains[train_id]
                runtime_target = AtoTarget(
                    target_position_m=sim_train.movement_authority_end_m,
                    permitted_speed_mps=max(
                        0.05,
                        min(
                            sim_train.permitted_speed_mps,
                            sim_train.movement_authority_speed_mps,
                        ),
                    ),
                    path_plan=sim_train._path_plan,
                )
                runtime_state = TrainState(
                    train_id=train_id,
                    position_m=sim_train.path_position_m,
                    speed_mps=sim_train.speed_mps,
                    sim_time_s=engine.clock.sim_time_seconds,
                )
                runtime_cache_key = controller._make_profile_cache_key(
                    runtime_state,
                    runtime_target,
                )
                installed_cache_key = controller._profile_cache_key
                trace = recent_control_states[train_id]
                control_record = {
                    "simTimeMs": snapshot.sim_time_ms,
                    "pathPositionM": train_state.get("pathPositionM"),
                    "speedMps": train_state.get("speedMps"),
                    "targetSpeedMps": train_state.get("targetSpeedMps"),
                    "localSpeedLimitMps": local_limit,
                    "tractionPercent": train_state.get("tractionPercent"),
                    "brakePercent": train_state.get("brakePercent"),
                    "emergencyBrakeActive": train_state.get("emergencyBrakeActive"),
                    "commandSource": train_state.get("commandSource"),
                    "movementAuthorityEndM": train_state.get("movementAuthorityEndM"),
                    "movementAuthorityReason": train_state.get("movementAuthorityReason"),
                    "movementAuthoritySpeedMps": train_state.get("movementAuthoritySpeedMps"),
                    "accelerationMps2": train_state.get("accelerationMps2"),
                    "phase": train_state.get("phase"),
                    "currentSegmentId": train_state.get("currentSegmentId"),
                    "profileInstalled": profile is not None,
                    "profileMode": controller.last_profile_mode,
                    "profileTargetPositionM": (
                        round(profile.target_position_m, 3) if profile is not None else None
                    ),
                    "profilePermittedSpeedMps": (
                        round(profile.permitted_speed_mps, 3) if profile is not None else None
                    ),
                    "profileCacheKeyMatchesRuntime": (
                        installed_cache_key == runtime_cache_key
                    ),
                    "installedCacheKeyCore": (
                        list(installed_cache_key[1:9])
                        if installed_cache_key is not None else None
                    ),
                    "runtimeCacheKeyCore": list(runtime_cache_key[1:9]),
                }
                trace.append(control_record)
                control_state_history[train_id].append(control_record)
                old_limit = previous_local_limit.get(train_id)
                if old_limit is not None and local_limit < old_limit - 0.05:
                    speed_limit_transitions.append({
                        "trainId": train_id,
                        "fromLimitMps": old_limit,
                        "toLimitMps": local_limit,
                        "trace": list(trace),
                    })
                previous_local_limit[train_id] = local_limit
            if frame.sim_time_ms >= capture_start_ms + capture_seconds * 1000:
                break
        else:
            raise TimeoutError(
                "TIMETABLE_TRAJECTORY_CAPTURE_TIMEOUT: "
                + json.dumps(last_diagnostic, ensure_ascii=False)
            )

        if not frames or frames[-1].sim_time_ms < frames[0].sim_time_ms + capture_seconds * 1000:
            raise TimeoutError("TIMETABLE_TRAJECTORY_CAPTURE_INCOMPLETE")
        final_state = engine.operation_plan_state()
        tracking_metrics = _operation_tracking_metrics(engine, captured_snapshots)
        coverage = {
            "movingSampleCount": sum(
                sample.speed_mps >= 0.5 for frame in frames for sample in frame.samples
            ),
            "tractionSampleCount": sum(
                sample.traction_force_n > 1.0 for frame in frames for sample in frame.samples
            ),
            "brakingSampleCount": sum(
                sample.electric_brake_force_n > 1.0 for frame in frames for sample in frame.samples
            ),
            "regenSampleCount": sum(
                (sample.regen_power_available_kw or 0.0) > 1e-6
                for frame in frames for sample in frame.samples
            ),
        }
        control_quality = _control_quality(frames)
        for event in control_quality["rapidLowSpeedBrakeReapplications"]:
            released_at_ms = int(event["releasedAtMs"])
            reapplied_at_ms = int(event["reappliedAtMs"])
            event["controlTrace"] = [
                item
                for item in control_state_history[str(event["trainId"])]
                if released_at_ms - 3_000
                <= int(item["simTimeMs"])
                <= reapplied_at_ms + 1_000
            ]
        for event in control_quality["emergencyBrakeInterventions"]:
            applied_at_ms = int(event["appliedAtMs"])
            event["controlTrace"] = [
                item
                for item in control_state_history[str(event["trainId"])]
                if applied_at_ms - 3_000
                <= int(item["simTimeMs"])
                <= applied_at_ms + 1_000
            ]
        final_snapshot = captured_snapshots[-1]
        final_acceptance = final_state["acceptance"]
        stuck_train_ids = {
            str(item["trainId"])
            for item in final_acceptance["stuckTrains"]
        }
        interlocking_snapshot = final_snapshot.interlocking
        operation_diagnostics = {
            "trainStates": [
                _compact_train_diagnostic(item) for item in final_snapshot.trains
            ],
            "stuckTrainStates": [
                _compact_train_diagnostic(item) for item in final_snapshot.trains
                if str(item.get("trainId", "")) in stuck_train_ids
            ],
            "lockedRoutes": [
                item for item in interlocking_snapshot.get("routes", [])
                if item.get("state") in {"LOCKED", "APPROACH_LOCKED"}
            ],
            "departureAuthorities": list(
                interlocking_snapshot.get("departureAuthorities", [])
            ),
            "recentEvents": list(final_state["recentEvents"][-30:]),
        }
        metadata = {
            "scenarioId": engine.scenario.name,
            "scenarioSha256": _sha256(scenario_path),
            "planHash": final_state["planHash"],
            "passengerProfileId": final_state["experimentManifest"]["passengerProfileId"],
            "captureStartTimeMs": frames[0].sim_time_ms,
            "captureEndTimeMs": frames[-1].sim_time_ms,
            "captureSeconds": capture_seconds,
            "trainCount": len(frames[0].samples),
            "trackingMetrics": tracking_metrics,
            "trackingEvidence": {
                "speed": "MAIN_ENGINE_OVERSPEED_RMSE; UNDERSPEED_COVERED_BY_RUNTIME",
                "departureAndRuntime": "OPERATION_PLAN_EVENTS",
                "stopPosition": "PATHPLAN_PLATFORM_REFERENCE_ANCHOR",
            },
            "operationAcceptanceAtCaptureEnd": final_acceptance,
            "operationDiagnosticsAtCaptureEnd": operation_diagnostics,
            "speedLimitTransitions": speed_limit_transitions,
            "profileWarmup": final_state["profileWarmup"],
            "profileWarmupBeforeClockStart": profile_warmup_before_clock_start,
            "coverage": coverage,
            "controlQuality": control_quality,
            "timingControl": timing_control,
        }
        return frames, metadata
    finally:
        engine.speed_profile_service.shutdown()


def _write_trajectory(path: Path, frames: list[TrajectoryFrame], metadata: dict[str, Any]) -> None:
    with JsonlTrajectoryRecorder(path, metadata={
        key: value for key, value in metadata.items() if key != "trackingMetrics"
    }) as recorder:
        for frame in frames:
            recorder.write(frame)
        recorder.write_metadata({"trackingMetrics": metadata["trackingMetrics"]})


def run_storage_experiment(
    *,
    trajectory_path: Path,
    metadata: dict[str, Any],
    capture_seconds: int,
    evaluation_step_seconds: float,
    seeds: list[int],
    population_size: int,
    generations: int,
) -> dict[str, Any]:
    provider = JsonlTrajectoryProvider(trajectory_path)
    operation_tolerance = float(
        metadata["operationAcceptanceAtCaptureEnd"]["maxScheduleDeviationSec"]
    )
    config = JointExperimentConfig(
        train_count=int(metadata["trainCount"]),
        start_time_ms=int(metadata["captureStartTimeMs"]),
        horizon_sec=capture_seconds,
        time_step_sec=evaluation_step_seconds,
        nominal_max_speed_mps=max(22.22, metadata["trackingMetrics"]["maximumSpeedMps"]),
        max_service_deceleration_mps2=1.40,
        # Enforce comparable initial/final storage state directly instead of
        # relying only on an objective correction for retained energy.
        max_terminal_soc_deviation=0.05,
        max_departure_deviation_sec=operation_tolerance,
        max_runtime_deviation_sec=operation_tolerance,
    )
    evaluator = JointPowerEvaluator(
        TOPOLOGY,
        config,
        trajectory_provider=provider,
        variable_bounds=LIVE_STORAGE_VARIABLE_BOUNDS,
        baseline_candidate=LIVE_STORAGE_BASELINE_CANDIDATE,
    )
    baseline = evaluator.evaluate(evaluator.baseline_candidate)
    no_storage_baseline = evaluator.evaluate(
        evaluator.baseline_candidate,
        storage_enabled=False,
    )
    if not _non_storage_constraints_pass(baseline):
        raise RuntimeError(
            "TIMETABLE_POWER_BASELINE_REPLAY_INVALID: "
            f"{baseline['constraintViolations']}"
        )

    optimizer = Nsga2JointOptimizer(evaluator)
    repeats = [
        optimizer.run(
            "STORAGE_ONLY",
            seed=seed,
            population_size=population_size,
            generations=generations,
        )
        for seed in seeds
    ]
    recommended = min(
        (repeat["recommended"] for repeat in repeats),
        key=lambda item: relative_utility(item, baseline),
    )
    half_step_validation = evaluator.evaluate(
        recommended["candidate"],
        time_step_sec=evaluation_step_seconds / 2.0,
    )
    random_runs = [
        run_random_search(
            evaluator,
            "STORAGE_ONLY",
            seed=repeat["seed"],
            evaluation_count=repeat["evaluationCount"],
        )
        for repeat in repeats
    ]
    improvements = {
        name: (1.0 - recommended["objectives"][name] / max(value, 1e-9)) * 100.0
        for name, value in baseline["objectives"].items()
    }
    execution_gates = {
        "baselineNonStorageConstraintsPassed": _non_storage_constraints_pass(baseline),
        "noStorageBaselineNonStorageConstraintsPassed": (
            _non_storage_constraints_pass(no_storage_baseline)
        ),
        "allRecommendedFeasible": all(
            repeat["recommended"]["feasible"] for repeat in repeats
        ),
        "halfStepValidationFeasible": half_step_validation["feasible"],
        "operationalMetricsAvailable": bool(
            baseline["metrics"].get("operationalMetricsAvailable", 0.0) >= 1.0
        ),
        "strictTerminalSocEquivalent5Percent": (
            recommended["metrics"]["terminalSocDeviation"] <= 0.05
        ),
        "noRapidLowSpeedBrakeReapplication": (
            metadata["controlQuality"]["rapidLowSpeedBrakeReapplicationCount"] == 0
        ),
        "noEmergencyBrakeIntervention": (
            metadata["controlQuality"].get("emergencyBrakeInterventionCount", 0) == 0
        ),
        "noTractionBrakeOverlap": (
            metadata["controlQuality"]["tractionBrakeOverlapSampleCount"] == 0
        ),
    }
    hypothesis_checks = {
        "recommendedSatisfiesAllConstraints": recommended["feasible"],
        "recommendedDoesNotIncreaseNetEnergy": (
            recommended["objectives"]["netAcGridEnergyKwh"]
            <= baseline["objectives"]["netAcGridEnergyKwh"]
        ),
        "recommendedDoesNotIncreasePeak": (
            recommended["objectives"]["aggregateAcGridPeakKw"]
            <= baseline["objectives"]["aggregateAcGridPeakKw"]
        ),
        "recommendedDoesNotIncreaseWastedRegenRatio": (
            recommended["objectives"]["wastedRegenRatio"]
            <= baseline["objectives"]["wastedRegenRatio"]
        ),
        "strictTerminalSocEquivalent5Percent": (
            recommended["metrics"]["terminalSocDeviation"] <= 0.05
        ),
    }
    return {
        "experimentId": "TIMETABLE-POWER-STORAGE-REPLAY-V1",
        "status": "COMPLETED",
        "quality": "ENGINEERING_ESTIMATE",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "scope": (
            "Parallel-world Line 9 timetable trajectory comparison; storage variables are optimized "
            "on a fixed main-engine trajectory. Timing variables are not claimed as engine-validated."
        ),
        "method": {
            "mode": "STORAGE_ONLY",
            "algorithm": "NSGA2-CONSTRAINT-DOMINATION",
            "comparator": "RANDOM_SEARCH",
            "seeds": seeds,
            "populationSize": population_size,
            "generations": generations,
            "configuration": config.__dict__,
            "baselineCandidate": evaluator.baseline_candidate,
            "decisionVariableBounds": evaluator.variable_bounds,
            "terminalSocPolicy": "HARD_CONSTRAINT_MAX_5_PERCENT_DEVIATION",
        },
        "trajectory": {
            "path": str(trajectory_path.resolve().relative_to(ROOT)).replace("\\", "/"),
            "sha256": _sha256(trajectory_path),
            **metadata,
        },
        "baseline": baseline,
        "noStorageBaseline": no_storage_baseline,
        "storageOptimization": {
            "summary": summarize_repeats(repeats),
            "repeats": repeats,
            "recommended": recommended,
            "improvementsPercent": improvements,
            "halfStepValidation": half_step_validation,
        },
        "randomComparator": {
            "summary": summarize_repeats(random_runs),
            "runs": random_runs,
        },
        "executionGates": execution_gates,
        "executionPassed": all(execution_gates.values()),
        "hypothesisChecks": hypothesis_checks,
        "hypothesisSupported": all(hypothesis_checks.values()),
    }


def main() -> int:
    args = build_parser().parse_args()
    if args.capture_seconds <= 0:
        raise ValueError("capture-seconds must be positive")
    if args.population < 4 or args.generations < 1:
        raise ValueError("population must be at least 4 and generations must be positive")
    seeds = [int(item.strip()) for item in args.seeds.split(",") if item.strip()]
    if not seeds:
        raise ValueError("at least one seed is required")

    frames, metadata = capture_timetable_trajectory(
        scenario_path=args.scenario,
        max_duties=args.max_duties,
        capture_seconds=args.capture_seconds,
        tick_seconds=args.tick_seconds,
        wall_timeout_sec=args.wall_timeout_sec,
        profile_prewarm_timeout_sec=args.profile_prewarm_timeout_sec,
        profile_cache_dir=args.profile_cache_dir,
    )
    validation = validate_trajectory_frames(frames, allow_roster_changes=False)
    validation.require_valid()
    metadata["trajectoryValidation"] = {
        "passed": validation.passed,
        "frameCount": validation.frame_count,
        "sampleCount": validation.sample_count,
        "fixedRoster": True,
    }
    metadata["sourceRevision"] = _git_revision()
    metadata["topologySha256"] = _sha256(TOPOLOGY)
    _write_trajectory(args.trajectory, frames, metadata)

    report = run_storage_experiment(
        trajectory_path=args.trajectory,
        metadata=metadata,
        capture_seconds=args.capture_seconds,
        evaluation_step_seconds=args.evaluation_step_seconds,
        seeds=seeds,
        population_size=args.population,
        generations=args.generations,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "experimentId": report["experimentId"],
        "status": report["status"],
        "executionPassed": report["executionPassed"],
        "hypothesisSupported": report["hypothesisSupported"],
        "output": str(args.output),
        "trajectory": str(args.trajectory),
        "trajectoryValidation": metadata["trajectoryValidation"],
        "coverage": metadata["coverage"],
        "controlQuality": metadata["controlQuality"],
        "trackingMetrics": metadata["trackingMetrics"],
        "baselineObjectives": report["baseline"]["objectives"],
        "recommendedCandidate": report["storageOptimization"]["recommended"]["candidate"],
        "recommendedObjectives": report["storageOptimization"]["recommended"]["objectives"],
        "improvementsPercent": report["storageOptimization"]["improvementsPercent"],
        "executionGates": report["executionGates"],
        "hypothesisChecks": report["hypothesisChecks"],
    }, ensure_ascii=False, indent=2))
    return 0 if report["executionPassed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
