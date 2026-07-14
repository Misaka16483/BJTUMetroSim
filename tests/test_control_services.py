from __future__ import annotations

import math
import unittest

from app.domain.control import (
    ATOController,
    AtoConfig,
    AtoTarget,
    CabControlService,
    OperationMode,
    OptimizedSpeedProfile,
    SpeedProfilePoint,
    VehicleInteractiveSession,
    estimate_scheduled_run_time_s,
    optimize_speed_profile_dcdp,
    run_ato_stop_demo,
    stopping_target_speed_mps,
)
from app.domain.line.services import PathPlanner
from app.domain.vehicle import CommandSource, ControlCommand, SimpleVehicleModel, TrainState
from tests.test_phase0 import tiny_line_map


class ATOControllerTests(unittest.TestCase):
    @staticmethod
    def _installed_test_profile(target_position_m: float, permitted_speed_mps: float) -> OptimizedSpeedProfile:
        return OptimizedSpeedProfile(
            points=(
                SpeedProfilePoint(0.0, 0.0, 0.0, "START", 0.0, 0.0, 0.0),
                SpeedProfilePoint(10.0, target_position_m / 2.0, 5.0, "COAST", 0.0, 0.0, 0.0),
                SpeedProfilePoint(20.0, target_position_m, 0.0, "STOP", 0.0, 0.0, 0.0),
            ),
            target_position_m=target_position_m,
            permitted_speed_mps=permitted_speed_mps,
            scheduled_run_time_s=20.0,
            terminal_score=0.0,
        )

    def test_ato_uses_traction_when_target_is_far(self) -> None:
        controller = ATOController()
        state = TrainState("T001", position_m=0.0, speed_mps=0.0, acceleration_mps2=0.0, sim_time_s=0.0)
        command = controller.decide(state, AtoTarget(target_position_m=1000.0, permitted_speed_mps=12.0))

        self.assertGreater(command.traction_percent, 0)
        self.assertEqual(command.source, CommandSource.ATO)

    def test_ato_brakes_when_approaching_target(self) -> None:
        controller = ATOController()
        state = TrainState("T001", position_m=970.0, speed_mps=8.0, acceleration_mps2=0.0, sim_time_s=0.0)
        command = controller.decide(state, AtoTarget(target_position_m=1000.0, permitted_speed_mps=12.0))

        self.assertGreater(command.brake_percent, 0)
        self.assertEqual(command.traction_percent, 0)

    def test_ato_uses_pid_instead_of_full_brake_for_minor_overspeed(self) -> None:
        controller = ATOController(AtoConfig(use_dynamic_programming_profile=False))
        state = TrainState(
            "T001",
            position_m=890.0,
            speed_mps=12.15,
            acceleration_mps2=0.0,
            sim_time_s=10.0,
        )

        command = controller.decide(
            state,
            AtoTarget(target_position_m=1000.0, permitted_speed_mps=12.0),
        )

        self.assertGreater(command.brake_percent, 0.0)
        self.assertLess(command.brake_percent, 30.0)

    def test_ato_holds_brake_when_stopped_at_target(self) -> None:
        controller = ATOController()
        state = TrainState("T001", position_m=1000.0, speed_mps=0.0, acceleration_mps2=0.0, sim_time_s=0.0)
        command = controller.decide(state, AtoTarget(target_position_m=1000.0, permitted_speed_mps=12.0))

        self.assertEqual(command.brake_percent, 20.0)
        self.assertFalse(command.emergency_brake)

    def test_ato_emergency_target_overrides_rules(self) -> None:
        controller = ATOController()
        state = TrainState("T001", position_m=100.0, speed_mps=4.0, acceleration_mps2=0.0, sim_time_s=0.0)
        command = controller.decide(
            state,
            AtoTarget(target_position_m=1000.0, permitted_speed_mps=12.0, emergency_brake_required=True),
        )

        self.assertTrue(command.emergency_brake)
        self.assertEqual(command.traction_percent, 0)

    def test_ato_vehicle_loop_stops_near_target(self) -> None:
        controller = ATOController(AtoConfig(expected_deceleration_mps2=0.6))
        vehicle = SimpleVehicleModel()
        state = TrainState("T001", position_m=0.0, speed_mps=0.0, acceleration_mps2=0.0, sim_time_s=0.0)
        target_position_m = 200.0

        for _ in range(120):
            command = controller.decide(state, AtoTarget(target_position_m, permitted_speed_mps=12.0))
            state = vehicle.step(state, command, dt_s=1.0)
            if state.speed_mps <= 0.05 and abs(state.position_m - target_position_m) <= 1.0:
                break

        self.assertLessEqual(abs(state.position_m - target_position_m), 1.0)
        self.assertLessEqual(state.speed_mps, 0.05)

    def test_speed_profile_drops_near_stop_target(self) -> None:
        far_speed = stopping_target_speed_mps(
            position_m=0.0,
            target_position_m=200.0,
            permitted_speed_mps=12.0,
            cruise_speed_mps=12.0,
            expected_deceleration_mps2=0.6,
            stop_tolerance_m=1.0,
        )
        near_speed = stopping_target_speed_mps(
            position_m=190.0,
            target_position_m=200.0,
            permitted_speed_mps=12.0,
            cruise_speed_mps=12.0,
            expected_deceleration_mps2=0.6,
            stop_tolerance_m=1.0,
        )

        self.assertEqual(far_speed, 12.0)
        self.assertLess(near_speed, far_speed)

    def test_dynamic_programming_profile_starts_and_stops_smoothly(self) -> None:
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

        self.assertEqual(profile.points[0].speed_mps, 0.0)
        self.assertEqual(profile.points[-1].position_m, 200.0)
        self.assertEqual(profile.points[-1].speed_mps, 0.0)
        self.assertLess(profile.speed_at_position_mps(5.0), 12.0)
        self.assertLessEqual(max(point.speed_mps for point in profile.points), 12.0)

    def test_dynamic_programming_profile_respects_path_plan_limits(self) -> None:
        path_plan = PathPlanner(tiny_line_map()).plan_between_platforms(20, 21, direction="forward")
        profile = optimize_speed_profile_dcdp(
            target_position_m=path_plan.total_length_m,
            permitted_speed_mps=12.0,
            scheduled_run_time_s=35.0,
            dt_s=1.0,
            position_step_m=2.0,
            speed_step_mps=0.5,
            terminal_tolerance_m=1.0,
            max_states_per_stage=800,
            path_plan=path_plan,
        )

        self.assertTrue(math.isfinite(profile.terminal_score))
        self.assertEqual(profile.target_position_m, path_plan.total_length_m)
        for point in profile.points:
            allowed_speed_mps = path_plan.speed_limit_at(point.position_m, 12.0)
            self.assertLessEqual(point.speed_mps, allowed_speed_mps + 1e-6)

    def test_installed_profile_survives_runtime_authority_speed_rounding(self) -> None:
        path_plan = PathPlanner(tiny_line_map()).plan_between_platforms(
            20, 21, direction="forward"
        )
        controller = ATOController(
            AtoConfig(target_cruise_speed_mps=22.22),
            enable_synchronous_profile_optimization=False,
        )
        state = TrainState(
            "T001", position_m=0.0, speed_mps=0.0,
            acceleration_mps2=0.0, sim_time_s=0.0,
        )
        installed_target = AtoTarget(
            target_position_m=path_plan.total_length_m,
            permitted_speed_mps=80.0 / 3.6,
            path_plan=path_plan,
        )
        profile = self._installed_test_profile(
            path_plan.total_length_m,
            22.22,
        )
        controller.install_profile(state, installed_target, profile)

        runtime_target = AtoTarget(
            target_position_m=path_plan.total_length_m,
            permitted_speed_mps=22.22,
            path_plan=path_plan,
        )
        controller.target_speed_mps(state, runtime_target)

        self.assertNotEqual(controller.last_profile_mode, "BRAKING_CURVE")

    def test_installed_profile_does_not_override_shortened_authority_endpoint(self) -> None:
        path_plan = PathPlanner(tiny_line_map()).plan_between_platforms(
            20, 21, direction="forward"
        )
        controller = ATOController(
            AtoConfig(target_cruise_speed_mps=12.0),
            enable_synchronous_profile_optimization=False,
        )
        state = TrainState(
            "T001", position_m=0.0, speed_mps=0.0,
            acceleration_mps2=0.0, sim_time_s=0.0,
        )
        full_target = AtoTarget(
            target_position_m=path_plan.total_length_m,
            permitted_speed_mps=12.0,
            path_plan=path_plan,
        )
        profile = self._installed_test_profile(
            path_plan.total_length_m,
            12.0,
        )
        controller.install_profile(state, full_target, profile)

        controller.target_speed_mps(
            state,
            AtoTarget(
                target_position_m=path_plan.total_length_m / 2.0,
                permitted_speed_mps=12.0,
                path_plan=path_plan,
            ),
        )

        self.assertEqual(controller.last_profile_mode, "BRAKING_CURVE")

    def test_ato_dynamic_profile_does_not_start_at_full_speed(self) -> None:
        controller = ATOController(AtoConfig(expected_deceleration_mps2=0.6))
        state = TrainState("T001", position_m=0.0, speed_mps=0.0, acceleration_mps2=0.0, sim_time_s=0.0)
        command = controller.decide(state, AtoTarget(target_position_m=200.0, permitted_speed_mps=12.0))

        self.assertGreater(command.traction_percent, 0.0)
        self.assertGreater(controller.last_target_speed_mps, 0.0)
        self.assertLess(controller.last_target_speed_mps, 12.0)

    def test_ato_records_pid_tracking_diagnostics(self) -> None:
        controller = ATOController(AtoConfig(expected_deceleration_mps2=0.6))
        state = TrainState("T001", position_m=100.0, speed_mps=12.0, acceleration_mps2=0.0, sim_time_s=10.0)
        command = controller.decide(state, AtoTarget(target_position_m=120.0, permitted_speed_mps=12.0))

        self.assertLess(controller.last_target_speed_mps, state.speed_mps)
        self.assertLess(controller.last_speed_error_mps, 0.0)
        self.assertLess(controller.last_pid_output_percent, 0.0)
        self.assertGreater(command.brake_percent, 0)


