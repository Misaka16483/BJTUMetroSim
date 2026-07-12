from __future__ import annotations

import unittest
from pathlib import Path

from app.core.engine import SimulationEngine


ROOT = Path(__file__).resolve().parents[1]


class EngineStateContractTests(unittest.TestCase):
    def test_engine_start_and_state_contract_without_external_server(self) -> None:
        engine = SimulationEngine.load_from_files(
            scenario_path=ROOT / "data" / "scenarios" / "line9_5train_power.json",
            line_map_path=ROOT / "data" / "cache" / "line_map.json",
            stations_csv_path=ROOT / "MetroDynamicsJavaDemo" / "data" / "stations.csv",
        )
        engine.load()
        engine.clock.start()
        for _tick in range(24):
            engine._tick()
        snapshot = engine.snapshot()
        assert snapshot is not None
        self.assertEqual(snapshot.clock_state, "RUNNING")
        # Dynamic train management deliberately starts with no pre-created train.
        self.assertEqual(snapshot.trains, [])
        self.assertIn("solver", snapshot.power_network)

    def test_added_train_uses_route_table_plan_instead_of_shortest_path_fallback(self) -> None:
        engine = SimulationEngine.load_from_files(
            scenario_path=ROOT / "data" / "scenarios" / "line9_5train_power.json",
            line_map_path=ROOT / "data" / "cache" / "line_map.json",
            stations_csv_path=ROOT / "MetroDynamicsJavaDemo" / "data" / "stations.csv",
        )
        engine.load()
        self.assertTrue(engine.add_train({
            "trainId": "T-ROUTE", "initialStationCode": "GGZ", "direction": "UP",
        })["ok"])
        engine.clock.start()
        for _tick in range(22):
            engine._tick()

        snapshot = engine.snapshot()
        assert snapshot is not None
        train = next(item for item in snapshot.trains if item["trainId"] == "T-ROUTE")
        self.assertTrue(train["routeChainIds"])
        self.assertGreater(train["pathSegmentCount"], 0)
        self.assertNotEqual(train["phase"], "WAITING_ROUTE")

    def test_conflicting_route_is_held_for_retry_not_replaced_by_topology_fallback(self) -> None:
        engine = SimulationEngine.load_from_files(
            scenario_path=ROOT / "data" / "scenarios" / "line9_5train_power.json",
            line_map_path=ROOT / "data" / "cache" / "line_map.json",
            stations_csv_path=ROOT / "MetroDynamicsJavaDemo" / "data" / "stations.csv",
        )
        engine.load()
        for train_id in ("T-OWNER", "T-WAITER"):
            self.assertTrue(engine.add_train({
                "trainId": train_id, "initialStationCode": "GGZ", "direction": "UP",
            })["ok"])
        engine.clock.start()
        for _tick in range(22):
            engine._tick()

        snapshot = engine.snapshot()
        assert snapshot is not None
        waiter = next(item for item in snapshot.trains if item["trainId"] == "T-WAITER")
        self.assertEqual(waiter["phase"], "WAITING_ROUTE")
        self.assertEqual(waiter["routeFailureReason"], "CONFLICT_ROUTE_LOCKED")
        self.assertGreater(waiter["routeRetryAtMs"], snapshot.sim_time_ms)


    def test_route_release_keeps_unentered_future_sections_locked_for_ma(self) -> None:
        engine = SimulationEngine.load_from_files(
            scenario_path=ROOT / "data" / "scenarios" / "line9_5train_power.json",
            line_map_path=ROOT / "data" / "cache" / "line_map.json",
            stations_csv_path=ROOT / "MetroDynamicsJavaDemo" / "data" / "stations.csv",
        )
        engine.load()
        self.assertTrue(engine.add_train({
            "trainId": "T-SECTIONAL", "initialStationCode": "GGZ", "direction": "UP",
        })["ok"])
        engine.clock.start()
        for _tick in range(220):
            engine._tick()

        snapshot = engine.snapshot()
        assert snapshot is not None
        train = next(item for item in snapshot.trains if item["trainId"] == "T-SECTIONAL")
        self.assertEqual(train["currentSegmentId"], 50)
        self.assertEqual(train["movementAuthorityReason"], "STATION_STOP")
        route_28 = next(
            item for item in snapshot.interlocking["routes"]
            if item["routeId"] == "28" and item["trainId"] == "T-SECTIONAL"
        )
        self.assertIn("40", route_28["lockedSections"])

if __name__ == "__main__":
    unittest.main()
