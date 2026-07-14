from __future__ import annotations

import os
import unittest
from pathlib import Path

from app.core.engine import SimulationEngine
from app.domain.power.trajectory import (
    EngineSnapshotTrajectoryAdapter,
    JsonlTrajectoryProvider,
    validate_trajectory_frames,
)


ROOT = Path(__file__).resolve().parents[1]


class PowerTrajectoryEngineIntegrationTests(unittest.TestCase):
    def test_adapter_accepts_the_public_engine_snapshot_without_running_ticks(self) -> None:
        engine = SimulationEngine.load_from_files(
            scenario_path=ROOT / "data" / "scenarios" / "line9_single.json",
            line_map_path=ROOT / "data" / "cache" / "line_map.json",
            stations_csv_path=ROOT / "data" / "line9" / "stations.csv",
        )
        engine.load()
        result = engine.add_train({
            "trainId": "TRAJECTORY-001",
            "initialStationCode": "GGZ",
            "direction": "UP",
        })
        snapshot = engine.snapshot()

        self.assertTrue(result["ok"])
        self.assertIsNotNone(snapshot)
        frame = EngineSnapshotTrajectoryAdapter().frame_from_snapshot(snapshot)
        self.assertEqual(frame.source, "ENGINE_SNAPSHOT")
        self.assertEqual(frame.samples[0].train_id, "TRAJECTORY-001")
        self.assertEqual(frame.samples[0].current_station_code, "GGZ")
        self.assertGreater(frame.samples[0].mass_kg, 0.0)

    @unittest.skipUnless(
        os.environ.get("ENGINE_TRAJECTORY_JSONL"),
        "set ENGINE_TRAJECTORY_JSONL after interlocking/timetable fixes to validate a live trace",
    )
    def test_captured_live_trace_satisfies_the_trajectory_contract(self) -> None:
        provider = JsonlTrajectoryProvider(os.environ["ENGINE_TRAJECTORY_JSONL"])

        report = validate_trajectory_frames(provider.frames, allow_roster_changes=False)

        self.assertTrue(report.passed, report.issues)
        self.assertGreater(report.frame_count, 1)


if __name__ == "__main__":
    unittest.main()
