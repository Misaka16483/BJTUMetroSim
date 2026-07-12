from __future__ import annotations

import unittest
from pathlib import Path

from app.core.engine import SimulationEngine
from app.domain.interlocking.models import RouteRequest
from app.domain.signal.models import TrainState as InterlockingTrainState


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

    def test_add_train_rejects_an_overlapping_platform_placement(self) -> None:
        engine = SimulationEngine.load_from_files(
            scenario_path=ROOT / "data" / "scenarios" / "line9_5train_power.json",
            line_map_path=ROOT / "data" / "cache" / "line_map.json",
            stations_csv_path=ROOT / "MetroDynamicsJavaDemo" / "data" / "stations.csv",
        )
        engine.load()
        self.assertTrue(engine.add_train({
            "trainId": "T-OWNER", "initialStationCode": "GGZ", "direction": "UP",
        })["ok"])

        rejected = engine.add_train({
            "trainId": "T-WAITER", "initialStationCode": "GGZ", "direction": "UP",
        })

        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["error"], "INITIAL_PLACEMENT_OCCUPIED")
        self.assertEqual(rejected["conflictingTrainIds"], ["T-OWNER"])
        self.assertEqual([train.train_id for train in engine.trains], ["T-OWNER"])

    def test_add_train_rejects_a_platform_inside_another_locked_route(self) -> None:
        engine = SimulationEngine.load_from_files(
            scenario_path=ROOT / "data" / "scenarios" / "line9_5train_power.json",
            line_map_path=ROOT / "data" / "cache" / "line_map.json",
            stations_csv_path=ROOT / "MetroDynamicsJavaDemo" / "data" / "stations.csv",
        )
        engine.load()
        self.assertTrue(engine.route_service.request(
            RouteRequest("TEST-LOCK", "11", "T-RESERVED")
        ).accepted)

        rejected = engine.add_train({
            "trainId": "T-NEW", "initialStationCode": "GGZ",
            "initialSegmentId": 13, "direction": "UP",
        })

        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["error"], "INITIAL_PLACEMENT_ROUTE_LOCKED")
        self.assertEqual(rejected["conflictingRouteIds"], ["11"])

    def test_route_protection_uses_mapped_axle_section_not_same_numbered_section(self) -> None:
        engine = SimulationEngine.load_from_files(
            scenario_path=ROOT / "data" / "scenarios" / "line9_5train_power.json",
            line_map_path=ROOT / "data" / "cache" / "line_map.json",
            stations_csv_path=ROOT / "MetroDynamicsJavaDemo" / "data" / "stations.csv",
        )
        engine.load()

        # Route 36 protects protection-section 18. The source table maps that
        # protection section to JZ61/S73, not to same-numbered JZ18/S235.
        remote_train = InterlockingTrainState(
            train_id="T-REMOTE", seg_id=235, offset_m=50.0, length_m=20.0,
        )
        engine.section_occupation.update([remote_train], engine.track_query)
        self.assertTrue(engine.interlocking_rules.check("36", "T-LOCAL").ok)

        protected_train = InterlockingTrainState(
            train_id="T-PROTECTED", seg_id=73, offset_m=50.0, length_m=20.0,
        )
        engine.section_occupation.update([protected_train], engine.track_query)
        result = engine.interlocking_rules.check("36", "T-LOCAL")
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "SECTION_OCCUPIED")
        self.assertEqual(result.failed_section_id, "61")


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

    def test_terminal_turnback_reverses_on_same_platform(self) -> None:
        engine = SimulationEngine.load_from_files(
            scenario_path=ROOT / "data" / "scenarios" / "line9_5train_power.json",
            line_map_path=ROOT / "data" / "cache" / "line_map.json",
            stations_csv_path=ROOT / "MetroDynamicsJavaDemo" / "data" / "stations.csv",
        )
        engine.load()
        self.assertTrue(engine.add_train({
            "trainId": "T-TURNBACK", "initialStationCode": "KYL",
            "initialSegmentId": 55, "direction": "DOWN", "operationMode": "ATO",
        })["ok"])
        engine.clock.start()
        for _tick in range(1300):
            engine._tick()
            if engine.trains[0].turnback_count == 1:
                break
        train = engine.trains[0]
        self.assertEqual(train.turnback_count, 1)
        self.assertEqual(train.current_station_code, "GGZ")
        self.assertEqual(train.current_segment_id, 13)
        self.assertEqual(train.direction, "UP")
        self.assertEqual(train.next_station_code, "FSP")
        self.assertTrue(any(
            decision["action"] == "TURNBACK"
            for decision in engine.snapshot().dispatch_decisions
        ))
if __name__ == "__main__":
    unittest.main()
