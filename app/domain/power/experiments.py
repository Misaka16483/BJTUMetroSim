from __future__ import annotations

from dataclasses import dataclass
import json
import random
import sqlite3
import threading
from pathlib import Path
from typing import Any

from app.domain.power.flow_solver import DCTractionPowerFlowSolver
from app.domain.power.line9_topology import load_line9_power_network
from app.domain.power.network_models import TrainElectricalLoad


JsonDict = dict[str, Any]
SUPPORTED_PROBLEMS = {
    "REGEN_MATCHING",
    "TRACTION_STAGGER",
    "EFS_CAPACITY",
    "N1_ROBUST_TIMETABLE",
}


@dataclass(frozen=True)
class PowerExperimentRequest:
    problem: str
    algorithm: str = "EVOLUTIONARY"
    population_size: int = 8
    generations: int = 3
    seed: int = 20260711
    train_count: int = 12
    time_slots: int = 16
    slot_seconds: int = 5

    @classmethod
    def from_dict(cls, payload: JsonDict) -> "PowerExperimentRequest":
        request = cls(
            problem=str(payload.get("problem", "")).upper(),
            algorithm=str(payload.get("algorithm", "EVOLUTIONARY")).upper(),
            population_size=int(payload.get("populationSize", 8)),
            generations=int(payload.get("generations", 3)),
            seed=int(payload.get("seed", 20260711)),
            train_count=int(payload.get("trainCount", 12)),
            time_slots=int(payload.get("timeSlots", 16)),
            slot_seconds=int(payload.get("slotSeconds", 5)),
        )
        if request.problem not in SUPPORTED_PROBLEMS:
            raise ValueError("UNSUPPORTED_POWER_OPTIMIZATION_PROBLEM")
        if request.algorithm not in {"EVOLUTIONARY", "RANDOM_SEARCH"}:
            raise ValueError("UNSUPPORTED_POWER_OPTIMIZATION_ALGORITHM")
        if not 4 <= request.population_size <= 100:
            raise ValueError("populationSize must be in [4, 100]")
        if not 1 <= request.generations <= 100:
            raise ValueError("generations must be in [1, 100]")
        if not 2 <= request.train_count <= 80:
            raise ValueError("trainCount must be in [2, 80]")
        if not 4 <= request.time_slots <= 720:
            raise ValueError("timeSlots must be in [4, 720]")
        if not 1 <= request.slot_seconds <= 60:
            raise ValueError("slotSeconds must be in [1, 60]")
        return request

    def to_dict(self) -> JsonDict:
        return {
            "problem": self.problem,
            "algorithm": self.algorithm,
            "populationSize": self.population_size,
            "generations": self.generations,
            "seed": self.seed,
            "trainCount": self.train_count,
            "timeSlots": self.time_slots,
            "slotSeconds": self.slot_seconds,
        }


