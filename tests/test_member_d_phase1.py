from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.domain.power.phase1 import estimate_traction_energy
from app.domain.station.phase1 import StopResult, judge_stop
from app.domain.operations.phase1_member_d_demo import Phase1MemberDDemoRunner


class EnergyEstimationTests(unittest.TestCase):
    def test_full_traction(self):
        estimate = estimate_traction_energy("T001", "SEG-001", 70_000.0, 10.0, 30.0)
        self.assertEqual(estimate.train_id, "T001")
        self.assertEqual(estimate.power_kw, 700.0)
        self.assertAlmostEqual(estimate.energy_kwh, 5.8333, places=3)
        self.assertEqual(estimate.method, "SELF_SIM_PHASE1")

    def test_zero_traction(self):
        estimate = estimate_traction_energy("T001", "SEG-001", 0.0, 10.0, 30.0)
        self.assertEqual(estimate.power_kw, 0.0)
        self.assertEqual(estimate.energy_kwh, 0.0)

    def test_zero_speed(self):
        estimate = estimate_traction_energy("T001", "SEG-001", 70_000.0, 0.0, 30.0)
        self.assertEqual(estimate.power_kw, 0.0)
        self.assertEqual(estimate.energy_kwh, 0.0)

    def test_zero_duration(self):
        estimate = estimate_traction_energy("T001", "SEG-001", 70_000.0, 10.0, 0.0)
        self.assertEqual(estimate.energy_kwh, 0.0)

    def test_negative_force_clamped_to_zero(self):
        estimate = estimate_traction_energy("T001", "SEG-001", -5000.0, 10.0, 30.0)
        self.assertEqual(estimate.power_kw, 0.0)
        self.assertEqual(estimate.energy_kwh, 0.0)

    def test_cruising_power(self):
        estimate = estimate_traction_energy("T002", "SEG-002", 30_000.0, 15.0, 60.0)
        self.assertEqual(estimate.power_kw, 450.0)
        self.assertAlmostEqual(estimate.energy_kwh, 7.5, places=3)


class StopJudgmentTests(unittest.TestCase):
    def test_successful_stop(self):
        result = judge_stop("T001", "S-GGZ", 1660.52, 1660.30)
        self.assertTrue(result.is_stopped)
        self.assertEqual(result.stop_result, StopResult.SUCCESS)
        self.assertAlmostEqual(result.stop_error_m, -0.22, places=3)

    def test_overrun(self):
        result = judge_stop("T001", "S-GGZ", 1660.52, 1662.00)
        self.assertTrue(result.is_stopped)
        self.assertEqual(result.stop_result, StopResult.OVERRUN)
        self.assertAlmostEqual(result.stop_error_m, 1.48, places=3)

    def test_undershoot(self):
        result = judge_stop("T001", "S-GGZ", 1660.52, 1658.00)
        self.assertTrue(result.is_stopped)
        self.assertEqual(result.stop_result, StopResult.UNDERSHOOT)
        self.assertAlmostEqual(result.stop_error_m, -2.52, places=3)

    def test_not_stopped(self):
        result = judge_stop("T001", "S-GGZ", 1660.52, 1650.00, speed_mps=3.0)
        self.assertFalse(result.is_stopped)
        self.assertEqual(result.stop_result, StopResult.UNDERSHOOT)

    def test_exact_zero_error(self):
        result = judge_stop("T001", "S-GGZ", 1660.52, 1660.52)
        self.assertEqual(result.stop_result, StopResult.SUCCESS)
        self.assertEqual(result.stop_error_m, 0.0)

    def test_boundary_tolerance(self):
        result = judge_stop("T001", "S-GGZ", 1660.52, 1661.02)
        self.assertEqual(result.stop_result, StopResult.SUCCESS)

    def test_invalid_tolerance(self):
        with self.assertRaises(ValueError):
            judge_stop("T001", "S-GGZ", 1660.52, 1660.52, tolerance_m=0.0)

    def test_invalid_tolerance_negative(self):
        with self.assertRaises(ValueError):
            judge_stop("T001", "S-GGZ", 1660.52, 1660.52, tolerance_m=-0.5)

    def test_custom_tolerance(self):
        result = judge_stop("T001", "S-GGZ", 1660.52, 1661.00, tolerance_m=1.0)
        self.assertEqual(result.stop_result, StopResult.SUCCESS)


class Phase1MemberDDemoRunnerTests(unittest.TestCase):
    def test_demo_runner_output_shape(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_phase1.sqlite"
            summary = Phase1MemberDDemoRunner(db_path).run()
            self.assertEqual(summary["phase"], 1)
            self.assertEqual(summary["module"], "member-d-energy-stop")
            self.assertIn("energyEstimates", summary)
            self.assertIn("stopJudgments", summary)
            self.assertIn("runId", summary)

    def test_demo_runner_energy_scenarios(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_phase1.sqlite"
            summary = Phase1MemberDDemoRunner(db_path).run()
            self.assertEqual(len(summary["energyEstimates"]), 3)
            labels = [e["label"] for e in summary["energyEstimates"]]
            self.assertIn("accelerating", labels)
            self.assertIn("cruising", labels)
            self.assertIn("coasting", labels)

    def test_demo_runner_stop_scenarios(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_phase1.sqlite"
            summary = Phase1MemberDDemoRunner(db_path).run()
            self.assertEqual(len(summary["stopJudgments"]), 4)
            results = [j["stopResult"] for j in summary["stopJudgments"]]
            self.assertIn("SUCCESS", results)
            self.assertIn("OVERRUN", results)
            self.assertIn("UNDERSHOOT", results)

    def test_demo_runner_records(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_phase1.sqlite"
            Phase1MemberDDemoRunner(db_path).run()
            conn = sqlite3.connect(str(db_path))
            event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            metric_count = conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
            conn.close()
            self.assertEqual(event_count, 4)
            self.assertEqual(metric_count, 3)

    def test_coasting_zero_energy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_phase1.sqlite"
            summary = Phase1MemberDDemoRunner(db_path).run()
            coasting = [e for e in summary["energyEstimates"] if e["label"] == "coasting"][0]
            self.assertEqual(coasting["powerKw"], 0.0)
            self.assertEqual(coasting["energyKwh"], 0.0)


if __name__ == "__main__":
    unittest.main()
