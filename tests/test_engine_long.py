from __future__ import annotations

import sqlite3
import unittest
from contextlib import closing
from pathlib import Path

from app.core.engine import SimulationEngine
from app.infra.recorder import RunRecorder


ROOT = Path(__file__).resolve().parents[1]


class EngineLongRunTests(unittest.TestCase):
    def test_five_train_sixty_second_run_records_without_external_server(self) -> None:
        output_dir = ROOT / "outputs" / "test-runtime"
        output_dir.mkdir(parents=True, exist_ok=True)
        db_path = output_dir / "engine_long.sqlite"
        db_path.unlink(missing_ok=True)
        recorder = RunRecorder(db_path)
        try:
            engine = SimulationEngine.load_from_files(
                scenario_path=ROOT / "data" / "scenarios" / "line9_5train_power.json",
                line_map_path=ROOT / "data" / "cache" / "line_map.json",
                stations_csv_path=ROOT / "MetroDynamicsJavaDemo" / "data" / "stations.csv",
                recorder=recorder,
            )
            engine.load()
            engine.clock.start()
            for _tick in range(240):
                engine._tick()
            snapshot = engine.snapshot()
            assert snapshot is not None
            self.assertEqual(snapshot.tick, 240)
            self.assertTrue(all(item["phase"] != "IDLE" for item in snapshot.trains))
            self.assertTrue(snapshot.power_network["solver"]["converged"])
            with closing(sqlite3.connect(db_path)) as connection:
                self.assertGreater(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
                self.assertGreater(connection.execute("SELECT COUNT(*) FROM power_solver_records").fetchone()[0], 0)
                self.assertGreater(connection.execute("SELECT COUNT(*) FROM train_voltage_records").fetchone()[0], 0)
        finally:
            recorder.close()
            db_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
