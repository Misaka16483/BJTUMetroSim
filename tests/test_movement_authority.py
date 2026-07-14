from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.domain.control import ATOController, AtoConfig, AtoTarget
from app.domain.control.movement_authority import MovementAuthority, MovementAuthorityService, TrainPosition
from app.domain.interlocking.models import RouteRequest
from app.domain.interlocking.route_catalog import RouteCatalog
from app.domain.interlocking.route_service import RouteService
from app.domain.interlocking.rule_engine import InterlockingRuleEngine
from app.domain.interlocking.section_occupation import SectionOccupationService
from app.domain.interlocking.switch_lock import SwitchLockService
from app.domain.line.services import PathPlan, PathSegmentConstraint, TrackQueryService
from app.domain.vehicle.models import CommandSource, ControlCommand, TrainState, VehicleConfig


def _line_map() -> dict:
    return {
        "segments": [
            {"id": 1, "lengthM": 100.0, "endForwardSegId": 2},
            {"id": 2, "lengthM": 100.0, "endForwardSegId": 3},
            {"id": 3, "lengthM": 100.0, "endForwardSegId": None},
        ],
        "signals": [
            {"id": 10, "segmentId": 1, "offsetM": 0.0},
            {"id": 11, "segmentId": 2, "offsetM": 0.0},
            {"id": 12, "segmentId": 3, "offsetM": 100.0},
        ],
        "axleSections": [
            {"id": 1, "segmentIds": [1]},
            {"id": 2, "segmentIds": [2]},
            {"id": 3, "segmentIds": [3]},
        ],
        "switches": [],
        "routes": [
            {"id": 1, "name": "R1", "type": "0x0001", "startSignalId": 10, "endSignalId": 11,
             "axleSectionIds": [1, 2], "protectionSectionIds": []},
            {"id": 2, "name": "R2", "type": "0x0001", "startSignalId": 11, "endSignalId": 12,
             "axleSectionIds": [3], "protectionSectionIds": []},
        ],
    }


def _path_plan() -> PathPlan:
    constraints = tuple(
        PathSegmentConstraint(
            segment_id=segment_id, start_offset_m=0.0, end_offset_m=100.0,
            path_start_m=index * 100.0, path_end_m=(index + 1) * 100.0,
            speed_limit_mps=20.0, grade_ratio=0.0,
        )
        for index, segment_id in enumerate((1, 2, 3))
    )
    return PathPlan(1, 2, "forward", (1, 2, 3), constraints, 300.0, 1, 0.0, 3, 100.0)


class MovementAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.line_map = _line_map()
        catalog = RouteCatalog(self.line_map)
        self.occupation = SectionOccupationService(self.line_map)
        switches = SwitchLockService([])
        self.routes = RouteService(catalog, InterlockingRuleEngine(catalog, self.occupation, switches), self.occupation, switches)
        self.service = MovementAuthorityService(self.line_map, catalog, self.routes, self.occupation)
        self.path = _path_plan()
        self.vehicle = VehicleConfig(train_id="T1")

    def _lock_chain(self) -> None:
        for route_id in ("1", "2"):
            self.assertTrue(self.routes.request(RouteRequest("req-" + route_id, route_id, "T1")).accepted)

    def test_without_locked_route_authority_ends_at_current_position(self) -> None:
        authority = self.service.calculate(train_id="T1", path_plan=self.path, route_chain_ids=("1", "2"), position_m=25.0, speed_mps=0.0, vehicle=self.vehicle)
        self.assertEqual(authority.end_reason, "ROUTE_NOT_LOCKED")
        self.assertEqual(authority.end_position_m, 25.0)
        self.assertLessEqual(authority.permitted_speed_mps, 0.05)

    def test_complete_locked_chain_authorizes_station_stop(self) -> None:
        self._lock_chain()
        authority = self.service.calculate(train_id="T1", path_plan=self.path, route_chain_ids=("1", "2"), position_m=0.0, speed_mps=0.0, vehicle=self.vehicle)
        self.assertEqual(authority.end_reason, "STATION_STOP")
        self.assertEqual(authority.end_position_m, 300.0)
        self.assertEqual(authority.locked_route_ids, ("1", "2"))

    def test_later_locked_route_cannot_bridge_an_unlocked_first_route(self) -> None:
        self.assertTrue(
            self.routes.request(RouteRequest("req-2", "2", "T1")).accepted
        )

        authority = self.service.calculate(
            train_id="T1",
            path_plan=self.path,
            route_chain_ids=("1", "2"),
            position_m=0.0,
            speed_mps=0.0,
            vehicle=self.vehicle,
        )

        self.assertEqual(authority.end_reason, "ROUTE_NOT_LOCKED")
        self.assertEqual(authority.end_position_m, 0.0)
        self.assertEqual(authority.locked_route_ids, ())

    def test_station_authority_keeps_a_usable_creep_speed_near_stop(self) -> None:
        self._lock_chain()
        authority = self.service.calculate(
            train_id="T1", path_plan=self.path, route_chain_ids=("1", "2"),
            position_m=295.0, speed_mps=0.0, vehicle=self.vehicle,
        )
        self.assertEqual(authority.end_reason, "STATION_STOP")
        self.assertGreater(authority.permitted_speed_mps, 0.05)
    def test_other_train_occupied_section_limits_authority_before_section(self) -> None:
        self._lock_chain()
        self.occupation.update([SimpleNamespace(train_id="T2", seg_id=3, offset_m=20.0, length_m=10.0, direction="FORWARD")], TrackQueryService(self.line_map))
        authority = self.service.calculate(train_id="T1", path_plan=self.path, route_chain_ids=("1", "2"), position_m=0.0, speed_mps=0.0, vehicle=self.vehicle)
        self.assertEqual(authority.end_reason, "OCCUPIED_SECTION")
        self.assertEqual(authority.end_position_m, 190.0)

    def test_leading_train_tail_creates_a_continuous_moving_block_boundary(self) -> None:
        self._lock_chain()
        self.occupation.update(
            [SimpleNamespace(train_id="T2", seg_id=3, offset_m=50.0, length_m=80.0, direction="FORWARD")],
            TrackQueryService(self.line_map),
        )
        authority = self.service.calculate(
            train_id="T1", path_plan=self.path, route_chain_ids=("1", "2"),
            position_m=0.0, speed_mps=0.0, vehicle=self.vehicle,
            other_trains=(TrainPosition("T2", "forward", self.path, 250.0, 80.0),),
        )

        self.assertEqual(authority.end_reason, "LEADING_TRAIN")
        self.assertEqual(authority.end_position_m, 160.0)
    def test_atp_overrides_an_overspeed_command(self) -> None:
        authority = MovementAuthority("T1", 100.0, "ROUTE_ENDPOINT", 5.0, 75.0, 80.0, ("1",), ("1",))
        command = self.service.supervise(ControlCommand("T1", traction_percent=30.0, source=CommandSource.ATO), authority, position_m=82.0, speed_mps=8.0)
        self.assertTrue(command.emergency_brake)
        self.assertEqual(command.source, CommandSource.ATP_OVERRIDE)

    def test_atp_tolerates_small_tracking_excess_near_station_stop(self) -> None:
        authority = MovementAuthority(
            "T1", 100.0, "STATION_STOP", 0.66, 80.0, 89.0, ("1",), ("1",)
        )
        requested = ControlCommand(
            "T1", brake_percent=31.0, source=CommandSource.ATO
        )

        command = self.service.supervise(
            requested, authority, position_m=99.2, speed_mps=0.69
        )

        self.assertEqual(command, requested)

    def test_atp_uses_full_service_before_emergency_overspeed_threshold(self) -> None:
        authority = MovementAuthority(
            "T1", 100.0, "STATION_STOP", 0.60, 80.0, 89.0, ("1",), ("1",)
        )

        command = self.service.supervise(
            ControlCommand("T1", brake_percent=43.0, source=CommandSource.ATO),
            authority,
            position_m=99.3,
            speed_mps=0.87,
        )

        self.assertFalse(command.emergency_brake)
        self.assertEqual(command.brake_percent, 100.0)
        self.assertEqual(command.source, CommandSource.ATP_OVERRIDE)

    def test_ato_respects_authority_target_even_when_path_continues(self) -> None:
        controller = ATOController(AtoConfig(use_dynamic_programming_profile=False))
        command = controller.decide(TrainState("T1", position_m=195.0, speed_mps=6.0, sim_time_s=10.0), AtoTarget(200.0, 10.0, path_plan=self.path))
        self.assertGreater(command.brake_percent, 0.0)


if __name__ == "__main__":
    unittest.main()
