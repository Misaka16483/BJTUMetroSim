from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from app.domain.power.line9_topology import load_line9_power_network
from app.domain.power.services import PowerSection, PowerService, TrainPowerRequest


ROOT = Path(__file__).resolve().parents[1]


class PowerServiceNetworkTests(unittest.TestCase):
    def test_legacy_update_still_works_without_positions(self) -> None:
        service = PowerService([
            PowerSection("PWR-09-UP", "Up", max_traction_power_kw=1000.0, warning_power_kw=800.0),
        ])

        states = service.update([
            TrainPowerRequest("T1", "PWR-09-UP", speed_mps=10.0, traction_force_n=70_000.0),
        ], dt_sec=1.0)

        self.assertIn("PWR-09-UP", states)
        self.assertIsNone(service.last_network_snapshot)
        self.assertGreater(states["PWR-09-UP"].requested_power_kw, 0.0)

    def test_network_update_returns_legacy_state_and_snapshot(self) -> None:
        network = load_line9_power_network(ROOT / "data" / "scenarios" / "line9_power_topology.json")
        service = PowerService(
            [
                PowerSection("PWR-09-UP", "Up", max_traction_power_kw=6000.0, warning_power_kw=4500.0),
                PowerSection("PWR-09-DOWN", "Down", max_traction_power_kw=6000.0, warning_power_kw=4500.0),
            ],
            network=network,
        )

        states = service.update([
            TrainPowerRequest(
                "T1",
                "PWR-09-UP",
                speed_mps=18.0,
                traction_force_n=95_000.0,
                position_m=2_400.0,
                direction="UP",
                aux_power_kw=60.0,
            ),
        ], dt_sec=1.0, sim_time_ms=12_345)

        state = states["PWR-09-UP"]
        self.assertIsNotNone(service.last_network_snapshot)
        assert service.last_network_snapshot is not None
        self.assertEqual(service.last_network_snapshot.sim_time_ms, 12_345)
        self.assertGreater(state.min_train_voltage_v, 650.0)
        self.assertGreater(state.max_train_current_a, 0.0)
        self.assertGreaterEqual(state.substation_count, 10)
        self.assertEqual(state.quality, "ENGINEERING_ESTIMATE")

    def test_sustained_feeder_overload_trips_protection(self) -> None:
        network = load_line9_power_network(ROOT / "data" / "scenarios" / "line9_power_topology.json")
        for feeder_id, feeder in list(network.feeders.items()):
            network.feeders[feeder_id] = replace(
                feeder,
                continuous_current_a=1.0,
                short_time_current_a=2.0,
            )
        service = PowerService(
            [
                PowerSection("PWR-09-UP", "Up", max_traction_power_kw=20_000.0, warning_power_kw=15_000.0),
                PowerSection("PWR-09-DOWN", "Down", max_traction_power_kw=20_000.0, warning_power_kw=15_000.0),
            ],
            network=network,
        )
        request = TrainPowerRequest(
            "T1",
            "PWR-09-UP",
            speed_mps=18.0,
            traction_force_n=95_000.0,
            position_m=2_400.0,
            direction="UP",
            aux_power_kw=60.0,
        )
        for tick in range(9):
            service.update([request], dt_sec=0.25, sim_time_ms=tick * 250)
        self.assertTrue(any(item.status == "OPEN" for item in network.feeders.values()))

    def test_contact_rail_protection_trip_is_effective_in_same_tick(self) -> None:
        network = load_line9_power_network(ROOT / "data" / "scenarios" / "line9_power_topology.json")
        target_id = "CR-09-02-UP"
        network.contact_sections[target_id] = replace(network.contact_sections[target_id], current_limit_a=1.0)
        service = PowerService(
            [
                PowerSection("PWR-09-UP", "Up", max_traction_power_kw=20_000.0, warning_power_kw=15_000.0),
                PowerSection("PWR-09-DOWN", "Down", max_traction_power_kw=20_000.0, warning_power_kw=15_000.0),
            ],
            network=network,
        )
        request = TrainPowerRequest(
            "T1", "PWR-09-UP", speed_mps=18.0, traction_force_n=95_000.0,
            position_m=2_400.0, direction="UP", aux_power_kw=60.0,
        )

        for tick in range(7):
            service.update([request], dt_sec=0.25, sim_time_ms=tick * 250)

        state = service.update([request], dt_sec=0.25, sim_time_ms=1_750)
        self.assertEqual(network.contact_sections[target_id].status, "DEENERGIZED")
        assert service.last_network_snapshot is not None
        train = service.last_network_snapshot.trains[0]
        self.assertEqual(train.traction_limit_ratio, 0.0)
        self.assertEqual(state["PWR-09-UP"].traction_limit_ratio, 0.0)
        self.assertTrue(any(item["type"] == "CONTACT_RAIL_PROTECTION_TRIP" for item in service.last_network_snapshot.alerts))

    def test_invalid_solution_preserves_last_valid_snapshot(self) -> None:
        network = load_line9_power_network(ROOT / "data" / "scenarios" / "line9_power_topology.json")
        service = PowerService(
            [PowerSection("PWR-09-UP", "Up", max_traction_power_kw=20_000.0, warning_power_kw=15_000.0)],
            network=network,
        )
        request = TrainPowerRequest(
            "T1", "PWR-09-UP", speed_mps=18.0, traction_force_n=95_000.0,
            position_m=2_400.0, direction="UP", aux_power_kw=60.0,
        )
        service.update([request], dt_sec=0.25, sim_time_ms=0)
        valid = service.last_network_snapshot
        assert service.solver is not None
        original_solve = service.solver.solve

        def failed_solve(*args, **kwargs):
            return replace(original_solve(*args, **kwargs), converged=False, power_balance_error_ratio=0.02)

        service.solver.solve = failed_solve  # type: ignore[method-assign]
        service.update([request], dt_sec=0.25, sim_time_ms=250)

        self.assertIs(service.last_network_snapshot, valid)
        self.assertIsNotNone(service.last_failed_network_snapshot)
        self.assertEqual(
            service.last_solver_failure["reasons"],
            ["NOT_CONVERGED", "POWER_BALANCE_EXCEEDED"],
        )


if __name__ == "__main__":
    unittest.main()
