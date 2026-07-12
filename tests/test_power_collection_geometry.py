from __future__ import annotations

import unittest
from pathlib import Path

from app.core.engine import SimulationEngine
from app.domain.power.flow_solver import DCTractionPowerFlowSolver
from app.domain.power.line9_topology import load_line9_power_network
from app.domain.power.network_models import TrainElectricalLoad
from app.domain.vehicle.models import VehicleConfig


ROOT = Path(__file__).resolve().parents[1]
TOPOLOGY = ROOT / "data" / "scenarios" / "line9_power_topology.json"


class PowerCollectionGeometryTests(unittest.TestCase):
    def test_vehicle_config_has_two_collection_points_inside_train(self) -> None:
        config = VehicleConfig()

        self.assertEqual(config.train_length_m, 118.0)
        self.assertEqual(len(config.pantograph_offsets_from_head_m), 2)
        self.assertTrue(all(0.0 < value < config.train_length_m for value in config.pantograph_offsets_from_head_m))

    def test_engine_exports_directional_head_tail_and_collection_points(self) -> None:
        engine = SimulationEngine.load_from_files(
            scenario_path=ROOT / "data" / "scenarios" / "line9_5train_power.json",
            line_map_path=ROOT / "data" / "cache" / "line_map.json",
            stations_csv_path=ROOT / "MetroDynamicsJavaDemo" / "data" / "stations.csv",
        )
        engine.load()
        snapshot = engine.snapshot()
        assert snapshot is not None

        up = next(item for item in snapshot.trains if item["direction"] == "UP" and item["trainId"] == "T0902")
        down = next(item for item in snapshot.trains if item["direction"] == "DOWN" and item["trainId"] == "T0904")
        self.assertAlmostEqual(up["headMileageM"] - up["tailMileageM"], 118.0, places=3)
        self.assertAlmostEqual(down["tailMileageM"] - down["headMileageM"], 118.0, places=3)
        self.assertEqual(len(up["pantographMileagesM"]), 2)
        self.assertTrue(up["spannedPowerSectionIds"])

    def test_train_spanning_boundary_reports_both_supply_sections(self) -> None:
        network = load_line9_power_network(TOPOLOGY)
        sections = network.sections_spanned(1_700.0, 1_582.0, "UP")

        self.assertEqual(
            {item.section_id for item in sections},
            {"PWR-0901-0902-UP", "PWR-0902-0903-UP"},
        )

    def test_collection_points_on_both_sides_of_boundary_create_three_source_paths(self) -> None:
        network = load_line9_power_network(TOPOLOGY)
        solver = DCTractionPowerFlowSolver(network)
        snapshot = solver.solve(
            [
                TrainElectricalLoad(
                    "T-SPAN",
                    "UP",
                    1_641.0,
                    18.0,
                    traction_force_n=95_000.0,
                    aux_power_kw=60.0,
                    head_mileage_m=1_700.0,
                    tail_mileage_m=1_582.0,
                    pantograph_mileages_m=(1_670.5, 1_611.5),
                )
            ],
            dt_sec=0.25,
        )

        train = snapshot.trains[0]
        active_substations = {
            item.substation_id
            for item in snapshot.substations
            if item.current_a > 0.1
        }
        self.assertEqual(active_substations, {"TS-0901", "TS-0902", "TS-0903"})
        self.assertEqual(
            set(train.spanned_power_section_ids),
            {"PWR-0901-0902-UP", "PWR-0902-0903-UP"},
        )
        self.assertEqual(train.pantograph_mileages_m, (1_670.5, 1_611.5))


if __name__ == "__main__":
    unittest.main()
