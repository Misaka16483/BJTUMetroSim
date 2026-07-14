from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from tools.run_long_horizon_timing_refinement_experiment import (
    _operation_gates,
    _timing_selection_key,
    parse_traction_values,
)
from tools.run_timetable_power_experiment import (
    _non_storage_constraints_pass,
    _wait_for_operation_profile_prewarm,
)


class LongHorizonTimingRefinementTests(unittest.TestCase):
    def test_traction_values_require_unique_zero_baseline(self) -> None:
        self.assertEqual(
            parse_traction_values("0.5,0,0.25,1.0"),
            (0.0, 0.5, 0.25, 1.0),
        )
        with self.assertRaises(ValueError):
            parse_traction_values("0.25,0.5")
        with self.assertRaises(ValueError):
            parse_traction_values("0,0.5,0.5")

    def test_operation_gates_require_completed_ready_safe_trajectory(self) -> None:
        case = {
            "trajectoryMetadata": {
                "operationAcceptanceAtCaptureEnd": {
                    "completedServiceCount": 6,
                    "readyForAnalysis": True,
                    "scheduleWithinTolerance": True,
                    "stuckTrainCount": 0,
                },
                "profileWarmup": {"allProfilesReady": True},
                "controlQuality": {
                    "rapidLowSpeedBrakeReapplicationCount": 0,
                    "tractionBrakeOverlapSampleCount": 0,
                    "emergencyBrakeInterventionCount": 0,
                },
                "coverage": {
                    "movingSampleCount": 1,
                    "tractionSampleCount": 1,
                    "brakingSampleCount": 1,
                    "regenSampleCount": 1,
                },
            }
        }
        gates = _operation_gates(
            case,
            {"constraints": {"minimumVoltage": False, "powerBalance": True}},
            minimum_completed_services=1,
            require_ready_for_analysis=True,
        )
        self.assertTrue(all(gates.values()))

        case["trajectoryMetadata"]["operationAcceptanceAtCaptureEnd"][
            "readyForAnalysis"
        ] = False
        gates = _operation_gates(
            case,
            {"constraints": {"minimumVoltage": False, "powerBalance": True}},
            minimum_completed_services=1,
            require_ready_for_analysis=True,
        )
        self.assertFalse(gates["readyForAnalysis"])

    def test_timing_selection_prefers_utility_then_smaller_change(self) -> None:
        cases = [
            {
                "caseId": "LARGER",
                "relativeUtilityVsBaselineNoStorage": 0.98,
                "timingCandidate": {"tractionTimingSec": 0.75},
            },
            {
                "caseId": "SMALLER",
                "relativeUtilityVsBaselineNoStorage": 0.98,
                "timingCandidate": {"tractionTimingSec": 0.5},
            },
        ]
        self.assertEqual(min(cases, key=_timing_selection_key)["caseId"], "SMALLER")

    def test_storage_remediable_failures_do_not_invalidate_replay(self) -> None:
        self.assertTrue(_non_storage_constraints_pass({
            "constraints": {
                "minimumVoltage": False,
                "substationCapacity": False,
                "terminalSoc": False,
                "dynamicsClosure": True,
                "operationalMetrics": True,
            }
        }))
        self.assertFalse(_non_storage_constraints_pass({
            "constraints": {
                "minimumVoltage": False,
                "terminalSoc": False,
                "dynamicsClosure": False,
            }
        }))

    def test_batch_capture_waits_for_every_profile_before_clock_start(self) -> None:
        status = {
            "ready": True,
            "failedProfileCount": 0,
            "pendingCacheKeys": [],
            "errors": {},
        }
        service = Mock()
        service.wait_for.return_value = status
        engine = SimpleNamespace(
            _operation_profile_requests={"profile-b": object(), "profile-a": object()},
            speed_profile_service=service,
            _refresh_operation_profile_warmup=Mock(),
        )

        self.assertIs(
            _wait_for_operation_profile_prewarm(engine, 12.5),
            status,
        )
        service.wait_for.assert_called_once_with(("profile-b", "profile-a"), 12.5)
        engine._refresh_operation_profile_warmup.assert_called_once_with()

    def test_batch_capture_rejects_incomplete_profile_prewarm(self) -> None:
        service = Mock()
        service.wait_for.return_value = {
            "ready": False,
            "failedProfileCount": 0,
            "pendingCacheKeys": ["profile-a"],
            "errors": {},
        }
        engine = SimpleNamespace(
            _operation_profile_requests={"profile-a": object()},
            speed_profile_service=service,
            _refresh_operation_profile_warmup=Mock(),
        )

        with self.assertRaisesRegex(TimeoutError, "profile-a"):
            _wait_for_operation_profile_prewarm(engine, 0.0)


if __name__ == "__main__":
    unittest.main()
