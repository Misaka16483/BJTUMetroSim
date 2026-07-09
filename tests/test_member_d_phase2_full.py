from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.domain.operations.phase2_member_d_full_demo import Phase2MemberDFullDemoRunner


class Phase2MemberDFullDemoRunnerTests(unittest.TestCase):
    def test_demo_runner_output_shape(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_phase2_full.sqlite"
            summary = Phase2MemberDFullDemoRunner(db_path).run()
            self.assertEqual(summary["phase"], 2)
            self.assertEqual(summary["module"], "member-d-full-line9")
            self.assertEqual(summary["stationCount"], 13)
            self.assertIn("runId", summary)
            self.assertIn("recordDb", summary)
            self.assertIn("stations", summary)
            self.assertIn("powerStates", summary)
            self.assertIn("dispatch", summary)
            self.assertIn("counts", summary)

    def test_demo_runner_all_stations(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_phase2_full.sqlite"
            summary = Phase2MemberDFullDemoRunner(db_path).run()
            self.assertEqual(len(summary["stations"]), 13)
            station_ids = [s["stationId"] for s in summary["stations"]]
            self.assertEqual(station_ids[0], "S-GGZ")
            self.assertEqual(station_ids[-1], "S-GTG")

    def test_demo_runner_records_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_phase2_full.sqlite"
            Phase2MemberDFullDemoRunner(db_path).run()
            conn = sqlite3.connect(str(db_path))
            sp_count = conn.execute("SELECT COUNT(*) FROM station_passenger_records").fetchone()[0]
            tl_count = conn.execute("SELECT COUNT(*) FROM train_load_records").fetchone()[0]
            dw_count = conn.execute("SELECT COUNT(*) FROM dwell_records").fetchone()[0]
            dd_count = conn.execute("SELECT COUNT(*) FROM dispatch_decisions").fetchone()[0]
            pr_count = conn.execute("SELECT COUNT(*) FROM power_records").fetchone()[0]
            conn.close()
            self.assertEqual(sp_count, 13)
            self.assertEqual(tl_count, 13)
            self.assertEqual(dw_count, 13)
            self.assertGreaterEqual(dd_count, 1)
            self.assertGreaterEqual(pr_count, 1)

    def test_demo_runner_train_load_evolves(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_phase2_full.sqlite"
            summary = Phase2MemberDFullDemoRunner(db_path).run()
            first_load = summary["stations"][0]["onboardPaxAfter"]
            last_load = summary["stations"][-1]["onboardPaxAfter"]
            self.assertNotEqual(first_load, last_load,
                                "Train load should change along the route")

    def test_demo_runner_power_two_sections(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_phase2_full.sqlite"
            summary = Phase2MemberDFullDemoRunner(db_path).run()
            self.assertGreaterEqual(len(summary["powerStates"]), 1)
            for state in summary["powerStates"].values():
                self.assertIn("tractionLimitRatio", state)
                self.assertIn("voltageLevel", state)
                self.assertIn("energyKwh", state)

    def test_demo_runner_dispatch_decisions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_phase2_full.sqlite"
            summary = Phase2MemberDFullDemoRunner(db_path).run()
            self.assertGreaterEqual(len(summary["dispatch"]), 1)
            actions = [d["action"] for d in summary["dispatch"]]
            self.assertEqual(len(actions), 3)


if __name__ == "__main__":
    unittest.main()
