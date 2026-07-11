from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