class CabControlServiceTests(unittest.TestCase):
    def test_cab_uses_ato_command_in_ato_mode(self) -> None:
        service = CabControlService()
        ato_command = ControlCommand("T001", traction_percent=40.0, source=CommandSource.ATO)

        final_command = service.compose("T001", OperationMode.ATO, ato_command=ato_command)

        self.assertEqual(final_command, ato_command)

    def test_cab_atp_emergency_overrides_ato(self) -> None:
        service = CabControlService()
        ato_command = ControlCommand("T001", traction_percent=40.0, source=CommandSource.ATO)

        final_command = service.compose(
            "T001",
            OperationMode.ATO,
            ato_command=ato_command,
            atp_emergency_brake=True,
        )

        self.assertTrue(final_command.emergency_brake)
        self.assertEqual(final_command.source, CommandSource.ATP_OVERRIDE)


class VehicleDemoScenarioTests(unittest.TestCase):
    def test_vehicle_demo_returns_reproducible_summary(self) -> None:
        result = run_ato_stop_demo(target_position_m=200.0)
        payload = result.to_dict()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "STOPPED_AT_TARGET")
        self.assertLessEqual(abs(payload["stop_error_m"]), 1.0)
        self.assertEqual(payload["final_speed_mps"], 0.0)


class VehicleInteractiveSessionTests(unittest.TestCase):
    def test_manual_traction_and_brake_commands_step_state(self) -> None:
        session = VehicleInteractiveSession(target_position_m=200.0)

        after_traction = session.apply_command("traction 60")
        after_brake = session.apply_command("brake 40")

        self.assertEqual(after_traction["command"]["mode"], "TRACTION")
        self.assertGreater(after_traction["speedMps"], 0)
        self.assertEqual(after_brake["command"]["mode"], "BRAKE")
        self.assertEqual(after_brake["ticks"], 2)

    def test_ato_command_can_be_applied_interactively(self) -> None:
        session = VehicleInteractiveSession(target_position_m=200.0)
        payload = session.apply_command("ato")

        self.assertEqual(payload["command"]["source"], "ATO")
        self.assertGreater(payload["command"]["tractionPercent"], 0)
        self.assertIn("targetSpeedMps", payload["command"])

    def test_reset_restores_initial_state(self) -> None:
        session = VehicleInteractiveSession(target_position_m=200.0)
        session.apply_command("traction 3")

        payload = session.apply_command("reset")

        self.assertEqual(payload["message"], "reset")
        self.assertEqual(payload["positionM"], 0.0)
        self.assertEqual(payload["ticks"], 0)

    def test_handle_step_maps_to_traction_and_brake_percent(self) -> None:
        session = VehicleInteractiveSession(target_position_m=200.0)

        traction = session.command_from_handle_step(3)
        coast = session.command_from_handle_step(0)
        brake = session.command_from_handle_step(-2)

        self.assertEqual(traction.traction_percent, 60.0)
        self.assertEqual(coast.traction_percent, 0)
        self.assertEqual(coast.brake_percent, 0)
        self.assertEqual(brake.brake_percent, 40.0)

    def test_handle_step_steps_state(self) -> None:
        session = VehicleInteractiveSession(target_position_m=200.0)

        payload = session.apply_handle_step(2)

        self.assertEqual(payload["command"]["mode"], "TRACTION")
        self.assertEqual(payload["command"]["tractionPercent"], 40.0)
        self.assertEqual(payload["ticks"], 1)


if __name__ == "__main__":
    unittest.main()
