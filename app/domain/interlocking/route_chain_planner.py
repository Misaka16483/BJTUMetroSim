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
from enum import Enum
from typing import Protocol

from app.domain.interlocking.route_catalog import RouteCatalog
from app.domain.interlocking.terminal_turnback_config import (
    DEFAULT_TERMINAL_TURNBACKS,
    TerminalTurnbackConfig,
)
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


class OperationIntent(str, Enum):
    """The operational reason for a route selection.

    ``NORMAL`` is deliberately constrained to the train's actual platform.
    Terminal reversal is a separate, explicit ``TURNBACK`` plan.
    """

    NORMAL = "NORMAL"
    TURNBACK = "TURNBACK"
    TRANSFER = "TRANSFER"
    DEPOT = "DEPOT"


@dataclass(frozen=True)
class TurnbackPhase:
    """One physical leg of a terminal manoeuvre.

    ``route_ids``, ``signal_ids``, and ``route_switch_positions`` retain the
    authoritative interlocking data; ``segment_ids`` is the ordered physical
    path the vehicle follows.
    """

    direction: str
    route_ids: tuple[str, ...]
    signal_ids: tuple[tuple[int, int], ...]
    route_switch_positions: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]
    segment_ids: tuple[int, ...]


@dataclass(frozen=True)
class TurnbackPlan:
    """A multi-phase, route-table-validated terminal turnback plan."""

    terminal_id: str
    origin_platform_id: int
    final_platform_id: int
    turning_point_segment_id: int
    phases: tuple[TurnbackPhase, ...]
    intent: OperationIntent = OperationIntent.TURNBACK


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

    def plan_operation(
        self,
        *,
        intent: OperationIntent,
        origin_platform_id: int,
        destination_platform_ids: tuple[int, ...] = (),
        direction: str | None = None,
        terminal_id: str | None = None,
    ) -> RouteChainPlan | TurnbackPlan:
        """Plan a task-aware operation without inventing a platform change.

        Normal passenger movements must supply the actual platform occupied by
        the train.  Depot and transfer callers may use the same route-chain
        mechanism, but they must state a direction explicitly.  A terminal
        turnback is only available through a configured, multi-phase plan.
        """
        intent = OperationIntent(intent)
        if intent is OperationIntent.TURNBACK:
            if destination_platform_ids or direction is not None:
                raise ValueError("TURNBACK_IGNORES_NORMAL_DESTINATION")
            if terminal_id is None:
                raise ValueError("TURNBACK_CONFIG_REQUIRED")
            return self.plan_turnback(terminal_id, origin_platform_id)

        if direction not in {"forward", "backward"}:
            raise ValueError("OPERATION_DIRECTION_REQUIRED")
        if origin_platform_id not in self._platforms:
            raise ValueError("UNKNOWN_ORIGIN_PLATFORM")
        if intent is OperationIntent.NORMAL and not self._is_passenger_platform(origin_platform_id):
            raise ValueError("NORMAL_OPERATION_REQUIRES_PASSENGER_PLATFORM")
        if not destination_platform_ids:
            raise ValueError("DESTINATION_PLATFORM_REQUIRED")

        destinations = tuple(
            platform_id
            for platform_id in destination_platform_ids
            if platform_id in self._platforms
            and (intent is not OperationIntent.NORMAL or self._is_passenger_platform(platform_id))
        )
        if not destinations:
            raise ValueError("NO_OPERATIONAL_DESTINATION_PLATFORM")
        return self.plan_between_platform_sets(
            (origin_platform_id,), destinations, direction
        )

    def plan_turnback(
        self,
        terminal_id: str,
        origin_platform_id: int,
        configurations: tuple[TerminalTurnbackConfig, ...] = DEFAULT_TERMINAL_TURNBACKS,
    ) -> TurnbackPlan:
        """Build a configured terminal reversal from real route-table paths.

        This never asks the ordinary platform-to-platform planner to cross a
        terminal.  The vehicle reaches a configured turnback point, stops and
        changes ends, then traverses a separately authorised return leg.
        """
        configuration = next(
            (
                item
                for item in configurations
                if item.terminal_id == terminal_id and item.origin_platform_id == origin_platform_id
            ),
            None,
        )
        if configuration is None:
            raise ValueError("TURNBACK_CONFIG_NOT_FOUND")
        return self._build_turnback_plan(configuration)

    def plan_between_platform_sets(
        self,
        origin_platform_ids: tuple[int, ...],
        destination_platform_ids: tuple[int, ...],
        direction: str,
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

    def _build_turnback_plan(self, configuration: TerminalTurnbackConfig) -> TurnbackPlan:
        if len(configuration.phases) < 2:
            raise ValueError("TURNBACK_CONFIG_INVALID: requires multiple phases")
        if configuration.origin_platform_id not in self._platforms:
            raise ValueError("TURNBACK_CONFIG_INVALID: unknown origin platform")
        if configuration.final_platform_id not in self._platforms:
            raise ValueError("TURNBACK_CONFIG_INVALID: unknown final platform")

        phases = tuple(
            self._build_turnback_phase(phase.direction, phase.route_ids)
            for phase in configuration.phases
        )
        origin_segment = int(self._platforms[configuration.origin_platform_id]["segmentId"])
        final_segment = int(self._platforms[configuration.final_platform_id]["segmentId"])
        if phases[0].segment_ids[0] != origin_segment:
            raise ValueError("TURNBACK_CONFIG_INVALID: first phase does not start at origin platform")
        if phases[-1].segment_ids[-1] != final_segment:
            raise ValueError("TURNBACK_CONFIG_INVALID: final phase does not end at final platform")
        if phases[0].segment_ids[-1] != configuration.turning_point_segment_id:
            raise ValueError("TURNBACK_CONFIG_INVALID: first phase misses turnback point")
        if phases[1].segment_ids[0] != configuration.turning_point_segment_id:
            raise ValueError("TURNBACK_CONFIG_INVALID: second phase misses turnback point")
        if any(
            previous.segment_ids[-1] != following.segment_ids[0]
            for previous, following in zip(phases, phases[1:])
        ):
            raise ValueError("TURNBACK_CONFIG_INVALID: discontinuous phases")

        return TurnbackPlan(
            terminal_id=configuration.terminal_id,
            origin_platform_id=configuration.origin_platform_id,
            final_platform_id=configuration.final_platform_id,
            turning_point_segment_id=configuration.turning_point_segment_id,
            phases=phases,
        )

    def _build_turnback_phase(
        self,
        direction: str,
        route_ids: tuple[str, ...],
    ) -> TurnbackPhase:
        if direction not in {"forward", "backward"} or not route_ids:
            raise ValueError("TURNBACK_CONFIG_INVALID: phase direction/routes")
        segment_ids: tuple[int, ...] = ()
        signal_ids: list[tuple[int, int]] = []
        route_switch_positions: list[tuple[str, tuple[tuple[str, str], ...]]] = []
        for route_id in route_ids:
            route = self._catalog.get(route_id)
            if route is None:
                raise ValueError(f"TURNBACK_CONFIG_INVALID: unknown route {route_id}")
            route_segments = self._route_segments(route_id, direction)
            if not route_segments:
                opposite = "backward" if direction == "forward" else "forward"
                route_segments = tuple(reversed(self._route_segments(route_id, opposite)))
            if not route_segments:
                raise ValueError(f"TURNBACK_CONFIG_INVALID: no Seg path for route {route_id}")
            segment_ids = self._merge_sequences(segment_ids, route_segments, direction) if segment_ids else route_segments
            if not segment_ids:
                raise ValueError(f"TURNBACK_CONFIG_INVALID: discontinuous route {route_id}")
            signal_ids.append((route.start_signal_id, route.end_signal_id))
            route_switch_positions.append(
                (route_id, tuple(sorted(route.required_switches.items())))
            )

        self._validate_segment_sequence(segment_ids, direction)
        return TurnbackPhase(
            direction=direction,
            route_ids=route_ids,
            signal_ids=tuple(signal_ids),
            route_switch_positions=tuple(route_switch_positions),
            segment_ids=segment_ids,
        )

    def _validate_segment_sequence(self, segment_ids: tuple[int, ...], direction: str) -> None:
        for current, following in zip(segment_ids, segment_ids[1:]):
            next_ids = {
                int(item["id"])
                for item in self._track.get_next_segments(current, direction)
            }
            if following not in next_ids:
                raise ValueError(
                    f"TURNBACK_CONFIG_INVALID: non-contiguous Seg path {current}->{following}"
                )

    def _is_passenger_platform(self, platform_id: int) -> bool:
        direction = str(self._platforms[platform_id].get("direction", "")).lower()
        return direction in {"0x55", "0xaa"}

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
