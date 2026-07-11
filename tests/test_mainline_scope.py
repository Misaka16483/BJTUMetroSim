from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.core.engine import SimulationEngine


ROOT = Path(__file__).resolve().parents[1]


class MainlineScopeIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = SimulationEngine.load_from_files(
            scenario_path=ROOT / "data" / "scenarios" / "line9_single.json",
            line_map_path=ROOT / "data" / "cache" / "line_map.json",
            stations_csv_path=ROOT / "MetroDynamicsJavaDemo" / "data" / "stations.csv",
        )

    def test_scenario_enforces_versioned_mainline_scope(self) -> None:
        scope = self.engine.line_scope
        self.assertIsNotNone(scope)
        assert scope is not None
        self.assertEqual(scope.scope_id, "line9-mainline-v1")
        self.assertEqual(scope.line_id, "9")
        self.assertEqual(len(scope.segment_ids), 77)
        self.assertEqual(self.engine.path_planner.allowed_segment_ids, scope.segment_ids)

    def test_all_adjacent_station_paths_stay_inside_mainline_scope(self) -> None:
        scope = self.engine.line_scope
        assert scope is not None
        station_count = len(self.engine._station_list)

        for origin_idx in range(station_count - 1):
            forward = self.engine._path_plan_for_station_pair(origin_idx, origin_idx + 1)
            reverse = self.engine._path_plan_for_station_pair(origin_idx + 1, origin_idx)
            self.assertIsNotNone(forward)
            self.assertIsNotNone(reverse)
            assert forward is not None and reverse is not None
            self.assertTrue(set(forward.segment_ids) <= scope.segment_ids)
            self.assertTrue(set(reverse.segment_ids) <= scope.segment_ids)

    def test_vehicle_depot_segment_is_rejected(self) -> None:
        scope = self.engine.line_scope
        assert scope is not None
        depot_segment_id = 307
        self.assertNotIn(depot_segment_id, scope.segment_ids)
        with self.assertRaises(ValueError):
            self.engine.path_planner._find_segment_path(
                depot_segment_id,
                next(iter(scope.segment_ids)),
                "forward",
            )

    def test_copied_scenario_resolves_scope_from_line_data_directory(self) -> None:
        source = ROOT / "data" / "scenarios" / "line9_single.json"
        with tempfile.TemporaryDirectory() as temporary_dir:
            copied = Path(temporary_dir) / "copied-scenario.json"
            copied.write_text(
                json.dumps(json.loads(source.read_text(encoding="utf-8")), ensure_ascii=False),
                encoding="utf-8",
            )
            engine = SimulationEngine.load_from_files(
                scenario_path=copied,
                line_map_path=ROOT / "data" / "cache" / "line_map.json",
                stations_csv_path=ROOT / "MetroDynamicsJavaDemo" / "data" / "stations.csv",
            )
            self.assertIsNotNone(engine.line_scope)
            assert engine.line_scope is not None
            self.assertEqual(engine.line_scope.scope_id, "line9-mainline-v1")


if __name__ == "__main__":
    unittest.main()
