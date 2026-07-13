"""Route-table-driven planning for ordinary adjacent-station movements.

The planner deliberately separates two concerns:

* it derives an ordered Seg path only from route-table records and their
  signal-to-signal continuity;
* a policy ranks otherwise valid route chains without embedding a dispatch
  preference in the topology or vehicle engine.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Protocol

from app.domain.interlocking.route_catalog import RouteCatalog
from app.domain.line.services import PathPlan, PathPlanner, TrackQueryService


JsonDict = dict[str, object]


@dataclass(frozen=True)
class RouteChainCandidate:
    route_ids: tuple[str, ...]
    segment_ids: tuple[int, ...]


@dataclass(frozen=True)
class RouteChainPlan:
    route_ids: tuple[str, ...]
    path_plan: PathPlan


class RouteSelectionPolicy(Protocol):
    """Ranks legal route chains; dispatch can replace this policy later."""

    def sort_key(self, candidate: RouteChainCandidate, catalog: RouteCatalog) -> tuple[object, ...]:
        ...


class MainRouteFirstPolicy:
    """Default passenger policy: main routes, fewer routes, stable IDs."""

    MAIN_ROUTE_TYPE = "0x0001"

    def sort_key(self, candidate: RouteChainCandidate, catalog: RouteCatalog) -> tuple[object, ...]:
        non_main_count = sum(
            1
            for route_id in candidate.route_ids
            if (route := catalog.get(route_id)) is None or route.route_type != self.MAIN_ROUTE_TYPE
        )
        return (
            non_main_count,
            len(candidate.route_ids),
            tuple(self._stable_route_id(route_id) for route_id in candidate.route_ids),
        )

    @staticmethod
    def _stable_route_id(route_id: str) -> tuple[int, int | str]:
        return (0, int(route_id)) if route_id.isdigit() else (1, route_id)


class RouteChainPlanner:
    """Finds a platform-to-platform Seg sequence using only route-table paths."""

    MAX_ROUTE_HOPS = 8
    MAX_CANDIDATES = 256

    def __init__(
        self,
        line_map: JsonDict,
        catalog: RouteCatalog,
        policy: RouteSelectionPolicy | None = None,
    ) -> None:
        self._line_map = line_map
        self._catalog = catalog
        self._policy = policy or MainRouteFirstPolicy()
        self._track = TrackQueryService(line_map)
        self._path_planner = PathPlanner(line_map)
        self._platforms = {
            int(item["id"]): item
            for item in line_map.get("platforms", [])
            if item.get("id") is not None and item.get("segmentId") is not None
        }
        self._signals = {
            int(item["id"]): item
            for item in line_map.get("signals", [])
            if item.get("id") is not None and item.get("segmentId") is not None
        }
        self._axle_segments = {
            str(item["id"]): [int(seg_id) for seg_id in item.get("segmentIds", [])]
            for item in line_map.get("axleSections", [])
            if item.get("id") is not None
        }
        self._routes_by_start_signal: dict[int, list[str]] = {}
        for route_id in catalog.route_ids:
            route = catalog.get(route_id)
            if route is not None:
                self._routes_by_start_signal.setdefault(route.start_signal_id, []).append(route_id)
        for route_ids in self._routes_by_start_signal.values():
            route_ids.sort(key=MainRouteFirstPolicy._stable_route_id)
        self._ordered_routes: dict[tuple[str, str], tuple[int, ...]] = {}

    def plan_between_platform_sets(
        self,
        origin_platform_ids: tuple[int, ...],
        destination_platform_ids: tuple[int, ...],
        direction: str,
        train_length_m: float | None = None,
    ) -> RouteChainPlan:
        """Return the policy-selected route-table plan for an adjacent station pair.

        No topology shortest-path fallback is intentionally provided.  A caller
        must wait and retry or surface the data/planning problem when no chain
        can be constructed.
        """
        if direction not in {"forward", "backward"}:
            raise ValueError("direction must be 'forward' or 'backward'")

        candidates: list[tuple[int, int, RouteChainCandidate]] = []
        for origin_platform_id in origin_platform_ids:
            for destination_platform_id in destination_platform_ids:
                candidates.extend(
                    (origin_platform_id, destination_platform_id, candidate)
                    for candidate in self._candidates_for_platform_pair(
                        origin_platform_id, destination_platform_id, direction
                    )
                )

        if not candidates:
            raise ValueError("NO_ROUTE_CHAIN")

        candidates.sort(key=lambda item: self._policy.sort_key(item[2], self._catalog))
        for origin_platform_id, destination_platform_id, candidate in candidates:
            try:
                path_plan = self._path_planner.plan_for_segment_sequence(
                    origin_platform_id,
                    destination_platform_id,
                    list(candidate.segment_ids),
                    direction,
                    train_length_m=train_length_m,
                )
            except ValueError:
                continue
            return RouteChainPlan(route_ids=candidate.route_ids, path_plan=path_plan)

        raise ValueError("NO_CONTIGUOUS_ROUTE_CHAIN")

    def _candidates_for_platform_pair(
        self,
        origin_platform_id: int,
        destination_platform_id: int,
        direction: str,
    ) -> list[RouteChainCandidate]:
        origin = self._platforms.get(origin_platform_id)
        destination = self._platforms.get(destination_platform_id)
        if origin is None or destination is None:
            return []
        origin_seg = int(origin["segmentId"])
        destination_seg = int(destination["segmentId"])

        starts = [
            route_id
            for route_id in self._catalog.route_ids
            if origin_seg in self._route_segments(route_id, direction)
        ]
        results: list[RouteChainCandidate] = []
        pending: deque[tuple[tuple[str, ...], tuple[int, ...]]] = deque()
        for route_id in starts:
            route_segments = self._route_segments(route_id, direction)
            if route_segments:
                pending.append(((route_id,), route_segments))

        while pending and len(results) < self.MAX_CANDIDATES:
            route_ids, segment_ids = pending.popleft()
            candidate_segments = self._slice_between_platform_segments(
                segment_ids, origin_seg, destination_seg
            )
            if candidate_segments is not None:
                results.append(RouteChainCandidate(route_ids, candidate_segments))
                continue
            if len(route_ids) >= self.MAX_ROUTE_HOPS:
                continue

            tail = self._catalog.get(route_ids[-1])
            if tail is None:
                continue
            for next_route_id in self._routes_by_start_signal.get(tail.end_signal_id, []):
                if next_route_id in route_ids:
                    continue
                next_segments = self._route_segments(next_route_id, direction)
                merged = self._merge_sequences(segment_ids, next_segments, direction)
                if merged:
                    pending.append((route_ids + (next_route_id,), merged))
        return results

    def _route_segments(self, route_id: str, direction: str) -> tuple[int, ...]:
        cache_key = (route_id, direction)
        if cache_key in self._ordered_routes:
            return self._ordered_routes[cache_key]

        route = self._catalog.get(route_id)
        if route is None:
            return ()
        start_signal = self._signals.get(route.start_signal_id)
        end_signal = self._signals.get(route.end_signal_id)
        if start_signal is None or end_signal is None:
            return ()

        covered: set[int] = set()
        for section_id in route.axle_section_ids:
            covered.update(self._axle_segments.get(str(section_id), []))
        start_seg = int(start_signal["segmentId"])
        end_seg = int(end_signal["segmentId"])
        covered.update((start_seg, end_seg))

        ordered = self._find_path_within_coverage(start_seg, end_seg, covered, direction)
        self._ordered_routes[cache_key] = ordered
        return ordered

    def _find_path_within_coverage(
        self,
        start_seg: int,
        end_seg: int,
        covered: set[int],
        direction: str,
    ) -> tuple[int, ...]:
        queue: deque[tuple[int, ...]] = deque([(start_seg,)])
        visited = {start_seg}
        while queue:
            path = queue.popleft()
            current = path[-1]
            if current == end_seg:
                return path
            for item in self._track.get_next_segments(current, direction):
                next_seg = int(item["id"])
                if next_seg in covered and next_seg not in visited:
                    visited.add(next_seg)
                    queue.append(path + (next_seg,))
        return ()

    def _merge_sequences(
        self,
        first: tuple[int, ...],
        second: tuple[int, ...],
        direction: str,
    ) -> tuple[int, ...]:
        if not first or not second:
            return ()
        if first[-1] == second[0]:
            return first + second[1:]
        next_ids = {int(item["id"]) for item in self._track.get_next_segments(first[-1], direction)}
        return first + second if second[0] in next_ids else ()

    @staticmethod
    def _slice_between_platform_segments(
        segment_ids: tuple[int, ...],
        origin_seg: int,
        destination_seg: int,
    ) -> tuple[int, ...] | None:
        try:
            origin_index = segment_ids.index(origin_seg)
        except ValueError:
            return None
        for destination_index in range(origin_index, len(segment_ids)):
            if segment_ids[destination_index] == destination_seg:
                return segment_ids[origin_index:destination_index + 1]
        return None
