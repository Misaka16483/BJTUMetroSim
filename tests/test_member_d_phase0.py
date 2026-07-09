from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.domain.power.phase0 import DEFAULT_POWER_SECTIONS, DefaultPowerState, generate_default_power_state
from app.domain.station.phase0 import (
    LINE9_STATIONS,
    DefaultStationState,
    StationMetricNames,
    compute_crowding_level,
    generate_default_station_state,
)
from app.domain.operations.phase0_member_d_demo import Phase0MemberDDemoRunner


class DefaultStationStateTests(unittest.TestCase):
    def test_default_values(self):
        state = DefaultStationState(station_id="S-GGZ", station_name="郭公庄")
        self.assertEqual(state.station_id, "S-GGZ")
        self.assertEqual(state.station_name, "郭公庄")
        self.assertEqual(state.direction, "UP")
        self.assertEqual(state.waiting_pax, 0)
        self.assertEqual(state.crowding_level, "LOW")
        self.assertEqual(state.platform_density_pax_per_m2, 0.0)

    def test_crowding_level_low(self):
        self.assertEqual(compute_crowding_level(0.0), "LOW")
        self.assertEqual(compute_crowding_level(1.0), "LOW")

    def test_crowding_level_medium(self):
        self.assertEqual(compute_crowding_level(1.2), "MEDIUM")
        self.assertEqual(compute_crowding_level(2.0), "MEDIUM")

    def test_crowding_level_high(self):
        self.assertEqual(compute_crowding_level(2.5), "HIGH")
        self.assertEqual(compute_crowding_level(3.5), "HIGH")

    def test_crowding_level_critical(self):
        self.assertEqual(compute_crowding_level(4.0), "CRITICAL")
        self.assertEqual(compute_crowding_level(6.0), "CRITICAL")

    def test_generate_default_station_state(self):
        state = generate_default_station_state("S-GGZ", "郭公庄")
        self.assertIsInstance(state, DefaultStationState)
        self.assertEqual(state.station_id, "S-GGZ")
        self.assertEqual(state.station_name, "郭公庄")

    def test_generate_with_custom_params(self):
        state = generate_default_station_state("S-TEST", "Test", direction="DOWN", platform_area_m2=80.0)
        self.assertEqual(state.direction, "DOWN")
        self.assertEqual(state.platform_area_m2, 80.0)


class DefaultPowerStateTests(unittest.TestCase):
    def test_default_values(self):
        state = DefaultPowerState(
            power_section_id="PWR-09-UP",
            name="Line 9 Up-track",
            max_traction_power_kw=1000.0,
            available_power_kw=1000.0,
            warning_power_kw=800.0,
        )
        self.assertEqual(state.power_section_id, "PWR-09-UP")
        self.assertEqual(state.voltage_level, "NORMAL")
        self.assertEqual(state.traction_limit_ratio, 1.0)
        self.assertEqual(state.requested_power_kw, 0.0)
        self.assertEqual(state.source, "DEFAULT")
        self.assertEqual(state.quality, "ESTIMATED")

    def test_line9_default_sections(self):
        self.assertEqual(len(DEFAULT_POWER_SECTIONS), 2)
        section_ids = {s.power_section_id for s in DEFAULT_POWER_SECTIONS}
        self.assertIn("PWR-09-UP", section_ids)
        self.assertIn("PWR-09-DOWN", section_ids)

    def test_generate_default_power_state(self):
        state = generate_default_power_state("PWR-TEST", "Test", 500.0, 500.0, 400.0)
        self.assertEqual(state.power_section_id, "PWR-TEST")
        self.assertEqual(state.max_traction_power_kw, 500.0)
        self.assertEqual(state.regen_absorb_limit_kw, 0.0)


class StationMetricNamesTests(unittest.TestCase):
    def test_metric_name_constants(self):
        names = StationMetricNames()
        all_names = [v for k, v in vars(names).items() if not k.startswith("_")]
        self.assertGreaterEqual(len(all_names), 10)
        for name in all_names:
            self.assertIsInstance(name, str)
            self.assertTrue(len(name) > 0)
            self.assertTrue(name.startswith("memberD."))


class Phase0MemberDDemoRunnerTests(unittest.TestCase):
    def test_demo_runner_creates_records(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_phase0.sqlite"
            runner = Phase0MemberDDemoRunner(db_path)
            summary = runner.run()
            self.assertGreater(summary["runId"], 0)
            self.assertTrue(db_path.exists())
            conn = sqlite3.connect(str(db_path))
            station_count = conn.execute("SELECT COUNT(*) FROM station_passenger_records").fetchone()[0]
            conn.close()
            self.assertEqual(station_count, 26)

    def test_demo_runner_output_shape(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_phase0.sqlite"
            summary = Phase0MemberDDemoRunner(db_path).run()
            self.assertEqual(summary["phase"], 0)
            self.assertEqual(summary["module"], "member-d-station-power")
            self.assertIn("runId", summary)
            self.assertIn("recordDb", summary)
            self.assertIn("stationStates", summary)
            self.assertIn("powerStates", summary)
            self.assertIn("metricNames", summary)
            self.assertIn("counts", summary)

    def test_demo_runner_all_stations(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_phase0.sqlite"
            summary = Phase0MemberDDemoRunner(db_path).run()
            self.assertEqual(len(summary["stationStates"]), 26)
            station_ids = {s["stationId"] for s in summary["stationStates"]}
            self.assertEqual(len(station_ids), 13)

    def test_demo_runner_power_sections(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_phase0.sqlite"
            summary = Phase0MemberDDemoRunner(db_path).run()
            self.assertEqual(len(summary["powerStates"]), 2)
            section_ids = {s["powerSectionId"] for s in summary["powerStates"]}
            self.assertIn("PWR-09-UP", section_ids)
            self.assertIn("PWR-09-DOWN", section_ids)

    def test_demo_runner_metric_names(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_phase0.sqlite"
            summary = Phase0MemberDDemoRunner(db_path).run()
            self.assertGreaterEqual(len(summary["metricNames"]), 10)


if __name__ == "__main__":
    unittest.main()
