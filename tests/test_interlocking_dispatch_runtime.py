from __future__ import annotations

import math
import unittest
from dataclasses import dataclass

from app.core.engine import DWELLING, SimulationEngine
from app.domain.dispatch.runtime import DispatchRuntimeCoordinator
from app.domain.dispatch.services import RuleBasedDispatchService
from app.domain.interlocking.runtime import InterlockingRuntimeCoordinator


SCENARIO = "data/scenarios/line9_interactive.json"
LINE_MAP = "data/cache/line_map.json"
STATIONS = "data/line9/stations.csv"


def make_engine() -> SimulationEngine:
    engine = SimulationEngine.load_from_files(SCENARIO, LINE_MAP, STATIONS)
    engine.load()
    return engine


@dataclass
class FakeTrain:
    train_id: str
    station_index: int = 0
    current_station_code: str = "GGZ"
    direction: str = "UP"
    phase: str = DWELLING


class DispatchRuntimeTests(unittest.TestCase):
    def test_real_phase_transition_records_departure_and_headway(self) -> None:
        service = RuleBasedDispatchService()
        runtime = DispatchRuntimeCoordinator(service)
        first = FakeTrain("T1")
        second = FakeTrain("T2")
        runtime.register_train(first)
        runtime.register_train(second)

        first.phase = "DEPARTING"
        records = runtime.observe([first, second], 100.0)
        self.assertEqual([item.train_id for item in records], ["T1"])

        front, rear = runtime.headways_for("T2", 0, "UP", 145.0)
        self.assertEqual(front, 45.0)
        self.assertIsNone(rear)


