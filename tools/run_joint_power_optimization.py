from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.domain.power.joint_optimization import (
    BASELINE_CANDIDATE,
    MODE_VARIABLES,
    VARIABLE_BOUNDS,
    JointExperimentConfig,
    JointPowerEvaluator,
    Nsga2JointOptimizer,
    normalize_candidate,
    relative_utility,
    run_random_search,
    summarize_repeats,
)


TOPOLOGY = ROOT / "data" / "scenarios" / "line9_power_topology.json"
MANIFEST = ROOT / "data" / "contracts" / "power_credibility_baseline_v1.json"
DEFAULT_OUTPUT = ROOT / "outputs" / "joint-power-optimization-v2.json"
DEFAULT_REPORT = ROOT / "docs" / "测试与验收" / "多车时序与超级电容联合优化实验报告.md"
DEFAULT_SUMMARY = ROOT / "data" / "contracts" / "joint_power_optimization_v2_summary.json"


def _improvements(candidate: dict, baseline: dict) -> dict[str, float]:
    return {
        name: (1.0 - candidate["objectives"][name] / max(value, 1e-9)) * 100.0
        for name, value in baseline["objectives"].items()
    }


def _sensitivity(evaluator: JointPowerEvaluator, candidate: dict) -> list[dict]:
    cases: list[dict] = []
    for name, (low, high) in VARIABLE_BOUNDS.items():
        for direction in (-1.0, 1.0):
            perturbed = dict(candidate)
            perturbed[name] = min(high, max(low, candidate[name] + direction * 0.10 * (high - low)))
            result = evaluator.evaluate(
                perturbed,
                time_step_sec=evaluator.config.time_step_sec / 2.0,
            )
            cases.append({
                "variable": name,
                "direction": "minus10PercentRange" if direction < 0 else "plus10PercentRange",
                "candidate": result["candidate"],
                "feasible": result["feasible"],
                "objectives": result["objectives"],
                "constraints": result["constraints"],
            })
    return cases


