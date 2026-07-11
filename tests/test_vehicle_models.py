from __future__ import annotations

import unittest

from app.domain.vehicle import (
    BrakeBlendService,
    CommandSource,
    ControlCommand,
    SimpleVehicleModel,
    TractionDriveModel,
    TrainState,
    VehicleConfig,
)


class VehicleModelDataTests(unittest.TestCase):
    def test_vehicle_config_defaults(self) -> None:
        config = VehicleConfig()
        self.assertEqual(config.train_id, "T001")
        self.assertEqual(config.mass_kg, 225_000.0)
        self.assertEqual(config.train_length_m, 118.0)
        self.assertEqual(config.motor_count, 16)

    def test_passenger_load_updates_total_mass(self) -> None:
        config = VehicleConfig.for_load("T001", onboard_pax=500)
        self.assertEqual(config.mass_kg, 225_000.0 + 500 * 65.0)

    def test_teacher_curve_reduces_traction_capacity_at_high_speed(self) -> None:
        drive = TractionDriveModel(VehicleConfig())
        self.assertGreater(drive.traction_capacity_n(2.0), drive.traction_capacity_n(22.22))

    def test_regen_limit_is_filled_by_pneumatic_brake(self) -> None:
        drive = TractionDriveModel(VehicleConfig())
        demand = drive.demand(ControlCommand("T001", brake_percent=80.0), speed_mps=15.0)
        blend = BrakeBlendService.blend(demand, regen_limit_ratio=0.25)
        self.assertAlmostEqual(blend.total_brake_force_n, demand.total_brake_force_n)
        self.assertGreater(blend.pneumatic_brake_force_n, blend.electric_brake_force_n)

    def test_train_state_rejects_negative_speed(self) -> None:
        with self.assertRaises(ValueError):
            TrainState(
                train_id="T001",
                position_m=0.0,
                speed_mps=-1.0,
                acceleration_mps2=0.0,
                sim_time_s=0.0,
            )

    def test_control_command_rejects_conflicting_percentages(self) -> None:
        with self.assertRaises(ValueError):
            ControlCommand(train_id="T001", traction_percent=10.0, brake_percent=10.0)

    def test_control_command_serializes_source(self) -> None:
        command = ControlCommand(train_id="T001", traction_percent=40.0, source=CommandSource.ATO)
        self.assertEqual(command.to_dict()["source"], "ATO")


class SimpleVehicleModelTests(unittest.TestCase):
    def test_vehicle_accelerates_with_traction_command(self) -> None:
        model = SimpleVehicleModel()
        state = TrainState("T001", position_m=0.0, speed_mps=0.0, acceleration_mps2=0.0, sim_time_s=0.0)
        next_state = model.step(state, ControlCommand("T001", traction_percent=40.0), dt_s=1.0)

        self.assertGreater(next_state.speed_mps, state.speed_mps)
        self.assertGreater(next_state.position_m, state.position_m)
        self.assertGreater(next_state.net_energy_kwh, state.net_energy_kwh)

    def test_vehicle_brakes_to_stop_without_negative_speed(self) -> None:
        model = SimpleVehicleModel()
        state = TrainState("T001", position_m=100.0, speed_mps=2.0, acceleration_mps2=0.0, sim_time_s=0.0)
        next_state = model.step(state, ControlCommand("T001", brake_percent=100.0), dt_s=10.0)

        self.assertEqual(next_state.speed_mps, 0.0)
        self.assertLess(next_state.acceleration_mps2, 0.0)
        self.assertGreaterEqual(next_state.position_m, state.position_m)
        self.assertLessEqual(next_state.net_energy_kwh, state.net_energy_kwh)

    def test_emergency_brake_is_stronger_than_service_brake(self) -> None:
        model = SimpleVehicleModel()
        state = TrainState("T001", position_m=0.0, speed_mps=10.0, acceleration_mps2=0.0, sim_time_s=0.0)

        service = model.step(state, ControlCommand("T001", brake_percent=20.0), dt_s=1.0)
        emergency = model.step(state, ControlCommand("T001", emergency_brake=True), dt_s=1.0)

        self.assertLess(emergency.speed_mps, service.speed_mps)
        self.assertLess(emergency.acceleration_mps2, service.acceleration_mps2)

    def test_power_limit_reduces_acceleration(self) -> None:
        model = SimpleVehicleModel()
        state = TrainState("T001", position_m=0.0, speed_mps=0.0, acceleration_mps2=0.0, sim_time_s=0.0)
        command = ControlCommand("T001", traction_percent=80.0)

        unrestricted = model.step(state, command, traction_limit_ratio=1.0)
        limited = model.step(state, command, traction_limit_ratio=0.5)

        self.assertLess(limited.acceleration_mps2, unrestricted.acceleration_mps2)

    def test_command_percent_must_not_exceed_100(self) -> None:
        with self.assertRaises(ValueError):
            ControlCommand("T001", traction_percent=101.0)


if __name__ == "__main__":
    unittest.main()
