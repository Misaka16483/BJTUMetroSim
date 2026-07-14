from __future__ import annotations

from types import SimpleNamespace
import unittest

from app.domain.control import AtoConfig
from app.domain.dispatch.timetable import TimetableService
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


if __name__ == "__main__":
    unittest.main()
