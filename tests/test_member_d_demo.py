from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.domain.operations.member_d_demo import Phase2MemberDDemoRunner


class MemberDDemoRunnerTests(unittest.TestCase):
    def test_demo_runner_records_integrated_phase2_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "member_d_demo.sqlite"
            summary = Phase2MemberDDemoRunner(db_path).run()

            self.assertGreater(summary["runId"], 0)
            self.assertEqual(summary["recordDb"], str(db_path))
            self.assertGreaterEqual(summary["counts"]["station_passenger_records"], 2)
            self.assertEqual(summary["counts"]["train_load_records"], 1)
            self.assertEqual(summary["counts"]["dwell_records"], 1)
            self.assertEqual(summary["counts"]["dispatch_decisions"], 3)
            self.assertEqual(summary["counts"]["power_records"], 1)
            self.assertLess(summary["power"]["PWR-0901"]["tractionLimitRatio"], 1.0)

            actions = {decision["action"] for decision in summary["dispatch"]}
            self.assertIn("STAGGER_DEPARTURE", actions)
            self.assertIn("HOLD", actions)
            self.assertIn("RELEASE", actions)

            connection = sqlite3.connect(db_path)
            try:
                decision_rows = connection.execute(
                    "SELECT action, reason FROM dispatch_decisions ORDER BY id"
                ).fetchall()
                self.assertEqual(decision_rows[0], ("STAGGER_DEPARTURE", "POWER_LIMITED"))
                self.assertEqual(
                    connection.execute("SELECT source FROM power_records").fetchone()[0],
                    "SELF_SIM",
                )
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
