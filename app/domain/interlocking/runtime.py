"""Runtime bridge between the simulation engine and interlocking services."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from app.domain.interlocking.models import RouteRequest
from app.domain.interlocking.route_catalog import RouteCatalog
from app.domain.interlocking.route_service import RouteService
from app.domain.interlocking.rule_engine import InterlockingRuleEngine
from app.domain.interlocking.section_occupation import SectionOccupationService
from app.domain.interlocking.signal_resolver import SignalAspectResolver
from app.domain.interlocking.switch_lock import SwitchLockService

JsonDict = dict[str, Any]


@dataclass(frozen=True)
class DepartureAuthority:
    train_id: str
    granted: bool
    authority_mode: str = "INTERLOCKING_ROUTE"
    route_ids: tuple[str, ...] = ()
    signal_aspects: dict[str, str] = field(default_factory=dict)
    failure_reason: str | None = None

    def to_dict(self) -> JsonDict:
        return {
            "trainId": self.train_id,
            "granted": self.granted,
            "authorityMode": self.authority_mode,
            "routeIds": list(self.route_ids),
            "signalAspects": dict(self.signal_aspects),
            "failureReason": self.failure_reason,
        }


class InterlockingRuntimeCoordinator:
    """Owns one coherent CI runtime and exposes interval departure authority.

    Member C RouteService remains the only owner of route entry and release.
    This coordinator only translates dispatch requests into route-table locks
    and keeps short-lived interval reservations so queued departures do not
    overlap while a preceding train is still occupying the same interval.
    """

    def __init__(
        self,
        line_map: JsonDict,
        track_query: Any,
        allowed_segment_ids: Iterable[int] | None = None,
    ) -> None:
        self.line_map = line_map
        self.track_query = track_query
        self.allowed_segment_ids = (
            frozenset(int(item) for item in allowed_segment_ids)
            if allowed_segment_ids is not None
            else None
        )
        self.catalog = RouteCatalog(line_map)
        self.section_occupation = SectionOccupationService(line_map)
        self.switch_lock = SwitchLockService(
            [
                switch_def
                for switch_id in self.catalog.switch_ids
                for switch_def in [self.catalog.get_switch(switch_id)]
                if switch_def is not None
            ]
        )
        self.rule_engine = InterlockingRuleEngine(
            self.catalog,
            self.section_occupation,
            self.switch_lock,
        )
        self.route_service = RouteService(
            self.catalog,
            self.rule_engine,
            self.section_occupation,
            self.switch_lock,
        )
        self.signal_resolver = SignalAspectResolver(
            self.catalog,
            self.route_service,
            self.section_occupation,
            self.switch_lock,
        )

        self._signals = {
            int(item["id"]): item
            for item in line_map.get("signals", [])
            if item.get("id") is not None
        }
        self._axle_segments = {
            str(item["id"]): frozenset(int(seg) for seg in (item.get("segmentIds") or []))
            for item in line_map.get("axleSections", [])
            if item.get("id") is not None
        }
        self._eligible_route_ids = self._find_eligible_routes()
        self._entered_route_ids: set[str] = set()
        self._train_route_ids: dict[str, tuple[str, ...]] = {}
        self._train_path_keys: dict[str, tuple[object, ...]] = {}
        self._interval_reservations: dict[tuple[object, ...], dict[str, Any]] = {}
        self._last_authorities: dict[str, DepartureAuthority] = {}
        self._request_sequence = 0

    def reset(self) -> None:
        """Clear runtime state while retaining immutable line definitions."""
        self.__init__(self.line_map, self.track_query, self.allowed_segment_ids)

    def update(self, train_states: list[Any]) -> None:
        """Refresh occupation, release tail-cleared routes, and resolve signals."""
        self.section_occupation.update(train_states, self.track_query)
        # RouteService is the sole authority for approach locking, entry and
        # tail-clear release. The coordinator only observes that lifecycle.
        self.route_service.update()
        self._drop_released_assignments()
        self._drop_cleared_reservations()
        self.signal_resolver.refresh()

    def request_departure(self, train_id: str, path_plan: Any, route_chain_ids: tuple[str, ...] | None = None) -> DepartureAuthority:
        """Lock the non-overlapping mainline routes needed by one station interval."""
        path_key = tuple(path_plan.cache_key())
        assigned = self._train_route_ids.get(train_id, ())
        if self._train_path_keys.get(train_id) == path_key and assigned:
            active = self._active_routes_by_owner()
            if all(active.get(route_id) == train_id for route_id in assigned):
                authority = self._make_authority(train_id, assigned, granted=True)
                self._last_authorities[train_id] = authority
                return authority

        section_ids = self._sections_for_path(path_plan)
        if not section_ids:
            authority = DepartureAuthority(
                train_id=train_id,
                granted=False,
                failure_reason="NO_MAINLINE_ROUTE_MAPPING",
            )
            self._last_authorities[train_id] = authority
            return authority

        for reservation in self._interval_reservations.values():
            if reservation["trainId"] != train_id and section_ids.intersection(reservation["sectionIds"]):
                authority = DepartureAuthority(
                    train_id=train_id,
                    granted=False,
                    authority_mode="INTERLOCKING_ROUTE",
                    failure_reason="INTERVAL_RESERVED",
                )
                self._last_authorities[train_id] = authority
                return authority
        for section_id in section_ids:
            occupying_trains = set(self.section_occupation.occupied_by(section_id)) - {train_id}
            if occupying_trains:
                authority = DepartureAuthority(
                    train_id=train_id,
                    granted=False,
                    authority_mode="INTERLOCKING_ROUTE",
                    failure_reason="SECTION_OCCUPIED",
                )
                self._last_authorities[train_id] = authority
                return authority

        route_ids = tuple(route_chain_ids) if route_chain_ids is not None else self.routes_for_path(path_plan)
        if not route_ids:
            authority = DepartureAuthority(
                train_id=train_id,
                granted=False,
                authority_mode="INTERLOCKING_ROUTE",
                failure_reason="NO_ROUTE_TABLE_MAPPING",
            )
            self._last_authorities[train_id] = authority
            return authority

        newly_locked: list[str] = []
        active = self._active_routes_by_owner()
        for route_id in route_ids:
            if active.get(route_id) == train_id:
                continue
            self._request_sequence += 1
            result = self.route_service.request(
                RouteRequest(
                    request_id=f"CI-{self._request_sequence:06d}",
                    route_id=route_id,
                    train_id=train_id,
                    source="DISPATCH",
                )
            )
            if not result.accepted:
                for locked_route_id in newly_locked:
                    self.route_service.release(locked_route_id, "CANCEL")
                authority = DepartureAuthority(
                    train_id=train_id,
                    granted=False,
                    authority_mode="INTERLOCKING_ROUTE",
                    route_ids=tuple(route_ids),
                    failure_reason=result.failure_reason or "ROUTE_NOT_LOCKABLE",
                )
                self.signal_resolver.refresh()
                self._last_authorities[train_id] = authority
                return authority
            newly_locked.append(route_id)

        assigned_routes = tuple(route_ids)
        self._train_route_ids[train_id] = assigned_routes
        self._train_path_keys[train_id] = path_key
        self._interval_reservations[path_key] = {
            "trainId": train_id,
            "routeIds": assigned_routes,
            "sectionIds": frozenset(section_ids),
            "entered": False,
        }
        self.signal_resolver.refresh()
        authority = self._make_authority(
            train_id,
            assigned_routes,
            granted=True,
            authority_mode="INTERLOCKING_ROUTE",
        )
        if assigned_routes:
            first_route = self.catalog.get(assigned_routes[0])
            first_aspect = (
                authority.signal_aspects.get(str(first_route.start_signal_id))
                if first_route is not None
                else "RED"
            )
            if first_aspect == "RED":
                for route_id in assigned_routes:
                    if self._active_routes_by_owner().get(route_id) == train_id:
                        self.route_service.release(route_id, "CANCEL")
                self._train_route_ids.pop(train_id, None)
                self._train_path_keys.pop(train_id, None)
                self._interval_reservations.pop(path_key, None)
                authority = DepartureAuthority(
                    train_id=train_id,
                    granted=False,
                    authority_mode="INTERLOCKING_ROUTE",
                    route_ids=assigned_routes,
                    signal_aspects=authority.signal_aspects,
                    failure_reason="SIGNAL_AT_STOP",
                )
        self._last_authorities[train_id] = authority
        return authority

    def release_train(self, train_id: str) -> None:
        owned_route_ids = {
            str(item["routeId"])
            for item in self.route_service.snapshot()
            if str(item.get("trainId") or "") == train_id
            and item.get("state") in {"LOCKED", "APPROACH_LOCKED"}
        }
        owned_route_ids.update(self._train_route_ids.pop(train_id, ()))
        for route_id in owned_route_ids:
            if self._active_routes_by_owner().get(route_id) == train_id:
                self.route_service.release(route_id, "EMERGENCY")
            self._entered_route_ids.discard(route_id)
        self._train_path_keys.pop(train_id, None)
        for path_key, reservation in list(self._interval_reservations.items()):
            if reservation["trainId"] == train_id:
                self._interval_reservations.pop(path_key, None)
        self._last_authorities.pop(train_id, None)
        self.signal_resolver.refresh()

    def route_available(self, train_id: str, path_plan: Any | None = None) -> bool:
        if path_plan is not None:
            path_key = tuple(path_plan.cache_key())
            if self._train_path_keys.get(train_id) != path_key:
                return False
        authority = self._last_authorities.get(train_id)
        return bool(authority and authority.granted)

    def routes_for_path(self, path_plan: Any) -> tuple[str, ...]:
        """Return a deterministic, direction-correct, non-hostile route chain."""
        path_segments = frozenset(int(item) for item in path_plan.segment_ids)
        candidates: list[tuple[float, float, str, frozenset[str]]] = []
        seen_signatures: set[tuple[int, int, frozenset[str]]] = set()

        for route_id in self._eligible_route_ids:
            route = self.catalog.get(route_id)
            if route is None:
                continue
            section_ids = frozenset(str(item) for item in route.axle_section_ids)
            covered = frozenset().union(
                *(self._axle_segments.get(section_id, frozenset()) for section_id in section_ids)
            )
            if not covered or not covered.issubset(path_segments):
                continue
            start_position = self._signal_path_position(route.start_signal_id, path_plan)
            end_position = self._signal_path_position(route.end_signal_id, path_plan)
            if start_position is None:
                continue
            # A station-to-station path can end before the terminal signal of
            # its final physical route. If the route starts on this path and
            # every protected axle section belongs to the path, keep it as the
            # terminal overlap so the last in-path signal can be cleared.
            if end_position is None:
                end_position = float(path_plan.total_length_m)
            if end_position <= start_position + 1e-6:
                continue
            signature = (route.start_signal_id, route.end_signal_id, section_ids)
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            candidates.append((start_position, end_position, route_id, section_ids))

        candidates.sort(key=lambda item: (item[0], -(item[1] - item[0]), self._route_sort_key(item[2])))
        selected: list[str] = []
        selected_sections: set[str] = set()
        for _, _, route_id, section_ids in candidates:
            if selected_sections.intersection(section_ids):
                continue
            selected.append(route_id)
            selected_sections.update(section_ids)
        return tuple(selected)

    def snapshot(self) -> JsonDict:
        route_states = {str(item["routeId"]): item for item in self.route_service.snapshot()}
        routes = []
        relevant_switch_ids: set[str] = set()
        for route_id in self._eligible_route_ids:
            route = self.catalog.get(route_id)
            if route is None:
                continue
            relevant_switch_ids.update(route.required_switches)
            state = route_states.get(route_id, {})
            axle_section_ids = list(route.axle_section_ids)
            routes.append({
                "routeId": route_id,
                "name": route.name,
                "startSignalId": route.start_signal_id,
                "endSignalId": route.end_signal_id,
                "axleSectionIds": axle_section_ids,
                "lockedSections": list(state.get("lockedSections", axle_section_ids)),
                "state": state.get("state", "IDLE"),
                "trainId": state.get("trainId"),
                "failureReason": state.get("failureReason"),
            })

        section_snapshot = []
        for item in self.section_occupation.snapshot():
            segments = self._axle_segments.get(str(item["sectionId"]), frozenset())
            if self.allowed_segment_ids is None or segments.intersection(self.allowed_segment_ids):
                section_snapshot.append({**item, "segmentIds": sorted(segments)})

        switch_snapshot = [
            item for item in self.switch_lock.snapshot()
            if str(item["switchId"]) in relevant_switch_ids
        ]
        signal_ids = {
            str(signal_id)
            for route_id in self._eligible_route_ids
            for route in [self.catalog.get(route_id)]
            if route is not None
            for signal_id in (route.start_signal_id, route.end_signal_id)
        }
        signals = [
            item for item in self.signal_resolver.snapshot()
            if str(item["signalId"]) in signal_ids
        ]
        return {
            "mode": "MAINLINE_RUNTIME",
            "routeCount": len(routes),
            "occupiedSectionCount": sum(1 for item in section_snapshot if item.get("occupied")),
            "lockedRouteCount": sum(1 for item in routes if item.get("state") in {"LOCKED", "APPROACH_LOCKED"}),
            "reservedIntervalCount": len(self._interval_reservations),
            "routes": routes,
            "sections": section_snapshot,
            "switches": switch_snapshot,
            "signals": signals,
            "departureAuthorities": [item.to_dict() for item in self._last_authorities.values()],
        }

    def _find_eligible_routes(self) -> tuple[str, ...]:
        route_ids: list[str] = []
        for route_id in self.catalog.route_ids:
            route = self.catalog.get(route_id)
            if route is None:
                continue
            covered = frozenset().union(
                *(self._axle_segments.get(str(item), frozenset()) for item in route.axle_section_ids)
            )
            start_signal = self._signals.get(route.start_signal_id)
            end_signal = self._signals.get(route.end_signal_id)
            if not covered or start_signal is None or end_signal is None:
                continue
            route_ids.append(route_id)
        return tuple(sorted(route_ids, key=self._route_sort_key))

    def _signal_path_position(self, signal_id: int, path_plan: Any) -> float | None:
        signal = self._signals.get(int(signal_id))
        if signal is None or signal.get("segmentId") is None:
            return None
        segment_id = int(signal["segmentId"])
        offset_m = float(signal.get("offsetM", 0.0))
        for constraint in path_plan.constraints:
            if int(constraint.segment_id) != segment_id:
                continue
            low, high = sorted((constraint.start_offset_m, constraint.end_offset_m))
            if low - 1e-6 <= offset_m <= high + 1e-6:
                return constraint.path_start_m + abs(offset_m - constraint.start_offset_m)
        return None

    def _make_authority(
        self,
        train_id: str,
        route_ids: tuple[str, ...],
        *,
        granted: bool,
        authority_mode: str = "INTERLOCKING_ROUTE",
    ) -> DepartureAuthority:
        aspects = {}
        for route_id in route_ids:
            route = self.catalog.get(route_id)
            if route is not None:
                aspects[str(route.start_signal_id)] = self.signal_resolver.resolve(route.start_signal_id)
        return DepartureAuthority(
            train_id=train_id,
            granted=granted,
            authority_mode=authority_mode,
            route_ids=route_ids,
            signal_aspects=aspects,
        )

    def _active_routes_by_owner(self) -> dict[str, str]:
        return {
            str(item["routeId"]): str(item.get("trainId") or "")
            for item in self.route_service.snapshot()
            if item.get("state") in {"LOCKED", "APPROACH_LOCKED"}
        }

    def _drop_released_assignments(self) -> None:
        active = self._active_routes_by_owner()
        for train_id, route_ids in list(self._train_route_ids.items()):
            retained = tuple(route_id for route_id in route_ids if active.get(route_id) == train_id)
            if retained:
                self._train_route_ids[train_id] = retained
            else:
                self._train_route_ids.pop(train_id, None)

    def _drop_cleared_reservations(self) -> None:
        """Release dispatch-only reservations after their train clears them.

        RouteService owns the real interlocking lifecycle. Reservations only
        prevent dispatch from launching another train into the same station
        interval while the previous one is still physically there.
        """
        active = self._active_routes_by_owner()
        for path_key, reservation in list(self._interval_reservations.items()):
            train_id = str(reservation["trainId"])
            owns_active_route = any(
                active.get(route_id) == train_id
                for route_id in reservation.get("routeIds", ())
            )
            occupies_reserved_section = any(
                train_id in self.section_occupation.axle_occupied_by(section_id)
                for section_id in reservation["sectionIds"]
            )
            if owns_active_route or occupies_reserved_section:
                continue
            self._interval_reservations.pop(path_key, None)
            if self._train_path_keys.get(train_id) == path_key:
                self._train_path_keys.pop(train_id, None)

    def _sections_for_path(self, path_plan: Any) -> frozenset[str]:
        path_segments = frozenset(int(item) for item in path_plan.segment_ids)
        return frozenset(
            section_id
            for section_id, segment_ids in self._axle_segments.items()
            if segment_ids.intersection(path_segments)
        )

    @staticmethod
    def _route_sort_key(route_id: str) -> tuple[int, str]:
        return (int(route_id), route_id) if route_id.isdigit() else (2**31 - 1, route_id)
