from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.domain.power.joint_optimization import (
    JointExperimentConfig,
    JointPowerEvaluator,
)
from app.domain.power.trajectory import JsonlTrajectoryProvider, TrajectoryFrame, validate_trajectory_frames
from tools.run_timetable_power_experiment import (
    DEFAULT_SCENARIO,
    LIVE_STORAGE_BASELINE_CANDIDATE,
    LIVE_STORAGE_VARIABLE_BOUNDS,
    TOPOLOGY,
    _git_revision,
    _sha256,
    _write_trajectory,
    capture_timetable_trajectory,
    run_storage_experiment,
)


DEFAULT_OUTPUT = ROOT / "outputs" / "closed-loop-joint-screening-v1.json"
DEFAULT_ARTIFACT_DIR = ROOT / "outputs" / "closed-loop-joint-screening-v1"
DEFAULT_TIMING_CASES = (
    {
        "caseId": "BASELINE",
        "candidate": {
            "departureSpreadSec": 0.0,
            "tractionTimingSec": 0.0,
            "brakeTimingSec": 0.0,
        },
    },
    {
        "caseId": "EARLY_COAST_BRAKE",
        "candidate": {
            "departureSpreadSec": 0.0,
            "tractionTimingSec": -1.0,
            "brakeTimingSec": -1.0,
        },
    },
    {
        "caseId": "STAGGERED_EARLY_COAST_BRAKE",
        "candidate": {
            "departureSpreadSec": 10.0,
            "tractionTimingSec": -1.0,
            "brakeTimingSec": -1.0,
        },
    },
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a closed-loop timing/storage screening experiment: every timing "
            "candidate regenerates a main-engine trajectory before storage control is optimized."
        )
    )
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--max-duties", type=int, default=2)
    parser.add_argument("--capture-seconds", type=int, default=60)
    parser.add_argument("--screen-tick-seconds", type=float, default=0.5)
    parser.add_argument("--validation-tick-seconds", type=float, default=0.25)
    parser.add_argument("--evaluation-step-seconds", type=float, default=1.0)
    parser.add_argument("--storage-seed", type=int, default=20260714)
    parser.add_argument("--population", type=int, default=6)
    parser.add_argument("--generations", type=int, default=1)
    parser.add_argument(
        "--local-grid-step-seconds",
        type=float,
        default=None,
        help=(
            "Replace the three V1 cases with a 3x3 traction/brake timing grid "
            "at {-step, 0, +step}; departure spread remains zero."
        ),
    )
    parser.add_argument("--wall-timeout-sec", type=float, default=600.0)
    parser.add_argument("--skip-high-fidelity-validation", action="store_true")
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def build_local_timing_grid(step_seconds: float) -> tuple[dict[str, Any], ...]:
    step = float(step_seconds)
    if not 0.0 < step <= 5.0:
        raise ValueError("local-grid-step-seconds must be in (0, 5]")

    def token(value: float) -> str:
        if abs(value) < 1e-12:
            return "ZERO"
        sign = "MINUS" if value < 0.0 else "PLUS"
        return f"{sign}_{abs(value):.2f}".replace(".", "P")

    values = (-step, 0.0, step)
    baseline = {
        "caseId": "BASELINE",
        "candidate": {
            "departureSpreadSec": 0.0,
            "tractionTimingSec": 0.0,
            "brakeTimingSec": 0.0,
        },
    }
    alternatives = []
    for traction_timing in values:
        for brake_timing in values:
            if traction_timing == 0.0 and brake_timing == 0.0:
                continue
            alternatives.append({
                "caseId": (
                    f"TRACTION_{token(traction_timing)}_"
                    f"BRAKE_{token(brake_timing)}"
                ),
                "candidate": {
                    "departureSpreadSec": 0.0,
                    "tractionTimingSec": traction_timing,
                    "brakeTimingSec": brake_timing,
                },
            })
    return (baseline, *alternatives)