def run_experiment(*, seeds: list[int], population_size: int, generations: int) -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    topology_hash = hashlib.sha256(TOPOLOGY.read_bytes()).hexdigest()
    if not manifest["releaseGatePassed"] or topology_hash != manifest["topology"]["sha256"]:
        raise RuntimeError("POWER_CREDIBILITY_BASELINE_NOT_VALID")

    config = JointExperimentConfig()
    evaluator = JointPowerEvaluator(TOPOLOGY, config)
    optimizer = Nsga2JointOptimizer(evaluator)
    baseline = evaluator.evaluate(BASELINE_CANDIDATE)
    if not baseline["feasible"]:
        raise RuntimeError("JOINT_EXPERIMENT_BASELINE_INFEASIBLE")
    no_storage_baseline = evaluator.evaluate(BASELINE_CANDIDATE, storage_enabled=False)

    mode_results: dict[str, dict] = {}
    for mode in MODE_VARIABLES:
        repeats = [
            optimizer.run(
                mode,
                seed=seed,
                population_size=population_size,
                generations=generations,
            )
            for seed in seeds
        ]
        validations = []
        for repeat in repeats:
            checked = evaluator.evaluate(
                repeat["recommended"]["candidate"],
                time_step_sec=config.time_step_sec / 2.0,
            )
            validations.append({
                "seed": repeat["seed"],
                "passed": checked["feasible"],
                "objectives": checked["objectives"],
                "constraints": checked["constraints"],
                "metrics": checked["metrics"],
            })
        mode_results[mode] = {
            "summary": summarize_repeats(repeats),
            "repeats": repeats,
            "independentHalfStepValidation": validations,
        }

    joint_repeats = mode_results["JOINT"]["repeats"]
    no_storage_timing_repeats = [
        optimizer.run(
            "TIMING_ONLY",
            seed=seed,
            population_size=population_size,
            generations=generations,
            storage_enabled=False,
        )
        for seed in seeds
    ]
    baseline_fine = evaluator.evaluate(
        BASELINE_CANDIDATE,
        time_step_sec=config.time_step_sec / 2.0,
    )
    pooled: dict[tuple[float, ...], tuple[int, dict, dict]] = {}
    for repeat in joint_repeats:
        for trial in repeat["paretoFront"]:
            key = tuple(trial["candidate"][name] for name in VARIABLE_BOUNDS)
            if key not in pooled:
                pooled[key] = (
                    repeat["seed"],
                    trial,
                    evaluator.evaluate(
                        trial["candidate"],
                        time_step_sec=config.time_step_sec / 2.0,
                    ),
                )
    balanced = [
        item for item in pooled.values()
        if item[2]["feasible"]
        and all(
            item[2]["objectives"][name] <= baseline_fine["objectives"][name]
            for name in baseline_fine["objectives"]
        )
    ]
    if not balanced:
        raise RuntimeError("NO_FINE_STEP_ALL_OBJECTIVE_IMPROVING_SOLUTION")
    source_seed, search_recommended, recommended = min(
        balanced,
        key=lambda item: relative_utility(item[2], baseline_fine),
    )
    recommended_run = next(item for item in joint_repeats if item["seed"] == source_seed)
    random_results = [
        run_random_search(
            evaluator,
            "JOINT",
            seed=seed,
            evaluation_count=recommended_run["evaluationCount"],
        )
        for seed in seeds
    ]
    report = {
        "experimentId": "JOINT-POWER-OPTIMIZATION-V2",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": "COMPLETED",
        "quality": "ENGINEERING_ESTIMATE",
        "theme": "multi-train timing and supercapacitor control joint optimization",
        "method": {
            "algorithm": "NSGA2-CONSTRAINT-DOMINATION",
            "comparator": "RANDOM_SEARCH",
            "seeds": seeds,
            "populationSize": population_size,
            "generations": generations,
            "decisionVariables": VARIABLE_BOUNDS,
            "objectives": ["netAcGridEnergyKwh", "aggregateAcGridPeakKw", "wastedRegenRatio"],
            "configuration": config.__dict__,
        },
        "frozenInputs": {
            "credibilityBaselineId": manifest["baselineId"],
            "topologySha256": topology_hash,
            "pointContractSha256": manifest["pointContract"]["sha256"],
        },
        "baseline": baseline,
        "noStorageBaseline": no_storage_baseline,
        "modeResults": mode_results,
        "noStorageTimingComparator": {
            "summary": summarize_repeats(no_storage_timing_repeats),
            "repeats": no_storage_timing_repeats,
        },
        "recommended": {
            "sourceSeed": source_seed,
            "candidate": recommended["candidate"],
            "objectives": recommended["objectives"],
            "searchStepObjectives": search_recommended["objectives"],
            "constraints": recommended["constraints"],
            "metrics": recommended["metrics"],
            "relativeUtility": relative_utility(recommended, baseline_fine),
            "improvementsPercent": _improvements(recommended, baseline_fine),
        },
        "randomComparator": {
            "summary": summarize_repeats(random_results),
            "runs": random_results,
        },
        "sensitivity": _sensitivity(evaluator, recommended["candidate"]),
    }
    report["fineStepBaseline"] = baseline_fine
    objective_drift = {
        name: abs(recommended["objectives"][name] - search_recommended["objectives"][name])
        / max(baseline_fine["objectives"][name], 1e-9)
        for name in baseline_fine["objectives"]
    }
    report["recommended"]["searchToValidationDriftRatio"] = objective_drift
    convergence = evaluator.evaluate(
        recommended["candidate"],
        time_step_sec=config.time_step_sec / 4.0,
    )
    convergence_drift = {
        name: abs(convergence["objectives"][name] - recommended["objectives"][name])
        / max(abs(convergence["objectives"][name]), 1e-9)
        for name in convergence["objectives"]
    }
    report["numericalConvergence"] = {
        "quarterStep": convergence,
        "halfToQuarterStepDriftRatio": convergence_drift,
    }
    report["acceptance"] = {
        "baselineFeasible": baseline["feasible"],
        "allRecommendedFeasibleAtSearchStep": all(
            repeat["recommended"]["feasible"]
            for mode in mode_results.values()
            for repeat in mode["repeats"]
        ),
        "allRecommendedFeasibleAtHalfStep": all(
            item["passed"]
            for mode in mode_results.values()
            for item in mode["independentHalfStepValidation"]
        ),
        "jointMedianImprovesBaseline": mode_results["JOINT"]["summary"]["medianImprovementPercent"] > 0.0,
        "jointMedianBeatsRandom": (
            mode_results["JOINT"]["summary"]["medianImprovementPercent"]
            >= report["randomComparator"]["summary"]["medianImprovementPercent"]
        ),
        "balancedSolutionImprovesAllObjectives": all(
            value > 0.0 for value in report["recommended"]["improvementsPercent"].values()
        ),
        "balancedObjectiveDriftUnder5Percent": all(value <= 0.05 for value in objective_drift.values()),
        "quarterStepObjectiveDriftUnder2Percent": all(value <= 0.02 for value in convergence_drift.values()),
        "sensitivityFeasibleRateAtLeast75Percent": (
            sum(item["feasible"] for item in report["sensitivity"])
            / max(len(report["sensitivity"]), 1)
            >= 0.75
        ),
        "dynamicsClosed": recommended["metrics"]["maxDynamicsResidualN"] <= 1e-6,
        "terminalSocEquivalent": recommended["constraints"]["terminalSoc"],
        "trainSeparationMaintained": recommended["constraints"]["trainSeparation"],
    }
    report["passed"] = all(report["acceptance"].values())
    return report


