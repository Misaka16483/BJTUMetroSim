from __future__ import annotations

from dataclasses import dataclass
import heapq
import json
from pathlib import Path
from typing import Any


JsonDict = dict[str, Any]
DEFAULT_SPEED_LIMIT_MPS = 22.22


@dataclass(frozen=True)
class PathSegmentConstraint:
    segment_id: int
    start_offset_m: float
    end_offset_m: float
    path_start_m: float
    path_end_m: float
    speed_limit_mps: float
    grade_ratio: float
    gradient_raw: float | None = None
    speed_restriction_id: int | None = None
    gradient_id: int | None = None
    direction: str = "forward"

    @property
    def length_m(self) -> float:
        return self.path_end_m - self.path_start_m

    @property
    def midpoint_m(self) -> float:
        return (self.path_start_m + self.path_end_m) / 2.0


@dataclass(frozen=True)
class PathPlan:
    origin_platform_id: int
    destination_platform_id: int
    direction: str
    segment_ids: tuple[int, ...]
    constraints: tuple[PathSegmentConstraint, ...]
    total_length_m: float
    start_segment_id: int
    start_offset_m: float
    end_segment_id: int
    end_offset_m: float

    @property
    def target_position_m(self) -> float:
        return self.total_length_m

    def constraint_at(self, position_m: float) -> PathSegmentConstraint | None:
        if not self.constraints:
            return None
        bounded = min(max(0.0, position_m), self.total_length_m)
        for constraint in self.constraints:
            if constraint.path_start_m <= bounded <= constraint.path_end_m + 1e-9:
                return constraint
        return self.constraints[-1]

    def speed_limit_at(self, position_m: float, default_mps: float = DEFAULT_SPEED_LIMIT_MPS) -> float:
        constraint = self.constraint_at(position_m)
        if constraint is None:
            return default_mps
        return min(default_mps, constraint.speed_limit_mps)

    def grade_ratio_at(self, position_m: float) -> float:
        constraint = self.constraint_at(position_m)
        return 0.0 if constraint is None else constraint.grade_ratio

    def cache_key(self) -> tuple[object, ...]:
        return (
            self.origin_platform_id,
            self.destination_platform_id,
            self.direction,
            round(self.total_length_m, 3),
            self.segment_ids,
            tuple(
                (
                    constraint.segment_id,
                    round(constraint.path_start_m, 3),
                    round(constraint.path_end_m, 3),
                    round(constraint.speed_limit_mps, 3),
                    round(constraint.grade_ratio, 7),
                )
                for constraint in self.constraints
            ),
        )


@dataclass(frozen=True)
class LineScope:
    """可追溯的线路运行范围。

    全量 line_map 仍保留正线、车辆段和库线数据；LineScope 只限制特定
    运行场景允许访问的 Seg，不破坏原始电子地图。
    """

    schema_version: str
    scope_id: str
    line_id: str
    segment_ids: frozenset[int]
    source: str = ""
    description: str = ""

    @classmethod
    def from_dict(cls, data: JsonDict) -> "LineScope":
        segment_ids = frozenset(int(item) for item in data.get("segmentIds", []))
        if not segment_ids:
            raise ValueError("line scope must contain at least one segment id")

        pair_segment_ids = {
            int(segment_id)
            for pair in data.get("stationPairPaths", [])
            for segment_id in pair.get("segmentIds", [])
        }
        if pair_segment_ids and pair_segment_ids != set(segment_ids):
            missing = sorted(pair_segment_ids - set(segment_ids))
            unused = sorted(set(segment_ids) - pair_segment_ids)
            raise ValueError(
                "line scope segmentIds must equal the union of stationPairPaths: "
                f"missing={missing}, unused={unused}"
            )

        return cls(
            schema_version=str(data.get("schemaVersion", "line-scope.v1")),
            scope_id=str(data["scopeId"]),
            line_id=str(data["lineId"]),
            segment_ids=segment_ids,
            source=str(data.get("source", "")),
            description=str(data.get("description", "")),
        )

    @classmethod
    def load(cls, path: str | Path) -> "LineScope":
        with Path(path).open("r", encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))


