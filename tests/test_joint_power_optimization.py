from __future__ import annotations

import unittest
from pathlib import Path

from app.domain.power.joint_optimization import (
    BASELINE_CANDIDATE,
    VARIABLE_BOUNDS,
    JointExperimentConfig,
    JointPowerEvaluator,
    Nsga2JointOptimizer,
    nondominated_fronts,
)


ROOT = Path(__file__).resolve().parents[1]
TOPOLOGY = ROOT / "data" / "scenarios" / "line9_power_topology.json"


class JointPowerOptimizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evaluator = JointPowerEvaluator(
            TOPOLOGY,
            JointExperimentConfig(
                train_count=6,
                horizon_sec=120,
                time_step_sec=10.0,
                max_terminal_soc_deviation=0.15,
            ),
        )

    def test_baseline_is_deterministic_feasible_and_physically_balanced(self) -> None:
        first = self.evaluator.evaluate(BASELINE_CANDIDATE)
        second = self.evaluator.evaluate(BASELINE_CANDIDATE)
        self.assertEqual(first["objectives"], second["objectives"])
        self.assertTrue(first["feasible"], first)
        self.assertLess(first["metrics"]["maxBalanceErrorRatio"], 0.01)
        self.assertLess(first["metrics"]["maxRegenBalanceErrorKw"], 1e-6)
        self.assertLessEqual(first["metrics"]["maxDynamicsResidualN"], 1e-6)
        self.assertTrue(first["constraints"]["terminalSoc"])
        self.assertTrue(first["constraints"]["trainSeparation"])

    def test_timing_candidate_preserves_runtime_and_stop_distance(self) -> None:
        result = self.evaluator.evaluate({
            **BASELINE_CANDIDATE,
            "departureSpreadSec": 10.0,
            "tractionTimingSec": 2.0,
            "brakeTimingSec": -2.0,
        })
        self.assertEqual(result["metrics"]["runtimeDeviationSec"], 0.0)
        self.assertAlmostEqual(result["metrics"]["stopPositionErrorM"], 0.0, places=9)
        self.assertTrue(result["constraints"]["speedTracking"])

    def test_nsga2_is_repeatable_and_returns_pareto_solution(self) -> None:
        optimizer = Nsga2JointOptimizer(self.evaluator)
        first = optimizer.run("JOINT", seed=42, population_size=6, generations=1)
        second = optimizer.run("JOINT", seed=42, population_size=6, generations=1)
        self.assertEqual(first["recommended"]["candidate"], second["recommended"]["candidate"])
        self.assertTrue(first["recommended"]["feasible"])
        self.assertGreaterEqual(len(first["paretoFront"]), 1)

    def test_constraint_domination_places_feasible_solution_first(self) -> None:
        feasible = self.evaluator.evaluate(BASELINE_CANDIDATE)
        infeasible = {**feasible, "feasible": False, "totalConstraintViolation": 1.0}
        fronts = nondominated_fronts([infeasible, feasible])
        self.assertEqual(fronts[0], [1])

    def test_recommended_solution_survives_finer_step(self) -> None:
        result = Nsga2JointOptimizer(self.evaluator).run("TIMING_ONLY", seed=7, population_size=6, generations=1)
        fine = self.evaluator.evaluate(result["recommended"]["candidate"], time_step_sec=5.0)
        self.assertTrue(fine["feasible"], fine)

    def test_default_step_objectives_are_stable_at_half_step(self) -> None:
        evaluator = JointPowerEvaluator(TOPOLOGY, JointExperimentConfig())
        coarse = evaluator.evaluate(BASELINE_CANDIDATE)
        fine = evaluator.evaluate(
            BASELINE_CANDIDATE,
            time_step_sec=evaluator.config.time_step_sec / 2.0,
        )

        for objective_name in (
            "netAcGridEnergyKwh",
            "aggregateAcGridPeakKw",
            "wastedRegenRatio",
        ):
            relative_drift = abs(
                coarse["objectives"][objective_name] - fine["objectives"][objective_name]
            ) / max(abs(fine["objectives"][objective_name]), 1e-9)
            self.assertLess(
                relative_drift,
                0.05,
                f"{objective_name} drifted by {relative_drift:.2%}",
            )

    def test_no_storage_comparator_exposes_storage_contribution(self) -> None:
        with_storage = self.evaluator.evaluate(BASELINE_CANDIDATE)
        without_storage = self.evaluator.evaluate(BASELINE_CANDIDATE, storage_enabled=False)

        self.assertTrue(without_storage["feasible"])
        self.assertGreater(
            without_storage["objectives"]["netAcGridEnergyKwh"],
            with_storage["objectives"]["netAcGridEnergyKwh"],
        )
        self.assertGreater(
            without_storage["objectives"]["wastedRegenRatio"],
            with_storage["objectives"]["wastedRegenRatio"],
        )

    def test_force_demand_closes_vehicle_dynamics(self) -> None:
        result = self.evaluator.evaluate({
            **BASELINE_CANDIDATE,
            "tractionTimingSec": -1.0,
            "brakeTimingSec": 1.0,
        })

        self.assertLessEqual(result["metrics"]["maxDynamicsResidualN"], 1e-6)
        self.assertLessEqual(result["metrics"]["maxTractionForceN"], self.evaluator.config.max_traction_force_n)
        self.assertLessEqual(result["metrics"]["maxBrakeForceN"], self.evaluator.config.max_service_brake_force_n)

    def test_optimizer_honors_experiment_specific_storage_domain(self) -> None:
        bounds = {
            **VARIABLE_BOUNDS,
            "storageChargeLimitKw": (0.0, 250.0),
            "storageDischargeLimitKw": (0.0, 300.0),
            "storageTriggerKw": (0.0, 1000.0),
        }
        baseline = {
            **BASELINE_CANDIDATE,
            "storageChargeLimitKw": 0.0,
            "storageDischargeLimitKw": 0.0,
            "storageTriggerKw": 500.0,
        }
        evaluator = JointPowerEvaluator(
            TOPOLOGY,
            JointExperimentConfig(
                train_count=6,
                horizon_sec=120,
                time_step_sec=10.0,
                max_terminal_soc_deviation=0.15,
            ),
            variable_bounds=bounds,
            baseline_candidate=baseline,
        )

        result = Nsga2JointOptimizer(evaluator).run(
            "STORAGE_ONLY",
            seed=11,
            population_size=4,
            generations=1,
        )

        self.assertEqual(result["baseline"]["candidate"], baseline)
        for trial in result["trials"]:
            for name, (lower, upper) in bounds.items():
                self.assertGreaterEqual(trial["candidate"][name], lower)
                self.assertLessEqual(trial["candidate"][name], upper)


if __name__ == "__main__":
    unittest.main()
