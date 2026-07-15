from __future__ import annotations

import math
import unittest
from dataclasses import dataclass, replace
from unittest.mock import patch

from app.core.engine import DWELLING, SimulationEngine
from app.domain.dispatch.runtime import DispatchRuntimeCoordinator
from app.domain.dispatch.services import RuleBasedDispatchService
from app.domain.interlocking.models import RouteRequest
from app.domain.interlocking.runtime import InterlockingRuntimeCoordinator


SCENARIO = "data/scenarios/line9_interactive.json"
LINE_MAP = "data/cache/line_map.json"
STATIONS = "data/line9/stations.csv"


def make_engine() -> SimulationEngine:
    engine = SimulationEngine.load_from_files(SCENARIO, LINE_MAP, STATIONS)
    engine._ato_config = replace(
        engine._ato_config,
        use_dynamic_programming_profile=False,
    )
    engine.load()
    return engine


@dataclass
class FakeTrain:
    train_id: str
    station_index: int = 0
    current_station_code: str = "GGZ"
    direction: str = "UP"
    phase: str = DWELLING


@dataclass(frozen=True)
class FakePathPlan:
    key: str
    segment_ids: tuple[int, ...]

    def cache_key(self) -> tuple[str]:
        return (self.key,)


class DispatchRuntimeTests(unittest.TestCase):
    def test_real_phase_transition_records_departure_and_headway(self) -> None:
        service = RuleBasedDispatchService()
        runtime = DispatchRuntimeCoordinator(service)
        first = FakeTrain("T1")
        second = FakeTrain("T2")
        runtime.register_train(first)
        runtime.register_train(second)

        first.phase = "DEPARTING"
        records = runtime.observe([first, second], 100.0, 21_700_000)
        self.assertEqual([item.train_id for item in records], ["T1"])
        self.assertEqual(records[0].to_dict()["simTimeMs"], 21_700_000)

        front, rear = runtime.headways_for("T2", 0, "UP", 145.0)
        self.assertEqual(front, 45.0)
        self.assertIsNone(rear)

    def test_physical_turnback_motion_is_not_recorded_as_service_departure(self) -> None:
        service = RuleBasedDispatchService()
        runtime = DispatchRuntimeCoordinator(service)
        train = FakeTrain("T1")
        train.turnback_state = "RUNNING"
        runtime.register_train(train)

        train.phase = "DEPARTING"
        self.assertEqual(runtime.observe([train], 100.0), [])

        train.phase = DWELLING
        runtime.observe([train], 110.0)
        train.turnback_state = "COMPLETED"
        train.phase = "DEPARTING"
        records = runtime.observe([train], 120.0)
        self.assertEqual([item.train_id for item in records], ["T1"])


