from __future__ import annotations

import unittest
from pathlib import Path

from app.core.engine import SimulationEngine
from app.domain.interlocking.models import RouteRequest
from app.domain.signal.models import TrainState as InterlockingTrainState


ROOT = Path(__file__).resolve().parents[1]
LINE_MAP = ROOT / "data" / "cache" / "line_map.json"
STATIONS_CSV = ROOT / "data" / "line9" / "stations.csv"
INTERACTIVE_SCENARIO = ROOT / "data" / "scenarios" / "line9_interactive.json"
POWER_SCENARIO = ROOT / "data" / "scenarios" / "line9_5train_power.json"
SINGLE_SCENARIO = ROOT / "data" / "scenarios" / "line9_single.json"


def load_engine(scenario_path: Path = INTERACTIVE_SCENARIO) -> SimulationEngine:
    return SimulationEngine.load_from_files(
        scenario_path=scenario_path,
        line_map_path=LINE_MAP,
        stations_csv_path=STATIONS_CSV,
    )


class EngineStateContractTests(unittest.TestCase):
    def test_start_is_idempotent_while_running(self) -> None:
        engine = load_engine(SINGLE_SCENARIO)
        self.assertEqual(engine.start(), "STARTED")
        self.assertEqual(engine.snapshot().clock_state, "RUNNING")
        self.assertEqual(engine.start(), "ALREADY_RUNNING")
        self.assertEqual(engine.snapshot().clock_state, "RUNNING")
        engine.stop()

    def test_stop_clears_runtime_and_allows_new_roster_for_restart(self) -> None:
        engine = load_engine(SINGLE_SCENARIO)
        engine.remove_train("T0901")
        self.assertTrue(engine.add_train({"trainId": "UP-1", "initialStationCode": "GGZ", "direction": "UP"})["ok"])
        engine.start()
        engine.stop()
        stopped = engine.snapshot()
        self.assertEqual(stopped.clock_state, "STOPPED")
        self.assertEqual(stopped.tick, 0)
        self.assertEqual(stopped.trains, [])
        self.assertTrue(engine.add_train({"trainId": "DOWN-1", "initialStationCode": "GTG", "direction": "DOWN"})["ok"])
        engine.start()
        self.assertEqual({train["trainId"] for train in engine.snapshot().trains}, {"DOWN-1"})
        engine.stop()

    def test_add_train_validates_station_code_direction_and_terminus(self) -> None:
        engine = load_engine(SINGLE_SCENARIO)
        engine.load()
        invalid_station = engine.add_train({
            "trainId": "BAD-STATION", "initialStationCode": "NO_SUCH_STATION", "direction": "UP",
        })
        self.assertEqual(invalid_station["error"], "INVALID_INITIAL_STATION")

        invalid_direction = engine.add_train({
            "trainId": "BAD-DIRECTION", "initialStationCode": "GGZ", "direction": "SIDEWAYS",
        })
        self.assertEqual(invalid_direction["error"], "INVALID_DIRECTION")

        up_terminal = engine.add_train({
            "trainId": "BAD-UP-END", "initialStationCode": "GTG", "direction": "UP",
        })
        self.assertEqual(up_terminal["error"], "INITIAL_STATION_HAS_NO_FORWARD_ROUTE")

        down_terminal = engine.add_train({
            "trainId": "BAD-DOWN-END", "initialStationCode": "GGZ", "direction": "DOWN",
        })
        self.assertEqual(down_terminal["error"], "INITIAL_STATION_HAS_NO_FORWARD_ROUTE")

        valid = engine.add_train({
            "trainId": "T-VALID", "initialStationCode": "GGZ", "direction": "UP",
        })
        self.assertTrue(valid["ok"])
        self.assertEqual(valid["train"]["currentStationCode"], "GGZ")
        self.assertEqual(valid["train"]["nextStationCode"], "FSP")

        middle_up = engine.add_train({
            "trainId": "T-MIDDLE-UP", "initialStationCode": "KYL", "direction": "UP",
        })
        middle_down = engine.add_train({
            "trainId": "T-MIDDLE-DOWN", "initialStationCode": "KYL", "direction": "DOWN",
        })
        self.assertTrue(middle_up["ok"])
        self.assertEqual(middle_up["train"]["nextStationCode"], "FTN")
        self.assertTrue(middle_down["ok"])
        self.assertEqual(middle_down["train"]["nextStationCode"], "FSP")

        engine.trains[0].station_index = len(engine._station_list) - 1
        engine.trains[0].current_station_code = "GTG"
        engine.trains[0].current_station_name = "国家图书馆"
        engine.trains[0].direction = "UP"
        engine._turn_train_at_terminal(engine.trains[0])
        self.assertEqual(engine.trains[0].direction, "UP")
        self.assertEqual(engine.trains[0].turnback_state, "RUNNING")
        self.assertEqual(engine.trains[0].active_route_ids, ("90",))
        self.assertEqual(engine.trains[0].phase, "DWELLING")

    def test_interactive_scenario_starts_empty_without_external_server(self) -> None:
        engine = load_engine(INTERACTIVE_SCENARIO)
        engine.load()
        engine.clock.start()
        for _tick in range(24):
            engine._tick()
        snapshot = engine.snapshot()
        assert snapshot is not None
        self.assertEqual(snapshot.clock_state, "RUNNING")
        self.assertEqual(snapshot.trains, [])
        self.assertIn("solver", snapshot.power_network)

    def test_power_scenario_starts_with_configured_full_line_trains(self) -> None:
        engine = load_engine(POWER_SCENARIO)
        engine.load()
        snapshot = engine.snapshot()
        assert snapshot is not None
        self.assertEqual(
            {train["trainId"] for train in snapshot.trains},
            {"T0901", "T0902", "T0903", "T0904", "T0905"},
        )

    def test_added_train_uses_route_table_plan_instead_of_shortest_path_fallback(self) -> None:
        engine = load_engine(INTERACTIVE_SCENARIO)
        engine.load()
        self.assertTrue(engine.add_train({
            "trainId": "T-ROUTE", "initialStationCode": "GGZ",
            "initialSegmentId": 13, "direction": "UP",
        })["ok"])
        engine.clock.start()
        for _tick in range(180):
            engine._tick()

        snapshot = engine.snapshot()
        assert snapshot is not None
        train = next(item for item in snapshot.trains if item["trainId"] == "T-ROUTE")
        self.assertTrue(train["routeChainIds"])
        self.assertGreater(train["pathSegmentCount"], 0)
        self.assertNotEqual(train["phase"], "WAITING_ROUTE")

    def test_add_train_rejects_an_overlapping_platform_placement(self) -> None:
        engine = load_engine(INTERACTIVE_SCENARIO)
        engine.load()
        self.assertTrue(engine.add_train({
            "trainId": "T-OWNER", "initialStationCode": "GGZ",
            "initialSegmentId": 13, "direction": "UP",
        })["ok"])

        rejected = engine.add_train({
            "trainId": "T-WAITER", "initialStationCode": "GGZ",
            "initialSegmentId": 13, "direction": "UP",
        })

        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["error"], "INITIAL_PLACEMENT_OCCUPIED")
        self.assertEqual(rejected["conflictingTrainIds"], ["T-OWNER"])
        self.assertEqual([train.train_id for train in engine.trains], ["T-OWNER"])

    def test_add_train_rejects_a_platform_inside_another_locked_route(self) -> None:
        engine = load_engine(INTERACTIVE_SCENARIO)
        engine.load()
        self.assertTrue(engine.route_service.request(
            RouteRequest("TEST-LOCK", "9", "T-RESERVED")
        ).accepted)

        rejected = engine.add_train({
            "trainId": "T-NEW", "initialStationCode": "GGZ",
            "initialSegmentId": 13, "direction": "UP",
        })

        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["error"], "INITIAL_PLACEMENT_ROUTE_LOCKED")
        self.assertEqual(rejected["conflictingRouteIds"], ["9"])

    def test_route_protection_uses_mapped_axle_section_not_same_numbered_section(self) -> None:
        engine = load_engine(INTERACTIVE_SCENARIO)
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
        engine = load_engine(INTERACTIVE_SCENARIO)
        engine.load()
        self.assertTrue(engine.add_train({
            "trainId": "T-SECTIONAL", "initialStationCode": "GGZ",
            "initialSegmentId": 13, "direction": "UP",
        })["ok"])
        engine.clock.start()
        for _tick in range(700):
            engine._tick()
            if engine.trains[0].current_segment_id == 50:
                break

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

    def test_terminal_turnback_runs_through_reversing_routes_to_other_platform(self) -> None:
        engine = load_engine(INTERACTIVE_SCENARIO)
        engine.load()
        self.assertTrue(engine.add_train({
            "trainId": "T-TURNBACK", "initialStationCode": "KYL",
            "initialSegmentId": 55, "direction": "DOWN", "operationMode": "ATO",
        })["ok"])
        engine.clock.start()
        for _tick in range(3000):
            engine._tick()
            if engine.trains[0].turnback_count == 1:
                break
        train = engine.trains[0]
        self.assertEqual(train.turnback_count, 1)
        self.assertEqual(train.current_station_code, "GGZ")
        self.assertEqual(train.current_platform_id, 2)
        self.assertEqual(train.current_segment_id, 39)
        self.assertEqual(train.direction, "UP")
        self.assertEqual(train.next_station_code, "FSP")
        self.assertTrue(any(
            decision["action"] == "TURNBACK"
            for decision in engine.snapshot().dispatch_decisions
        ))


if __name__ == "__main__":
    unittest.main()
