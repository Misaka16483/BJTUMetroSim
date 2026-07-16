from __future__ import annotations

import json
import sqlite3
import threading
import tempfile
import time
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from app.api_server import Line9DataService, build_server
from app.core.engine import SimulationEngine
from app.infra.recorder import RunRecorder


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "data" / "scenarios" / "line9_timetable_operation.json"


def load_engine() -> SimulationEngine:
    return SimulationEngine.load_from_files(
        scenario_path=SCENARIO,
        line_map_path=ROOT / "data" / "cache" / "line_map.json",
        stations_csv_path=ROOT / "data" / "line9" / "stations.csv",
    )


class OperationPlanLifecycleTests(unittest.TestCase):
    def test_timetable_creates_distinct_services_duties_and_physical_trains(self) -> None:
        engine = load_engine()
        engine.load()
        state = engine.operation_plan_state()
        self.assertTrue(state["enabled"])
        self.assertEqual(len(state["duties"]), 4)
        self.assertEqual(len(state["services"]), 8)
        self.assertEqual(len(engine.trains), 4)
        self.assertEqual(len({item["dutyId"] for item in state["duties"]}), 4)
        self.assertEqual(len({item["trainId"] for item in state["duties"]}), 4)
        for duty in state["duties"]:
            self.assertEqual(len(duty["serviceIds"]), 2)
        first = engine.trains[0]
        dispatch_state = engine.dispatch_service._train_states[first.train_id]
        self.assertEqual(dispatch_state.service.service_id, first.service_id)

    def test_due_service_requests_ci_authority_and_departs_on_schedule(self) -> None:
        engine = load_engine()
        engine.load()
        engine.clock.start()
        for _ in range(145):
            engine._tick()
        first = engine.trains[0]
        self.assertEqual(first.lifecycle_state, "IN_SERVICE")
        self.assertEqual(first.phase, "DEPARTING")
        self.assertLessEqual(
            abs(first.actual_departure_ms - first.planned_departure_ms),
            round(engine.clock.tick_seconds * 1000),
        )
        self.assertLessEqual(abs(first.schedule_deviation_sec), engine.clock.tick_seconds)
        self.assertGreater(first.last_boarding, 0)
        events = engine.operation_plan_state()["recentEvents"]
        self.assertEqual(events[-1]["event"], "DEPARTURE")
        self.assertEqual(events[-1]["serviceId"], first.service_id)

    def test_round_trip_switches_service_at_turnback_then_returns_to_depot(self) -> None:
        engine = load_engine()
        engine.load()
        train = engine.trains[0]
        duty = engine._operation_duties[train.duty_id]
        outbound_service_id = duty.service_ids[0]
        return_service_id = duty.service_ids[1]

        train.station_index = len(engine._station_list) - 1
        train.current_station_code = str(engine._station_list[-1]["code"])
        self.assertFalse(engine._handle_planned_terminal(train, 22_000_000))
        self.assertEqual(train.lifecycle_state, "TURNBACK")
        self.assertEqual(train.service_id, return_service_id)
        self.assertNotEqual(train.service_id, outbound_service_id)
        # Service assignment switches at the terminal, while the physical
        # direction changes only after the route-backed turnback completes.
        self.assertEqual(train.direction, "UP")
        self.assertIsNotNone(train._turnback_plan)
        self.assertEqual(
            engine.dispatch_service._train_states[train.train_id].service.service_id,
            return_service_id,
        )

        train.station_index = 0
        train.current_station_code = str(engine._station_list[0]["code"])
        self.assertTrue(engine._handle_planned_terminal(train, 25_000_000))
        self.assertEqual(train.lifecycle_state, "RETURN_REQUESTED")
        engine._advance_operation_lifecycle(25_000_250)
        self.assertEqual(train.lifecycle_state, "STORED")
        self.assertEqual(train.phase, "IDLE")

    def test_stored_train_is_not_a_moving_block_obstacle(self) -> None:
        engine = load_engine()
        engine.load()
        following, stored = engine.trains[:2]
        path_plan = engine._ensure_interval_path(following, following.station_index + 1)
        self.assertIsNotNone(path_plan)
        stored._path_plan = path_plan
        stored.path_position_m = path_plan.total_length_m
        stored.lifecycle_state = "STORED"

        positions = engine._other_train_positions(following)

        self.assertNotIn(stored.train_id, {item.train_id for item in positions})

    def test_plan_uses_dcdp_runtime_and_exposes_reproducible_window(self) -> None:
        engine = load_engine()
        engine.load()
        state = engine.operation_plan_state()
        self.assertEqual(
            state["timetables"][0]["runTimeSource"],
            "DCDP_TARGET_WITH_RECOVERY_MARGIN",
        )
        self.assertEqual(len(state["planHash"]), 64)
        self.assertTrue(state["profileWarmup"]["ready"])
        self.assertEqual(
            state["experimentManifest"]["runTimeSource"],
            "DCDP_TARGET_WITH_RECOVERY_MARGIN",
        )

        last_planned_end_ms = round(
            max(item.planned_end_s for item in engine._operation_duties.values()) * 1000
        )
        window = state["experimentWindow"]
        self.assertEqual(window["measurementEndTimeMs"], last_planned_end_ms)
        self.assertEqual(window["clearanceEndTimeMs"], last_planned_end_ms + 300_000)
        self.assertEqual(window["phase"], "WARMUP")
        self.assertEqual(state["acceptance"]["status"], "PENDING")

    def test_return_service_uses_authoritative_reverse_station_indices(self) -> None:
        engine = load_engine()
        engine.load()
        duty = next(iter(engine._operation_duties.values()))
        outbound = engine._operation_services[duty.service_ids[0]]
        returning = engine._operation_services[duty.service_ids[1]]
        self.assertEqual([stop.station_index for stop in outbound.stops], list(range(13)))
        self.assertEqual([stop.station_index for stop in returning.stops], list(reversed(range(13))))
        self.assertTrue(all(stop.distance_from_origin_m >= 0 for stop in returning.stops))

    def test_stop_restart_rebuilds_the_planned_roster(self) -> None:
        engine = load_engine()
        engine.load()
        self.assertEqual(len(engine.trains), 4)
        duties = sorted(
            engine._operation_duties.values(),
            key=lambda item: item.planned_start_s,
        )
        added = engine.add_operation_duty(
            "EMU-9-901",
            duties[-1].planned_start_s + 120.0,
        )
        self.assertTrue(added["ok"])
        self.assertEqual(len(engine.trains), 5)
        engine.clock.start()
        engine.stop()
        self.assertEqual(engine.trains, [])
        self.assertEqual(engine.start(), "STARTED")
        self.assertEqual(len(engine.trains), 4)
        self.assertNotIn("EMU-9-901", {train.train_id for train in engine.trains})
        self.assertEqual(len(engine._operation_duties), 4)
        self.assertTrue(all(train.lifecycle_state in {"READY", "IN_DEPOT"} for train in engine.trains))
        engine.stop()

    def test_pending_duty_reschedule_shifts_the_complete_round_trip(self) -> None:
        engine = load_engine()
        engine.load()
        duties = sorted(
            engine._operation_duties.values(),
            key=lambda item: item.planned_start_s,
        )
        duty = duties[1]
        services = [engine._operation_services[item] for item in duty.service_ids]
        previous_stops = [
            [(stop.planned_arrival_s, stop.planned_departure_s) for stop in service.stops]
            for service in services
        ]
        previous_start_s = duty.planned_start_s
        previous_end_s = duty.planned_end_s
        previous_hash = engine.operation_plan_state()["planHash"]
        train = next(item for item in engine.trains if item.duty_id == duty.duty_id)
        previous_train_departure_ms = train.planned_departure_ms
        requested_start_s = previous_start_s + 30.0

        result = engine.reschedule_operation_duty(duty.duty_id, requested_start_s)

        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertEqual(duty.planned_start_s, requested_start_s)
        self.assertEqual(duty.planned_end_s, previous_end_s + 30.0)
        self.assertEqual(train.planned_departure_ms, previous_train_departure_ms + 30_000)
        for service, original in zip(services, previous_stops):
            self.assertEqual(
                [(stop.planned_arrival_s, stop.planned_departure_s) for stop in service.stops],
                [(arrival + 30.0, departure + 30.0) for arrival, departure in original],
            )
        state = engine.operation_plan_state()
        self.assertNotEqual(state["planHash"], previous_hash)
        self.assertEqual(state["recentEvents"][-1]["event"], "AUTO_DISPATCH_QUEUE_RESCHEDULED")
        self.assertEqual(result["operationPlan"]["planHash"], state["planHash"])
        engine._advance_operation_lifecycle(round(requested_start_s * 1000))
        self.assertEqual(train.lifecycle_state, "DEPARTURE_REQUESTED")

    def test_add_operation_duty_rejects_stopped_state_without_orphan_train(self) -> None:
        engine = load_engine()
        engine.load()
        duties = sorted(
            engine._operation_duties.values(),
            key=lambda item: item.planned_start_s,
        )
        engine.stop()

        result = engine.add_operation_duty(
            "EMU-9-901",
            duties[-1].planned_start_s + 120.0,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "SIMULATION_NOT_EDITABLE")
        self.assertEqual(engine.start(), "STARTED")
        self.assertEqual(len(engine.trains), 4)
        self.assertEqual(len(engine._operation_duties), 4)
        self.assertTrue(
            all(train.duty_id in engine._operation_duties for train in engine.trains)
        )
        engine.stop()

    def test_paused_background_runtime_adds_atomically_and_resumes(self) -> None:
        engine = load_engine()
        engine.load()
        duties = sorted(
            engine._operation_duties.values(),
            key=lambda item: item.planned_start_s,
        )
        self.assertEqual(engine.start(), "STARTED")
        time.sleep(0.05)
        engine.pause()

        result = engine.add_operation_duty(
            "EMU-9-901",
            duties[-1].planned_start_s + 120.0,
        )

        self.assertTrue(result["ok"])
        engine.resume()
        time.sleep(0.05)
        snapshot = engine.snapshot()
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.clock_state, "RUNNING")
        self.assertIn("EMU-9-901", {train["trainId"] for train in snapshot.trains})
        engine.stop()

    def test_duty_reschedule_enforces_pause_and_minimum_headway(self) -> None:
        engine = load_engine()
        engine.load()
        duties = sorted(
            engine._operation_duties.values(),
            key=lambda item: item.planned_start_s,
        )
        target = duties[1]

        engine.clock.start()
        running_result = engine.reschedule_operation_duty(
            target.duty_id,
            target.planned_start_s + 30.0,
        )
        engine.clock.pause()
        conflict_result = engine.reschedule_operation_duty(
            target.duty_id,
            duties[0].planned_start_s + 10.0,
        )

        self.assertFalse(running_result["ok"])
        self.assertEqual(running_result["error"], "SIMULATION_MUST_BE_PAUSED")
        self.assertFalse(conflict_result["ok"])
        self.assertEqual(conflict_result["error"], "MINIMUM_HEADWAY_CONFLICT")
        self.assertEqual(conflict_result["conflictingDutyId"], duties[0].duty_id)

    def test_active_duty_cannot_be_rescheduled(self) -> None:
        engine = load_engine()
        engine.load()
        duty = sorted(
            engine._operation_duties.values(),
            key=lambda item: item.planned_start_s,
        )[1]
        duty.lifecycle_state = "IN_SERVICE"

        result = engine.reschedule_operation_duty(
            duty.duty_id,
            duty.planned_start_s + 30.0,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "DUTY_NOT_EDITABLE")

    def test_add_operation_duty_creates_round_trip_and_physical_train(self) -> None:
        engine = load_engine()
        engine.load()
        original_duties = sorted(
            engine._operation_duties.values(),
            key=lambda item: item.planned_start_s,
        )
        template = original_duties[0]
        template_services = [
            engine._operation_services[service_id]
            for service_id in template.service_ids
        ]
        requested_start_s = original_duties[-1].planned_start_s + 120.0
        previous_hash = engine.operation_plan_state()["planHash"]

        result = engine.add_operation_duty("EMU-9-901", requested_start_s)

        self.assertTrue(result["ok"])
        self.assertEqual(len(engine._operation_duties), 5)
        self.assertEqual(len(engine._operation_services), 10)
        self.assertEqual(len(engine._operation_timetables[0].services), 5)
        self.assertEqual(len(engine.trains), 5)
        added_duty = engine._operation_duties[result["duty"]["dutyId"]]
        self.assertEqual(added_duty.train_id, "EMU-9-901")
        self.assertEqual(added_duty.planned_start_s, requested_start_s)
        self.assertEqual(len(added_duty.service_ids), 2)
        added_services = [
            engine._operation_services[service_id]
            for service_id in added_duty.service_ids
        ]
        self.assertEqual(
            [service.direction for service in added_services],
            [service.direction for service in template_services],
        )
        self.assertEqual(
            added_services[0].stops[0].planned_departure_s,
            requested_start_s,
        )
        self.assertEqual(
            added_services[1].planned_run_time_s,
            template_services[1].planned_run_time_s,
        )
        train = next(item for item in engine.trains if item.train_id == "EMU-9-901")
        self.assertEqual(train.duty_id, added_duty.duty_id)
        self.assertEqual(train.service_id, added_duty.service_ids[0])
        self.assertEqual(train.next_service_id, added_duty.service_ids[1])
        self.assertEqual(
            engine.dispatch_service._train_states[train.train_id].service.service_id,
            train.service_id,
        )
        state = engine.operation_plan_state()
        self.assertNotEqual(state["planHash"], previous_hash)
        self.assertEqual(
            state["recentEvents"][-1]["event"],
            "AUTO_DISPATCH_QUEUE_DUTY_ADDED",
        )
        self.assertEqual(result["operationPlan"]["planHash"], state["planHash"])

    def test_add_operation_duty_rejects_conflicts_without_partial_mutation(self) -> None:
        engine = load_engine()
        engine.load()
        duties = sorted(
            engine._operation_duties.values(),
            key=lambda item: item.planned_start_s,
        )
        original_counts = (
            len(engine.trains),
            len(engine._operation_duties),
            len(engine._operation_services),
            len(engine._operation_timetables[0].services),
            engine.dispatch_runtime.snapshot()["registeredTrainCount"],
        )
        original_hash = engine.operation_plan_state()["planHash"]
        original_event_count = len(engine._operation_events)

        invalid_id = engine.add_operation_duty("bad train id", duties[-1].planned_start_s + 120.0)
        invalid_type = engine.add_operation_duty(None, duties[-1].planned_start_s + 120.0)
        duplicate_id = engine.add_operation_duty("emu-9-001", duties[-1].planned_start_s + 120.0)
        headway_conflict = engine.add_operation_duty(
            "EMU-9-902",
            duties[0].planned_start_s + 10.0,
        )
        engine.clock.start()
        running = engine.add_operation_duty(
            "EMU-9-903",
            duties[-1].planned_start_s + 120.0,
        )
        engine.clock.pause()

        self.assertEqual(invalid_id["error"], "INVALID_TRAIN_ID")
        self.assertEqual(invalid_type["error"], "INVALID_TRAIN_ID")
        self.assertEqual(duplicate_id["error"], "TRAIN_ID_EXISTS")
        self.assertEqual(headway_conflict["error"], "MINIMUM_HEADWAY_CONFLICT")
        self.assertEqual(running["error"], "SIMULATION_MUST_BE_PAUSED")
        self.assertEqual(
            (
                len(engine.trains),
                len(engine._operation_duties),
                len(engine._operation_services),
                len(engine._operation_timetables[0].services),
                engine.dispatch_runtime.snapshot()["registeredTrainCount"],
            ),
            original_counts,
        )
        self.assertEqual(engine.operation_plan_state()["planHash"], original_hash)
        self.assertEqual(len(engine._operation_events), original_event_count)

    def test_add_operation_duty_persists_updated_manifest_and_event_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            recorder = RunRecorder(Path(temporary_dir) / "operations.sqlite")
            engine = SimulationEngine.load_from_files(
                scenario_path=SCENARIO,
                line_map_path=ROOT / "data" / "cache" / "line_map.json",
                stations_csv_path=ROOT / "data" / "line9" / "stations.csv",
                recorder=recorder,
            )
            try:
                engine.load()
                duties = sorted(
                    engine._operation_duties.values(),
                    key=lambda item: item.planned_start_s,
                )
                run_id = engine._run_id
                self.assertIsNotNone(run_id)

                result = engine.add_operation_duty(
                    "EMU-9-901",
                    duties[-1].planned_start_s + 120.0,
                )

                self.assertTrue(result["ok"])
                connection = sqlite3.connect(recorder.db_path)
                try:
                    metadata = json.loads(
                        connection.execute(
                            "SELECT metadata_json FROM runs WHERE id = ?",
                            (run_id,),
                        ).fetchone()[0]
                    )
                finally:
                    connection.close()
                self.assertEqual(metadata["trainCount"], 5)
                self.assertEqual(metadata["dutyCount"], 5)
                self.assertEqual(metadata["serviceCount"], 10)
                self.assertEqual(
                    metadata["operationPlanHash"],
                    result["operationPlan"]["planHash"],
                )
                events = recorder.replay_events(run_id, "operations.lifecycle")
                self.assertEqual(
                    events[-1]["payload"]["event"],
                    "AUTO_DISPATCH_QUEUE_DUTY_ADDED",
                )
                self.assertNotIn(
                    "AUTO_DISPATCH_QUEUE_DUTY_ADDED",
                    [event["event"] for event in engine._pending_operation_events],
                )
            finally:
                if engine.clock.state.value not in {"IDLE", "STOPPED"}:
                    engine.stop()
                recorder.close()

    def test_auto_dispatch_add_http_endpoint_returns_updated_plan(self) -> None:
        engine = load_engine()
        engine.load()
        duties = sorted(
            engine._operation_duties.values(),
            key=lambda item: item.planned_start_s,
        )
        with tempfile.TemporaryDirectory() as temporary_dir:
            service = Line9DataService(run_dir=Path(temporary_dir))
            server = build_server("127.0.0.1", 0, service)
            server.RequestHandlerClass.engine = engine
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address
                body = json.dumps({
                    "trainId": "EMU-9-904",
                    "plannedStartS": duties[-1].planned_start_s + 120.0,
                }).encode("utf-8")
                request = Request(
                    f"http://{host}:{port}/api/sim/auto-dispatch/queue/add",
                    data=body,
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with urlopen(request, timeout=15) as response:
                    result = json.load(response)
                    self.assertEqual(response.status, 201)
                self.assertTrue(result["ok"])
                self.assertEqual(result["duty"]["trainId"], "EMU-9-904")
                self.assertEqual(len(result["operationPlan"]["duties"]), 5)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_lifecycle_transitions_are_recorded_with_the_authoritative_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = RunRecorder(Path(tmp) / "operations.sqlite")
            engine = SimulationEngine.load_from_files(
                scenario_path=SCENARIO,
                line_map_path=ROOT / "data" / "cache" / "line_map.json",
                stations_csv_path=ROOT / "data" / "line9" / "stations.csv",
                recorder=recorder,
            )
            engine.load()
            run_id = engine.snapshot().run_id
            self.assertIsNotNone(run_id)
            engine.clock.start()
            for _ in range(145):
                engine._tick()
            events = recorder.replay_events(run_id, "operations.lifecycle")
            event_names = {item["payload"]["event"] for item in events}
            self.assertIn("LIFECYCLE_TRANSITION", event_names)
            self.assertIn("DEPARTURE", event_names)
            recorder.close()


if __name__ == "__main__":
    unittest.main()
