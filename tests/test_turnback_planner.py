"""Acceptance tests for task-aware route selection and terminal turnback."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from app.domain.interlocking.route_catalog import RouteCatalog
from app.domain.interlocking.route_chain_planner import (
    MainRouteFirstPolicy,
    OperationIntent,
    RouteChainCandidate,
    RouteChainPlanner,
)
from app.domain.interlocking.terminal_turnback_config import (
    TerminalTurnbackConfig,
    TurnbackPhaseConfig,
)


LINE_MAP_PATH = Path(__file__).parents[1] / "data" / "cache" / "line_map.json"


def _policy_catalog() -> RouteCatalog:
    return RouteCatalog(
        {
            "routes": [
                {"id": 2, "type": "0x0001"},
                {"id": 3, "type": "0x0001"},
                {"id": 10, "type": "0x0001"},
                {"id": 20, "type": "0x0002"},
            ]
        }
    )


class MainRouteFirstPolicyTests(unittest.TestCase):
    def test_prefers_main_routes_then_fewer_routes_then_stable_ids(self) -> None:
        policy = MainRouteFirstPolicy()
        catalog = _policy_catalog()

        main = RouteChainCandidate(("10", "3"), ())
        branch = RouteChainCandidate(("20",), ())
        short_main = RouteChainCandidate(("10",), ())
        stable_low = RouteChainCandidate(("2",), ())

        self.assertLess(policy.sort_key(main, catalog), policy.sort_key(branch, catalog))
        self.assertLess(policy.sort_key(short_main, catalog), policy.sort_key(main, catalog))
        self.assertLess(policy.sort_key(stable_low, catalog), policy.sort_key(short_main, catalog))


class TaskAwareRouteSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with LINE_MAP_PATH.open(encoding="utf-8") as source:
            line_map = json.load(source)
        cls.planner = RouteChainPlanner(line_map, RouteCatalog(line_map))

    def test_normal_intermediate_service_keeps_actual_origin_platform(self) -> None:
        plan = self.planner.plan_operation(
            intent=OperationIntent.NORMAL,
            origin_platform_id=14,
            destination_platform_ids=(15, 16),
            direction="forward",
        )

        # 六里桥东 14 号站台（S103）继续向下一站；规划器没有退回
        # 到同站的 13 号站台（S88）重新挑选候选。
        self.assertEqual(plan.path_plan.origin_platform_id, 14)
        self.assertEqual(plan.path_plan.segment_ids[0], 103)
        self.assertEqual(plan.route_ids, ("51", "53", "55"))

    def test_normal_service_excludes_floating_platform_candidates(self) -> None:
        plan = self.planner.plan_operation(
            intent=OperationIntent.NORMAL,
            origin_platform_id=14,
            destination_platform_ids=(16, 27),  # 27 is a non-passenger/floating platform
            direction="forward",
        )

        self.assertEqual(plan.path_plan.destination_platform_id, 16)

        with self.assertRaisesRegex(ValueError, "NORMAL_OPERATION_REQUIRES_PASSENGER_PLATFORM"):
            self.planner.plan_operation(
                intent=OperationIntent.NORMAL,
                origin_platform_id=27,
                destination_platform_ids=(16,),
                direction="forward",
            )


class TerminalTurnbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with LINE_MAP_PATH.open(encoding="utf-8") as source:
            line_map = json.load(source)
        cls.planner = RouteChainPlanner(line_map, RouteCatalog(line_map))

    def test_both_terminals_produce_legal_multiphase_turnbacks(self) -> None:
        ggz = self.planner.plan_operation(
            intent=OperationIntent.TURNBACK,
            origin_platform_id=1,
            terminal_id="GGZ",
        )
        gtg = self.planner.plan_operation(
            intent=OperationIntent.TURNBACK,
            origin_platform_id=26,
            terminal_id="GTG",
        )

        self.assertEqual(ggz.turning_point_segment_id, 22)
        self.assertEqual([(p.direction, p.route_ids) for p in ggz.phases], [
            ("forward", ("10",)),
            ("backward", ("13", "12")),
        ])
        self.assertEqual(ggz.phases[0].segment_ids[-1], ggz.phases[1].segment_ids[0])
        self.assertEqual(ggz.phases[1].signal_ids, ((11, 10), (10, 57)))
        self.assertEqual(ggz.phases[0].route_switch_positions, (("10", (("12", "NORMAL"), ("8", "NORMAL"), ("9", "NORMAL"))),))
        self.assertEqual(ggz.phases[1].route_switch_positions, (
            ("13", (("12", "NORMAL"),)),
            ("12", (("10", "REVERSE"), ("9", "REVERSE"))),
        ))
        self.assertEqual(gtg.turning_point_segment_id, 213)
        self.assertEqual([(p.direction, p.route_ids) for p in gtg.phases], [
            ("forward", ("90",)),
            ("backward", ("87",)),
        ])
        self.assertEqual(gtg.phases[0].segment_ids[-1], gtg.phases[1].segment_ids[0])
        self.assertEqual(gtg.phases[0].route_switch_positions, (("90", (("38", "REVERSE"), ("39", "REVERSE"))),))

    def test_missing_or_invalid_configuration_fails_explicitly(self) -> None:
        with self.assertRaisesRegex(ValueError, "TURNBACK_CONFIG_NOT_FOUND"):
            self.planner.plan_turnback("GGZ", 2)

        invalid = TerminalTurnbackConfig(
            terminal_id="TEST",
            origin_platform_id=1,
            final_platform_id=2,
            turning_point_segment_id=22,
            phases=(
                TurnbackPhaseConfig(direction="forward", route_ids=("10",)),
                TurnbackPhaseConfig(direction="backward", route_ids=("missing",)),
            ),
        )
        with self.assertRaisesRegex(ValueError, "TURNBACK_CONFIG_INVALID: unknown route missing"):
            self.planner.plan_turnback("TEST", 1, (invalid,))

    def test_rejects_reverse_traversal_of_an_opposite_direction_route(self) -> None:
        unsafe = TerminalTurnbackConfig(
            terminal_id="TEST",
            origin_platform_id=1,
            final_platform_id=2,
            turning_point_segment_id=22,
            phases=(
                TurnbackPhaseConfig(direction="forward", route_ids=("10",)),
                TurnbackPhaseConfig(direction="backward", route_ids=("23",)),
            ),
        )

        with self.assertRaisesRegex(
            ValueError, "TURNBACK_CONFIG_INVALID: route 23 has no backward Seg path"
        ):
            self.planner.plan_turnback("TEST", 1, (unsafe,))


if __name__ == "__main__":
    unittest.main()