@dataclass(frozen=True)
class _PathPortion:
    segment_id: int
    start_offset_m: float
    end_offset_m: float
    path_start_m: float
    path_end_m: float
    direction: str

    @property
    def length_m(self) -> float:
        return self.path_end_m - self.path_start_m

    def contains_offset(self, offset_m: float) -> bool:
        low, high = sorted((self.start_offset_m, self.end_offset_m))
        return low - 1e-9 <= offset_m <= high + 1e-9

    def offset_at(self, path_position_m: float) -> float:
        if self.length_m <= 1e-9:
            return self.end_offset_m
        ratio = (path_position_m - self.path_start_m) / self.length_m
        return self.start_offset_m + (self.end_offset_m - self.start_offset_m) * ratio

    def position_at_offset(self, offset_m: float) -> float:
        distance_from_start_m = abs(offset_m - self.start_offset_m)
        return self.path_start_m + distance_from_start_m


@dataclass(frozen=True)
class _GradientRange:
    path_start_m: float
    path_end_m: float
    grade_ratio: float
    gradient_raw: float | None
    gradient_id: int | None


class LineMapRepository:
    def __init__(self, cache_path: str | Path) -> None:
        self.cache_path = Path(cache_path)

    def load(self) -> JsonDict:
        with self.cache_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def save(self, line_map: JsonDict) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self.cache_path.open("w", encoding="utf-8") as handle:
            json.dump(line_map, handle, ensure_ascii=False, indent=2)


class TrackQueryService:
    def __init__(self, line_map: JsonDict) -> None:
        self.line_map = line_map
        self.segments = {
            int(item["id"]): item for item in line_map.get("segments", []) if item.get("id") is not None
        }
        self.signals_by_seg = self._group_by_seg(line_map.get("signals", []), "segmentId")
        self.platforms_by_seg = self._group_by_seg(line_map.get("platforms", []), "segmentId")
        self.speed_by_seg = self._group_by_seg(line_map.get("speedRestrictions", []), "segmentId")
        self.gradients = line_map.get("gradients", [])

    @staticmethod
    def _group_by_seg(items: list[JsonDict], key: str) -> dict[int, list[JsonDict]]:
        grouped: dict[int, list[JsonDict]] = {}
        for item in items:
            seg_id = item.get(key)
            if seg_id is None:
                continue
            grouped.setdefault(int(seg_id), []).append(item)
        return grouped

    def get_segment(self, seg_id: int) -> JsonDict | None:
        return self.segments.get(int(seg_id))

    def get_next_segments(self, seg_id: int, direction: str = "forward") -> list[JsonDict]:
        segment = self.get_segment(seg_id)
        if not segment:
            return []
        keys = (
            ["endForwardSegId", "endDivergingSegId"]
            if direction == "forward"
            else ["startForwardSegId", "startDivergingSegId"]
        )
        next_ids = [segment.get(key) for key in keys]
        return [self.segments[int(next_id)] for next_id in next_ids if next_id in self.segments]

    def get_speed_limit(self, seg_id: int, offset_m: float) -> JsonDict | None:
        candidates = self.speed_by_seg.get(int(seg_id), [])
        covering = [
            item
            for item in candidates
            if self._offset_in_range(offset_m, item.get("startOffsetM"), item.get("endOffsetM"))
        ]
        if covering:
            return min(covering, key=lambda item: item.get("speedLimitMps") or 9999)
        if candidates:
            return min(
                candidates,
                key=lambda item: abs((item.get("startOffsetM") or 0.0) - offset_m),
            )
        return None

    def get_gradient(self, seg_id: int, offset_m: float) -> JsonDict | None:
        matches = [
            item
            for item in self.gradients
            if item.get("startSegmentId") == int(seg_id) or item.get("endSegmentId") == int(seg_id)
        ]
        if not matches:
            return None
        return min(
            matches,
            key=lambda item: abs((item.get("startOffsetM") or item.get("endOffsetM") or 0.0) - offset_m),
        )

    def get_nearest_platform(
        self,
        seg_id: int,
        offset_m: float,
        direction: str = "forward",
    ) -> JsonDict | None:
        platforms = self.platforms_by_seg.get(int(seg_id), [])
        if not platforms:
            return None
        if direction == "forward":
            ahead = [item for item in platforms if (item.get("offsetM") or 0.0) >= offset_m]
        else:
            ahead = [item for item in platforms if (item.get("offsetM") or 0.0) <= offset_m]
        candidates = ahead or platforms
        return min(candidates, key=lambda item: abs((item.get("offsetM") or 0.0) - offset_m))

    def get_next_signal(
        self,
        seg_id: int,
        offset_m: float,
        direction: str = "forward",
    ) -> JsonDict | None:
        signals = self.signals_by_seg.get(int(seg_id), [])
        if not signals:
            return None
        if direction == "forward":
            candidates = [item for item in signals if (item.get("offsetM") or 0.0) >= offset_m]
        else:
            candidates = [item for item in signals if (item.get("offsetM") or 0.0) <= offset_m]
        if not candidates:
            return None
        reverse = direction != "forward"
        return sorted(candidates, key=lambda item: item.get("offsetM") or 0.0, reverse=reverse)[0]

    @staticmethod
    def _offset_in_range(offset: float, start: float | None, end: float | None) -> bool:
        if start is None and end is None:
            return True
        if start is None:
            return offset <= float(end)
        if end is None:
            return offset >= float(start)
        low, high = sorted([float(start), float(end)])
        return low <= offset <= high


