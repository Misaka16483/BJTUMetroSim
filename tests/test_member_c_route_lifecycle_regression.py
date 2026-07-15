"""Deterministic acceptance coverage for the Member-C route lifecycle fixes.

The production integration is intentionally exercised through the real Line 9
cache where possible.  The remaining target-interface checks document the
cross-team contracts from ``docs/member-c-parallel/README.md``:

* ``current_platform_id`` records the platform actually reached;
* ``TrainTrackTrace`` preserves a train body across consecutive PathPlans;
* a terminal turnback is an ordered, route-backed plan, rather than a direct
  direction or Seg mutation.

No test uses wall-clock waiting: every state transition is driven by an
explicit service update or engine tick.
"""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.core.engine import SimulationEngine
from app.domain.interlocking.models import RouteRequest
from app.domain.interlocking.route_chain_planner import (
    OperationIntent,
    TurnbackPlan,
)
from app.domain.interlocking.section_occupation import SectionOccupationService
from app.domain.interlocking.train_track_trace import TrainTrackTrace
from app.domain.signal.models import TrainState


ROOT = Path(__file__).resolve().parents[1]
LINE_MAP = ROOT / "data" / "cache" / "line_map.json"
STATIONS_CSV = ROOT / "data" / "line9" / "stations.csv"
SCENARIO = ROOT / "data" / "scenarios" / "line9_interactive.json"

QLZ_INDEX = 5
LLQD_INDEX = 6
GGZ_INDEX = 0
GTG_INDEX = 12


def _load_engine() -> SimulationEngine:
    engine = SimulationEngine.load_from_files(
        scenario_path=SCENARIO,
        line_map_path=LINE_MAP,
        stations_csv_path=STATIONS_CSV,
    )
    engine._ato_config = replace(
        engine._ato_config,
        use_dynamic_programming_profile=False,
    )
    engine.load()
    return engine


class _Track:
    """Small deterministic TrackQuery double with explicit predecessor links."""

    def __init__(
        self,
        links: dict[tuple[int, str], list[int]],
        segment_ids: tuple[int, ...] = (),
    ) -> None:
        self._links = links
        ids = set(segment_ids)
        for (segment_id, _direction), next_ids in links.items():
            ids.add(segment_id)
            ids.update(next_ids)
        self._segments = {
            segment_id: {"id": segment_id, "lengthM": 100.0}
            for segment_id in ids
        }

    def get_segment(self, segment_id: int) -> dict[str, int | float] | None:
        return self._segments.get(int(segment_id))

    def get_next_segments(self, segment_id: int, direction: str) -> list[dict[str, int]]:
        return [{"id": item} for item in self._links.get((segment_id, direction), [])]