class PowerExperimentRunner:
    """Deterministic batch optimizer backed by the actual DC power-flow solver."""

    def __init__(self, topology_path: str | Path) -> None:
        self.topology_path = Path(topology_path)

    def run(self, request: PowerExperimentRequest, *, experiment_id: str) -> JsonDict:
        rng = random.Random(request.seed)
        baseline_candidate = self._baseline_candidate(request.problem)
        trials: list[JsonDict] = []
        cache: dict[tuple[tuple[str, float], ...], JsonDict] = {}

        def evaluate(candidate: JsonDict, generation: int) -> JsonDict:
            normalized = self._normalize_candidate(request.problem, candidate)
            key = tuple(sorted((name, float(value)) for name, value in normalized.items()))
            cached = cache.get(key)
            if cached is not None:
                return cached
            metrics = self._simulate(request, normalized)
            objectives = self._objectives(request.problem, normalized, metrics)
            constraints = {
                "allConverged": metrics["failedSteps"] == 0,
                "balanceUnder1Percent": metrics["maxBalanceErrorRatio"] < 0.01,
                "minimumVoltageAtLeast500V": metrics["minVoltageV"] >= 500.0,
            }
            penalty = sum(1_000_000.0 for passed in constraints.values() if not passed)
            trial = {
                "trialId": f"{experiment_id}-T{len(cache) + 1:04d}",
                "generation": generation,
                "candidate": normalized,
                "objectives": objectives,
                "constraints": constraints,
                "feasible": all(constraints.values()),
                "score": objectives["weightedScore"] + penalty,
                "metrics": metrics,
            }
            cache[key] = trial
            trials.append(trial)
            return trial

        baseline = evaluate(baseline_candidate, 0)
        population = [baseline_candidate]
        population.extend(
            self._random_candidate(request.problem, rng)
            for _ in range(request.population_size - 1)
        )
        generation_results: list[JsonDict] = []
        for generation in range(request.generations):
            evaluated = [evaluate(candidate, generation) for candidate in population]
            evaluated.sort(key=lambda item: (item["score"], item["trialId"]))
            generation_results.append({
                "generation": generation,
                "bestScore": evaluated[0]["score"],
                "feasibleCount": sum(1 for item in evaluated if item["feasible"]),
            })
            if request.algorithm == "RANDOM_SEARCH":
                population = [
                    self._random_candidate(request.problem, rng)
                    for _ in range(request.population_size)
                ]
                continue
            elites = evaluated[:max(2, request.population_size // 3)]
            population = [dict(item["candidate"]) for item in elites]
            while len(population) < request.population_size:
                parent = rng.choice(elites)["candidate"]
                population.append(self._mutate_candidate(request.problem, parent, rng))

        best = min(trials, key=lambda item: (item["score"], item["trialId"]))
        baseline_score = float(baseline["score"])
        improvement_percent = (
            max(0.0, (baseline_score - float(best["score"])) / max(abs(baseline_score), 1e-9) * 100.0)
        )
        return {
            "experimentId": experiment_id,
            "status": "COMPLETED",
            "request": request.to_dict(),
            "model": {
                "source": "SELF_SIM",
                "quality": "ENGINEERING_ESTIMATE",
                "topologyPath": str(self.topology_path),
            },
            "baseline": baseline,
            "bestTrial": best,
            "improvementPercent": improvement_percent,
            "generationSummary": generation_results,
            "trialCount": len(trials),
            "trials": trials,
        }

    def _simulate(self, request: PowerExperimentRequest, candidate: JsonDict) -> JsonDict:
        scenario_ids: list[str | None] = [None]
        if request.problem == "N1_ROBUST_TIMETABLE":
            scenario_ids.extend(["TS-0903", "TS-0905", "TS-0907"])
        aggregate = {
            "generatedRegenKwh": 0.0,
            "absorbedRegenKwh": 0.0,
            "feedbackRegenKwh": 0.0,
            "wastedRegenKwh": 0.0,
            "lossesKwh": 0.0,
            "peakRectifierPowerKw": 0.0,
            "minVoltageV": float("inf"),
            "curtailmentIndex": 0.0,
            "maxBalanceErrorRatio": 0.0,
            "failedSteps": 0,
            "scenarioCount": len(scenario_ids),
        }
        for outage_id in scenario_ids:
            network = load_line9_power_network(self.topology_path)
            efs_capacity_kw = float(candidate.get("efsCapacityKw", 0.0))
            if efs_capacity_kw > 0:
                for substation_id in ("TS-0901", "TS-0905", "TS-0909"):
                    item = network.substations[substation_id]
                    network.substations[substation_id] = type(item)(
                        **{**item.__dict__, "efs_capacity_kw": efs_capacity_kw}
                    )
            if outage_id is not None:
                network.apply_substation_outage(outage_id, big_bilateral=True)
            solver = DCTractionPowerFlowSolver(network)
            for slot in range(request.time_slots):
                loads = self._slot_loads(request, candidate, slot)
                snapshot = solver.solve(loads, dt_sec=request.slot_seconds, sim_time_ms=slot * request.slot_seconds * 1000)
                hours = request.slot_seconds / 3600.0
                aggregate["generatedRegenKwh"] += snapshot.generated_regen_kw * hours
                aggregate["absorbedRegenKwh"] += snapshot.absorbed_regen_kw * hours
                aggregate["feedbackRegenKwh"] += snapshot.feedback_regen_kw * hours
                aggregate["wastedRegenKwh"] += snapshot.wasted_regen_kw * hours
                aggregate["lossesKwh"] += snapshot.losses_kw * hours
                aggregate["peakRectifierPowerKw"] = max(
                    aggregate["peakRectifierPowerKw"],
                    sum(max(item.rectifier_power_kw, 0.0) for item in snapshot.substations),
                )
                aggregate["minVoltageV"] = min(
                    aggregate["minVoltageV"],
                    *(item.voltage_v for item in snapshot.trains),
                )
                aggregate["curtailmentIndex"] += sum(
                    1.0 - item.traction_limit_ratio
                    for item in snapshot.trains
                    if item.requested_power_kw > 0
                )
                aggregate["maxBalanceErrorRatio"] = max(
                    aggregate["maxBalanceErrorRatio"],
                    snapshot.power_balance_error_ratio,
                )
                if not snapshot.converged or snapshot.power_balance_error_ratio >= 0.01:
                    aggregate["failedSteps"] += 1
        aggregate["regenUtilizationRatio"] = (
            (aggregate["absorbedRegenKwh"] + aggregate["feedbackRegenKwh"])
            / max(aggregate["generatedRegenKwh"], 1e-9)
        )
        aggregate["scheduleDeviationSec"] = (
            float(candidate.get("departureStaggerSec", 0.0)) * (request.train_count - 1) / 2.0
        )
        return aggregate

    @staticmethod
    def _slot_loads(
        request: PowerExperimentRequest,
        candidate: JsonDict,
        slot: int,
    ) -> list[TrainElectricalLoad]:
        first_m, last_m = 313.0, 16_048.92
        span_m = last_m - first_m
        stagger_slots = float(candidate.get("departureStaggerSec", 0.0)) / request.slot_seconds
        regen_shift_slots = float(candidate.get("regenPhaseOffsetSec", 0.0)) / request.slot_seconds
        traction_scale = float(candidate.get("tractionScale", 1.0))
        loads: list[TrainElectricalLoad] = []
        for index in range(request.train_count):
            phase_value = (slot + index * stagger_slots) % 8.0
            is_regen_group = index % 2 == 1
            if is_regen_group:
                phase_value = (phase_value + regen_shift_slots) % 8.0
            phase = int(phase_value)
            traction_force_n = 0.0
            brake_force_n = 0.0
            speed_mps = 15.0 + index % 4
            if phase <= 2:
                traction_force_n = (88_000.0 + (index % 3) * 8_000.0) * traction_scale
            elif phase in {5, 6}:
                brake_force_n = 82_000.0 + (index % 3) * 6_000.0
            mileage_m = first_m + span_m * ((index + 0.5) / request.train_count)
            loads.append(TrainElectricalLoad(
                train_id=f"OPT-{index + 1:03d}",
                direction="UP" if index % 2 == 0 else "DOWN",
                mileage_m=mileage_m,
                speed_mps=speed_mps,
                traction_force_n=traction_force_n,
                brake_force_n=brake_force_n,
                aux_power_kw=80.0,
            ))
        return loads

    @staticmethod
    def _objectives(problem: str, candidate: JsonDict, metrics: JsonDict) -> JsonDict:
        if problem == "REGEN_MATCHING":
            score = (
                metrics["wastedRegenKwh"] * 100.0
                + metrics["lossesKwh"] * 5.0
                + metrics["scheduleDeviationSec"] * 0.03
            )
        elif problem == "TRACTION_STAGGER":
            score = (
                metrics["peakRectifierPowerKw"]
                + metrics["curtailmentIndex"] * 10_000.0
                + metrics["scheduleDeviationSec"] * 0.5
            )
        elif problem == "EFS_CAPACITY":
            score = (
                metrics["wastedRegenKwh"] * 100.0
                + float(candidate["efsCapacityKw"]) * 0.035
                + metrics["lossesKwh"] * 3.0
            )
        else:
            score = (
                metrics["curtailmentIndex"] * 20_000.0
                + max(0.0, 650.0 - metrics["minVoltageV"]) * 100.0
                + metrics["peakRectifierPowerKw"]
                + metrics["scheduleDeviationSec"] * 0.4
            )
        return {
            "weightedScore": score,
            "wastedRegenKwh": metrics["wastedRegenKwh"],
            "peakRectifierPowerKw": metrics["peakRectifierPowerKw"],
            "minimumVoltageV": metrics["minVoltageV"],
            "curtailmentIndex": metrics["curtailmentIndex"],
            "scheduleDeviationSec": metrics["scheduleDeviationSec"],
        }

    @staticmethod
    def _baseline_candidate(problem: str) -> JsonDict:
        if problem == "REGEN_MATCHING":
            return {"departureStaggerSec": 0.0, "regenPhaseOffsetSec": 0.0}
        if problem == "TRACTION_STAGGER":
            return {"departureStaggerSec": 0.0}
        if problem == "EFS_CAPACITY":
            return {"efsCapacityKw": 0.0}
        return {"departureStaggerSec": 0.0, "tractionScale": 1.0}

    @staticmethod
    def _specs(problem: str) -> JsonDict:
        if problem == "REGEN_MATCHING":
            return {"departureStaggerSec": (0.0, 40.0), "regenPhaseOffsetSec": (0.0, 35.0)}
        if problem == "TRACTION_STAGGER":
            return {"departureStaggerSec": (0.0, 40.0)}
        if problem == "EFS_CAPACITY":
            return {"efsCapacityKw": (0.0, 2_000.0)}
        return {"departureStaggerSec": (0.0, 40.0), "tractionScale": (0.80, 1.0)}

    def _random_candidate(self, problem: str, rng: random.Random) -> JsonDict:
        return {
            name: rng.uniform(bounds[0], bounds[1])
            for name, bounds in self._specs(problem).items()
        }

    def _mutate_candidate(self, problem: str, parent: JsonDict, rng: random.Random) -> JsonDict:
        candidate = dict(parent)
        for name, (lower, upper) in self._specs(problem).items():
            candidate[name] = min(
                upper,
                max(lower, float(parent[name]) + rng.gauss(0.0, (upper - lower) * 0.15)),
            )
        return candidate

    def _normalize_candidate(self, problem: str, candidate: JsonDict) -> JsonDict:
        normalized: JsonDict = {}
        for name, (lower, upper) in self._specs(problem).items():
            value = min(upper, max(lower, float(candidate.get(name, lower))))
            normalized[name] = round(value, 4)
        return normalized


class PowerExperimentRegistry:
    def __init__(self, topology_path: str | Path, database_path: str | Path) -> None:
        self.runner = PowerExperimentRunner(topology_path)
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database_path, check_same_thread=False)
        self._lock = threading.RLock()
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS power_experiments (
                experiment_id TEXT PRIMARY KEY,
                problem TEXT NOT NULL,
                status TEXT NOT NULL,
                request_json TEXT NOT NULL,
                result_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS power_experiment_trials (
                experiment_id TEXT NOT NULL,
                trial_id TEXT NOT NULL,
                generation INTEGER NOT NULL,
                score REAL NOT NULL,
                feasible INTEGER NOT NULL,
                trial_json TEXT NOT NULL,
                PRIMARY KEY(experiment_id, trial_id),
                FOREIGN KEY(experiment_id) REFERENCES power_experiments(experiment_id)
            );
        """)
        self.connection.commit()

    def create(self, payload: JsonDict) -> JsonDict:
        with self._lock:
            request = PowerExperimentRequest.from_dict(payload)
            sequence = self.connection.execute("SELECT COUNT(*) FROM power_experiments").fetchone()[0] + 1
            experiment_id = f"PWR-EXP-{sequence:06d}"
            result = self.runner.run(request, experiment_id=experiment_id)
            self.connection.execute(
                "INSERT INTO power_experiments VALUES (?, ?, ?, ?, ?)",
                (
                    experiment_id,
                    request.problem,
                    result["status"],
                    json.dumps(request.to_dict(), ensure_ascii=False),
                    json.dumps({key: value for key, value in result.items() if key != "trials"}, ensure_ascii=False),
                ),
            )
            self.connection.executemany(
                "INSERT INTO power_experiment_trials VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        experiment_id,
                        trial["trialId"],
                        trial["generation"],
                        trial["score"],
                        int(trial["feasible"]),
                        json.dumps(trial, ensure_ascii=False),
                    )
                    for trial in result["trials"]
                ],
            )
            self.connection.commit()
            return result

    def list(self) -> list[JsonDict]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT result_json FROM power_experiments ORDER BY experiment_id"
            ).fetchall()
            return [json.loads(row[0]) for row in rows]

    def get(self, experiment_id: str, *, include_trials: bool = False) -> JsonDict:
        with self._lock:
            row = self.connection.execute(
                "SELECT result_json FROM power_experiments WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchone()
            if row is None:
                raise KeyError("POWER_EXPERIMENT_NOT_FOUND")
            result = json.loads(row[0])
            if include_trials:
                rows = self.connection.execute(
                    "SELECT trial_json FROM power_experiment_trials WHERE experiment_id = ? ORDER BY generation, trial_id",
                    (experiment_id,),
                ).fetchall()
                result["trials"] = [json.loads(item[0]) for item in rows]
            return result

    def close(self) -> None:
        self.connection.close()
