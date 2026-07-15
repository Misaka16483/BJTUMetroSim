from __future__ import annotations

import math
import random
from dataclasses import asdict, replace
from typing import Any, Callable, Iterable

from app.domain.control.models import AtoConfig, AtoTarget
from app.domain.control.speed_profile import OptimizedSpeedProfile
from app.domain.control.stop_experiment import (
    STOP_EXPERIMENT_SCHEMA_VERSION,
    StopExperimentResult,
    StopExperimentScenario,
    baseline_ato_config,
    build_candidate_profile,
    evaluate_stop_scenario,
)
from app.domain.vehicle.models import VehicleConfig


JsonDict = dict[str, Any]

SCREENING_PARAMETER_RANGES: dict[str, tuple[float, float]] = {
    "expected_deceleration_mps2": (0.50, 0.70),
    "brake_margin_m": (12.0, 30.0),
    "profile_brake_timing_bias_s": (-1.50, -0.75),
    "brake_apply_slew_rate_percent_per_s": (28.0, 50.0),
    "brake_release_slew_rate_percent_per_s": (12.0, 24.0),
    "terminal_brake_floor_percent": (6.0, 12.0),
    "creep_distance_m": (4.0, 8.0),
}

OPTIMIZATION_PARAMETER_RANGES: dict[str, tuple[float, float]] = {
    "brake_apply_slew_rate_percent_per_s": (28.0, 50.0),
    "profile_brake_timing_bias_s": (-1.50, -0.75),
    "terminal_brake_floor_percent": (6.0, 12.0),
    "brake_release_slew_rate_percent_per_s": (12.0, 24.0),
}


def latin_hypercube_candidates(sample_count: int, seed: int) -> list[JsonDict]:
    if sample_count <= 0:
        raise ValueError("sample_count must be positive")
    rng = random.Random(seed)
    names = tuple(SCREENING_PARAMETER_RANGES)
    columns: dict[str, list[float]] = {}
    for name in names:
        low, high = SCREENING_PARAMETER_RANGES[name]
        strata = list(range(sample_count))
        rng.shuffle(strata)
        values = [
            low + (high - low) * (stratum + rng.random()) / sample_count
            for stratum in strata
        ]
        if name == "expected_deceleration_mps2":
            values = [round(round(value / 0.05) * 0.05, 2) for value in values]
        columns[name] = values
    return [
        {
            "candidateId": f"LHS-{index + 1:04d}",
            "parameters": {
                name: round(columns[name][index], 6)
                for name in names
            },
        }
        for index in range(sample_count)
    ]


def run_parameter_screening(
    scenarios: Iterable[StopExperimentScenario],
    *,
    sample_count: int = 64,
    seed: int = 20260715,
    ato_config: AtoConfig | None = None,
) -> JsonDict:
    scenario_list = list(scenarios)
    if not scenario_list:
        raise ValueError("scenarios must not be empty")
    base_config = ato_config or baseline_ato_config()
    profile_cache: dict[tuple[object, ...], OptimizedSpeedProfile] = {}

    baseline_results = [
        _evaluate_cached(scenario, base_config, profile_cache)
        for scenario in scenario_list
    ]
    candidates = latin_hypercube_candidates(sample_count, seed)
    candidate_reports: list[JsonDict] = []
    for candidate in candidates:
        config = replace(base_config, **candidate["parameters"])
        results = [
            _evaluate_cached(scenario, config, profile_cache)
            for scenario in scenario_list
        ]
        candidate_reports.append(
            _candidate_report(candidate, config, results, baseline_results)
        )

    sensitivity = _sensitivity_report(candidate_reports)
    recommended = sorted(
        SCREENING_PARAMETER_RANGES,
        key=lambda name: sensitivity[name]["maximumAbsoluteObjectiveCorrelation"],
        reverse=True,
    )[:4]
    return {
        "schemaVersion": STOP_EXPERIMENT_SCHEMA_VERSION,
        "stage": "screen",
        "seed": seed,
        "sampleCount": sample_count,
        "scenarioCount": len(scenario_list),
        "scenarios": [scenario.to_dict() for scenario in scenario_list],
        "parameterRanges": {
            name: {"minimum": bounds[0], "maximum": bounds[1]}
            for name, bounds in SCREENING_PARAMETER_RANGES.items()
        },
        "baselineAtoConfig": asdict(base_config),
        "baseline": [result.to_dict() for result in baseline_results],
        "profileCacheEntryCount": len(profile_cache),
        "feasibleCandidateCount": sum(bool(item["feasible"]) for item in candidate_reports),
        "sensitivity": sensitivity,
        "recommendedOptimizationParameters": recommended,
        "candidates": candidate_reports,
    }