class RouteLifecycleTopologyRegressionTests(unittest.TestCase):
    """Real-Line-9 planning and route-release regressions."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.line_map: dict[str, Any] = json.loads(LINE_MAP.read_text(encoding="utf-8"))

    def test_gtg_arrival_route_releases_with_own_train_across_jz184_and_jz185(self) -> None:
        engine = _load_engine()
        train_id = "T-GTG-ARRIVAL-RELEASE"
        result = engine.route_service.request(RouteRequest("REQ-GTG-89", "89", train_id))
        self.assertTrue(result.accepted)

        # At S220+0 m the 118 m consist still covers S219.  These are GTG's
        # adjacent terminal detectors JZ185 and JZ184, not evidence that the
        # train has failed to arrive or that another train occupies route 89.
        terminal_track = _Track({(220, "backward"): [219]}, (219, 220))
        terminal_train = TrainState(
            train_id=train_id,
            seg_id=220,
            offset_m=0.0,
            length_m=118.0,
            direction="FORWARD",
            path_track=terminal_track,
        )
        engine.section_occupation.update([terminal_train], terminal_track)
        engine.route_service.update()

        route_state = next(
            item for item in engine.route_service.snapshot() if item["routeId"] == "89"
        )
        self.assertEqual(route_state["lockedSections"], ["184", "185"])
        self.assertEqual(route_state["lockedSwitches"], {})
        self.assertTrue(engine.route_service.release_terminal_arrival("89", train_id))
        self.assertFalse(engine.route_service.is_locked("89"))

        # Ending the old arrival route must never falsify detector occupancy.
        self.assertEqual(engine.section_occupation.axle_occupied_by("184"), [train_id])
        self.assertEqual(engine.section_occupation.axle_occupied_by("185"), [train_id])

    def test_gtg_multisection_terminal_release_rejects_foreign_occupation(self) -> None:
        engine = _load_engine()
        train_id = "T-GTG-ARRIVAL-OWNER"
        other_train_id = "T-GTG-FOREIGN"
        result = engine.route_service.request(RouteRequest("REQ-GTG-89", "89", train_id))
        self.assertTrue(result.accepted)

        terminal_track = _Track({(220, "backward"): [219]}, (219, 220))
        engine.section_occupation.update(
            [
                TrainState(
                    train_id=train_id,
                    seg_id=220,
                    offset_m=0.0,
                    length_m=118.0,
                    direction="FORWARD",
                    path_track=terminal_track,
                ),
                TrainState(
                    train_id=other_train_id,
                    seg_id=219,
                    offset_m=50.0,
                    length_m=10.0,
                    direction="FORWARD",
                    path_track=terminal_track,
                ),
            ],
            terminal_track,
        )
        engine.route_service.update()

        self.assertFalse(engine.route_service.release_terminal_arrival("89", train_id))
        self.assertTrue(engine.route_service.is_locked("89"))

    def test_qilizhuang_to_liuliqiaodong_uses_49_50_and_arrives_at_s103(self) -> None:
        engine = _load_engine()

        route_chain = engine.route_chain_planner.plan_between_platform_sets(
            (12,), (14,), "forward"
        )

        self.assertEqual(route_chain.route_ids, ("49", "50"))
        self.assertEqual(route_chain.path_plan.origin_platform_id, 12)
        self.assertEqual(route_chain.path_plan.destination_platform_id, 14)
        self.assertEqual(route_chain.path_plan.segment_ids[-1], 103)

    def test_arrival_tick_keeps_actual_s103_platform_instead_of_reanchoring_s88(self) -> None:
        engine = _load_engine()
        created = engine.add_train({
            "trainId": "T-S103-ARRIVAL",
            "initialStationCode": "QLZ",
            "initialSegmentId": 98,
            "direction": "UP",
        })
        self.assertTrue(created["ok"])
        train = engine.trains[0]
        path = engine._ensure_interval_path(train, LLQD_INDEX)
        self.assertIsNotNone(path)
        self.assertEqual(path.destination_platform_id, 14)

        train.unclamped_path_position_m = path.total_length_m - 0.4
        train.speed_mps = 0.12
        train.constraint_reaction_force_n = 125.0

        # Complete the already approved 12 -> 14 PathPlan in one deterministic
        # arrival tick.  The engine must retain the actual path destination.
        engine._complete_path_arrival(
            train, LLQD_INDEX, engine._station_list[LLQD_INDEX], sim_time_ms=1
        )

        self.assertEqual(train.current_segment_id, 103)
        self.assertNotEqual(train.current_segment_id, 88)
        self.assertEqual(getattr(train, "current_platform_id", None), 14)
        self.assertAlmostEqual(train.last_arrival_raw_stop_error_m, -0.4)
        self.assertEqual(train.last_arrival_speed_mps, 0.12)
        self.assertEqual(train.last_arrival_constraint_reaction_force_n, 125.0)
        self.assertEqual(train.last_arrival_sim_time_ms, 1)
        self.assertEqual(len(train.last_arrival_ato_config_fingerprint or ""), 64)
        payload = train.to_dict()
        self.assertEqual(payload["pathPositionM"], round(path.total_length_m, 1))
        self.assertEqual(payload["lastArrivalRawStopErrorM"], -0.4)

    def test_route_50_observes_s103_until_the_train_tail_clears(self) -> None:
        engine = _load_engine()
        occupation = engine.section_occupation
        routes = engine.route_service
        train_id = "T-ROUTE-50"

        request = routes.request(RouteRequest("REG-50", "50", train_id))
        self.assertTrue(request.accepted, request.failure_reason)

        def update_at(segment_id: int, offset_m: float, length_m: float = 118.0) -> None:
            occupation.update(
                [TrainState(
                    train_id=train_id,
                    seg_id=segment_id,
                    offset_m=offset_m,
                    length_m=length_m,
                    direction="FORWARD",
                )],
                engine.track_query,
            )
            routes.update()

        # JZ85/S100 is route 50's approach detector.  The train then enters
        # JZ86/S101 and reaches JZ88/S103, the final locked route section.
        update_at(100, 590.0)
        self.assertEqual(routes.state_of("50"), "APPROACH_LOCKED")
        update_at(101, 110.0)
        update_at(103, 20.0)
        self.assertIn(train_id, occupation.axle_occupied_by("88"))
        self.assertTrue(routes.is_locked("50"), "S103 remains under the train body")

        # The head may have entered S104 while the tail is still on S103; the
        # route must remain locked until that tail has physically cleared.
        update_at(104, 20.0)
        self.assertIn(train_id, occupation.axle_occupied_by("88"))
        self.assertTrue(routes.is_locked("50"), "tail has not cleared S103")

        update_at(104, 128.0, length_m=20.0)
        self.assertNotIn(train_id, occupation.axle_occupied_by("88"))
        self.assertFalse(routes.is_locked("50"), "release follows final-tail clearance")

    def test_next_interval_uses_actual_platform_14_and_s103(self) -> None:
        engine = _load_engine()
        created = engine.add_train({
            "trainId": "T-S103-NEXT",
            "initialStationCode": "QLZ",
            "initialSegmentId": 98,
            "direction": "UP",
        })
        self.assertTrue(created["ok"])
        train = engine.trains[0]
        self.assertIsNotNone(engine._ensure_interval_path(train, LLQD_INDEX))
        engine._complete_path_arrival(
            train, LLQD_INDEX, engine._station_list[LLQD_INDEX], sim_time_ms=1
        )

        origin_platform_id = getattr(train, "current_platform_id", None)
        self.assertEqual(origin_platform_id, 14)
        next_plan = engine._route_chain_plan_for_station_pair(
            LLQD_INDEX, LLQD_INDEX + 1, origin_platform_id
        )
        self.assertIsNotNone(next_plan)
        assert next_plan is not None
        self.assertEqual(next_plan.path_plan.origin_platform_id, 14)
        self.assertEqual(next_plan.path_plan.segment_ids[0], 103)
        self.assertNotIn(88, next_plan.path_plan.segment_ids[:1])

    def test_normal_intermediate_stop_cannot_reverse_or_change_tracks_implicitly(self) -> None:
        engine = _load_engine()
        created = engine.add_train({
            "trainId": "T-NORMAL-STOP",
            "initialStationCode": "QLZ",
            "initialSegmentId": 98,
            "direction": "UP",
        })
        self.assertTrue(created["ok"])
        train = engine.trains[0]
        self.assertIsNotNone(engine._ensure_interval_path(train, LLQD_INDEX))
        engine._complete_path_arrival(
            train, LLQD_INDEX, engine._station_list[LLQD_INDEX], sim_time_ms=1
        )

        self.assertEqual(train.direction, "UP")
        self.assertEqual(getattr(train, "current_platform_id", None), 14)
        next_path = engine._ensure_interval_path(train, LLQD_INDEX + 1)
        self.assertIsNotNone(next_path)
        assert next_path is not None
        self.assertEqual(next_path.origin_platform_id, 14)
        self.assertEqual(next_path.segment_ids[0], 103)


class TrainTraceAndTurnoutRegressionTests(unittest.TestCase):
    """Tail tracing must follow the approved path, including a PathPlan boundary."""

    def test_cross_pathplan_tail_keeps_old_path_occupied_from_train_track_trace(self) -> None:
        line_map = {
            "segments": [
                {"id": 1, "lengthM": 100.0},
                {"id": 2, "lengthM": 100.0},
            ],
            "axleSections": [
                {"id": 1, "segmentIds": [1]},
                {"id": 2, "segmentIds": [2]},
            ],
        }
        service = SectionOccupationService(line_map)
        global_track = _Track({(2, "backward"): [1]}, (1, 2))
        trace = TrainTrackTrace(global_track, (1, 2))
        train = TrainState(
            train_id="T-CROSS-PLAN",
            seg_id=2,
            offset_m=10.0,
            length_m=120.0,
            direction="FORWARD",
            # The active PathPlan begins at S2, but the body tail remains on
            # the S1 path that just completed.  The production trace itself
            # is the path_track consumed by SectionOccupationService.
            path_track=trace,
        )

        service.update([train], global_track)

        self.assertTrue(service.is_axle_occupied("2"))
        self.assertTrue(
            service.is_axle_occupied("1"),
            "TrainTrackTrace must preserve the old-plan tail until it clears S1",
        )

    def test_turnout_footprint_marks_only_the_branch_actually_taken(self) -> None:
        line_map = {
            "segments": [
                {"id": 41, "lengthM": 100.0},
                {"id": 43, "lengthM": 100.0},
                {"id": 44, "lengthM": 100.0},
            ],
            "axleSections": [
                {"id": 41, "segmentIds": [41]},
                {"id": 43, "segmentIds": [43]},
                {"id": 44, "segmentIds": [44]},
            ],
        }
        service = SectionOccupationService(line_map)
        train = TrainState(
            train_id="T-BRANCH",
            seg_id=43,
            offset_m=10.0,
            length_m=120.0,
            direction="FORWARD",
            # The approved route reaches S43 through S44, not S41.
            path_track=_Track({(43, "backward"): [44]}),
        )

        service.update([train], _Track({(43, "backward"): [41]}))

        self.assertTrue(service.is_axle_occupied("43"))
        self.assertTrue(service.is_axle_occupied("44"))
        self.assertFalse(service.is_axle_occupied("41"))
        self.assertEqual(service.covered_segments_for("T-BRANCH"), {43, 44})


class TerminalTurnbackRegressionTests(unittest.TestCase):
    """Terminal reversal must be a real, staged movement rather than a teleport."""

    def _terminal_train(self, terminal_index: int, inbound_direction: str) -> tuple[SimulationEngine, Any]:
        engine = _load_engine()
        created = engine.add_train({
            "trainId": f"T-TURNBACK-{terminal_index}-{inbound_direction}",
            "initialStationCode": "KYL",
            "initialSegmentId": 55,
            "direction": "DOWN",
        })
        self.assertTrue(created["ok"])
        train = engine.trains[0]
        station = engine._station_list[terminal_index]
        train.station_index = terminal_index
        train.current_station_code = str(station["code"])
        train.current_station_name = str(station["name"])
        train.direction = inbound_direction
        train.speed_mps = 0.0
        return engine, train

    def test_gongzhuzhuang_and_national_library_turnback_do_not_flip_direction_in_place(self) -> None:
        for terminal_index, inbound_direction, terminal_id, origin_platform_id in (
            (GGZ_INDEX, "DOWN", "GGZ", 1),
            (GTG_INDEX, "UP", "GTG", 26),
        ):
            with self.subTest(terminal_index=terminal_index):
                engine, train = self._terminal_train(terminal_index, inbound_direction)
                plan = engine.route_chain_planner.plan_operation(
                    intent=OperationIntent.TURNBACK,
                    origin_platform_id=origin_platform_id,
                    terminal_id=terminal_id,
                )

                self.assertIsInstance(plan, TurnbackPlan)
                self.assertTrue(plan.phases)
                engine._turn_train_at_terminal(train)

                self.assertEqual(
                    train.direction,
                    inbound_direction,
                    "terminal arrival must schedule a TurnbackPlan before reversal",
                )

    def test_turnback_plan_requires_route_lock_ma_motion_and_tail_release_per_stage(self) -> None:
        engine, train = self._terminal_train(GGZ_INDEX, "DOWN")
        plan = engine.route_chain_planner.plan_operation(
            intent=OperationIntent.TURNBACK,
            origin_platform_id=1,
            terminal_id="GGZ",
        )
        self.assertIsInstance(plan, TurnbackPlan)
        self.assertGreater(len(plan.phases), 1)
        for phase in plan.phases:
            with self.subTest(phase=phase):
                self.assertTrue(phase.route_ids)
                self.assertTrue(phase.signal_ids)
                self.assertEqual(len(phase.route_ids), len(phase.route_switch_positions))
                self.assertTrue(phase.segment_ids)

        engine._turn_train_at_terminal(train)

        first_phase = plan.phases[0]
        self.assertTrue(
            set(first_phase.route_ids).issubset(engine.route_service.locked_routes()),
            "the engine must lock the first TurnbackPlan phase before movement",
        )
        self.assertEqual(
            train.movement_authority_locked_route_ids,
            first_phase.route_ids,
            "the first TurnbackPlan phase must be published as movement authority",
        )

    def test_gongzhuzhuang_turnback_runs_both_phases_and_releases_final_route(self) -> None:
        engine, train = self._terminal_train(GGZ_INDEX, "DOWN")
        engine._speed_multiplier = 10
        engine._turn_train_at_terminal(train)
        train._passenger_service_pending = False
        train.door_state = "CLOSED"
        train.door_notice = "CLOSED"
        train.dwell_remaining_sec = 0.0
        train.phase = "DWELLING"

        visited_phases: set[int] = set()
        for tick in range(1_000):
            sim_time_ms = tick * 250
            engine.interlocking_runtime.update(engine._interlocking_train_states(sim_time_ms))
            _, prepared = engine._prepare_train_step(train, sim_time_ms)
            if prepared is not None:
                engine._apply_prepared_train_step(prepared, None, sim_time_ms)
            engine.interlocking_runtime.update(engine._interlocking_train_states(sim_time_ms))
            if train.turnback_phase_index is not None:
                visited_phases.add(train.turnback_phase_index)
            if train.turnback_state == "COMPLETED":
                break

        self.assertEqual(visited_phases, {0, 1})
        self.assertEqual(train.turnback_state, "COMPLETED")
        self.assertEqual(train.direction, "UP")
        self.assertEqual(train.current_platform_id, 2)
        self.assertEqual(train.current_segment_id, 39)

        # The next CI scan applies the guarded terminal-arrival release for
        # the final route before ordinary service planning resumes.
        sim_time_ms = (tick + 1) * 250
        engine.interlocking_runtime.update(engine._interlocking_train_states(sim_time_ms))
        engine._prepare_train_step(train, sim_time_ms)
        self.assertFalse({"10", "13", "12"} & set(engine.route_service.locked_routes()))
        self.assertEqual(train.active_route_ids, ())

    def test_national_library_arrival_runs_89_then_turns_via_90_and_87(self) -> None:
        engine, train = self._terminal_train(GTG_INDEX, "UP")
        engine._anchor_train_at_platform(train, 26)
        train._path_plan = None
        train._planned_route_ids = ()
        train._path_origin_station_index = None
        train._path_destination_station_index = None
        train._track_trace = None
        train._trace_path_start_index = None

        # Reproduce the live terminal boundary: inbound route 89 has been
        # entered, while the stopped consist still occupies JZ184 and JZ185.
        result = engine.route_service.request(
            RouteRequest("REQ-GTG-DYNAMIC-89", "89", train.train_id)
        )
        self.assertTrue(result.accepted)
        terminal_track = _Track({(220, "backward"): [219]}, (219, 220))
        engine.section_occupation.update(
            [
                TrainState(
                    train_id=train.train_id,
                    seg_id=220,
                    offset_m=0.0,
                    length_m=train.train_length_m,
                    direction="FORWARD",
                    path_track=terminal_track,
                )
            ],
            terminal_track,
        )
        engine.route_service.update()
        train._terminal_arrival_release_route_ids = ("89",)
        engine._plan_terminal_turnback(train)
        train._passenger_service_pending = False
        train.door_state = "CLOSED"
        train.door_notice = "CLOSED"
        train.dwell_remaining_sec = 0.0
        train.phase = "DWELLING"

        observed_routes: set[str] = {"89"}
        self.assertEqual(engine.section_occupation.axle_occupied_by("184"), [train.train_id])
        self.assertEqual(engine.section_occupation.axle_occupied_by("185"), [train.train_id])

        # The first preparation call consumes the guarded arrival release
        # before the normal runtime scan replaces the manually staged boundary.
        _, prepared = engine._prepare_train_step(train, 0)
        if prepared is not None:
            engine._apply_prepared_train_step(prepared, None, 0)
        self.assertFalse(engine.route_service.is_locked("89"))

        for tick in range(1, 3_000):
            sim_time_ms = tick * 250
            engine.interlocking_runtime.update(engine._interlocking_train_states(sim_time_ms))
            observed_routes.update(engine.route_service.locked_routes())
            _, prepared = engine._prepare_train_step(train, sim_time_ms)
            if prepared is not None:
                engine._apply_prepared_train_step(prepared, None, sim_time_ms)
            engine.interlocking_runtime.update(engine._interlocking_train_states(sim_time_ms))
            observed_routes.update(engine.route_service.locked_routes())
            if train.turnback_state == "COMPLETED":
                break

        self.assertTrue({"89", "90", "87"}.issubset(observed_routes))
        self.assertNotIn("88", observed_routes)
        self.assertEqual(train.turnback_state, "COMPLETED")
        self.assertEqual(train.direction, "DOWN")
        self.assertEqual(train.current_platform_id, 25)
        self.assertEqual(train.current_segment_id, 207)


if __name__ == "__main__":
    unittest.main()
