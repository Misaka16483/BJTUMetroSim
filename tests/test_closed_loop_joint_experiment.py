from __future__ import annotations

from types import SimpleNamespace
import unittest

from app.domain.control import AtoConfig
from app.domain.dispatch.timetable import TimetableService
from tools.run_closed_loop_joint_experiment import (
    _recommendation_key,
    build_local_timing_grid,
)
from tools.run_timetable_power_experiment import configure_engine_timing_candidate


class ClosedLoopJointExperimentTests(unittest.TestCase):
    @staticmethod
    def _engine() -> SimpleNamespace:
        return SimpleNamespace(
            _ato_config=AtoConfig(),
            timetable_service=TimetableService(),
        )

    def test_candidate_changes_ato_phase_lookup_and_operation_headway(self) -> None:
        engine = self._engine()
        baseline_headway = dict(engine.timetable_service.headway_config.period_headway_sec)

        applied = configure_engine_timing_candidate(engine, {
            "departureSpreadSec": 10.0,
            "tractionTimingSec": -1.0,
            "brakeTimingSec": -0.5,
        })

        self.assertEqual(engine._ato_config.profile_traction_timing_bias_s, -1.0)
        self.assertEqual(engine._ato_config.profile_brake_timing_bias_s, -0.5)
        for name, seconds in baseline_headway.items():
            self.assertEqual(
                engine.timetable_service.headway_config.period_headway_sec[name],
                seconds + 10.0,
            )
        self.assertEqual(
            applied["semantics"]["departureSpreadSec"],
            "ADDED_TO_PERIOD_HEADWAY",
        )

    def test_candidate_bounds_are_enforced_before_engine_run(self) -> None:
        with self.assertRaises(ValueError):
            configure_engine_timing_candidate(
                self._engine(),
                {"departureSpreadSec": 31.0},
            )
        with self.assertRaises(ValueError):
            configure_engine_timing_candidate(
                self._engine(),
                {"brakeTimingSec": -5.1},
            )

    def test_local_grid_is_baseline_first_complete_and_single_factor_separable(self) -> None:
        cases = build_local_timing_grid(0.5)

        self.assertEqual(len(cases), 9)
        self.assertEqual(cases[0]["caseId"], "BASELINE")
        candidates = [item["candidate"] for item in cases]
        self.assertEqual(
            {
                (
                    item["tractionTimingSec"],
                    item["brakeTimingSec"],
                )
                for item in candidates
            },
            {
                (traction, brake)
                for traction in (-0.5, 0.0, 0.5)
                for brake in (-0.5, 0.0, 0.5)
            },
        )
        self.assertTrue(all(item["departureSpreadSec"] == 0.0 for item in candidates))

        with self.assertRaises(ValueError):
            build_local_timing_grid(0.0)

    def test_equivalent_utility_prefers_the_least_intrusive_timing_change(self) -> None:
        cases = [
            {
                "caseId": "BRAKE_MINUS",
                "relativeUtilityVsGlobalBaseline": 0.95,
                "timingCandidate": {
                    "departureSpreadSec": 0.0,
                    "tractionTimingSec": 0.5,
                    "brakeTimingSec": -0.5,
                },
            },
            {
                "caseId": "BRAKE_ZERO",
                "relativeUtilityVsGlobalBaseline": 0.95,
                "timingCandidate": {
                    "departureSpreadSec": 0.0,
                    "tractionTimingSec": 0.5,
                    "brakeTimingSec": 0.0,
                },
            },
        ]

        self.assertEqual(min(cases, key=_recommendation_key)["caseId"], "BRAKE_ZERO")


if __name__ == "__main__":
    unittest.main()
