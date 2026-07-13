"""Per-train fixed-boundary movement authority for the CBTC/ZC simulation layer."""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Any

from app.domain.interlocking.route_catalog import RouteCatalog
from app.domain.interlocking.route_service import RouteService
from app.domain.interlocking.section_occupation import SectionOccupationService
from app.domain.line.services import PathPlan
from app.domain.vehicle.models import CommandSource, ControlCommand, VehicleConfig


JsonDict = dict[str, Any]


@dataclass(frozen=True)
class MovementAuthorityConfig:
    """Explicit course-simulation margins, not claimed operational parameters."""

    position_margin_m: float = 10.0
    brake_build_up_s: float = 1.0
    minimum_service_deceleration_mps2: float = 0.6
    minimum_emergency_deceleration_mps2: float = 0.8
    overspeed_tolerance_mps: float = 0.25


@dataclass(frozen=True)
class MovementAuthority:
    train_id: str
    end_position_m: float
    end_reason: str
    permitted_speed_mps: float
    service_brake_start_m: float
    emergency_brake_start_m: float
    route_chain_ids: tuple[str, ...]
    locked_route_ids: tuple[str, ...]

    def to_dict(self) -> JsonDict:
        return {
            "trainId": self.train_id,
            "endPositionM": round(self.end_position_m, 1),
            "endReason": self.end_reason,
            "permittedSpeedMps": round(self.permitted_speed_mps, 2),
            "serviceBrakeStartM": round(self.service_brake_start_m, 1),
            "emergencyBrakeStartM": round(self.emergency_brake_start_m, 1),
            "routeChainIds": list(self.route_chain_ids),
            "lockedRouteIds": list(self.locked_route_ids),
        }

@dataclass(frozen=True)
class TrainPosition:
    """Start-of-tick train footprint used for moving-block MA calculations."""

    train_id: str
    direction: str
    path_plan: PathPlan | None
    head_position_m: float
    length_m: float


