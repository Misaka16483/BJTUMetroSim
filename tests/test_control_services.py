from __future__ import annotations

import unittest

from app.domain.control import (
    ATOController,
    AtoConfig,
    AtoTarget,
    CabControlService,
    OperationMode,
    run_ato_stop_demo,
)
from app.domain.vehicle import CommandSource, ControlCommand, SimpleVehicleModel, TrainState


class ATOControllerTests(unittest.TestCase):
    def test_ato_uses_traction_when_target_is_far(self) -> None:
        controller = ATOController()
        state = TrainState("T001", position_m=0.0, speed_mps=0.0, acceleration_mps2=0.0, sim_time_s=0.0)
        command = controller.decide(state, AtoTarget(target_position_m=1000.0, permitted_speed_mps=12.0))

        self.assertGreater(command.traction_level, 0)
        self.assertEqual(command.source, CommandSource.ATO)

    def test_ato_brakes_when_approaching_target(self) -> None:
        controller = ATOController()
        state = TrainState("T001", position_m=970.0, speed_mps=8.0, acceleration_mps2=0.0, sim_time_s=0.0)
        command = controller.decide(state, AtoTarget(target_position_m=1000.0, permitted_speed_mps=12.0))

        self.assertGreater(command.brake_level, 0)
        self.assertEqual(command.traction_level, 0)

    def test_ato_holds_brake_when_stopped_at_target(self) -> None:
        controller = ATOController()
        state = TrainState("T001", position_m=1000.0, speed_mps=0.0, acceleration_mps2=0.0, sim_time_s=0.0)
        command = controller.decide(state, AtoTarget(target_position_m=1000.0, permitted_speed_mps=12.0))

        self.assertEqual(command.brake_level, 1)
        self.assertFalse(command.emergency_brake)

    def test_ato_emergency_target_overrides_rules(self) -> None:
        controller = ATOController()
        state = TrainState("T001", position_m=100.0, speed_mps=4.0, acceleration_mps2=0.0, sim_time_s=0.0)
        command = controller.decide(
            state,
            AtoTarget(target_position_m=1000.0, permitted_speed_mps=12.0, emergency_brake_required=True),
        )

        self.assertTrue(command.emergency_brake)
        self.assertEqual(command.traction_level, 0)

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


class CabControlServiceTests(unittest.TestCase):
    def test_cab_uses_ato_command_in_ato_mode(self) -> None:
        service = CabControlService()
        ato_command = ControlCommand("T001", traction_level=2, source=CommandSource.ATO)

        final_command = service.compose("T001", OperationMode.ATO, ato_command=ato_command)

        self.assertEqual(final_command, ato_command)

    def test_cab_atp_emergency_overrides_ato(self) -> None:
        service = CabControlService()
        ato_command = ControlCommand("T001", traction_level=2, source=CommandSource.ATO)

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


if __name__ == "__main__":
    unittest.main()
