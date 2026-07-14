from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.domain.power.joint_optimization import JointExperimentConfig, JointPowerEvaluator
from app.domain.power.trajectory import JsonlTrajectoryProvider
from tools.run_closed_loop_joint_experiment import (
    _capture_case,
    _fixed_storage_validation,
    _improvements,
    _relative_utility,
)
from tools.run_timetable_power_experiment import (
    DEFAULT_SCENARIO,
    LIVE_STORAGE_BASELINE_CANDIDATE,
    LIVE_STORAGE_VARIABLE_BOUNDS,
    TOPOLOGY,
    _sha256,
    _non_storage_constraints_pass,
    run_storage_experiment,
)


DEFAULT_OUTPUT = ROOT / "outputs" / "long-horizon-timing-refinement-v3.json"
DEFAULT_ARTIFACT_DIR = ROOT / "outputs" / "long-horizon-timing-refinement-v3"
DEFAULT_TRACTION_VALUES = (0.0, 0.25, 0.5, 0.75, 1.0)
V2_STORAGE_CANDIDATE = {
    "departureSpreadSec": 0.0,
    "tractionTimingSec": 0.0,
    "brakeTimingSec": 0.0,
    "storageChargeLimitKw": 240.484703,
    "storageDischargeLimitKw": 143.016277,
    "storageTriggerKw": 1501.570907,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a long-horizon traction-phase refinement. Timing candidates are "
            "ranked without storage; storage is optimized only for the selected timing."
        )
    )
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--traction-values", default="0,0.25,0.5,0.75,1.0")
    parser.add_argument("--max-duties", type=int, default=3)
    parser.add_argument("--capture-seconds", type=int, default=3900)
    parser.add_argument("--screen-tick-seconds", type=float, default=0.5)
    parser.add_argument("--validation-tick-seconds", type=float, default=0.25)
    parser.add_argument("--evaluation-step-seconds", type=float, default=1.0)
    parser.add_argument("--storage-seed", type=int, default=20260716)
    parser.add_argument("--population", type=int, default=6)
    parser.add_argument("--generations", type=int, default=1)
    parser.add_argument("--minimum-completed-services", type=int, default=1)
    parser.add_argument("--require-ready-for-analysis", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--wall-timeout-sec", type=float, default=1800.0)
    parser.add_argument("--skip-storage-optimization", action="store_true")
    parser.add_argument("--skip-high-fidelity-validation", action="store_true")
    parser.add_argument(
        "--reuse-timing-report",
        type=Path,
        default=None,
        help=(
            "Reuse already completed timing cases from a compatible V3 report; "
            "trajectory hashes and experiment settings are verified before storage replay."
        ),
    )
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def parse_traction_values(raw: str) -> tuple[float, ...]:
    try:
        values = tuple(float(item.strip()) for item in raw.split(",") if item.strip())
    except ValueError as exc:
        raise ValueError("traction-values must be a comma-separated number list") from exc
    if not values:
        raise ValueError("traction-values must not be empty")
    if any(abs(value) > 5.0 for value in values):
        raise ValueError("traction timing values must be within +/- 5 seconds")
    if len(set(values)) != len(values):
        raise ValueError("traction-values must be unique")
    if 0.0 not in values:
        raise ValueError("traction-values must include the zero baseline")
    return (0.0, *(value for value in values if value != 0.0))


def _case_id(traction_timing_sec: float) -> str:
    if abs(traction_timing_sec) < 1e-12:
        return "BASELINE"
    sign = "MINUS" if traction_timing_sec < 0.0 else "PLUS"
    value = f"{abs(traction_timing_sec):.2f}".replace(".", "P")
    return f"TRACTION_{sign}_{value}"


def _no_storage_evaluation(
    case: dict[str, Any],
    *,
    capture_seconds: int,
    evaluation_step_seconds: float,
) -> dict[str, Any]:
    metadata = case["trajectoryMetadata"]
    provider = JsonlTrajectoryProvider(ROOT / case["trajectoryPath"])
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
    return evaluator.evaluate(evaluator.baseline_candidate, storage_enabled=False)


def _operation_gates(
    case: dict[str, Any],
    no_storage: dict[str, Any],
    *,
    minimum_completed_services: int,
    require_ready_for_analysis: bool,
) -> dict[str, bool]:
    metadata = case["trajectoryMetadata"]
    acceptance = metadata["operationAcceptanceAtCaptureEnd"]
    warmup = metadata["profileWarmup"]
    quality = metadata["controlQuality"]
    coverage = metadata["coverage"]
    return {
        "profileWarmupReady": bool(
            warmup.get("allProfilesReady", warmup.get("ready", False))
        ),
        "minimumCompletedServices": (
            int(acceptance["completedServiceCount"]) >= minimum_completed_services
        ),
        "readyForAnalysis": (
            bool(acceptance["readyForAnalysis"]) if require_ready_for_analysis else True
        ),
        "scheduleWithinTolerance": bool(acceptance["scheduleWithinTolerance"]),
        "noStuckTrain": int(acceptance["stuckTrainCount"]) == 0,
        "noRapidLowSpeedBrakeReapplication": (
            int(quality["rapidLowSpeedBrakeReapplicationCount"]) == 0
        ),
        "noEmergencyBrakeIntervention": (
            int(quality.get("emergencyBrakeInterventionCount", 0)) == 0
        ),
        "noTractionBrakeOverlap": int(quality["tractionBrakeOverlapSampleCount"]) == 0,
        "movingCoverage": int(coverage["movingSampleCount"]) > 0,
        "tractionCoverage": int(coverage["tractionSampleCount"]) > 0,
        "brakingCoverage": int(coverage["brakingSampleCount"]) > 0,
        "regenCoverage": int(coverage["regenSampleCount"]) > 0,
        # Undervoltage/capacity are outcomes this experiment asks storage to
        # improve; they must not disqualify an otherwise valid timing replay.
        "noStorageNonStorageConstraintsPassed": (
            _non_storage_constraints_pass(no_storage)
        ),
    }


def _timing_selection_key(case: dict[str, Any]) -> tuple[float, float, str]:
    return (
        round(float(case["relativeUtilityVsBaselineNoStorage"]), 12),
        abs(float(case["timingCandidate"]["tractionTimingSec"])),
        str(case["caseId"]),
    )


def _reusable_timing_cases(
    report_path: Path,
    *,
    args: argparse.Namespace,
    traction_values: tuple[float, ...],
) -> list[dict[str, Any]]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("experimentId") != "LONG-HORIZON-TRACTION-TIMING-REFINEMENT-V3":
        raise ValueError("reuse-timing-report is not a V3 timing refinement report")
    design = report.get("design", {})
    expected = {
        "tractionTimingValuesSec": list(traction_values),
        "captureSeconds": args.capture_seconds,
        "maxDuties": args.max_duties,
        "screenTickSeconds": args.screen_tick_seconds,
        "evaluationStepSeconds": args.evaluation_step_seconds,
    }
    mismatches = {
        name: {"expected": value, "actual": design.get(name)}
        for name, value in expected.items()
        if design.get(name) != value
    }
    if mismatches:
        raise ValueError(f"reuse-timing-report design mismatch: {mismatches}")
    cases = list(report.get("timingCases", ()))
    if len(cases) != len(traction_values):
        raise ValueError("reuse-timing-report does not contain every requested timing case")
    scenario_sha = _sha256(args.scenario)
    for case in cases:
        path = ROOT / str(case["trajectoryPath"])
        if not path.is_file():
            raise FileNotFoundError(f"reused trajectory is missing: {path}")
        if _sha256(path) != case["trajectorySha256"]:
            raise ValueError(f"reused trajectory hash mismatch: {path}")
        if case["trajectoryMetadata"]["scenarioSha256"] != scenario_sha:
            raise ValueError(f"reused trajectory scenario mismatch: {path}")
    return cases


def run_experiment(args: argparse.Namespace) -> dict[str, Any]:
    traction_values = parse_traction_values(args.traction_values)
    if args.max_duties < 2:
        raise ValueError("max-duties must be at least 2")
    if args.capture_seconds <= 0:
        raise ValueError("capture-seconds must be positive")
    if args.minimum_completed_services < 1:
        raise ValueError("minimum-completed-services must be positive")
    if args.population < 4 or args.generations < 1:
        raise ValueError("population must be at least 4 and generations must be positive")
    args.artifact_dir.mkdir(parents=True, exist_ok=True)

    cases: list[dict[str, Any]] = []
    if args.reuse_timing_report is not None:
        cases = _reusable_timing_cases(
            args.reuse_timing_report,
            args=args,
            traction_values=traction_values,
        )
    else:
        for value in traction_values:
            case = _capture_case(
                case_id=_case_id(value),
                timing_candidate={
                    "departureSpreadSec": 0.0,
                    "tractionTimingSec": value,
                    "brakeTimingSec": 0.0,
                },
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
                optimize_storage=False,
            )
            no_storage = _no_storage_evaluation(
                case,
                capture_seconds=args.capture_seconds,
                evaluation_step_seconds=args.evaluation_step_seconds,
            )
            gates = _operation_gates(
                case,
                no_storage,
                minimum_completed_services=args.minimum_completed_services,
                require_ready_for_analysis=args.require_ready_for_analysis,
            )
            case.update({
                "noStorageEvaluation": no_storage,
                "operationGates": gates,
                "executionPassed": all(gates.values()),
            })
            cases.append(case)

    baseline = cases[0]
    baseline_objectives = baseline["noStorageEvaluation"]["objectives"]
    for case in cases:
        objectives = case["noStorageEvaluation"]["objectives"]
        case["relativeUtilityVsBaselineNoStorage"] = _relative_utility(
            objectives,
            baseline_objectives,
        )
        case["improvementsPercentVsBaselineNoStorage"] = _improvements(
            objectives,
            baseline_objectives,
        )

    eligible = [case for case in cases if case["executionPassed"]]
    recommended = min(eligible, key=_timing_selection_key) if eligible else None
    v2_storage_validation: dict[str, Any] | None = None
    storage_report: dict[str, Any] | None = None
    storage_path: Path | None = None
    high_fidelity: dict[str, Any] | None = None
    if recommended is not None:
        v2_storage_validation = _fixed_storage_validation(
            case=recommended,
            storage_candidate=V2_STORAGE_CANDIDATE,
            capture_seconds=args.capture_seconds,
            evaluation_step_seconds=args.evaluation_step_seconds,
        )
        if not args.skip_storage_optimization:
            storage_report = run_storage_experiment(
                trajectory_path=ROOT / recommended["trajectoryPath"],
                metadata=recommended["trajectoryMetadata"],
                capture_seconds=args.capture_seconds,
                evaluation_step_seconds=args.evaluation_step_seconds,
                seeds=[args.storage_seed],
                population_size=args.population,
                generations=args.generations,
            )
            storage_path = args.artifact_dir / "recommended-long-horizon-storage.json"
            storage_path.write_text(
                json.dumps(storage_report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
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
                fixed = _fixed_storage_validation(
                    case=validation_case,
                    storage_candidate=dict(
                        storage_report["storageOptimization"]["recommended"]["candidate"]
                    ),
                    capture_seconds=args.capture_seconds,
                    evaluation_step_seconds=args.evaluation_step_seconds,
                )
                high_no_storage = fixed["zeroStorageBaseline"]
                high_operation_gates = _operation_gates(
                    validation_case,
                    high_no_storage,
                    minimum_completed_services=args.minimum_completed_services,
                    require_ready_for_analysis=args.require_ready_for_analysis,
                )
                high_fidelity = {
                    **validation_case,
                    **fixed,
                    "operationGates": high_operation_gates,
                    "passed": bool(fixed["passed"] and all(high_operation_gates.values())),
                }

    fingerprints = {case["frameFingerprintSha256"] for case in cases}
    execution_gates = {
        "baselineLongHorizonPassed": baseline["executionPassed"],
        # Exploratory candidates are allowed to be infeasible; the experiment
        # is valid when every requested case completed and invalid cases were
        # excluded from recommendation.
        "allTimingCandidatesCompleted": len(cases) == len(traction_values),
        "timingCandidatesChangeMainEngineFrames": (
            len(traction_values) == 1 or len(fingerprints) > 1
        ),
        "timingRecommendationAvailable": recommended is not None,
        "selectedStorageOptimizationPassed": (
            True if args.skip_storage_optimization else bool(
                storage_report and storage_report["executionPassed"]
            )
        ),
        "highFidelityValidationPassed": (
            True if args.skip_high_fidelity_validation or args.skip_storage_optimization
            else bool(high_fidelity and high_fidelity["passed"])
        ),
    }
    return {
        "experimentId": "LONG-HORIZON-TRACTION-TIMING-REFINEMENT-V3",
        "status": "COMPLETED",
        "quality": "ENGINEERING_ESTIMATE_LONG_HORIZON",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "scope": (
            "Parallel-world Line 9 long-horizon timing refinement. Timing is ranked "
            "without storage; storage is optimized only after timing selection."
        ),
        "design": {
            "method": "LONG_HORIZON_TIMING_FIRST_THEN_STORAGE",
            "tractionTimingValuesSec": list(traction_values),
            "brakeTimingSec": 0.0,
            "departureSpreadSec": 0.0,
            "captureSeconds": args.capture_seconds,
            "maxDuties": args.max_duties,
            "screenTickSeconds": args.screen_tick_seconds,
            "validationTickSeconds": args.validation_tick_seconds,
            "evaluationStepSeconds": args.evaluation_step_seconds,
            "minimumCompletedServices": args.minimum_completed_services,
            "requireReadyForAnalysis": args.require_ready_for_analysis,
            "storageSeed": args.storage_seed,
            "storagePopulation": args.population,
            "storageGenerations": args.generations,
            "reusedTimingReport": (
                str(args.reuse_timing_report) if args.reuse_timing_report else None
            ),
        },
        "baselineNoStorageObjectives": baseline_objectives,
        "timingCases": cases,
        "recommendedTimingCaseId": recommended["caseId"] if recommended else None,
        "recommendedTimingCandidate": recommended["timingCandidate"] if recommended else None,
        "recommendedTimingObjectives": (
            recommended["noStorageEvaluation"]["objectives"] if recommended else None
        ),
        "recommendedTimingImprovementsPercent": (
            recommended["improvementsPercentVsBaselineNoStorage"] if recommended else None
        ),
        "v2StorageCandidate": V2_STORAGE_CANDIDATE,
        "v2StorageValidationOnRecommendedTiming": v2_storage_validation,
        "storageOptimization": storage_report,
        "storageReportPath": (
            str(storage_path.resolve().relative_to(ROOT)).replace("\\", "/")
            if storage_path else None
        ),
        "storageReportSha256": _sha256(storage_path) if storage_path else None,
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
        "recommendedTimingCaseId": report["recommendedTimingCaseId"],
        "recommendedTimingCandidate": report["recommendedTimingCandidate"],
        "baselineNoStorageObjectives": report["baselineNoStorageObjectives"],
        "recommendedTimingObjectives": report["recommendedTimingObjectives"],
        "recommendedTimingImprovementsPercent": report[
            "recommendedTimingImprovementsPercent"
        ],
        "executionGates": report["executionGates"],
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))
    return 0 if report["executionPassed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