class InterlockingRuntimeTests(unittest.TestCase):
    def test_missing_route_table_path_holds_instead_of_using_legacy_motion(self) -> None:
        engine = make_engine()
        try:
            self.assertTrue(engine.add_train({
                "trainId": "HOLD", "initialStationCode": "QLZ", "direction": "UP"
            })["ok"])
            train = engine.trains[0]
            train._path_plan = None
            train.speed_mps = 5.0

            with patch.object(engine, "_ensure_interval_path", return_value=None):
                handled, prepared = engine._prepare_train_step(
                    train,
                    engine._absolute_sim_time_ms(),
                )

            self.assertTrue(handled)
            self.assertIsNone(prepared)
            self.assertEqual(train.speed_mps, 0.0)
            self.assertFalse(train.departure_authorized)
            self.assertEqual(train.interlocking_hold_reason, "NO_ROUTE_TABLE_PATH")
            self.assertEqual(train.current_platform_id, 12)
        finally:
            engine.speed_profile_service.shutdown()

    def test_arrival_platform_continuity_completes_route_50_lifecycle(self) -> None:
        engine = make_engine()
        try:
            self.assertTrue(engine.add_train({
                "trainId": "R50", "initialStationCode": "QLZ", "direction": "UP"
            })["ok"])
            engine.clock.start()

            for _ in range(1200):
                engine._tick()
                train = engine.trains[0]
                if train.current_station_code == "LLQ":
                    break
            else:
                self.fail("train did not arrive at LLQ")

            train = engine.trains[0]
            route_50 = next(
                item for item in engine.route_service.snapshot()
                if item["routeId"] == "50"
            )
            self.assertEqual(train.current_platform_id, 14)
            self.assertEqual(train.current_segment_id, 103)
            platform = engine._platform_by_id[14]
            self.assertAlmostEqual(
                train.current_segment_offset_m,
                engine._platform_head_stop_offset_m(
                    platform,
                    train.direction,
                    train.train_length_m,
                ),
            )
            self.assertIsNotNone(train._track_trace)
            self.assertEqual(train._track_trace.head_segment_id, 103)
            self.assertEqual(route_50["state"], "IDLE")
            self.assertIsNone(route_50["trainId"])

            # The next interval must continue from the platform actually
            # reached by routes 49/50, not another platform at the same station.
            engine._tick()
            self.assertEqual(train._path_plan.origin_platform_id, 14)
            self.assertEqual(train._path_plan.start_segment_id, 103)
            self.assertEqual(train.current_platform_id, 14)
            self.assertEqual(train.current_segment_id, 103)
            self.assertIn(102, train._track_trace.segment_ids)
            self.assertIn(104, train._track_trace.segment_ids)
            self.assertIn(
                103,
                engine.section_occupation.covered_segments_for(train.train_id),
            )

            self.assertEqual(engine.route_service.state_of("50"), "IDLE")
            self.assertNotEqual(train.current_segment_id, 88)
        finally:
            engine.speed_profile_service.shutdown()

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
        self.assertEqual(second["interlockingHoldReason"], "CONFLICT_ROUTE_LOCKED")
        self.assertEqual(second["lastDispatchAction"], "HOLD")
        self.assertEqual(second["lastDispatchReason"], "HEADWAY_TOO_SHORT")
        self.assertEqual(engine.snapshot().dispatch_runtime["departureCount"], 1)

    def test_independent_routes_are_not_blocked_by_broad_path_reservations(self) -> None:
        engine = make_engine()
        runtime = InterlockingRuntimeCoordinator(engine.line_map, engine.track_query)

        first = runtime.request_departure(
            "T1",
            FakePathPlan("first", (49, 50, 51)),
            ("28",),
        )
        # Segment 49 deliberately overlaps the first diagnostic PathPlan.
        # Route 92 itself uses sections 185/186/187 and is independent of 28.
        second = runtime.request_departure(
            "T2",
            FakePathPlan("second", (49, 220, 221, 222, 223, 225)),
            ("92",),
        )

        self.assertTrue(first.granted)
        self.assertTrue(second.granted)
        owners = {
            item["routeId"]: item["trainId"]
            for item in runtime.route_service.snapshot()
            if item["state"] in {"LOCKED", "APPROACH_LOCKED"}
        }
        self.assertEqual(owners["28"], "T1")
        self.assertEqual(owners["92"], "T2")

    def test_same_real_route_is_still_exclusive(self) -> None:
        engine = make_engine()
        runtime = InterlockingRuntimeCoordinator(engine.line_map, engine.track_query)
        path = FakePathPlan("shared", (49, 50, 51))

        self.assertTrue(runtime.request_departure("T1", path, ("28",)).granted)
        second = runtime.request_departure("T2", path, ("28",))

        self.assertFalse(second.granted)
        self.assertEqual(second.failure_reason, "CONFLICT_ROUTE_LOCKED")

    def test_blocked_second_route_does_not_revoke_an_existing_first_route(self) -> None:
        engine = make_engine()
        runtime = InterlockingRuntimeCoordinator(engine.line_map, engine.track_query)
        path = engine._path_plan_for_station_pair(5, 6)
        self.assertIsNotNone(path)
        assert path is not None

        self.assertTrue(
            runtime.route_service.request(
                RouteRequest("BLOCK-50", "50", "T-BLOCKER")
            ).accepted
        )
        first = runtime.request_departure("T1", path, ("49",))
        self.assertTrue(first.granted)

        extension = runtime.request_departure("T1", path, ("49", "50"))
        self.assertFalse(extension.granted)
        self.assertEqual(runtime.route_service.locked_by("49"), "T1")
        self.assertEqual(runtime.route_service.locked_by("50"), "T-BLOCKER")

        # A passed signal correctly returns to red.  Extending authority must
        # validate newly requested route 50, not cancel route 49 behind T1.
        runtime.route_service._routes["49"].has_entered = True
        runtime.signal_resolver.refresh()
        runtime.route_service.release("50", "CANCEL")
        extension = runtime.request_departure("T1", path, ("49", "50"))
        self.assertTrue(extension.granted)
        self.assertEqual(runtime.route_service.locked_by("49"), "T1")
        self.assertEqual(runtime.route_service.locked_by("50"), "T1")

    def test_engine_departs_on_first_route_and_retries_blocked_second_route(self) -> None:
        engine = make_engine()
        self.assertTrue(engine.add_train({
            "trainId": "T-PROGRESSIVE",
            "initialStationCode": "QLZ",
            "initialSegmentId": 98,
            "direction": "UP",
        })["ok"])
        train = engine.trains[0]
        self.assertEqual(train._planned_route_ids, ("49", "50"))
        self.assertTrue(
            engine.route_service.request(
                RouteRequest("BLOCK-50", "50", "T-BLOCKER")
            ).accepted
        )
        train.dwell_remaining_sec = 0.0
        train.door_state = "CLOSED"
        train.door_notice = "CLOSED"
        train.phase = DWELLING

        engine._authorize_ready_departures(0)
        self.assertTrue(train.departure_authorized)
        self.assertEqual(train.active_route_ids, ("49",))
        authority = engine._movement_authority_for_train(
            train,
            train._path_plan,
            train.path_position_m,
            engine._make_vehicle_config(train.train_id, train.onboard_pax),
        )
        self.assertIsNotNone(authority)
        self.assertEqual(authority.end_reason, "ROUTE_ENDPOINT")
        self.assertEqual(authority.locked_route_ids, ("49",))
        self.assertLess(authority.end_position_m, train._path_plan.total_length_m)

        train.route_retry_at_ms = 0
        engine._extend_interval_authority(train, train._path_plan, 1_000)
        self.assertTrue(train.departure_authorized)
        self.assertEqual(train.active_route_ids, ("49",))
        self.assertEqual(
            train.interlocking_hold_reason,
            "NEXT_ROUTE_PENDING:CONFLICT_ROUTE_LOCKED",
        )

        engine.route_service.release("50", "CANCEL")
        train.route_retry_at_ms = 0
        engine._extend_interval_authority(train, train._path_plan, 2_000)
        self.assertEqual(train.active_route_ids, ("49", "50"))
        self.assertIsNone(train.interlocking_hold_reason)

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
        self.assertIn(
            waiting.interlocking_hold_reason,
            {"INTERVAL_RESERVED", "CONFLICT_ROUTE_LOCKED"},
        )
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
        saw_route_released = False
        for _ in range(1600):
            engine._tick()
            interlocking = engine.snapshot().interlocking
            locked_route_count = interlocking["lockedRouteCount"]
            peak_locked_route_count = max(peak_locked_route_count, locked_route_count)
            if peak_locked_route_count > 0 and locked_route_count < peak_locked_route_count:
                saw_route_released = True
            if engine.snapshot().dispatch_runtime["departureCount"] >= 2:
                break

        departures = engine.snapshot().dispatch_runtime["recentDepartures"]
        self.assertGreater(peak_locked_route_count, 0)
        self.assertTrue(saw_route_released)
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