class InterlockingRuntimeTests(unittest.TestCase):
    def test_every_mainline_interval_has_route_or_cbtc_section_authority(self) -> None:
        engine = make_engine()
        runtime = InterlockingRuntimeCoordinator(
            engine.line_map,
            engine.track_query,
            engine.line_scope.segment_ids if engine.line_scope else None,
        )
        for station_index in range(len(engine._station_list) - 1):
            for origin, destination in (
                (station_index, station_index + 1),
                (station_index + 1, station_index),
            ):
                path = engine._path_plan_for_station_pair(origin, destination)
                self.assertIsNotNone(path)
                self.assertTrue(engine.route_chain_planner.plan_between_platform_sets(engine._station_platform_ids[origin], engine._station_platform_ids[destination], "forward" if destination > origin else "backward").route_ids)

    def test_two_train_departure_is_serialized_by_real_interval_occupation(self) -> None:
        engine = make_engine()
        self.assertTrue(engine.add_train({
            "trainId": "T1", "initialStationCode": "GGZ", "direction": "UP"
        })["ok"])
        self.assertTrue(engine.add_train({
            "trainId": "T2", "initialStationCode": "GGZ", "direction": "UP"
        })["ok"])
        engine.clock.start()

        # Advance beyond the 30 s dwell and door transitions regardless of
        # the scenario's configured tick size.
        for _ in range(math.ceil(40.0 / engine.clock.tick_seconds)):
            engine._tick()

        first, second = engine.snapshot().trains
        self.assertNotEqual(first["phase"], DWELLING)
        self.assertTrue(first["departureAuthorized"])
        self.assertEqual(second["phase"], DWELLING)
        self.assertFalse(second["departureAuthorized"])
        self.assertEqual(second["interlockingHoldReason"], "INTERVAL_RESERVED")
        self.assertEqual(second["lastDispatchAction"], "HOLD")
        self.assertEqual(second["lastDispatchReason"], "HEADWAY_TOO_SHORT")
        self.assertEqual(engine.snapshot().dispatch_runtime["departureCount"], 1)

    def test_red_start_signal_revokes_departure_authority(self) -> None:
        engine = make_engine()
        runtime = InterlockingRuntimeCoordinator(
            engine.line_map,
            engine.track_query,
            engine.line_scope.segment_ids if engine.line_scope else None,
        )
        path = engine._path_plan_for_station_pair(0, 1)
        route_ids = runtime.routes_for_path(path)
        self.assertTrue(route_ids)
        first_route = runtime.catalog.get(route_ids[0])
        runtime.signal_resolver.set_fault(str(first_route.start_signal_id))
        runtime.update([])

        authority = runtime.request_departure("T-RED", path)

        self.assertFalse(authority.granted)
        self.assertEqual(authority.failure_reason, "SIGNAL_AT_STOP")
        self.assertEqual(runtime.snapshot()["lockedRouteCount"], 0)
        self.assertEqual(runtime.snapshot()["reservedIntervalCount"], 0)

    def test_completed_interval_releases_terminal_overlap_authority(self) -> None:
        engine = make_engine()
        runtime = engine.interlocking_runtime
        path = engine._path_plan_for_station_pair(0, 1)
        self.assertIsNotNone(path)

        authority = runtime.request_departure("T-ARRIVED", path)
        self.assertTrue(authority.granted)
        self.assertGreater(runtime.snapshot()["lockedRouteCount"], 0)
        self.assertEqual(runtime.snapshot()["reservedIntervalCount"], 1)

        runtime.complete_interval("T-ARRIVED")

        self.assertEqual(runtime.snapshot()["lockedRouteCount"], 0)
        self.assertEqual(runtime.snapshot()["reservedIntervalCount"], 0)
        self.assertNotIn(
            "T-ARRIVED",
            {item["trainId"] for item in runtime.snapshot()["departureAuthorities"]},
        )

    def test_interlocking_wait_closes_doors_without_restarting_dwell(self) -> None:
        engine = make_engine()
        self.assertTrue(engine.add_train({
            "trainId": "T-WAIT", "initialStationCode": "GGZ", "direction": "UP"
        })["ok"])
        path = engine._path_plan_for_station_pair(0, 1)
        self.assertIsNotNone(path)
        self.assertTrue(engine.interlocking_runtime.request_departure("T-BLOCK", path).granted)
        engine.clock.start()

        for _ in range(math.ceil(35.0 / engine.clock.tick_seconds)):
            engine._tick()

        waiting = engine.trains[0]
        self.assertEqual(waiting.phase, DWELLING)
        self.assertFalse(waiting.departure_authorized)
        self.assertEqual(waiting.interlocking_hold_reason, "INTERVAL_RESERVED")
        self.assertEqual(waiting.dwell_remaining_sec, 0.0)
        self.assertEqual(waiting.door_state, "CLOSED")
        self.assertEqual(waiting.door_transition_remaining_sec, 0.0)

    def test_tail_clear_releases_route_and_following_train_eventually_departs(self) -> None:
        engine = make_engine()
        for train_id in ("T1", "T2"):
            engine.add_train({
                "trainId": train_id,
                "initialStationCode": "GGZ",
                "direction": "UP",
            })
        engine.clock.start()

        peak_locked_route_count = 0
        saw_route_released_while_interval_reserved = False
        for _ in range(1600):
            engine._tick()
            interlocking = engine.snapshot().interlocking
            locked_route_count = interlocking["lockedRouteCount"]
            peak_locked_route_count = max(peak_locked_route_count, locked_route_count)
            if (
                peak_locked_route_count > 0
                and locked_route_count < peak_locked_route_count
                and interlocking["reservedIntervalCount"] > 0
            ):
                saw_route_released_while_interval_reserved = True
            if engine.snapshot().dispatch_runtime["departureCount"] >= 2:
                break

        departures = engine.snapshot().dispatch_runtime["recentDepartures"]
        self.assertGreater(peak_locked_route_count, 0)
        self.assertTrue(saw_route_released_while_interval_reserved)
        self.assertGreaterEqual(len(departures), 2)
        self.assertGreaterEqual(departures[1]["simTimeS"] - departures[0]["simTimeS"], 90.0)

    def test_snapshot_exposes_runtime_ci_and_ats_contracts(self) -> None:
        engine = make_engine()
        engine.add_train({"trainId": "T1", "initialStationCode": "GGZ", "direction": "UP"})
        engine.clock.start()
        for _ in range(30):
            engine._tick()
        snapshot = engine.snapshot()

        self.assertEqual(snapshot.interlocking["mode"], "MAINLINE_RUNTIME")
        self.assertGreater(snapshot.interlocking["routeCount"], 0)
        self.assertIn("sections", snapshot.interlocking)
        self.assertIn("signals", snapshot.interlocking)
        self.assertEqual(snapshot.dispatch_runtime["registeredTrainCount"], 1)
        self.assertIn("departureAuthorized", snapshot.trains[0])


if __name__ == "__main__":
    unittest.main()