class PathTrackQuery:
    """Route-aware view of the immutable track topology.

    A train's approved route is represented as an ordered Seg sequence.  This
    adapter keeps static attributes in ``TrackQueryService`` while resolving
    forward/backward neighbours only within that approved sequence.  It lets
    consumers such as section-occupation detection follow the train's actual
    path through a locked turnout instead of guessing from the global graph.
    """

    def __init__(self, track: TrackQueryService, segment_ids: list[int] | tuple[int, ...]) -> None:
        self.track = track
        self.segment_ids = tuple(int(segment_id) for segment_id in segment_ids)
        if len(set(self.segment_ids)) != len(self.segment_ids):
            raise ValueError("path segment IDs must be unique")
        self._index_by_segment = {
            segment_id: index for index, segment_id in enumerate(self.segment_ids)
        }

    def get_segment(self, seg_id: int) -> JsonDict | None:
        return self.track.get_segment(seg_id)

    def get_next_segments(self, seg_id: int, direction: str = "forward") -> list[JsonDict]:
        index = self._index_by_segment.get(int(seg_id))
        if index is None:
            return []
        next_index = index + 1 if direction == "forward" else index - 1
        if next_index < 0 or next_index >= len(self.segment_ids):
            return []
        segment = self.track.get_segment(self.segment_ids[next_index])
        return [segment] if segment is not None else []