def _frame_fingerprint(frames: list[TrajectoryFrame]) -> str:
    digest = hashlib.sha256()
    for frame in frames:
        payload = json.dumps(
            frame.to_dict(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest.update(payload.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _relative_utility(objectives: dict[str, float], baseline: dict[str, float]) -> float:
    return sum(
        float(objectives[name]) / max(float(baseline[name]), 1e-9)
        for name in baseline
    ) / len(baseline)


def _improvements(objectives: dict[str, float], baseline: dict[str, float]) -> dict[str, float]:
    return {
        name: (1.0 - float(objectives[name]) / max(float(value), 1e-9)) * 100.0
        for name, value in baseline.items()
    }


def _recommendation_key(case: dict[str, Any]) -> tuple[float, float, float, float, str]:
    """Prefer the least intrusive timing change when utilities are equivalent."""
    candidate = case["timingCandidate"]
    departure = abs(float(candidate["departureSpreadSec"]))
    traction = abs(float(candidate["tractionTimingSec"]))
    brake = abs(float(candidate["brakeTimingSec"]))
    return (
        round(float(case["relativeUtilityVsGlobalBaseline"]), 12),
        departure + traction + brake,
        brake,
        departure,
        str(case["caseId"]),
    )


def _capture_case(
    *,
    case_id: str,
    timing_candidate: dict[str, float],
    scenario_path: Path,
    max_duties: int,
    capture_seconds: int,
    tick_seconds: float,
    evaluation_step_seconds: float,
    storage_seed: int,
    population_size: int,
    generations: int,
    wall_timeout_sec: float,
    artifact_dir: Path,
    suffix: str,
    optimize_storage: bool = True,
) -> dict[str, Any]:
    print(
        "[closed-loop] "
        + json.dumps({
            "event": "CASE_STARTED",
            "caseId": case_id,
            "suffix": suffix,
            "tickSeconds": tick_seconds,
            "timingCandidate": timing_candidate,
        }, ensure_ascii=False),
        file=sys.stderr,
        flush=True,
    )
    frames, metadata = capture_timetable_trajectory(
        scenario_path=scenario_path,
        max_duties=max_duties,
        capture_seconds=capture_seconds,
        tick_seconds=tick_seconds,
        wall_timeout_sec=wall_timeout_sec,
        timing_candidate=timing_candidate,
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
    metadata["frameFingerprintSha256"] = _frame_fingerprint(frames)

    safe_name = case_id.lower().replace("_", "-")
    trajectory_path = artifact_dir / f"{safe_name}-{suffix}.jsonl"
    _write_trajectory(trajectory_path, frames, metadata)
    result: dict[str, Any] = {
        "caseId": case_id,
        "timingCandidate": timing_candidate,
        "tickSeconds": tick_seconds,
        "trajectoryPath": str(trajectory_path.resolve().relative_to(ROOT)).replace("\\", "/"),
        "trajectorySha256": _sha256(trajectory_path),
        "frameFingerprintSha256": metadata["frameFingerprintSha256"],
        "trajectoryMetadata": metadata,
    }
    if not optimize_storage:
        return result

    storage_report = run_storage_experiment(
        trajectory_path=trajectory_path,
        metadata=metadata,
        capture_seconds=capture_seconds,
        evaluation_step_seconds=evaluation_step_seconds,
        seeds=[storage_seed],
        population_size=population_size,
        generations=generations,
    )
    storage_path = artifact_dir / f"{safe_name}-{suffix}-storage.json"
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    storage_path.write_text(
        json.dumps(storage_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    recommended = storage_report["storageOptimization"]["recommended"]
    result.update({
        "storageReportPath": str(storage_path.resolve().relative_to(ROOT)).replace("\\", "/"),
        "storageReportSha256": _sha256(storage_path),
        "executionPassed": storage_report["executionPassed"],
        "hypothesisSupported": storage_report["hypothesisSupported"],
        "zeroThroughputBaseline": storage_report["baseline"],
        "noStorageBaseline": storage_report["noStorageBaseline"],
        "recommendedStorage": recommended,
        "storageSummary": storage_report["storageOptimization"]["summary"],
        "executionGates": storage_report["executionGates"],
        "hypothesisChecks": storage_report["hypothesisChecks"],
    })
    print(
        "[closed-loop] "
        + json.dumps({
            "event": "CASE_COMPLETED",
            "caseId": case_id,
            "executionPassed": result["executionPassed"],
            "hypothesisSupported": result["hypothesisSupported"],
            "objectives": recommended["objectives"],
        }, ensure_ascii=False),
        file=sys.stderr,
        flush=True,
    )
    return result


def _fixed_storage_validation(
    *,
    case: dict[str, Any],
    storage_candidate: dict[str, float],
    capture_seconds: int,
    evaluation_step_seconds: float,
) -> dict[str, Any]:
    metadata = case["trajectoryMetadata"]
    trajectory_path = ROOT / case["trajectoryPath"]
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
    zero_storage = evaluator.evaluate(
        evaluator.baseline_candidate,
        storage_enabled=False,
    )
    fixed = evaluator.evaluate(storage_candidate)
    half_step = evaluator.evaluate(
        storage_candidate,
        time_step_sec=evaluation_step_seconds / 2.0,
    )
    objective_checks = {
        name: fixed["objectives"][name] <= value
        for name, value in zero_storage["objectives"].items()
    }
    control_quality = metadata["controlQuality"]
    gates = {
        "fixedStorageFeasible": fixed["feasible"],
        "halfStepFeasible": half_step["feasible"],
        "strictTerminalSocEquivalent5Percent": (
            fixed["metrics"]["terminalSocDeviation"] <= 0.05
        ),
        "noRapidLowSpeedBrakeReapplication": (
            control_quality["rapidLowSpeedBrakeReapplicationCount"] == 0
        ),
        "noTractionBrakeOverlap": (
            control_quality["tractionBrakeOverlapSampleCount"] == 0
        ),
        "objectivesDoNotWorsenAgainstSameTrajectoryNoStorage": all(
            objective_checks.values()
        ),
    }
    return {
        "zeroStorageBaseline": zero_storage,
        "fixedStorageCandidate": storage_candidate,
        "fixedStorageEvaluation": fixed,
        "halfStepEvaluation": half_step,
        "objectiveChecks": objective_checks,
        "gates": gates,
        "passed": all(gates.values()),
    }


def run_experiment(args: argparse.Namespace) -> dict[str, Any]:
    if args.max_duties < 2:
        raise ValueError("max-duties must be at least 2")
    if args.capture_seconds <= 0:
        raise ValueError("capture-seconds must be positive")
    if args.population < 4 or args.generations < 1:
        raise ValueError("population must be at least 4 and generations must be positive")
    args.artifact_dir.mkdir(parents=True, exist_ok=True)

    timing_cases = (
        build_local_timing_grid(args.local_grid_step_seconds)
        if args.local_grid_step_seconds is not None
        else DEFAULT_TIMING_CASES
    )

    screen_cases = [
        _capture_case(
            case_id=item["caseId"],
            timing_candidate=dict(item["candidate"]),
            scenario_path=args.scenario,
            max_duties=args.max_duties,
            capture_seconds=args.capture_seconds,
            tick_seconds=args.screen_tick_seconds,
            evaluation_step_seconds=args.evaluation_step_seconds,
            storage_seed=args.storage_seed,
            population_size=args.population,
            generations=args.generations,
            wall_timeout_sec=args.wall_timeout_sec,
            artifact_dir=args.artifact_dir,
            suffix="screen",
        )
        for item in timing_cases
    ]
    global_baseline = screen_cases[0]["zeroThroughputBaseline"]["objectives"]
    for case in screen_cases:
        objectives = case["recommendedStorage"]["objectives"]
        case["relativeUtilityVsGlobalBaseline"] = _relative_utility(
            objectives,
            global_baseline,
        )
        case["improvementsPercentVsGlobalBaseline"] = _improvements(
            objectives,
            global_baseline,
        )
    eligible = [
        case for case in screen_cases
        if case["executionPassed"] and case["hypothesisSupported"]
    ]
    if not eligible:
        raise RuntimeError("NO_FEASIBLE_CLOSED_LOOP_SCREENING_CASE")
    recommended = min(
        eligible,
        key=_recommendation_key,
    )

    baseline_fingerprint = screen_cases[0]["frameFingerprintSha256"]
    changed_case_fingerprints = {
        case["frameFingerprintSha256"]
        for case in screen_cases[1:]
    }
    high_fidelity: dict[str, Any] | None = None
    if not args.skip_high_fidelity_validation:
        validation_case = _capture_case(
            case_id=recommended["caseId"],
            timing_candidate=dict(recommended["timingCandidate"]),
            scenario_path=args.scenario,
            max_duties=args.max_duties,
            capture_seconds=args.capture_seconds,
            tick_seconds=args.validation_tick_seconds,
            evaluation_step_seconds=args.evaluation_step_seconds,
            storage_seed=args.storage_seed,
            population_size=args.population,
            generations=args.generations,
            wall_timeout_sec=args.wall_timeout_sec,
            artifact_dir=args.artifact_dir,
            suffix="validation",
            optimize_storage=False,
        )
        fixed_validation = _fixed_storage_validation(
            case=validation_case,
            storage_candidate=dict(recommended["recommendedStorage"]["candidate"]),
            capture_seconds=args.capture_seconds,
            evaluation_step_seconds=args.evaluation_step_seconds,
        )
        high_fidelity = {
            **validation_case,
            **fixed_validation,
        }

    execution_gates = {
        "baselineScreeningPassed": screen_cases[0]["executionPassed"],
        "atLeastOneFeasibleTimingAlternative": any(
            case["caseId"] != "BASELINE" for case in eligible
        ),
        "timingCandidateChangesMainEngineFrames": any(
            fingerprint != baseline_fingerprint
            for fingerprint in changed_case_fingerprints
        ),
        "recommendedScreeningCasePassed": recommended["executionPassed"],
        "highFidelityValidationPassed": (
            True if high_fidelity is None else high_fidelity["passed"]
        ),
    }
    local_grid_enabled = args.local_grid_step_seconds is not None
    return {
        "experimentId": (
            "CLOSED-LOOP-JOINT-TIMING-STORAGE-LOCAL-GRID-V2"
            if local_grid_enabled
            else "CLOSED-LOOP-JOINT-TIMING-STORAGE-SCREENING-V1"
        ),
        "status": "COMPLETED",
        "quality": "ENGINEERING_ESTIMATE_SCREENING",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "scope": (
            "Parallel-world Line 9 closed-loop screening. Each timing candidate reruns the main "
            "simulation; storage is then optimized on that candidate-specific trajectory."
        ),
        "design": {
            "screeningMethod": (
                "LOCAL_3X3_ENGINE_SWEEP_PLUS_NESTED_STORAGE_NSGA2"
                if local_grid_enabled
                else "THREE_CASE_ENGINE_SWEEP_PLUS_NESTED_STORAGE_NSGA2"
            ),
            "screenTickSeconds": args.screen_tick_seconds,
            "validationTickSeconds": args.validation_tick_seconds,
            "captureSeconds": args.capture_seconds,
            "maxDuties": args.max_duties,
            "storageSeed": args.storage_seed,
            "storagePopulation": args.population,
            "storageGenerations": args.generations,
            "localGridStepSeconds": args.local_grid_step_seconds,
            "timingCases": list(timing_cases),
            "terminalSocPolicy": "HARD_CONSTRAINT_MAX_5_PERCENT_DEVIATION",
        },
        "globalBaselineObjectives": global_baseline,
        "screeningCases": screen_cases,
        "recommendedCaseId": recommended["caseId"],
        "recommendedTimingCandidate": recommended["timingCandidate"],
        "recommendedStorageCandidate": recommended["recommendedStorage"]["candidate"],
        "recommendedObjectives": recommended["recommendedStorage"]["objectives"],
        "recommendedImprovementsPercentVsGlobalBaseline": (
            recommended["improvementsPercentVsGlobalBaseline"]
        ),
        "highFidelityValidation": high_fidelity,
        "executionGates": execution_gates,
        "executionPassed": all(execution_gates.values()),
    }


def main() -> int:
    args = build_parser().parse_args()
    report = run_experiment(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({
        "experimentId": report["experimentId"],
        "status": report["status"],
        "executionPassed": report["executionPassed"],
        "recommendedCaseId": report["recommendedCaseId"],
        "recommendedTimingCandidate": report["recommendedTimingCandidate"],
        "recommendedStorageCandidate": report["recommendedStorageCandidate"],
        "globalBaselineObjectives": report["globalBaselineObjectives"],
        "recommendedObjectives": report["recommendedObjectives"],
        "improvementsPercent": report["recommendedImprovementsPercentVsGlobalBaseline"],
        "executionGates": report["executionGates"],
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))
    return 0 if report["executionPassed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
