from __future__ import annotations

import unittest
from dataclasses import replace

from app.domain.control.models import AtoConfig
from app.domain.control.stop_experiment import (
    StopExperimentScenario,
    baseline_ato_config,
    evaluate_stop_scenario,
    run_time_step_preflight,
)
from app.domain.control.stop_optimization import (
    SCREENING_PARAMETER_RANGES,
    latin_hypercube_candidates,
    run_holdout_validation,
    run_multiobjective_optimization,
    run_parameter_screening,
)


class StopComfortEnergyExperimentTests(unittest.TestCase):
    @staticmethod
    def _fast_config() -> AtoConfig:
        return replace(
            baseline_ato_config(),
            use_dynamic_programming_profile=False,
        )

    def test_evaluator_records_unanchored_stop_comfort_and_energy_metrics(self) -> None:
        scenario = StopExperimentScenario(
            scenario_id="unit-80m",
            target_position_m=80.0,
            permitted_speed_mps=8.0,
            onboard_pax=0,
            dt_s=0.1,
            control_period_s=0.1,
            max_time_s=120.0,
            train_id="UNIT-STOP-1",
        )

        result = evaluate_stop_scenario(scenario, ato_config=self._fast_config())

        self.assertEqual(result.status, "STOPPED_AT_TARGET")
        self.assertTrue(result.ok, result.violations)
        self.assertLessEqual(abs(result.metrics["rawStopErrorM"]), 1.0)
        self.assertGreater(result.metrics["terminalJerkSampleCount"], 0)
        self.assertGreaterEqual(result.metrics["p95TerminalAbsJerkMps3"], 0.0)
        self.assertGreater(result.metrics["auxiliaryEnergyKwh"], 0.0)
        expected_net = (
            result.metrics["tractionEnergyKwh"]
            + result.metrics["auxiliaryEnergyKwh"]
            - result.metrics["regenCreditedEnergyKwh"]
        )
        self.assertAlmostEqual(result.metrics["netEnergyKwh"], expected_net, places=12)
        self.assertNotEqual(result.metrics["rawStopErrorM"], 0.0)

    def test_config_fingerprint_changes_with_candidate_parameter(self) -> None:
        scenario = StopExperimentScenario(
            scenario_id="fingerprint",
            target_position_m=80.0,
            permitted_speed_mps=8.0,
            onboard_pax=0,
            dt_s=0.2,
            control_period_s=0.2,
            max_time_s=120.0,
            train_id="UNIT-STOP-2",
        )
        baseline = evaluate_stop_scenario(scenario, ato_config=self._fast_config())
        changed = evaluate_stop_scenario(
            scenario,
            ato_config=replace(self._fast_config(), brake_margin_m=21.0),
        )

        self.assertNotEqual(baseline.config_fingerprint, changed.config_fingerprint)

    def test_preflight_rejects_invalid_time_step_list(self) -> None:
        with self.assertRaises(ValueError):
            run_time_step_preflight(
                StopExperimentScenario(),
                time_steps_s=(0.1,),
                ato_config=self._fast_config(),
            )

    def test_latin_hypercube_screening_is_deterministic_and_auditable(self) -> None:
        first = latin_hypercube_candidates(4, 17)
        second = latin_hypercube_candidates(4, 17)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 4)
        for candidate in first:
            for name, value in candidate["parameters"].items():
                low, high = SCREENING_PARAMETER_RANGES[name]
                self.assertGreaterEqual(value, low)
                self.assertLessEqual(value, high)

        scenario = StopExperimentScenario(
            scenario_id="screen-unit-80m",
            target_position_m=80.0,
            permitted_speed_mps=8.0,
            onboard_pax=0,
            dt_s=0.1,
            control_period_s=0.1,
            max_time_s=120.0,
            train_id="UNIT-SCREEN",
        )
        report = run_parameter_screening(
            [scenario],
            sample_count=4,
            seed=17,
            ato_config=self._fast_config(),
        )

        self.assertEqual(report["stage"], "screen")
        self.assertEqual(report["sampleCount"], 4)
        self.assertEqual(len(report["candidates"]), 4)
        self.assertEqual(set(report["sensitivity"]), set(SCREENING_PARAMETER_RANGES))
        self.assertEqual(len(report["recommendedOptimizationParameters"]), 4)

    def test_small_multiobjective_run_respects_budget_and_random_control(self) -> None:
        scenario = StopExperimentScenario(
            scenario_id="opt-unit-80m",
            target_position_m=80.0,
            permitted_speed_mps=8.0,
            onboard_pax=0,
            dt_s=0.1,
            control_period_s=0.1,
            max_time_s=120.0,
            train_id="UNIT-OPT",
        )

        report = run_multiobjective_optimization(
            [scenario],
            seeds=(17,),
            population_size=4,
            generations=2,
            ato_config=self._fast_config(),
        )

        self.assertEqual(report["stage"], "optimize")
        self.assertEqual(report["maximumEvaluationsPerSeed"], 8)
        self.assertLessEqual(report["nsga2Runs"][0]["evaluationCount"], 8)
        self.assertEqual(
            report["randomSearchRuns"][0]["evaluationCount"],
            report["nsga2Runs"][0]["evaluationCount"],
        )
        self.assertIn("hypervolume", report["nsga2Runs"][0])

    def test_holdout_validation_compares_common_jerk_sample_period(self) -> None:
        scenario = StopExperimentScenario(
            scenario_id="validate-unit-80m",
            target_position_m=80.0,
            permitted_speed_mps=8.0,
            onboard_pax=0,
            dt_s=0.1,
            control_period_s=0.1,
            max_time_s=120.0,
            train_id="UNIT-VALIDATE",
        )
        base = self._fast_config()
        candidate = {
            name: getattr(base, name)
            for name in (
                "brake_apply_slew_rate_percent_per_s",
                "profile_brake_timing_bias_s",
                "terminal_brake_floor_percent",
                "brake_release_slew_rate_percent_per_s",
            )
        }

        report = run_holdout_validation(
            [scenario],
            {"baseline": {"candidate": candidate}},
            ato_config=base,
        )

        self.assertEqual(report["stage"], "validate")
        self.assertEqual(report["scenarioCount"], 1)
        self.assertTrue(report["candidates"]["baseline"]["allConvergencePassed"])


if __name__ == "__main__":
    unittest.main()
