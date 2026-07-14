from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from app.core.engine import SimulationEngine
from app.infra.recorder import RunRecorder


ROOT = Path(__file__).resolve().parents[1]


class SnapshotReplayTests(unittest.TestCase):
    def test_authoritative_snapshot_round_trip_preserves_canonical_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = RunRecorder(Path(tmp) / "runs.sqlite")
            engine = SimulationEngine.load_from_files(
                scenario_path=ROOT / "data" / "scenarios" / "line9_single.json",
                line_map_path=ROOT / "data" / "cache" / "line_map.json",
                stations_csv_path=ROOT / "data" / "line9" / "stations.csv",
                recorder=recorder,
            )
            engine.load()
            initial = engine.snapshot()
            self.assertIsNotNone(initial)
            assert initial is not None and initial.run_id is not None

            manifest = recorder.list_world_snapshots(initial.run_id)
            self.assertEqual(len(manifest), 1)
            replay = recorder.read_world_snapshot(
                initial.run_id,
                sequence=initial.snapshot_sequence,
            )
            replay_hash = replay.pop("snapshotHash")
            canonical = json.dumps(
                replay, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            self.assertEqual(hashlib.sha256(canonical).hexdigest(), replay_hash)
            self.assertEqual(replay["snapshotSequence"], initial.snapshot_sequence)
            self.assertEqual(replay["sessionId"], initial.session_id)
            self.assertEqual(replay["runId"], initial.run_id)
            self.assertEqual(replay["dataMode"], "LIVE_SIM")
            recorder.close()

    def test_tick_batch_can_be_rolled_back_without_partial_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = RunRecorder(Path(tmp) / "runs.sqlite")
            run_id = recorder.start_run("atomic")
            recorder.begin_batch()
            recorder.record_event(run_id, "train.state", {"trainId": "T1"}, tick=1)
            recorder.record_metric(run_id, "speed", 12.5, tick=1)
            recorder.rollback_batch()
            self.assertEqual(recorder.replay_events(run_id), [])
            exported = recorder.export_run(run_id)
            self.assertEqual(exported["tables"]["metrics"], [])
            recorder.close()

    def test_foreign_keys_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = RunRecorder(Path(tmp) / "runs.sqlite")
            with self.assertRaises(Exception):
                recorder.record_event(9999, "orphan", {}, tick=1)
            recorder.close()


if __name__ == "__main__":
    unittest.main()