class MovementAuthorityService:
    """Computes one train-specific authority from global interlocking state.

    This first stage uses route/axle-section boundaries.  It intentionally does
    not model a continuous leading-train rear position yet; that belongs to the
    later moving-block extension.
    """

    def __init__(
        self,
        line_map: JsonDict,
        catalog: RouteCatalog,
        route_service: RouteService,
        section_occupation: SectionOccupationService,
        config: MovementAuthorityConfig | None = None,
    ) -> None:
        self._catalog = catalog
        self._route_service = route_service
        self._section_occupation = section_occupation
        self.config = config or MovementAuthorityConfig()
        self._signals = {
            int(item["id"]): item
            for item in line_map.get("signals", [])
            if item.get("id") is not None and item.get("segmentId") is not None
        }
        self._sections_by_seg: dict[int, list[str]] = {}
        for section in line_map.get("axleSections", []):
            if section.get("id") is None:
                continue
            section_id = str(section["id"])
            for segment_id in section.get("segmentIds", []):
                self._sections_by_seg.setdefault(int(segment_id), []).append(section_id)

    def calculate(
        self,
        *,
        train_id: str,
        path_plan: PathPlan,
        route_chain_ids: tuple[str, ...],
        position_m: float,
        speed_mps: float,
        vehicle: VehicleConfig,
        other_trains: tuple[TrainPosition, ...] = (),
    ) -> MovementAuthority:
        position_m = min(max(0.0, position_m), path_plan.total_length_m)
        locked_route_ids, route_end, route_reason = self._locked_prefix(
            train_id, path_plan, route_chain_ids, position_m,
        )
        moving_end, precisely_located_train_ids = self._leading_train_boundary(
            train_id, path_plan, position_m, route_end, other_trains,
        )
        occupied_end = self._occupied_boundary(
            train_id, path_plan, position_m, route_end, precisely_located_train_ids,
        )
        boundaries = [
            (route_end, route_reason),
            *([(moving_end, "LEADING_TRAIN")] if moving_end is not None else []),
            *([(occupied_end, "OCCUPIED_SECTION")] if occupied_end is not None else []),
        ]
        end_position_m, end_reason = min(boundaries, key=lambda item: item[0])

        service_deceleration = max(
            self.config.minimum_service_deceleration_mps2,
            vehicle.max_service_brake_force_n / vehicle.mass_kg,
        )
        emergency_deceleration = max(
            self.config.minimum_emergency_deceleration_mps2,
            vehicle.emergency_brake_force_n / vehicle.mass_kg,
        )
        # The braking-distance model already contains the positional margin; do not subtract it twice.
        available_distance = max(0.0, end_position_m - position_m)
        permitted_speed = min(
            path_plan.speed_limit_at(position_m, vehicle.max_speed_mps),
            self._stoppable_speed(available_distance, service_deceleration),
        )
        service_start = end_position_m - self._braking_distance(speed_mps, service_deceleration)
        emergency_start = end_position_m - self._braking_distance(speed_mps, emergency_deceleration)
        return MovementAuthority(
            train_id=train_id,
            end_position_m=max(position_m, end_position_m),
            end_reason=end_reason,
            permitted_speed_mps=max(0.05, permitted_speed),
            service_brake_start_m=max(0.0, service_start),
            emergency_brake_start_m=max(0.0, emergency_start),
            route_chain_ids=route_chain_ids,
            locked_route_ids=locked_route_ids,
        )

    def remaining_route_ids(
        self,
        path_plan: PathPlan,
        route_chain_ids: tuple[str, ...],
        position_m: float,
    ) -> tuple[str, ...]:
        """Return route IDs whose end signals have not yet been passed."""
        remaining: list[str] = []
        for route_id in route_chain_ids:
            route = self._catalog.get(route_id)
            end_position_m = (
                self._signal_path_position(path_plan, route.end_signal_id, 0.0)
                if route is not None else None
            )
            if end_position_m is None or end_position_m > position_m + 1e-6:
                remaining.append(route_id)
        return tuple(remaining)

    def route_endpoint_position(
        self,
        path_plan: PathPlan,
        route_id: str,
        minimum_position_m: float = 0.0,
    ) -> float | None:
        route = self._catalog.get(route_id)
        if route is None:
            return None
        return self._signal_path_position(path_plan, route.end_signal_id, minimum_position_m)
    def supervise(
        self,
        command: ControlCommand,
        authority: MovementAuthority,
        *,
        position_m: float,
        speed_mps: float,
    ) -> ControlCommand:
        if position_m >= authority.emergency_brake_start_m and speed_mps > authority.permitted_speed_mps:
            return ControlCommand(
                train_id=command.train_id,
                emergency_brake=True,
                source=CommandSource.ATP_OVERRIDE,
            )
        if speed_mps > authority.permitted_speed_mps + self.config.overspeed_tolerance_mps:
            return ControlCommand(
                train_id=command.train_id,
                brake_percent=100.0,
                source=CommandSource.ATP_OVERRIDE,
            )
        return command

    def _locked_prefix(
        self,
        train_id: str,
        path_plan: PathPlan,
        route_chain_ids: tuple[str, ...],
        position_m: float,
    ) -> tuple[tuple[str, ...], float, str]:
        remaining_route_ids = self.remaining_route_ids(path_plan, route_chain_ids, position_m)

        if not remaining_route_ids:
            return (), path_plan.total_length_m, "STATION_STOP"

        locked: list[str] = []
        last_locked_index: int | None = None
        for index, route_id in enumerate(remaining_route_ids):
            if self._route_service.locked_by(route_id) == train_id:
                locked.append(route_id)
                last_locked_index = index

        if not locked:
            return (), position_m, "ROUTE_NOT_LOCKED"
        if last_locked_index == len(remaining_route_ids) - 1:
            return tuple(locked), path_plan.total_length_m, "STATION_STOP"

        route = self._catalog.get(locked[-1])
        if route is None:
            return tuple(locked), position_m, "ROUTE_NOT_FOUND"
        signal_position = self._signal_path_position(path_plan, route.end_signal_id, position_m)
        if signal_position is None:
            return tuple(locked), position_m, "UNMAPPED_ROUTE_ENDPOINT"
        return tuple(locked), signal_position, "ROUTE_ENDPOINT"

    def _occupied_boundary(
        self,
        train_id: str,
        path_plan: PathPlan,
        position_m: float,
        max_position_m: float,
        precisely_located_train_ids: frozenset[str],
    ) -> float | None:
        for constraint in path_plan.constraints:
            if constraint.path_end_m <= position_m or constraint.path_start_m >= max_position_m:
                continue
            for section_id in self._sections_by_seg.get(constraint.segment_id, []):
                occupants = self._section_occupation.axle_occupied_by(section_id)
                if any(
                    occupant != train_id and occupant not in precisely_located_train_ids
                    for occupant in occupants
                ):
                    return max(position_m, constraint.path_start_m - self.config.position_margin_m)
        return None

    def _leading_train_boundary(
        self,
        train_id: str,
        path_plan: PathPlan,
        position_m: float,
        max_position_m: float,
        other_trains: tuple[TrainPosition, ...],
    ) -> tuple[float | None, frozenset[str]]:
        boundary: float | None = None
        located_train_ids: set[str] = set()
        for other in other_trains:
            if (
                other.train_id == train_id
                or other.path_plan is None
                or other.direction != path_plan.direction
            ):
                continue
            head_coordinate = self._segment_offset_at(other.path_plan, other.head_position_m)
            rear_position_m = max(0.0, other.head_position_m - other.length_m)
            rear_coordinate = self._segment_offset_at(other.path_plan, rear_position_m)
            if head_coordinate is None or rear_coordinate is None:
                continue
            projected_head_m = self._path_position_for_segment_offset(
                path_plan, head_coordinate[0], head_coordinate[1], position_m,
            )
            projected_rear_m = self._path_position_for_segment_offset(
                path_plan, rear_coordinate[0], rear_coordinate[1], position_m,
            )
            if projected_head_m is None or projected_rear_m is None:
                continue
            located_train_ids.add(other.train_id)
            if projected_head_m <= position_m + 1e-6:
                continue
            candidate = max(position_m, projected_rear_m - self.config.position_margin_m)
            if candidate <= max_position_m and (boundary is None or candidate < boundary):
                boundary = candidate
        return boundary, frozenset(located_train_ids)

    @staticmethod
    def _segment_offset_at(path_plan: PathPlan, position_m: float) -> tuple[int, float] | None:
        constraint = path_plan.constraint_at(position_m)
        if constraint is None or constraint.path_end_m <= constraint.path_start_m:
            return None
        ratio = (position_m - constraint.path_start_m) / (
            constraint.path_end_m - constraint.path_start_m
        )
        offset_m = constraint.start_offset_m + (
            constraint.end_offset_m - constraint.start_offset_m
        ) * ratio
        return constraint.segment_id, offset_m

    @staticmethod
    def _path_position_for_segment_offset(
        path_plan: PathPlan,
        segment_id: int,
        offset_m: float,
        minimum_position_m: float,
    ) -> float | None:
        for constraint in path_plan.constraints:
            if constraint.segment_id != segment_id or constraint.path_end_m < minimum_position_m:
                continue
            low, high = sorted((constraint.start_offset_m, constraint.end_offset_m))
            if not low - 1e-6 <= offset_m <= high + 1e-6:
                continue
            if constraint.end_offset_m == constraint.start_offset_m:
                return constraint.path_start_m
            ratio = (offset_m - constraint.start_offset_m) / (
                constraint.end_offset_m - constraint.start_offset_m
            )
            return constraint.path_start_m + ratio * (constraint.path_end_m - constraint.path_start_m)
        return None
    def _signal_path_position(
        self,
        path_plan: PathPlan,
        signal_id: int,
        minimum_position_m: float,
    ) -> float | None:
        signal = self._signals.get(signal_id)
        if signal is None:
            return None
        segment_id = int(signal["segmentId"])
        offset_m = float(signal.get("offsetM") or 0.0)
        for constraint in path_plan.constraints:
            if constraint.segment_id != segment_id or constraint.path_end_m < minimum_position_m:
                continue
            low, high = sorted((constraint.start_offset_m, constraint.end_offset_m))
            if low - 1e-6 <= offset_m <= high + 1e-6:
                ratio = (offset_m - constraint.start_offset_m) / (
                    constraint.end_offset_m - constraint.start_offset_m
                ) if constraint.end_offset_m != constraint.start_offset_m else 0.0
                return constraint.path_start_m + ratio * (constraint.path_end_m - constraint.path_start_m)
        return None

    def _braking_distance(self, speed_mps: float, deceleration_mps2: float) -> float:
        return (
            speed_mps * self.config.brake_build_up_s
            + speed_mps * speed_mps / (2.0 * deceleration_mps2)
            + self.config.position_margin_m
        )

    def _stoppable_speed(self, distance_m: float, deceleration_mps2: float) -> float:
        build = self.config.brake_build_up_s
        return max(0.0, -deceleration_mps2 * build + sqrt(
            (deceleration_mps2 * build) ** 2 + 2.0 * deceleration_mps2 * distance_m
        ))
