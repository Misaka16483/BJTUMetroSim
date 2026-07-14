from __future__ import annotations

import unittest
from unittest.mock import patch

from app.domain.control.models import AtoConfig, AtoTarget
from app.domain.control.services import ATOController
from app.domain.control.speed_profile import (
    OptimizedSpeedProfile,
    SpeedProfilePoint,
    _candidate_commands,
)
from app.domain.vehicle.models import TrainState, VehicleConfig


class PowerCurveControlConsistencyTests(unittest.TestCase):
    def test_profile_traction_feedforward_fades_near_speed_target(self) -> None:
        controller = ATOController(AtoConfig())
        profile = OptimizedSpeedProfile(
            points=(
                SpeedProfilePoint(0.0, 0.0, 0.0, "START", 0.0, 0.0, 0.0),
                SpeedProfilePoint(1.0, 20.0, 20.0, "MAX_TRACTION", 100.0, 0.0, 0.1),
            ),
            target_position_m=1_000.0,
            permitted_speed_mps=20.0,
            scheduled_run_time_s=60.0,
            terminal_score=0.0,
        )
        state = TrainState("T1", position_m=0.0, speed_mps=19.93)
        target = AtoTarget(target_position_m=1_000.0, permitted_speed_mps=20.0)

        with patch.object(controller, "_profile_for", return_value=profile):
            output = controller._apply_profile_feedforward(state, target, 20.0, 0.0)

        self.assertGreater(output, 0.0)
        self.assertLess(output, 5.0)
        self.assertAlmostEqual(output, controller.last_profile_feedforward_percent, places=9)

    def test_cruise_candidate_matches_speed_dependent_running_resistance(self) -> None:
        config = VehicleConfig(train_id="T1")
        candidates = dict(_candidate_commands("T1", 22.0, config, gradient_force_n=0.0))

        self.assertGreater(candidates["CRUISE"].traction_percent, 15.0)
        self.assertLess(candidates["CRUISE"].traction_percent, 30.0)
        self.assertEqual(candidates["CRUISE"].brake_percent, 0.0)

    def test_feedforward_configuration_must_exceed_pid_deadband(self) -> None:
        with self.assertRaises(ValueError):
            AtoConfig(profile_feedforward_full_error_mps=0.06)


if __name__ == "__main__":
    unittest.main()
