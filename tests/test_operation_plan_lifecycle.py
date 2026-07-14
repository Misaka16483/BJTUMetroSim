from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

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
        engine.clock.start()
        engine.stop()
        self.assertEqual(engine.trains, [])
        self.assertEqual(engine.start(), "STARTED")
        self.assertEqual(len(engine.trains), 4)
        self.assertTrue(all(train.lifecycle_state in {"READY", "IN_DEPOT"} for train in engine.trains))
        engine.stop()

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
