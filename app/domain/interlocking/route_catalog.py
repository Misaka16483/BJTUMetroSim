"""Route catalog — Member C Phase 2.

Loads every route from line_map.json and pre-computes:
1. The conflict (hostile-route) table — both section-based and switch-based.
2. Each route's required switch positions.
"""

from __future__ import annotations

from typing import Any

from app.domain.interlocking.models import RouteDef, SwitchDef

JsonDict = dict[str, Any]


class RouteCatalog:
    """Immutable catalogue of all routes on a line with conflict data."""

    def __init__(self, line_map: JsonDict) -> None:
        # -- switch definitions --
        self._switches: dict[str, SwitchDef] = {}
        for raw in line_map.get("switches", []):
            sid = raw.get("id")
            if sid is None:
                continue
            self._switches[str(sid)] = SwitchDef(
                switch_id=str(sid),
                name=str(raw.get("name", sid)),
                normal_seg_id=raw.get("normalSegId"),
                reverse_seg_id=raw.get("reverseSegId"),
                frog_seg_id=raw.get("frogSegId"),
            )

        # -- axle-section to segments map (for switch derivation) --
        self._axle_section_segs: dict[str, set[int]] = {}
        for raw in line_map.get("axleSections", []):
            sid = raw.get("id")
            if sid is None:
                continue
            self._axle_section_segs[str(sid)] = set(
                int(s) for s in (raw.get("segmentIds") or []) if s is not None
            )

        # -- route definitions --
        self._routes: dict[str, RouteDef] = {}
        for raw in line_map.get("routes", []):
            if raw.get("id") is None:
                continue
            route_id = str(raw["id"])
            self._routes[route_id] = RouteDef(
                route_id=route_id,
                name=str(raw.get("name", route_id)),
                route_type=str(raw.get("type", "")),
                start_signal_id=int(raw.get("startSignalId", 0)),
                end_signal_id=int(raw.get("endSignalId", 0)),
                axle_section_ids=[
                    str(s) for s in (raw.get("axleSectionIds") or []) if s is not None
                ],
                protection_section_ids=[
                    str(s) for s in (raw.get("protectionSectionIds") or []) if s is not None
                ],
                approach_section_ids=[
                    str(s) for s in (raw.get("pointApproachSectionIds") or []) if s is not None
                ],
                ci_area_id=raw.get("ciAreaId"),
            )

        # -- derive switch requirements per route --
        self._derive_switch_requirements()

        # -- pre-computed conflict table --
        self._conflicts: dict[str, set[str]] = {}
        self._compute_conflicts()

        # -- by-start-signal index --
        self._by_start_signal: dict[int, list[str]] = {}
        for route_id, rdef in self._routes.items():
            self._by_start_signal.setdefault(rdef.start_signal_id, []).append(route_id)

    # -- query interface ----------------------------------------------------

    @property
    def route_ids(self) -> list[str]:
        return list(self._routes)

    @property
    def switch_ids(self) -> list[str]:
        return list(self._switches)

    def get(self, route_id: str) -> RouteDef | None:
        return self._routes.get(route_id)

    def get_switch(self, switch_id: str) -> SwitchDef | None:
        return self._switches.get(switch_id)

    def by_start_signal(self, signal_id: int) -> list[str]:
        return list(self._by_start_signal.get(signal_id, []))

    def conflicts_with(self, route_id: str) -> set[str]:
        return self._conflicts.get(route_id, set())

    def are_hostile(self, route_a: str, route_b: str) -> bool:
        return route_b in self._conflicts.get(route_a, set())

    def to_dict(self) -> dict:
        return {
            "routeCount": len(self._routes),
            "switchCount": len(self._switches),
            "sectionConflictPairs": sum(
                1 for rid, conflicts in self._conflicts.items()
                for cid in conflicts if rid < cid and self._section_conflict(rid, cid)
            ),
            "switchConflictPairs": sum(
                1 for rid, conflicts in self._conflicts.items()
                for cid in conflicts if rid < cid and self._switch_conflict(rid, cid)
            ),
            "routes": [
                {
                    "routeId": r.route_id,
                    "name": r.name,
                    "startSignalId": r.start_signal_id,
                    "endSignalId": r.end_signal_id,
                    "axleSectionIds": r.axle_section_ids,
                    "protectionSectionIds": r.protection_section_ids,
                    "requiredSwitches": r.required_switches,
                    "conflictingRouteIds": sorted(self._conflicts.get(r.route_id, set())),
                }
                for r in self._routes.values()
            ],
        }

    # -- internal: switch derivation ----------------------------------------

    def _derive_switch_requirements(self) -> None:
        """For each route, compute which switches it needs in which position.

        A route's set of covered segments is the union of segs from all its
        axle sections.  For each switch, if the route's seg set contains:
          - frogSeg + normalSeg  → requires NORMAL
          - frogSeg + reverseSeg → requires REVERSE
        """
        for route_id, route in self._routes.items():
            # Collect all segments covered by this route's axle sections
            route_segs: set[int] = set()
            for section_id in route.axle_section_ids:
                route_segs |= self._axle_section_segs.get(section_id, set())

            if not route_segs:
                continue

            req: dict[str, str] = {}
            for switch_id, sw in self._switches.items():
                has_frog = sw.frog_seg_id is not None and sw.frog_seg_id in route_segs
                if not has_frog:
                    continue
                has_normal = sw.normal_seg_id is not None and sw.normal_seg_id in route_segs
                has_reverse = sw.reverse_seg_id is not None and sw.reverse_seg_id in route_segs

                if has_normal and not has_reverse:
                    req[switch_id] = "NORMAL"
                elif has_reverse and not has_normal:
                    req[switch_id] = "REVERSE"
                # If both or neither present — ambiguous; skip

            if req:
                # Create a new RouteDef with switch requirements filled in
                new_route = RouteDef(
                    route_id=route.route_id,
                    name=route.name,
                    route_type=route.route_type,
                    start_signal_id=route.start_signal_id,
                    end_signal_id=route.end_signal_id,
                    axle_section_ids=route.axle_section_ids,
                    protection_section_ids=route.protection_section_ids,
                    approach_section_ids=route.approach_section_ids,
                    ci_area_id=route.ci_area_id,
                    required_switches=req,
                )
                self._routes[route_id] = new_route

    # -- internal: conflict computation -------------------------------------

    def _section_conflict(self, rid_a: str, rid_b: str) -> bool:
        ra = self._routes.get(rid_a)
        rb = self._routes.get(rid_b)
        if ra is None or rb is None:
            return False
        return bool(set(ra.axle_section_ids) & set(rb.axle_section_ids))

    def _switch_conflict(self, rid_a: str, rid_b: str) -> bool:
        """Two routes conflict on a switch if they share a switch but
        require opposite positions."""
        ra = self._routes.get(rid_a)
        rb = self._routes.get(rid_b)
        if ra is None or rb is None:
            return False
        common = set(ra.required_switches) & set(rb.required_switches)
        for switch_id in common:
            if ra.required_switches[switch_id] != rb.required_switches[switch_id]:
                return True
        return False

    def _compute_conflicts(self) -> None:
        """Two routes conflict if:
        1. They share any axle section, OR
        2. They share a switch but require opposite positions.
        """
        for rid_a, ra in self._routes.items():
            conflicts: set[str] = set()
            sec_a = set(ra.axle_section_ids)
            for rid_b, rb in self._routes.items():
                if rid_a == rid_b:
                    continue
                # Section overlap
                if sec_a & set(rb.axle_section_ids):
                    conflicts.add(rid_b)
                    continue
                # Switch conflict
                common = set(ra.required_switches) & set(rb.required_switches)
                for sw_id in common:
                    if ra.required_switches[sw_id] != rb.required_switches[sw_id]:
                        conflicts.add(rid_b)
                        break
            if conflicts:
                self._conflicts[rid_a] = conflicts