def _evaluate_cached(
    scenario: StopExperimentScenario,
    config: AtoConfig,
    profile_cache: dict[tuple[object, ...], OptimizedSpeedProfile],
) -> StopExperimentResult:
    vehicle_config = VehicleConfig.for_load(scenario.train_id, scenario.onboard_pax)
    profile: OptimizedSpeedProfile | None = None
    if config.use_dynamic_programming_profile:
        key = _profile_key(scenario, config, vehicle_config)
        profile = profile_cache.get(key)
        if profile is None:
            target = AtoTarget(
                target_position_m=scenario.resolved_target_position_m,
                permitted_speed_mps=scenario.permitted_speed_mps,
                path_plan=scenario.path_plan,
            )
            profile = build_candidate_profile(target, config, vehicle_config)
            profile_cache[key] = profile
    return evaluate_stop_scenario(
        scenario,
        ato_config=config,
        vehicle_config=vehicle_config,
        optimized_profile=profile,
    )


def _profile_key(
    scenario: StopExperimentScenario,
    config: AtoConfig,
    vehicle_config: VehicleConfig,
) -> tuple[object, ...]:
    return (
        scenario.path_plan.cache_key() if scenario.path_plan is not None else scenario.resolved_target_position_m,
        round(scenario.permitted_speed_mps, 6),
        round(vehicle_config.mass_kg, 3),
        round(config.expected_deceleration_mps2, 6),
        round(config.target_cruise_speed_mps, 6),
        config.profile_run_time_s,
        round(config.profile_runtime_margin_ratio, 6),
        round(config.profile_time_step_s, 6),
        round(config.profile_position_step_m, 6),
        round(config.profile_speed_step_mps, 6),
        config.profile_max_states_per_stage,
        round(config.stop_tolerance_m, 6),
    )


def _candidate_report(
    candidate: JsonDict,
    config: AtoConfig,
    results: list[StopExperimentResult],
    baselines: list[StopExperimentResult],
) -> JsonDict:
    violations: list[str] = []
    scenario_results: list[JsonDict] = []
    for result, baseline in zip(results, baselines):
        scenario_violations = list(result.violations)
        run_time_limit_s = max(3.0, float(baseline.metrics["runTimeSec"]) * 0.02)
        if abs(float(result.metrics["runTimeSec"]) - float(baseline.metrics["runTimeSec"])) > run_time_limit_s:
            scenario_violations.append("RUNTIME_DEVIATION_LIMIT")
        if float(result.metrics["maximumAbsJerkMps3"]) > (
            float(baseline.metrics["maximumAbsJerkMps3"]) * 1.05 + 1e-12
        ):
            scenario_violations.append("MAXIMUM_JERK_REGRESSION")
        violations.extend(
            f"{result.scenario['scenarioId']}:{violation}"
            for violation in scenario_violations
        )
        payload = result.to_dict()
        payload["screeningViolations"] = scenario_violations
        scenario_results.append(payload)

    absolute_errors = [float(result.metrics["absoluteStopErrorM"]) for result in results]
    comfort = [float(result.metrics["p95TerminalAbsJerkMps3"]) for result in results]
    energy_ratios = [
        float(result.metrics["netEnergyKwh"]) / max(float(baseline.metrics["netEnergyKwh"]), 1e-12)
        for result, baseline in zip(results, baselines)
    ]
    return {
        "candidateId": candidate["candidateId"],
        "parameters": candidate["parameters"],
        "atoConfig": asdict(config),
        "feasible": not violations,
        "violations": violations,
        "objectives": {
            "p95AbsoluteStopErrorM": _percentile(absolute_errors, 0.95),
            "meanP95TerminalAbsJerkMps3": sum(comfort) / len(comfort),
            "meanNetEnergyRatioToBaseline": sum(energy_ratios) / len(energy_ratios),
        },
        "scenarioResults": scenario_results,
    }


def _sensitivity_report(candidate_reports: list[JsonDict]) -> JsonDict:
    objective_names = (
        "p95AbsoluteStopErrorM",
        "meanP95TerminalAbsJerkMps3",
        "meanNetEnergyRatioToBaseline",
    )
    report: JsonDict = {}
    for parameter in SCREENING_PARAMETER_RANGES:
        x = [float(item["parameters"][parameter]) for item in candidate_reports]
        correlations = {
            objective: _spearman(
                x,
                [float(item["objectives"][objective]) for item in candidate_reports],
            )
            for objective in objective_names
        }
        report[parameter] = {
            "spearman": correlations,
            "maximumAbsoluteObjectiveCorrelation": max(abs(value) for value in correlations.values()),
            "standardizedLowerUpperEffect": {
                objective: _standardized_lower_upper_effect(
                    x,
                    [float(item["objectives"][objective]) for item in candidate_reports],
                )
                for objective in objective_names
            },
        }
    return report


