from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.domain.control.stop_experiment import (  # noqa: E402
    StopExperimentScenario,
    baseline_ato_config,
    run_time_step_preflight,
)
from app.domain.control.stop_optimization import (  # noqa: E402
    run_holdout_validation,
    run_multiobjective_optimization,
    run_parameter_screening,
)
from app.domain.line.services import LineMapRepository, PathPlanner  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the stopping accuracy-comfort-energy experiment in auditable stages."
    )
    parser.add_argument("--stage", choices=("preflight", "screen", "optimize", "validate"), default="preflight")
    parser.add_argument("--time-steps", default="0.10,0.05")
    parser.add_argument("--control-period", type=float, default=0.10)
    parser.add_argument("--target-position", type=float, default=200.0)
    parser.add_argument("--permitted-speed", type=float, default=12.0)
    parser.add_argument("--onboard-pax", type=int, default=700)
    parser.add_argument("--max-time", type=float, default=180.0)
    parser.add_argument("--from-platform", type=int)
    parser.add_argument("--to-platform", type=int)
    parser.add_argument("--direction", choices=("forward", "backward"))
    parser.add_argument("--line-map", type=Path, default=ROOT / "data" / "cache" / "line_map.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=64)
    parser.add_argument("--seed", type=int, default=20260715)
    parser.add_argument("--seeds", default="20260715,20260716,20260717")
    parser.add_argument("--population", type=int, default=32)
    parser.add_argument("--generations", type=int, default=15)
    parser.add_argument("--screen-report", type=Path)
    parser.add_argument("--optimization-report", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    time_steps = tuple(float(value.strip()) for value in args.time_steps.split(",") if value.strip())
    if (args.from_platform is None) != (args.to_platform is None):
        raise ValueError("--from-platform and --to-platform must be used together")
    path_plan = None
    if args.from_platform is not None and args.to_platform is not None:
        line_map_path = args.line_map if args.line_map.is_absolute() else ROOT / args.line_map
        path_plan = PathPlanner(LineMapRepository(line_map_path).load()).plan_between_platforms(
            args.from_platform,
            args.to_platform,
            args.direction,
        )
    target_position_m = path_plan.total_length_m if path_plan is not None else args.target_position
    scenario = StopExperimentScenario(
        scenario_id=(
            f"platform-{args.from_platform}-to-{args.to_platform}-load{args.onboard_pax}"
            if path_plan is not None
            else f"synthetic-{args.target_position:g}m-load{args.onboard_pax}"
        ),
        target_position_m=target_position_m,
        permitted_speed_mps=args.permitted_speed,
        onboard_pax=args.onboard_pax,
        dt_s=time_steps[0],
        control_period_s=args.control_period,
        max_time_s=args.max_time,
        path_plan=path_plan,
    )
    if args.stage == "preflight":
        report = run_time_step_preflight(
            scenario,
            time_steps_s=time_steps,
            ato_config=baseline_ato_config(),
        )
    elif args.stage == "screen":
        line_map_path = args.line_map if args.line_map.is_absolute() else ROOT / args.line_map
        planner = PathPlanner(LineMapRepository(line_map_path).load())
        scenario_specs = (
            ("short-FSP-KYL", 4, 6),
            ("medium-LLQ-LLE", 14, 16),
            ("long-JBG-BDZ", 20, 22),
        )
        screening_scenarios = [
            StopExperimentScenario(
                scenario_id=f"{scenario_id}-load700",
                target_position_m=planner.plan_between_platforms(origin, destination, "forward").total_length_m,
                permitted_speed_mps=args.permitted_speed,
                onboard_pax=700,
                dt_s=0.1,
                control_period_s=0.1,
                max_time_s=max(args.max_time, 300.0),
                train_id=f"SCREEN-{origin}-{destination}",
                path_plan=planner.plan_between_platforms(origin, destination, "forward"),
            )
            for scenario_id, origin, destination in scenario_specs
        ]
        report = run_parameter_screening(
            screening_scenarios,
            sample_count=args.samples,
            seed=args.seed,
            ato_config=baseline_ato_config(),
        )
    elif args.stage == "optimize":
        line_map_path = args.line_map if args.line_map.is_absolute() else ROOT / args.line_map
        planner = PathPlanner(LineMapRepository(line_map_path).load())
        scenario_specs = (
            ("short-FSP-KYL", 4, 6),
            ("medium-LLQ-LLE", 14, 16),
            ("long-JBG-BDZ", 20, 22),
        )
        optimization_scenarios = []
        for scenario_id, origin, destination in scenario_specs:
            path_plan = planner.plan_between_platforms(origin, destination, "forward")
            for onboard_pax in (0, 700, 1400):
                optimization_scenarios.append(StopExperimentScenario(
                    scenario_id=f"{scenario_id}-load{onboard_pax}",
                    target_position_m=path_plan.total_length_m,
                    permitted_speed_mps=args.permitted_speed,
                    onboard_pax=onboard_pax,
                    dt_s=0.1,
                    control_period_s=0.1,
                    max_time_s=max(args.max_time, 300.0),
                    train_id=f"OPT-{origin}-{destination}-{onboard_pax}",
                    path_plan=path_plan,
                ))
        seed_candidates = []
        if args.screen_report is not None:
            screen_path = args.screen_report if args.screen_report.is_absolute() else ROOT / args.screen_report
            screen_payload = json.loads(screen_path.read_text(encoding="utf-8"))
            seed_candidates = [
                item["parameters"]
                for item in screen_payload.get("candidates", [])
                if item.get("feasible")
            ]
        checkpoint_path = output.with_name(f"{output.stem}.checkpoint{output.suffix}")

        def write_checkpoint(payload: dict[str, object]) -> None:
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            checkpoint_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(json.dumps({
                "stage": payload["stage"],
                "completedSeeds": payload["completedSeeds"],
                "checkpoint": str(checkpoint_path),
            }, ensure_ascii=False), flush=True)

        report = run_multiobjective_optimization(
            optimization_scenarios,
            seeds=tuple(int(value.strip()) for value in args.seeds.split(",") if value.strip()),
            population_size=args.population,
            generations=args.generations,
            ato_config=baseline_ato_config(),
            seed_candidates=seed_candidates,
            progress_callback=write_checkpoint,
        )
    else:
        if args.optimization_report is None:
            raise ValueError("--optimization-report is required for validate stage")
        optimization_path = (
            args.optimization_report
            if args.optimization_report.is_absolute()
            else ROOT / args.optimization_report
        )
        optimization_payload = json.loads(optimization_path.read_text(encoding="utf-8"))
        line_map_path = args.line_map if args.line_map.is_absolute() else ROOT / args.line_map
        planner = PathPlanner(LineMapRepository(line_map_path).load())
        holdout_scenarios = []
        for scenario_id, origin, destination in (
            ("reverse-short-KYL-FSP", 6, 4),
            ("reverse-medium-LLE-LLQ", 16, 14),
            ("reverse-long-BDZ-JBG", 22, 20),
        ):
            path_plan = planner.plan_between_platforms(origin, destination, "backward")
            for onboard_pax in (0, 700, 1400):
                holdout_scenarios.append(StopExperimentScenario(
                    scenario_id=f"{scenario_id}-load{onboard_pax}",
                    target_position_m=path_plan.total_length_m,
                    permitted_speed_mps=args.permitted_speed,
                    onboard_pax=onboard_pax,
                    dt_s=0.1,
                    control_period_s=0.1,
                    max_time_s=max(args.max_time, 300.0),
                    train_id=f"VAL-{origin}-{destination}-{onboard_pax}",
                    path_plan=path_plan,
                ))
        for scenario_id, origin, destination in (
            ("unseen-KYL-FTN", 6, 8),
            ("unseen-FTN-FTD", 8, 10),
            ("unseen-BDZ-BQS", 22, 24),
        ):
            path_plan = planner.plan_between_platforms(origin, destination, "forward")
            holdout_scenarios.append(StopExperimentScenario(
                scenario_id=f"{scenario_id}-load700",
                target_position_m=path_plan.total_length_m,
                permitted_speed_mps=args.permitted_speed,
                onboard_pax=700,
                dt_s=0.1,
                control_period_s=0.1,
                max_time_s=max(args.max_time, 300.0),
                train_id=f"VAL-{origin}-{destination}-700",
                path_plan=path_plan,
            ))
        report = run_holdout_validation(
            holdout_scenarios,
            optimization_payload["representativeSolutions"],
            ato_config=baseline_ato_config(),
            high_fidelity_dt_s=0.05,
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    if args.stage == "preflight":
        summary = {
            "stage": report["stage"],
            "passed": report["passed"],
            "comparisons": report["comparisons"],
        }
        exit_code = 0 if report["passed"] else 2
    elif args.stage == "screen":
        summary = {
            "stage": report["stage"],
            "sampleCount": report["sampleCount"],
            "feasibleCandidateCount": report["feasibleCandidateCount"],
            "profileCacheEntryCount": report["profileCacheEntryCount"],
            "recommendedOptimizationParameters": report["recommendedOptimizationParameters"],
        }
        exit_code = 0
    elif args.stage == "optimize":
        summary = {
            "stage": report["stage"],
            "seeds": report["seeds"],
            "maximumEvaluationsPerSeed": report["maximumEvaluationsPerSeed"],
            "profileCacheEntryCount": report["profileCacheEntryCount"],
            "nsga2": [
                {
                    "seed": item["seed"],
                    "evaluationCount": item["evaluationCount"],
                    "feasibleCount": item["feasibleCount"],
                    "paretoSize": len(item["paretoFront"]),
                    "hypervolume": item["hypervolume"],
                }
                for item in report["nsga2Runs"]
            ],
            "randomSearch": [
                {
                    "seed": item["seed"],
                    "evaluationCount": item["evaluationCount"],
                    "feasibleCount": item["feasibleCount"],
                    "paretoSize": len(item["paretoFront"]),
                    "hypervolume": item["hypervolume"],
                }
                for item in report["randomSearchRuns"]
            ],
            "combinedParetoSize": len(report["combinedParetoFront"]),
        }
        exit_code = 0
    else:
        summary = {
            "stage": report["stage"],
            "scenarioCount": report["scenarioCount"],
            "profileCacheEntryCount": report["profileCacheEntryCount"],
            "supportedCandidates": report["supportedCandidates"],
            "candidates": {
                name: {
                    "normalFeasible": item["normalFidelity"]["feasible"],
                    "highFidelityFeasible": item["highFidelity"]["feasible"],
                    "allConvergencePassed": item["allConvergencePassed"],
                    "hypothesisSupported": item["hypothesisSupported"],
                    "changesFromBaseline": item["changesFromBaseline"],
                }
                for name, item in report["candidates"].items()
            },
        }
        exit_code = 0
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
