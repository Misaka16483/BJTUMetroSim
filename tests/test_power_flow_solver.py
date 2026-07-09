from __future__ import annotations

import unittest
from pathlib import Path

from app.domain.power.flow_solver import DCTractionPowerFlowSolver
from app.domain.power.line9_topology import load_line9_power_network
from app.domain.power.network_models import TrainElectricalLoad


ROOT = Path(__file__).resolve().parents[1]


def _network():
    return load_line9_power_network(ROOT / "data" / "scenarios" / "line9_power_topology.json")


class DCTractionPowerFlowSolverTests(unittest.TestCase):
    def test_single_train_midpoint_bilateral_supply_is_balanced(self) -> None:
        network = _network()
        solver = DCTractionPowerFlowSolver(network)

        snapshot = solver.solve(
            [
                TrainElectricalLoad(
                    train_id="T1",
                    direction="UP",
                    mileage_m=(1660.52 + 3429.32) / 2,
                    speed_mps=18.0,
                    traction_force_n=95_000.0,
                    aux_power_kw=60.0,
                )
            ],
            dt_sec=1.0,
        )

        train = snapshot.trains[0]
        self.assertGreater(train.voltage_v, 650.0)
        self.assertEqual(train.traction_limit_ratio, 1.0)
        left = next(item for item in snapshot.substations if item.substation_id == "TS-0902")
        right = next(item for item in snapshot.substations if item.substation_id == "TS-0903")
        self.assertAlmostEqual(left.current_a, right.current_a, delta=300.0)

    def test_single_train_near_one_side_loads_nearer_substation_more(self) -> None:
        network = _network()
        solver = DCTractionPowerFlowSolver(network)

        snapshot = solver.solve(
            [
                TrainElectricalLoad(
                    train_id="T1",
                    direction="UP",
                    mileage_m=1800.0,
                    speed_mps=18.0,
                    traction_force_n=95_000.0,
                    aux_power_kw=60.0,
                )
            ],
            dt_sec=1.0,
        )

        left = next(item for item in snapshot.substations if item.substation_id == "TS-0902")
        right = next(item for item in snapshot.substations if item.substation_id == "TS-0903")
        self.assertGreater(left.current_a, right.current_a)

    def test_multiple_trains_raise_load_and_create_warning(self) -> None:
        network = _network()
        solver = DCTractionPowerFlowSolver(network)

        snapshot = solver.solve(
            [
                TrainElectricalLoad(f"T{i}", "UP", 2_200.0 + i * 160.0, 20.0, traction_force_n=145_000.0, aux_power_kw=150.0)
                for i in range(4)
            ],
            dt_sec=1.0,
        )

        self.assertLess(min(train.voltage_v for train in snapshot.trains), 750.0)
        self.assertTrue(any(item["type"] in {"SUBSTATION_WARNING", "FEEDER_WARNING", "LIMITED"} for item in snapshot.alerts))

    def test_regen_can_be_absorbed_by_traction_train(self) -> None:
        network = _network()
        solver = DCTractionPowerFlowSolver(network)

        snapshot = solver.solve(
            [
                TrainElectricalLoad("T1", "UP", 2200.0, 18.0, traction_force_n=95_000.0, aux_power_kw=60.0),
                TrainElectricalLoad("T2", "UP", 2600.0, 18.0, brake_force_n=80_000.0, aux_power_kw=60.0),
            ],
            dt_sec=1.0,
        )

        self.assertGreater(snapshot.generated_regen_kw, 0.0)
        self.assertGreater(snapshot.absorbed_regen_kw, 0.0)
        self.assertLess(snapshot.wasted_regen_kw, snapshot.generated_regen_kw)

    def test_regen_without_absorber_is_limited_and_wasted(self) -> None:
        network = _network()
        solver = DCTractionPowerFlowSolver(network)

        snapshot = solver.solve(
            [
                TrainElectricalLoad("T1", "UP", 6_900.0, 22.0, brake_force_n=180_000.0, aux_power_kw=20.0),
                TrainElectricalLoad("T2", "UP", 7_300.0, 22.0, brake_force_n=180_000.0, aux_power_kw=20.0),
            ],
            dt_sec=1.0,
        )

        self.assertGreater(snapshot.wasted_regen_kw, 0.0)
        self.assertTrue(any(train.regen_limit_ratio < 1.0 for train in snapshot.trains))
        self.assertTrue(any(item["type"] in {"REGEN_LIMITED", "OVERVOLTAGE", "REGEN_WASTED"} for item in snapshot.alerts))

    def test_n_minus_one_outage_generates_outage_alert(self) -> None:
        network = _network()
        network.apply_substation_outage("TS-0905")
        solver = DCTractionPowerFlowSolver(network)

        snapshot = solver.solve(
            [
                TrainElectricalLoad(f"T{i}", "UP", 6_100.0 + i * 180.0, 20.0, traction_force_n=140_000.0, aux_power_kw=120.0)
                for i in range(3)
            ],
            dt_sec=1.0,
        )

        self.assertTrue(any(item["type"] == "SUBSTATION_OUTAGE" and item["targetId"] == "TS-0905" for item in snapshot.alerts))


if __name__ == "__main__":
    unittest.main()
