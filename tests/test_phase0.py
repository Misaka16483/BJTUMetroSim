from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.core.clock import ClockState, SimulationClock
from app.core.message_bus import MessageBus
from app.domain.line.services import LineMapRepository, PathPlanner, TrackQueryService
from app.infra.excel_importer import cm_to_m, cmps_to_mps, parse_k_mileage, to_int, validate_line_map
from app.infra.recorder import RunRecorder


def tiny_line_map() -> dict:
    return {
        "counts": {"Seg表": 2, "信号机表": 1, "站台表": 2, "进路表": 1},
        "points": [{"id": 1}, {"id": 2}, {"id": 3}],
        "switches": [],
        "segments": [
            {
                "id": 1,
                "lengthM": 100.0,
                "startEndpointId": 1,
                "endEndpointId": 2,
                "startForwardSegId": None,
                "startDivergingSegId": None,
                "endForwardSegId": 2,
                "endDivergingSegId": None,
            },
            {
                "id": 2,
                "lengthM": 80.0,
                "startEndpointId": 2,
                "endEndpointId": 3,
                "startForwardSegId": 1,
                "startDivergingSegId": None,
                "endForwardSegId": None,
                "endDivergingSegId": None,
            },
        ],
        "signals": [{"id": 10, "segmentId": 1, "offsetM": 50.0}],
        "platforms": [
            {"id": 20, "segmentId": 1, "offsetM": 70.0, "triggerAxleSectionIds": [30]},
            {"id": 21, "segmentId": 2, "offsetM": 40.0, "triggerAxleSectionIds": [30]},
        ],
        "balises": [],
        "gradients": [
            {
                "id": 40,
                "startSegmentId": 1,
                "startOffsetM": 70.0,
                "endSegmentId": 2,
                "endOffsetM": 40.0,
                "slopePermille": 50,
                "direction": "0xaa",
            }
        ],
        "speedRestrictions": [
            {"id": 50, "segmentId": 1, "startOffsetM": 0.0, "endOffsetM": 100.0, "speedLimitMps": 13.33},
            {"id": 51, "segmentId": 2, "startOffsetM": 0.0, "endOffsetM": 80.0, "speedLimitMps": 6.0},
        ],
        "axleSections": [{"id": 30, "segmentIds": [1]}],
        "logicalSections": [{"id": 60, "startSegmentId": 1, "endSegmentId": 1}],
        "protectionSections": [{"id": 70, "axleSectionIds": [30]}],
        "pointApproachSections": [{"id": 80, "axleSectionIds": [30]}],
        "cbtcApproachSections": [{"id": 90, "logicalSectionIds": [60]}],
        "pointTriggerSections": [{"id": 100, "axleSectionIds": [30]}],
        "cbtcTriggerSections": [{"id": 110, "logicalSectionIds": [60]}],
        "routes": [
            {
                "id": 120,
                "startSignalId": 10,
                "endSignalId": 10,
                "axleSectionIds": [30],
                "protectionSectionIds": [70],
                "pointApproachSectionIds": [80],
                "cbtcApproachSectionIds": [90],
                "pointTriggerSectionIds": [100],
                "cbtcTriggerSectionIds": [110],
            }
        ],
    }


class Phase0UnitTests(unittest.TestCase):
    def test_unit_conversions_and_sentinel(self) -> None:
        self.assertIsNone(to_int(65535))
        self.assertEqual(cm_to_m(75800), 758.0)
        self.assertEqual(cmps_to_mps(1333), 13.33)
        self.assertEqual(parse_k_mileage("K1+660.520"), 1660.52)

    def test_line_validation_and_query(self) -> None:
        line_map = tiny_line_map()
        report = validate_line_map(line_map)
        self.assertTrue(report.ok, report.to_dict())
        service = TrackQueryService(line_map)
        self.assertEqual(service.get_segment(1)["lengthM"], 100.0)
        self.assertEqual(service.get_next_segments(1)[0]["id"], 2)
        self.assertEqual(service.get_speed_limit(1, 10.0)["speedLimitMps"], 13.33)
        self.assertEqual(service.get_next_signal(1, 10.0)["id"], 10)
        self.assertEqual(service.get_nearest_platform(1, 20.0)["id"], 20)

    def test_path_planner_builds_station_to_station_constraints(self) -> None:
        planner = PathPlanner(tiny_line_map())

        plan = planner.plan_between_platforms(20, 21, direction="forward")

        self.assertEqual(plan.segment_ids, (1, 2))
        self.assertAlmostEqual(plan.total_length_m, 70.0)
        self.assertEqual(plan.start_segment_id, 1)
        self.assertEqual(plan.end_segment_id, 2)
        self.assertAlmostEqual(plan.speed_limit_at(10.0), 13.33)
        self.assertAlmostEqual(plan.speed_limit_at(50.0), 6.0)
        self.assertAlmostEqual(plan.grade_ratio_at(10.0), 0.005)
        self.assertAlmostEqual(plan.grade_ratio_at(50.0), 0.005)
        self.assertEqual(plan.constraints[0].segment_id, 1)
        self.assertEqual(plan.constraints[-1].segment_id, 2)

        reverse_plan = planner.plan_between_platforms(21, 20, direction="backward")
        self.assertEqual(reverse_plan.segment_ids, (2, 1))
        self.assertAlmostEqual(reverse_plan.total_length_m, 70.0)
        self.assertAlmostEqual(reverse_plan.speed_limit_at(10.0), 6.0)
        self.assertAlmostEqual(reverse_plan.grade_ratio_at(10.0), -0.005)

    def test_repository_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "line_map.json"
            repo = LineMapRepository(path)
            repo.save(tiny_line_map())
            self.assertEqual(repo.load()["segments"][0]["id"], 1)

    def test_message_bus_latest_and_history(self) -> None:
        bus = MessageBus()
        seen = []
        bus.subscribe("train.state", lambda envelope: seen.append(envelope.payload["trainId"]))
        bus.publish("train.state", {"trainId": "T001"}, tick=1)
        latest = bus.latest("train.state")
        self.assertIsNotNone(latest)
        self.assertEqual(latest.payload["trainId"], "T001")
        self.assertEqual(bus.history("train.state")[0].sequence, 1)
        self.assertEqual(seen, ["T001"])

    def test_simulation_clock_lifecycle(self) -> None:
        clock = SimulationClock(tick_seconds=0.5)
        ticks = []
        clock.load()
        clock.start()
        clock.run_for_ticks(2, [lambda tick, sim_time: ticks.append((tick, sim_time))])
        clock.pause()
        clock.resume()
        clock.step()
        clock.stop()
        self.assertEqual(clock.state, ClockState.STOPPED)
        self.assertEqual(clock.current_tick, 3)
        self.assertEqual(ticks, [(1, 0.5), (2, 1.0)])

    def test_recorder_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = RunRecorder(Path(tmp) / "run.sqlite")
            try:
                run_id = recorder.start_run("test", {})
                recorder.record_event(run_id, "train.state", {"trainId": "T001"}, tick=1)
                recorder.record_metric(run_id, "delay", 0.0, unit="s", tick=1)
                self.assertGreater(run_id, 0)
            finally:
                recorder.close()


if __name__ == "__main__":
    unittest.main()