def _spearman(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    return _pearson(_ranks(left), _ranks(right))


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        average_rank = (start + end - 1) / 2.0 + 1.0
        for index in order[start:end]:
            ranks[index] = average_rank
        start = end
    return ranks


def _pearson(left: list[float], right: list[float]) -> float:
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    left_scale = math.sqrt(sum((x - left_mean) ** 2 for x in left))
    right_scale = math.sqrt(sum((y - right_mean) ** 2 for y in right))
    if left_scale <= 1e-12 or right_scale <= 1e-12:
        return 0.0
    return numerator / (left_scale * right_scale)


def _standardized_lower_upper_effect(x: list[float], y: list[float]) -> float:
    pairs = sorted(zip(x, y), key=lambda item: item[0])
    split = max(1, len(pairs) // 2)
    lower = [value for _, value in pairs[:split]]
    upper = [value for _, value in pairs[-split:]]
    pooled = lower + upper
    mean = sum(pooled) / len(pooled)
    scale = math.sqrt(sum((value - mean) ** 2 for value in pooled) / len(pooled))
    if scale <= 1e-12:
        return 0.0
    return ((sum(upper) / len(upper)) - (sum(lower) / len(lower))) / scale


def _percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    ratio = position - lower
    return ordered[lower] * (1.0 - ratio) + ordered[upper] * ratio


class StopMultiScenarioEvaluator:
    def __init__(
        self,
        scenarios: Iterable[StopExperimentScenario],
        *,
        ato_config: AtoConfig | None = None,
    ) -> None:
        self.scenarios = list(scenarios)
        if not self.scenarios:
            raise ValueError("scenarios must not be empty")
        self.base_config = ato_config or baseline_ato_config()
        self.profile_cache: dict[tuple[object, ...], OptimizedSpeedProfile] = {}
        self.baseline_results = [
            _evaluate_cached(scenario, self.base_config, self.profile_cache)
            for scenario in self.scenarios
        ]
        if not all(result.ok for result in self.baseline_results):
            failures = {
                result.scenario["scenarioId"]: list(result.violations)
                for result in self.baseline_results
                if not result.ok
            }
            raise RuntimeError(f"optimization baseline is infeasible: {failures}")

    @property
    def baseline_candidate(self) -> JsonDict:
        return {
            name: float(getattr(self.base_config, name))
            for name in OPTIMIZATION_PARAMETER_RANGES
        }

    def evaluate(self, candidate: JsonDict, trial_index: int) -> JsonDict:
        normalized = _normalize_optimization_candidate(candidate, self.baseline_candidate)
        config = replace(self.base_config, **normalized)
        results = [
            _evaluate_cached(scenario, config, self.profile_cache)
            for scenario in self.scenarios
        ]
        violations: list[str] = []
        total_violation = 0.0
        scenario_summaries: list[JsonDict] = []
        absolute_errors: list[float] = []
        comfort_values: list[float] = []
        energy_ratios: list[float] = []
        for result, baseline in zip(results, self.baseline_results):
            metrics = result.metrics
            baseline_metrics = baseline.metrics
            scenario_violations = list(result.violations)
            stop_excess = max(0.0, float(metrics["absoluteStopErrorM"]) - 1.0)
            total_violation += stop_excess
            if result.status != "STOPPED_AT_TARGET":
                total_violation += 1.0
            run_time_limit_s = max(3.0, float(baseline_metrics["runTimeSec"]) * 0.02)
            run_time_delta_s = abs(
                float(metrics["runTimeSec"]) - float(baseline_metrics["runTimeSec"])
            )
            if run_time_delta_s > run_time_limit_s:
                scenario_violations.append("RUNTIME_DEVIATION_LIMIT")
                total_violation += (run_time_delta_s - run_time_limit_s) / run_time_limit_s
            jerk_limit = float(baseline_metrics["maximumAbsJerkMps3"]) * 1.05
            maximum_jerk = float(metrics["maximumAbsJerkMps3"])
            if maximum_jerk > jerk_limit + 1e-12:
                scenario_violations.append("MAXIMUM_JERK_REGRESSION")
                total_violation += (maximum_jerk - jerk_limit) / max(jerk_limit, 1e-12)
            total_violation += int(metrics["rapidLowSpeedBrakeReapplicationCount"])
            total_violation += int(metrics["tractionBrakeOverlapSampleCount"])
            total_violation += int(metrics["emergencyBrakeInterventionCount"])
            violations.extend(
                f"{result.scenario['scenarioId']}:{violation}"
                for violation in scenario_violations
            )
            absolute_errors.append(float(metrics["absoluteStopErrorM"]))
            comfort_values.append(float(metrics["p95TerminalAbsJerkMps3"]))
            energy_ratios.append(
                float(metrics["netEnergyKwh"]) / max(float(baseline_metrics["netEnergyKwh"]), 1e-12)
            )
            scenario_summaries.append({
                "scenarioId": result.scenario["scenarioId"],
                "ok": not scenario_violations,
                "status": result.status,
                "violations": scenario_violations,
                "rawStopErrorM": metrics["rawStopErrorM"],
                "p95TerminalAbsJerkMps3": metrics["p95TerminalAbsJerkMps3"],
                "maximumAbsJerkMps3": metrics["maximumAbsJerkMps3"],
                "netEnergyKwh": metrics["netEnergyKwh"],
                "netEnergyRatioToBaseline": energy_ratios[-1],
                "runTimeSec": metrics["runTimeSec"],
                "runTimeDeltaSec": run_time_delta_s,
            })
        return {
            "trialIndex": trial_index,
            "candidate": normalized,
            "atoConfig": asdict(config),
            "feasible": not violations,
            "violations": violations,
            "totalConstraintViolation": total_violation,
            "objectives": {
                "p95AbsoluteStopErrorM": _percentile(absolute_errors, 0.95),
                "meanP95TerminalAbsJerkMps3": sum(comfort_values) / len(comfort_values),
                "meanNetEnergyRatioToBaseline": sum(energy_ratios) / len(energy_ratios),
            },
            "scenarioResults": scenario_summaries,
        }


def run_multiobjective_optimization(
    scenarios: Iterable[StopExperimentScenario],
    *,
    seeds: Iterable[int] = (20260715, 20260716, 20260717),
    population_size: int = 32,
    generations: int = 15,
    ato_config: AtoConfig | None = None,
    seed_candidates: Iterable[JsonDict] = (),
    progress_callback: Callable[[JsonDict], None] | None = None,
) -> JsonDict:
    if population_size < 4:
        raise ValueError("population_size must be at least 4")
    if generations <= 0:
        raise ValueError("generations must be positive")
    seed_list = [int(seed) for seed in seeds]
    if not seed_list:
        raise ValueError("seeds must not be empty")
    evaluator = StopMultiScenarioEvaluator(scenarios, ato_config=ato_config)
    projected_seeds = [
        {
            name: float(candidate[name])
            for name in OPTIMIZATION_PARAMETER_RANGES
            if name in candidate
        }
        for candidate in seed_candidates
    ]
    projected_seeds = [item for item in projected_seeds if len(item) == len(OPTIMIZATION_PARAMETER_RANGES)]
    nsga_runs: list[JsonDict] = []
    random_runs: list[JsonDict] = []
    for seed in seed_list:
        nsga = _run_nsga2(
            evaluator,
            seed=int(seed),
            population_size=population_size,
            generations=generations,
            seed_candidates=projected_seeds,
        )
        random_result = _run_random_search(
            evaluator,
            seed=int(seed),
            evaluation_count=nsga["evaluationCount"],
        )
        nsga_runs.append(nsga)
        random_runs.append(random_result)
        if progress_callback is not None:
            progress_callback({
                "schemaVersion": STOP_EXPERIMENT_SCHEMA_VERSION,
                "stage": "optimize-checkpoint",
                "completedSeeds": [item["seed"] for item in nsga_runs],
                "profileCacheEntryCount": len(evaluator.profile_cache),
                "nsga2Runs": nsga_runs,
                "randomSearchRuns": random_runs,
            })

    all_front_items = [
        item
        for run in nsga_runs
        for item in run["paretoFront"]
        if item["feasible"]
    ]
    combined_front = _feasible_front(all_front_items)
    representatives = _representative_solutions(combined_front)
    comparison_items = [
        item
        for run in nsga_runs + random_runs
        for item in run["paretoFront"]
        if item["feasible"]
    ]
    reference = _hypervolume_reference(comparison_items)
    for run in nsga_runs + random_runs:
        feasible_front = [item for item in run["paretoFront"] if item["feasible"]]
        run["hypervolume"] = _hypervolume_3d(feasible_front, reference)

    return {
        "schemaVersion": STOP_EXPERIMENT_SCHEMA_VERSION,
        "stage": "optimize",
        "algorithm": "NSGA2-CONSTRAINT-DOMINATION",
        "randomBaselineAlgorithm": "SAME_BUDGET_RANDOM_SEARCH",
        "populationSize": population_size,
        "generations": generations,
        "maximumEvaluationsPerSeed": population_size * generations,
        "seeds": seed_list,
        "scenarioCount": len(evaluator.scenarios),
        "scenarios": [scenario.to_dict() for scenario in evaluator.scenarios],
        "parameterRanges": {
            name: {"minimum": bounds[0], "maximum": bounds[1]}
            for name, bounds in OPTIMIZATION_PARAMETER_RANGES.items()
        },
        "baselineAtoConfig": asdict(evaluator.base_config),
        "baseline": [result.to_dict() for result in evaluator.baseline_results],
        "profileCacheEntryCount": len(evaluator.profile_cache),
        "hypervolumeReference": reference,
        "nsga2Runs": nsga_runs,
        "randomSearchRuns": random_runs,
        "combinedParetoFront": combined_front,
        "representativeSolutions": representatives,
    }


def _run_nsga2(
    evaluator: StopMultiScenarioEvaluator,
    *,
    seed: int,
    population_size: int,
    generations: int,
    seed_candidates: list[JsonDict],
) -> JsonDict:
    rng = random.Random(seed)
    cache: dict[tuple[float, ...], JsonDict] = {}
    trials: list[JsonDict] = []

    def evaluate(candidate: JsonDict) -> JsonDict:
        normalized = _normalize_optimization_candidate(candidate, evaluator.baseline_candidate)
        key = tuple(normalized[name] for name in OPTIMIZATION_PARAMETER_RANGES)
        if key not in cache:
            result = evaluator.evaluate(normalized, len(trials))
            cache[key] = result
            trials.append(result)
        return cache[key]

    population = [evaluator.baseline_candidate, *seed_candidates]
    population = population[:population_size]
    while len(population) < population_size:
        population.append(_random_optimization_candidate(rng))
    generation_summary: list[JsonDict] = []
    for generation in range(generations):
        evaluated = [evaluate(candidate) for candidate in population]
        fronts = _nondominated_fronts(evaluated)
        generation_summary.append({
            "generation": generation,
            "evaluationCount": len(trials),
            "feasibleCount": sum(item["feasible"] for item in evaluated),
            "frontSize": len(fronts[0]),
        })
        if generation == generations - 1:
            break
        rank: dict[int, int] = {}
        crowding: dict[int, float] = {}
        for front_rank, front in enumerate(fronts):
            for index in front:
                rank[index] = front_rank
            crowding.update(_crowding_distances(evaluated, front))

        def tournament() -> JsonDict:
            left, right = rng.sample(range(len(evaluated)), 2)
            left_key = (rank[left], -crowding[left])
            right_key = (rank[right], -crowding[right])
            return evaluated[left if left_key < right_key else right]["candidate"]

        offspring: list[JsonDict] = []
        while len(offspring) < population_size:
            offspring.extend(_crossover_mutate(tournament(), tournament(), rng))
        combined = evaluated + [evaluate(candidate) for candidate in offspring[:population_size]]
        population = _environmental_selection(combined, population_size)

    front = _feasible_front(trials)
    return {
        "seed": seed,
        "algorithm": "NSGA2-CONSTRAINT-DOMINATION",
        "evaluationCount": len(trials),
        "feasibleCount": sum(item["feasible"] for item in trials),
        "paretoFront": front,
        "generationSummary": generation_summary,
        "trials": trials,
    }


def _run_random_search(
    evaluator: StopMultiScenarioEvaluator,
    *,
    seed: int,
    evaluation_count: int,
) -> JsonDict:
    rng = random.Random(seed + 1_000_000)
    candidates = [evaluator.baseline_candidate]
    candidates.extend(_random_optimization_candidate(rng) for _ in range(evaluation_count - 1))
    trials = [evaluator.evaluate(candidate, index) for index, candidate in enumerate(candidates)]
    return {
        "seed": seed,
        "algorithm": "SAME_BUDGET_RANDOM_SEARCH",
        "evaluationCount": len(trials),
        "feasibleCount": sum(item["feasible"] for item in trials),
        "paretoFront": _feasible_front(trials),
        "trials": trials,
    }


def _normalize_optimization_candidate(candidate: JsonDict, baseline: JsonDict) -> JsonDict:
    normalized: JsonDict = {}
    for name, (low, high) in OPTIMIZATION_PARAMETER_RANGES.items():
        value = float(candidate.get(name, baseline[name]))
        normalized[name] = round(min(high, max(low, value)), 6)
    return normalized


def _random_optimization_candidate(rng: random.Random) -> JsonDict:
    return {
        name: rng.uniform(low, high)
        for name, (low, high) in OPTIMIZATION_PARAMETER_RANGES.items()
    }


def _crossover_mutate(first: JsonDict, second: JsonDict, rng: random.Random) -> list[JsonDict]:
    children: list[JsonDict] = [{}, {}]
    for name, (low, high) in OPTIMIZATION_PARAMETER_RANGES.items():
        alpha = rng.uniform(-0.10, 1.10)
        values = (
            alpha * float(first[name]) + (1.0 - alpha) * float(second[name]),
            alpha * float(second[name]) + (1.0 - alpha) * float(first[name]),
        )
        for child, value in zip(children, values):
            if rng.random() < 1.0 / len(OPTIMIZATION_PARAMETER_RANGES):
                value += rng.gauss(0.0, 0.10 * (high - low))
            child[name] = min(high, max(low, value))
    return children


def _dominates(first: JsonDict, second: JsonDict) -> bool:
    if first["feasible"] != second["feasible"]:
        return bool(first["feasible"])
    if not first["feasible"]:
        return float(first["totalConstraintViolation"]) < float(second["totalConstraintViolation"])
    left = tuple(float(value) for value in first["objectives"].values())
    right = tuple(float(value) for value in second["objectives"].values())
    return all(x <= y for x, y in zip(left, right)) and any(x < y for x, y in zip(left, right))


def _nondominated_fronts(items: list[JsonDict]) -> list[list[int]]:
    dominates: list[list[int]] = [[] for _ in items]
    dominated_count = [0] * len(items)
    fronts: list[list[int]] = [[]]
    for left, first in enumerate(items):
        for right, second in enumerate(items):
            if left == right:
                continue
            if _dominates(first, second):
                dominates[left].append(right)
            elif _dominates(second, first):
                dominated_count[left] += 1
        if dominated_count[left] == 0:
            fronts[0].append(left)
    current = 0
    while current < len(fronts) and fronts[current]:
        next_front: list[int] = []
        for left in fronts[current]:
            for right in dominates[left]:
                dominated_count[right] -= 1
                if dominated_count[right] == 0:
                    next_front.append(right)
        if next_front:
            fronts.append(next_front)
        current += 1
    return fronts


def _crowding_distances(items: list[JsonDict], front: list[int]) -> dict[int, float]:
    distances = {index: 0.0 for index in front}
    if len(front) <= 2:
        return {index: math.inf for index in front}
    for objective in items[front[0]]["objectives"]:
        ordered = sorted(front, key=lambda index: items[index]["objectives"][objective])
        distances[ordered[0]] = distances[ordered[-1]] = math.inf
        low = float(items[ordered[0]]["objectives"][objective])
        high = float(items[ordered[-1]]["objectives"][objective])
        if high <= low:
            continue
        for position in range(1, len(ordered) - 1):
            previous_value = float(items[ordered[position - 1]]["objectives"][objective])
            next_value = float(items[ordered[position + 1]]["objectives"][objective])
            distances[ordered[position]] += (next_value - previous_value) / (high - low)
    return distances


def _environmental_selection(items: list[JsonDict], count: int) -> list[JsonDict]:
    selected: list[JsonDict] = []
    for front in _nondominated_fronts(items):
        if len(selected) + len(front) <= count:
            selected.extend(items[index]["candidate"] for index in front)
            continue
        distances = _crowding_distances(items, front)
        ordered = sorted(front, key=lambda index: (-distances[index], items[index]["trialIndex"]))
        selected.extend(items[index]["candidate"] for index in ordered[:count - len(selected)])
        break
    return selected


def _feasible_front(items: list[JsonDict]) -> list[JsonDict]:
    feasible = [item for item in items if item["feasible"]]
    if not feasible:
        return []
    return [feasible[index] for index in _nondominated_fronts(feasible)[0]]


def _representative_solutions(front: list[JsonDict]) -> JsonDict:
    if not front:
        return {}
    objectives = tuple(front[0]["objectives"])
    minimums = {name: min(float(item["objectives"][name]) for item in front) for name in objectives}
    maximums = {name: max(float(item["objectives"][name]) for item in front) for name in objectives}

    def knee_distance(item: JsonDict) -> float:
        return math.sqrt(sum(
            (
                (float(item["objectives"][name]) - minimums[name])
                / max(maximums[name] - minimums[name], 1e-12)
            ) ** 2
            for name in objectives
        ))

    return {
        "accuracyPriority": min(front, key=lambda item: item["objectives"]["p95AbsoluteStopErrorM"]),
        "comfortPriority": min(front, key=lambda item: item["objectives"]["meanP95TerminalAbsJerkMps3"]),
        "energyPriority": min(front, key=lambda item: item["objectives"]["meanNetEnergyRatioToBaseline"]),
        "balancedKnee": min(front, key=knee_distance),
    }


def _hypervolume_reference(items: list[JsonDict]) -> JsonDict:
    names = (
        "p95AbsoluteStopErrorM",
        "meanP95TerminalAbsJerkMps3",
        "meanNetEnergyRatioToBaseline",
    )
    if not items:
        return {name: 1.0 for name in names}
    return {
        name: max(float(item["objectives"][name]) for item in items) * 1.05 + 1e-12
        for name in names
    }


def _hypervolume_3d(front: list[JsonDict], reference: JsonDict) -> float:
    if not front:
        return 0.0
    names = tuple(reference)
    points = [
        tuple(float(item["objectives"][name]) for name in names)
        for item in front
    ]
    points = [point for point in points if all(point[i] <= reference[names[i]] for i in range(3))]
    if not points:
        return 0.0
    x_values = sorted({point[0] for point in points})
    volume = 0.0
    for index, x_value in enumerate(x_values):
        next_x = x_values[index + 1] if index + 1 < len(x_values) else float(reference[names[0]])
        active = [(point[1], point[2]) for point in points if point[0] <= x_value]
        y_values = sorted({point[0] for point in active})
        area = 0.0
        for y_index, y_value in enumerate(y_values):
            next_y = y_values[y_index + 1] if y_index + 1 < len(y_values) else float(reference[names[1]])
            minimum_z = min(point[1] for point in active if point[0] <= y_value)
            area += max(0.0, next_y - y_value) * max(0.0, float(reference[names[2]]) - minimum_z)
        volume += max(0.0, next_x - x_value) * area
    return volume


def run_holdout_validation(
    scenarios: Iterable[StopExperimentScenario],
    representatives: JsonDict,
    *,
    ato_config: AtoConfig | None = None,
    high_fidelity_dt_s: float = 0.05,
) -> JsonDict:
    scenario_list = list(scenarios)
    if not scenario_list:
        raise ValueError("scenarios must not be empty")
    if not representatives:
        raise ValueError("representatives must not be empty")
    base_config = ato_config or baseline_ato_config()
    profile_cache: dict[tuple[object, ...], OptimizedSpeedProfile] = {}
    normal_scenarios = [replace(scenario, dt_s=0.1, control_period_s=0.1) for scenario in scenario_list]
    high_scenarios = [
        replace(scenario, dt_s=high_fidelity_dt_s, control_period_s=0.1)
        for scenario in scenario_list
    ]
    baseline_normal = [
        _evaluate_cached(scenario, base_config, profile_cache)
        for scenario in normal_scenarios
    ]
    baseline_high = [
        _evaluate_cached(scenario, base_config, profile_cache)
        for scenario in high_scenarios
    ]
    baseline_objectives = _aggregate_objectives(baseline_normal, baseline_normal)
    candidate_reports: JsonDict = {}
    for name, representative in representatives.items():
        parameters = representative.get("candidate", representative)
        candidate = _normalize_optimization_candidate(parameters, {
            parameter: float(getattr(base_config, parameter))
            for parameter in OPTIMIZATION_PARAMETER_RANGES
        })
        config = replace(base_config, **candidate)
        normal_results = [
            _evaluate_cached(scenario, config, profile_cache)
            for scenario in normal_scenarios
        ]
        high_results = [
            _evaluate_cached(scenario, config, profile_cache)
            for scenario in high_scenarios
        ]
        normal_report = _candidate_report(
            {"candidateId": name, "parameters": candidate},
            config,
            normal_results,
            baseline_normal,
        )
        high_report = _candidate_report(
            {"candidateId": name, "parameters": candidate},
            config,
            high_results,
            baseline_high,
        )
        convergence = [
            _convergence_comparison(normal, high)
            for normal, high in zip(normal_results, high_results)
        ]
        validation_objectives = normal_report["objectives"]
        stop_change_m = (
            float(validation_objectives["p95AbsoluteStopErrorM"])
            - float(baseline_objectives["p95AbsoluteStopErrorM"])
        )
        comfort_change_ratio = (
            float(validation_objectives["meanP95TerminalAbsJerkMps3"])
            / max(float(baseline_objectives["meanP95TerminalAbsJerkMps3"]), 1e-12)
            - 1.0
        )
        energy_change_ratio = float(validation_objectives["meanNetEnergyRatioToBaseline"]) - 1.0
        hypothesis_supported = (
            normal_report["feasible"]
            and high_report["feasible"]
            and all(item["passed"] for item in convergence)
            and float(validation_objectives["p95AbsoluteStopErrorM"]) <= 1.0
            and stop_change_m <= 0.10
            and (comfort_change_ratio <= -0.05 or energy_change_ratio <= -0.01)
            and (
                (comfort_change_ratio <= -0.05 and energy_change_ratio <= 0.02)
                or (energy_change_ratio <= -0.01 and comfort_change_ratio <= 0.02)
            )
        )
        candidate_reports[name] = {
            "candidate": candidate,
            "normalFidelity": normal_report,
            "highFidelity": high_report,
            "convergence": convergence,
            "allConvergencePassed": all(item["passed"] for item in convergence),
            "changesFromBaseline": {
                "p95AbsoluteStopErrorDeltaM": stop_change_m,
                "meanP95TerminalAbsJerkRatio": comfort_change_ratio,
                "meanNetEnergyRatio": energy_change_ratio,
            },
            "hypothesisSupported": hypothesis_supported,
        }
    return {
        "schemaVersion": STOP_EXPERIMENT_SCHEMA_VERSION,
        "stage": "validate",
        "scenarioCount": len(scenario_list),
        "normalDtS": 0.1,
        "highFidelityDtS": high_fidelity_dt_s,
        "scenarios": [scenario.to_dict() for scenario in scenario_list],
        "baselineAtoConfig": asdict(base_config),
        "baselineObjectives": baseline_objectives,
        "baselineNormal": [result.to_dict() for result in baseline_normal],
        "baselineHighFidelity": [result.to_dict() for result in baseline_high],
        "profileCacheEntryCount": len(profile_cache),
        "candidates": candidate_reports,
        "supportedCandidates": [
            name for name, item in candidate_reports.items()
            if item["hypothesisSupported"]
        ],
    }


def _aggregate_objectives(
    results: list[StopExperimentResult],
    baselines: list[StopExperimentResult],
) -> JsonDict:
    errors = [float(result.metrics["absoluteStopErrorM"]) for result in results]
    comfort = [float(result.metrics["p95TerminalAbsJerkMps3"]) for result in results]
    energy = [
        float(result.metrics["netEnergyKwh"]) / max(float(baseline.metrics["netEnergyKwh"]), 1e-12)
        for result, baseline in zip(results, baselines)
    ]
    return {
        "p95AbsoluteStopErrorM": _percentile(errors, 0.95),
        "meanP95TerminalAbsJerkMps3": sum(comfort) / len(comfort),
        "meanNetEnergyRatioToBaseline": sum(energy) / len(energy),
    }


def _convergence_comparison(
    normal: StopExperimentResult,
    high: StopExperimentResult,
) -> JsonDict:
    stop_error_delta_m = abs(
        float(normal.metrics["rawStopErrorM"]) - float(high.metrics["rawStopErrorM"])
    )
    energy_relative_delta = abs(
        float(normal.metrics["netEnergyKwh"]) - float(high.metrics["netEnergyKwh"])
    ) / max(abs(float(high.metrics["netEnergyKwh"])), 1e-12)
    jerk_relative_delta = abs(
        float(normal.metrics["p95TerminalAbsJerkMps3"])
        - float(high.metrics["p95TerminalAbsJerkMps3"])
    ) / max(abs(float(high.metrics["p95TerminalAbsJerkMps3"])), 1e-12)
    return {
        "scenarioId": normal.scenario["scenarioId"],
        "normalOk": normal.ok,
        "highFidelityOk": high.ok,
        "stopErrorDeltaM": stop_error_delta_m,
        "netEnergyRelativeDelta": energy_relative_delta,
        "p95JerkRelativeDelta": jerk_relative_delta,
        "passed": (
            normal.ok
            and high.ok
            and stop_error_delta_m <= 0.10
            and energy_relative_delta <= 0.01
            and jerk_relative_delta <= 0.10
        ),
    }