class PathPlanner:
    def __init__(
        self,
        line_map: JsonDict,
        default_speed_limit_mps: float = DEFAULT_SPEED_LIMIT_MPS,
        allowed_segment_ids: set[int] | frozenset[int] | None = None,
    ) -> None:
        self.line_map = line_map
        self.default_speed_limit_mps = default_speed_limit_mps
        self.track = TrackQueryService(line_map)
        self.allowed_segment_ids = (
            frozenset(int(item) for item in allowed_segment_ids)
            if allowed_segment_ids is not None
            else None
        )
        if self.allowed_segment_ids is not None:
            unknown = sorted(self.allowed_segment_ids - set(self.track.segments))
            if unknown:
                raise ValueError(f"line scope references unknown segment ids: {unknown}")
        self.platforms = {
            int(item["id"]): item for item in line_map.get("platforms", []) if item.get("id") is not None
        }

    def plan_between_platforms(
        self,
        origin_platform_id: int,
        destination_platform_id: int,
        direction: str | None = None,
    ) -> PathPlan:
        if direction is not None:
            return self._plan_between_platforms(origin_platform_id, destination_platform_id, direction)

        plans: list[PathPlan] = []
        for candidate_direction in ("forward", "backward"):
            try:
                plans.append(self._plan_between_platforms(origin_platform_id, destination_platform_id, candidate_direction))
            except ValueError:
                continue
        if not plans:
            raise ValueError(f"no segment path from platform {origin_platform_id} to {destination_platform_id}")
        return min(plans, key=lambda item: item.total_length_m)

    def plan_for_segment_sequence(
        self,
        origin_platform_id: int,
        destination_platform_id: int,
        segment_ids: list[int],
        direction: str,
        train_length_m: float | None = None,
    ) -> PathPlan:
        """Build a path plan from a caller-authorized ordered Seg sequence.

        Unlike :meth:`plan_between_platforms`, this method never searches the
        topology or chooses a shortest path.  It is used when an interlocking
        route chain has already decided the physical path.
        """
        if direction not in {"forward", "backward"}:
            raise ValueError("direction must be 'forward' or 'backward'")
        if not segment_ids:
            raise ValueError("authorized segment sequence is empty")

        origin = self._platform(origin_platform_id)
        destination = self._platform(destination_platform_id)
        start_segment_id = int(origin["segmentId"])
        end_segment_id = int(destination["segmentId"])
        if segment_ids[0] != start_segment_id or segment_ids[-1] != end_segment_id:
            raise ValueError("authorized sequence does not start/end at the requested platforms")

        for current, following in zip(segment_ids, segment_ids[1:]):
            next_ids = {
                int(segment["id"])
                for segment in self.track.get_next_segments(current, direction)
            }
            if following not in next_ids:
                raise ValueError(f"non-contiguous authorized sequence: {current} -> {following}")

        start_offset_m = self._platform_stop_offset_m(
            origin, direction, train_length_m
        )
        end_offset_m = self._platform_stop_offset_m(
            destination, direction, train_length_m
        )
        portions = self._build_path_portions(segment_ids, start_offset_m, end_offset_m, direction)
        total_length_m = portions[-1].path_end_m if portions else 0.0
        if total_length_m <= 0:
            raise ValueError("authorized platform path has no travel distance")

        return PathPlan(
            origin_platform_id=origin_platform_id,
            destination_platform_id=destination_platform_id,
            direction=direction,
            segment_ids=tuple(segment_ids),
            constraints=self._build_constraints(portions, direction),
            total_length_m=round(total_length_m, 6),
            start_segment_id=start_segment_id,
            start_offset_m=start_offset_m,
            end_segment_id=end_segment_id,
            end_offset_m=end_offset_m,
        )

    def _platform_stop_offset_m(
        self,
        platform: JsonDict,
        direction: str,
        train_length_m: float | None,
    ) -> float:
        """Return the head position that centres a train in a platform Seg.

        The source platform table associates a platform with the start of its
        129 m Seg, rather than providing a stopping-board coordinate.  Without
        a train length, retain that raw coordinate for compatibility.
        """
        raw_offset_m = float(platform.get("offsetM") or 0.0)
        if train_length_m is None:
            return raw_offset_m
        segment_length_m = self._segment_length_m(int(platform["segmentId"]))
        bounded_length_m = min(max(float(train_length_m), 0.0), segment_length_m)
        end_clearance_m = (segment_length_m - bounded_length_m) / 2.0
        if direction == "forward":
            return end_clearance_m + bounded_length_m
        return end_clearance_m

    def _plan_between_platforms(
        self,
        origin_platform_id: int,
        destination_platform_id: int,
        direction: str,
    ) -> PathPlan:
        if direction not in {"forward", "backward"}:
            raise ValueError("direction must be 'forward' or 'backward'")

        origin = self._platform(origin_platform_id)
        destination = self._platform(destination_platform_id)
        start_segment_id = int(origin["segmentId"])
        end_segment_id = int(destination["segmentId"])
        start_offset_m = float(origin.get("offsetM") or 0.0)
        end_offset_m = float(destination.get("offsetM") or 0.0)

        segment_ids = self._find_segment_path(start_segment_id, end_segment_id, direction)
        portions = self._build_path_portions(segment_ids, start_offset_m, end_offset_m, direction)
        total_length_m = portions[-1].path_end_m if portions else 0.0
        if total_length_m <= 0:
            raise ValueError(f"platform path {origin_platform_id}->{destination_platform_id} has no travel distance")

        constraints = self._build_constraints(portions, direction)
        return PathPlan(
            origin_platform_id=origin_platform_id,
            destination_platform_id=destination_platform_id,
            direction=direction,
            segment_ids=tuple(segment_ids),
            constraints=constraints,
            total_length_m=round(total_length_m, 6),
            start_segment_id=start_segment_id,
            start_offset_m=start_offset_m,
            end_segment_id=end_segment_id,
            end_offset_m=end_offset_m,
        )

    def _platform(self, platform_id: int) -> JsonDict:
        platform = self.platforms.get(int(platform_id))
        if platform is None:
            raise ValueError(f"unknown platform id {platform_id}")
        if platform.get("segmentId") is None:
            raise ValueError(f"platform {platform_id} has no segment id")
        if (
            self.allowed_segment_ids is not None
            and int(platform["segmentId"]) not in self.allowed_segment_ids
        ):
            raise ValueError(
                f"platform {platform_id} segment {platform['segmentId']} is outside the active line scope"
            )
        return platform

    def _find_segment_path(self, start_segment_id: int, end_segment_id: int, direction: str) -> list[int]:
        if self.allowed_segment_ids is not None:
            if start_segment_id not in self.allowed_segment_ids:
                raise ValueError(f"start segment {start_segment_id} is outside the active line scope")
            if end_segment_id not in self.allowed_segment_ids:
                raise ValueError(f"end segment {end_segment_id} is outside the active line scope")
        if start_segment_id == end_segment_id:
            return [start_segment_id]
        queue: list[tuple[float, int, tuple[int, ...]]] = [(0.0, start_segment_id, (start_segment_id,))]
        best_cost: dict[int, float] = {start_segment_id: 0.0}
        while queue:
            cost, segment_id, path = heapq.heappop(queue)
            if segment_id == end_segment_id:
                return list(path)
            if cost > best_cost.get(segment_id, float("inf")) + 1e-9:
                continue
            for next_segment in self.track.get_next_segments(segment_id, direction):
                next_segment_id = int(next_segment["id"])
                if (
                    self.allowed_segment_ids is not None
                    and next_segment_id not in self.allowed_segment_ids
                ):
                    continue
                if next_segment_id in path:
                    continue
                edge_cost = self._segment_length_m(segment_id)
                next_cost = cost + edge_cost
                if next_cost >= best_cost.get(next_segment_id, float("inf")):
                    continue
                best_cost[next_segment_id] = next_cost
                heapq.heappush(queue, (next_cost, next_segment_id, path + (next_segment_id,)))
        raise ValueError(f"no {direction} segment path from {start_segment_id} to {end_segment_id}")

    def _build_path_portions(
        self,
        segment_ids: list[int],
        start_offset_m: float,
        end_offset_m: float,
        direction: str,
    ) -> tuple[_PathPortion, ...]:
        portions: list[_PathPortion] = []
        path_position_m = 0.0
        for index, segment_id in enumerate(segment_ids):
            segment_length_m = self._segment_length_m(segment_id)
            if len(segment_ids) == 1:
                segment_start_offset_m = start_offset_m
                segment_end_offset_m = end_offset_m
            elif index == 0:
                segment_start_offset_m = start_offset_m
                segment_end_offset_m = segment_length_m if direction == "forward" else 0.0
            elif index == len(segment_ids) - 1:
                segment_start_offset_m = 0.0 if direction == "forward" else segment_length_m
                segment_end_offset_m = end_offset_m
            else:
                segment_start_offset_m = 0.0 if direction == "forward" else segment_length_m
                segment_end_offset_m = segment_length_m if direction == "forward" else 0.0

            travel_length_m = abs(segment_end_offset_m - segment_start_offset_m)
            if len(segment_ids) == 1 and travel_length_m <= 1e-9:
                raise ValueError("origin and destination offsets are the same")
            if direction == "forward" and segment_end_offset_m + 1e-9 < segment_start_offset_m:
                raise ValueError("destination is behind origin for forward path")
            if direction == "backward" and segment_end_offset_m > segment_start_offset_m + 1e-9:
                raise ValueError("destination is behind origin for backward path")

            portion = _PathPortion(
                segment_id=segment_id,
                start_offset_m=segment_start_offset_m,
                end_offset_m=segment_end_offset_m,
                path_start_m=path_position_m,
                path_end_m=path_position_m + travel_length_m,
                direction=direction,
            )
            portions.append(portion)
            path_position_m += travel_length_m
        return tuple(portions)

    def _build_constraints(
        self,
        portions: tuple[_PathPortion, ...],
        direction: str,
    ) -> tuple[PathSegmentConstraint, ...]:
        gradient_ranges = self._gradient_ranges_for_path(portions, direction)
        constraints: list[PathSegmentConstraint] = []
        for portion in portions:
            if portion.length_m <= 1e-9:
                continue
            breakpoints = {portion.path_start_m, portion.path_end_m}
            for offset_m in self._speed_break_offsets(portion.segment_id, portion.start_offset_m, portion.end_offset_m):
                breakpoints.add(portion.position_at_offset(offset_m))
            for gradient_range in gradient_ranges:
                if portion.path_start_m < gradient_range.path_start_m < portion.path_end_m:
                    breakpoints.add(gradient_range.path_start_m)
                if portion.path_start_m < gradient_range.path_end_m < portion.path_end_m:
                    breakpoints.add(gradient_range.path_end_m)

            ordered = sorted(breakpoints)
            for start_m, end_m in zip(ordered, ordered[1:]):
                if end_m - start_m <= 1e-9:
                    continue
                midpoint_m = (start_m + end_m) / 2.0
                start_offset_m = portion.offset_at(start_m)
                end_offset_m = portion.offset_at(end_m)
                speed_limit, speed_id = self._speed_limit_for_offset(portion.segment_id, portion.offset_at(midpoint_m))
                gradient = self._gradient_for_path_position(midpoint_m, gradient_ranges)
                constraints.append(
                    PathSegmentConstraint(
                        segment_id=portion.segment_id,
                        start_offset_m=round(start_offset_m, 6),
                        end_offset_m=round(end_offset_m, 6),
                        path_start_m=round(start_m, 6),
                        path_end_m=round(end_m, 6),
                        speed_limit_mps=round(speed_limit, 6),
                        grade_ratio=round(gradient.grade_ratio, 8),
                        gradient_raw=gradient.gradient_raw,
                        speed_restriction_id=speed_id,
                        gradient_id=gradient.gradient_id,
                        direction=direction,
                    )
                )
        return tuple(constraints)

    def _segment_length_m(self, segment_id: int) -> float:
        segment = self.track.get_segment(segment_id)
        if segment is None:
            raise ValueError(f"unknown segment id {segment_id}")
        return float(segment.get("lengthM") or 0.0)

    def _speed_break_offsets(self, segment_id: int, start_offset_m: float, end_offset_m: float) -> list[float]:
        low, high = sorted((start_offset_m, end_offset_m))
        breakpoints: set[float] = set()
        for restriction in self.track.speed_by_seg.get(int(segment_id), []):
            for key in ("startOffsetM", "endOffsetM"):
                value = restriction.get(key)
                if value is None:
                    continue
                offset_m = float(value)
                if low < offset_m < high:
                    breakpoints.add(offset_m)
        return sorted(breakpoints)

    def _speed_limit_for_offset(self, segment_id: int, offset_m: float) -> tuple[float, int | None]:
        restriction = self.track.get_speed_limit(segment_id, offset_m)
        if restriction is None or restriction.get("speedLimitMps") is None:
            return self.default_speed_limit_mps, None
        return float(restriction["speedLimitMps"]), restriction.get("id")

    def _gradient_ranges_for_path(
        self,
        portions: tuple[_PathPortion, ...],
        direction: str,
    ) -> tuple[_GradientRange, ...]:
        if not portions:
            return ()
        total_length_m = portions[-1].path_end_m
        ranges: list[_GradientRange] = []
        for gradient in self.track.gradients:
            start_segment_id = gradient.get("startSegmentId")
            end_segment_id = gradient.get("endSegmentId")
            if start_segment_id is None or end_segment_id is None:
                continue
            start_position_m = self._path_position_for_offset(
                portions,
                int(start_segment_id),
                float(gradient.get("startOffsetM") or 0.0),
                clamp=True,
            )
            end_position_m = self._path_position_for_offset(
                portions,
                int(end_segment_id),
                float(gradient.get("endOffsetM") or 0.0),
                clamp=True,
            )
            if start_position_m is None or end_position_m is None:
                continue
            low = max(0.0, min(start_position_m, end_position_m))
            high = min(total_length_m, max(start_position_m, end_position_m))
            if high - low <= 1e-9:
                continue
            ranges.append(
                _GradientRange(
                    path_start_m=low,
                    path_end_m=high,
                    grade_ratio=self._signed_grade_ratio(gradient, direction),
                    gradient_raw=float(gradient["slopePermille"]) if gradient.get("slopePermille") is not None else None,
                    gradient_id=gradient.get("id"),
                )
            )
        return tuple(sorted(ranges, key=lambda item: item.path_start_m))

    def _path_position_for_offset(
        self,
        portions: tuple[_PathPortion, ...],
        segment_id: int,
        offset_m: float,
        clamp: bool = False,
    ) -> float | None:
        matching = [portion for portion in portions if portion.segment_id == segment_id]
        if not matching:
            return None
        for portion in matching:
            if portion.contains_offset(offset_m):
                return portion.position_at_offset(offset_m)
        if not clamp:
            return None
        portion = matching[0]
        low, high = sorted((portion.start_offset_m, portion.end_offset_m))
        clamped_offset_m = min(max(offset_m, low), high)
        return portion.position_at_offset(clamped_offset_m)

    def _gradient_for_path_position(
        self,
        path_position_m: float,
        gradient_ranges: tuple[_GradientRange, ...],
    ) -> _GradientRange:
        for gradient_range in gradient_ranges:
            if gradient_range.path_start_m <= path_position_m <= gradient_range.path_end_m + 1e-9:
                return gradient_range
        return _GradientRange(0.0, 0.0, 0.0, None, None)

    @staticmethod
    def _signed_grade_ratio(gradient: JsonDict, path_direction: str) -> float:
        raw = gradient.get("slopePermille")
        if raw is None:
            return 0.0
        magnitude = abs(float(raw)) / 10000.0
        direction_code = str(gradient.get("direction") or "").lower()
        sign = 1.0 if direction_code == "0xaa" else -1.0
        if path_direction == "backward":
            sign *= -1.0
        return sign * magnitude
