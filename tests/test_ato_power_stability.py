from __future__ import annotations

import unittest

from app.domain.control import (
    ATOController,
    AtoConfig,
    AtoTarget,
    estimate_scheduled_run_time_s,
    optimize_speed_profile_dcdp,
)
from app.domain.vehicle import TrainState


def _command_group(point: object) -> str:
    if getattr(point, "brake_percent", 0.0) > 0:
        return "BRAKE"
    if getattr(point, "traction_percent", 0.0) > 0:
        return "TRACTION"
    return "NEUTRAL"


class AtoPowerStabilityTests(unittest.TestCase):
    def test_dcdp_profile_has_no_direct_traction_brake_chatter(self) -> None:
        scheduled_time_s = estimate_scheduled_run_time_s(
            target_position_m=200.0,
            permitted_speed_mps=12.0,
            acceleration_mps2=0.54,
            deceleration_mps2=0.6,
        )
        profile = optimize_speed_profile_dcdp(
            target_position_m=200.0,
            permitted_speed_mps=12.0,
            scheduled_run_time_s=scheduled_time_s,
            dt_s=1.0,
            position_step_m=5.0,
            speed_step_mps=0.5,
        )

        groups = [_command_group(point) for point in profile.points]
        direct_reversals = sum(
            {left, right} == {"TRACTION", "BRAKE"}
            for left, right in zip(groups, groups[1:])
        )
        switches = sum(left != right for left, right in zip(groups, groups[1:]))
        self.assertEqual(direct_reversals, 0)
        self.assertLessEqual(switches, 8)

        terminal_braking_started = False
        for point, group in zip(profile.points, groups):
            remaining_m = profile.target_position_m - point.position_m
            stopping_m = point.speed_mps * point.speed_mps / (2.0 * 0.6)
            if group == "BRAKE" and remaining_m <= stopping_m + 10.0:
                terminal_braking_started = True
            if terminal_braking_started:
                self.assertNotEqual(group, "TRACTION")

    def test_terminal_braking_latch_blocks_retraction_until_reset(self) -> None:
        controller = ATOController(AtoConfig(use_dynamic_programming_profile=False))
        target = AtoTarget(target_position_m=1000.0, permitted_speed_mps=12.0)

        braking = controller.decide(
            TrainState("T1", position_m=720.0, speed_mps=20.0, sim_time_s=0.0),
            target,
        )
        attempted_retraction = controller.decide(
            TrainState("T1", position_m=725.0, speed_mps=5.0, sim_time_s=0.25),
            target,
        )

        self.assertGreater(braking.brake_percent, 0.0)
        self.assertEqual(attempted_retraction.traction_percent, 0.0)
        self.assertGreater(attempted_retraction.brake_percent, 0.0)

        controller.reset()
        after_reset = controller.decide(
            TrainState("T1", position_m=0.0, speed_mps=0.0, sim_time_s=1.0),
            AtoTarget(target_position_m=1000.0, permitted_speed_mps=12.0),
        )
        self.assertGreater(after_reset.traction_percent, 0.0)

    def test_terminal_braking_latch_releases_when_movement_authority_extends(self) -> None:
        controller = ATOController(AtoConfig(use_dynamic_programming_profile=False))
        temporary_authority = AtoTarget(target_position_m=1000.0, permitted_speed_mps=12.0)

        braking = controller.decide(
            TrainState("T1", position_m=720.0, speed_mps=20.0, sim_time_s=0.0),
            temporary_authority,
        )
        controller.decide(
            TrainState("T1", position_m=725.0, speed_mps=0.0, sim_time_s=0.25),
            temporary_authority,
        )
        self.assertGreater(braking.brake_percent, 0.0)
        self.assertTrue(controller._terminal_braking_latched)

        extended_authority = AtoTarget(target_position_m=1400.0, permitted_speed_mps=12.0)
        commands = [
            controller.decide(
                TrainState("T1", position_m=725.0, speed_mps=0.0, sim_time_s=sim_time_s),
                extended_authority,
            )
            for sim_time_s in (0.5, 0.75, 1.0, 1.25, 1.5, 1.75)
        ]

        self.assertFalse(controller._terminal_braking_latched)
        self.assertGreater(commands[-1].traction_percent, 0.0)
        self.assertEqual(commands[-1].brake_percent, 0.0)

    def test_traction_command_uses_realistic_slew_rate(self) -> None:
        config = AtoConfig(
            use_dynamic_programming_profile=False,
            traction_slew_rate_percent_per_s=30.0,
        )
        controller = ATOController(config)
        target = AtoTarget(target_position_m=1000.0, permitted_speed_mps=12.0)

        first = controller.decide(
            TrainState("T1", position_m=0.0, speed_mps=0.0, sim_time_s=0.0),
            target,
        )
        second = controller.decide(
            TrainState("T1", position_m=0.0, speed_mps=0.0, sim_time_s=0.25),
            target,
        )

        self.assertLessEqual(first.traction_percent, 30.0)
        self.assertLessEqual(second.traction_percent - first.traction_percent, 7.5 + 1e-9)


if __name__ == "__main__":
    unittest.main()