def write_markdown(report: dict, path: Path) -> None:
    baseline = report["fineStepBaseline"]["objectives"]
    recommended = report["recommended"]
    lines = [
        "# 多车时序与超级电容联合优化实验报告 V2",
        "",
        f"总体结论：**{'通过' if report['passed'] else '未通过'}**。",
        "",
        "V2 使用动力学闭合的列车负荷、真实电气子步、交流侧净能耗、再生浪费率、终端 SOC 和同向列车间距约束。结果仍属于工程估算模型，不代表真实线路绝对收益。",
        "",
        "## 核心结果",
        "",
        "| 指标 | 基线 | 联合推荐解 | 改善率 |",
        "|---|---:|---:|---:|",
    ]
    for name in baseline:
        scale = 100.0 if name == "wastedRegenRatio" else 1.0
        suffix = "%" if name == "wastedRegenRatio" else ""
        lines.append(
            f"| `{name}` | {baseline[name] * scale:.6f}{suffix} | "
            f"{recommended['objectives'][name] * scale:.6f}{suffix} "
            f"| {recommended['improvementsPercent'][name]:.2f}% |"
        )
    lines.extend(["", "## 消融与重复性", "", "| 模式 | 中位改善 | 最差 | 最好 | 可行率 |", "|---|---:|---:|---:|---:|"])
    for mode, data in report["modeResults"].items():
        summary = data["summary"]
        lines.append(
            f"| `{mode}` | {summary['medianImprovementPercent']:.2f}% | "
            f"{summary['minImprovementPercent']:.2f}% | {summary['maxImprovementPercent']:.2f}% | "
            f"{summary['feasibleRate']:.1%} |"
        )
    random_summary = report["randomComparator"]["summary"]
    no_storage = report["noStorageBaseline"]["objectives"]
    storage_search_baseline = report["baseline"]["objectives"]
    no_storage_timing = report["noStorageTimingComparator"]["summary"]
    lines.extend([
        "",
        f"同预算随机搜索中位改善为 {random_summary['medianImprovementPercent']:.2f}%。",
        f"无储能时序优化中位改善为 {no_storage_timing['medianImprovementPercent']:.2f}%。",
        "",
        "## 储能对照",
        "",
        f"无储能基线净交流侧能耗为 {no_storage['netAcGridEnergyKwh']:.6f} kWh，"
        f"同搜索步长下带基线储能策略为 {storage_search_baseline['netAcGridEnergyKwh']:.6f} kWh。",
        f"无储能基线再生浪费率为 {no_storage['wastedRegenRatio']:.2%}，"
        f"同搜索步长下带基线储能策略为 {storage_search_baseline['wastedRegenRatio']:.2%}。",
        "",
        "## 推荐参数",
        "",
        "```json",
        json.dumps(recommended["candidate"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## 验收",
        "",
    ])
    for name, passed in report["acceptance"].items():
        lines.append(f"- `{name}`：{'通过' if passed else '失败'}")
    drift = report["numericalConvergence"]["halfToQuarterStepDriftRatio"]
    lines.extend([
        "",
        "## 数值收敛",
        "",
        *[f"- `{name}` 半步到四分之一步漂移：{value:.3%}" for name, value in drift.items()],
        "",
        "## 物理与运营边界",
        "",
        f"- 最大动力学残差：{recommended['metrics']['maxDynamicsResidualN']:.6e} N。",
        f"- 最小同向列车间距：{recommended['metrics']['minSameDirectionSpacingM']:.3f} m。",
        f"- 终端 SOC 偏差：{recommended['metrics']['terminalSocDeviation']:.3%}。",
        f"- 实际电气求解步长：{report['fineStepBaseline']['electricalTimeStepSec']:.6f} s。",
        "",
        "当前模型采用线路里程上的周期站间代理轨迹；在接入完整 ATS 运行图、真实坡度和客流载荷前，结果仅用于方案相对比较。",
        "",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_summary(report: dict, raw_path: Path, summary_path: Path) -> None:
    payload = {
        "experimentId": report["experimentId"],
        "quality": report["quality"],
        "passed": report["passed"],
        "rawOutput": {
            "path": str(raw_path.resolve().relative_to(ROOT)).replace("\\", "/"),
            "sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
            "trackedByGit": False,
            "reproducibleByRunner": True,
        },
        "frozenInputs": report["frozenInputs"],
        "method": report["method"],
        "baselineObjectives": report["fineStepBaseline"]["objectives"],
        "searchStepBaselineObjectives": report["baseline"]["objectives"],
        "noStorageBaselineObjectives": report["noStorageBaseline"]["objectives"],
        "recommendedCandidate": report["recommended"]["candidate"],
        "recommendedObjectives": report["recommended"]["objectives"],
        "improvementPercent": report["recommended"]["improvementsPercent"],
        "modeSummaries": {
            name: value["summary"] for name, value in report["modeResults"].items()
        },
        "noStorageTimingSummary": report["noStorageTimingComparator"]["summary"],
        "randomComparatorSummary": report["randomComparator"]["summary"],
        "numericalConvergence": {
            "halfToQuarterStepDriftRatio": report["numericalConvergence"]["halfToQuarterStepDriftRatio"]
        },
        "acceptance": report["acceptance"],
        "scopeStatement": (
            "Engineering-estimate comparison with dynamics-closed periodic segment trajectories; "
            "not a claim about real Beijing Line 9 performance or investment returns."
        ),
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run joint multi-train timing and supercapacitor optimization")
    parser.add_argument("--seeds", default="20260713,20260717,20260719")
    parser.add_argument("--population", type=int, default=12)
    parser.add_argument("--generations", type=int, default=4)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args()
    seeds = [int(item.strip()) for item in args.seeds.split(",") if item.strip()]
    report = run_experiment(seeds=seeds, population_size=args.population, generations=args.generations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(report, args.output.with_suffix(".md"))
    write_markdown(report, args.report)
    write_summary(report, args.output, args.summary)
    print(json.dumps({
        "passed": report["passed"],
        "output": str(args.output),
        "recommended": report["recommended"],
        "acceptance": report["acceptance"],
    }, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
