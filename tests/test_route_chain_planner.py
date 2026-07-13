from __future__ import annotations

import unittest

from app.domain.interlocking.route_catalog import RouteCatalog
from app.domain.interlocking.route_chain_planner import RouteChainPlanner


def _line_map() -> dict:
    return {
        "segments": [
            {"id": 1, "lengthM": 100.0, "endForwardSegId": 2, "endDivergingSegId": None},
            {"id": 2, "lengthM": 100.0, "endForwardSegId": 3, "endDivergingSegId": None},
            {"id": 3, "lengthM": 100.0, "endForwardSegId": None, "endDivergingSegId": None},
        ],
        "platforms": [
            {"id": 1, "segmentId": 1, "offsetM": 10.0},
            {"id": 2, "segmentId": 3, "offsetM": 90.0},
        ],
        "signals": [
            {"id": 10, "segmentId": 1},
            {"id": 11, "segmentId": 2},
            {"id": 12, "segmentId": 3},
        ],
        "axleSections": [
            {"id": 1, "segmentIds": [1, 2]},
            {"id": 2, "segmentIds": [2, 3]},
        ],
        "switches": [],
        "routes": [
            {
                "id": 1,
                "name": "MAIN-A",
                "type": "0x0001",
                "startSignalId": 10,
                "endSignalId": 11,
                "axleSectionIds": [1],
                "protectionSectionIds": [],
            },
            {
                "id": 2,
                "name": "MAIN-B",
                "type": "0x0001",
                "startSignalId": 11,
                "endSignalId": 12,
                "axleSectionIds": [2],
                "protectionSectionIds": [],
            },
        ],
        "speedRestrictions": [],
        "gradients": [],
    }


class RouteChainPlannerTests(unittest.TestCase):
    def test_builds_continuous_path_only_from_route_chain(self) -> None:
        line_map = _line_map()
        planner = RouteChainPlanner(line_map, RouteCatalog(line_map))

        result = planner.plan_between_platform_sets((1,), (2,), "forward")

        self.assertEqual(result.route_ids, ("1", "2"))
        self.assertEqual(result.path_plan.segment_ids, (1, 2, 3))
        self.assertAlmostEqual(result.path_plan.total_length_m, 280.0)

    def test_rejects_pair_without_route_table_chain(self) -> None:
        line_map = _line_map()
        line_map["routes"] = []
        planner = RouteChainPlanner(line_map, RouteCatalog(line_map))

        with self.assertRaisesRegex(ValueError, "NO_ROUTE_CHAIN"):
            planner.plan_between_platform_sets((1,), (2,), "forward")


if __name__ == "__main__":
    unittest.main()
