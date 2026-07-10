from __future__ import annotations

import unittest
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


if __name__ == "__main__":
    unittest.main()
