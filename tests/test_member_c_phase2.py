"""Phase 2 acceptance tests for member C — interlocking subsystem."""

from __future__ import annotations

import unittest
from typing import Any

from app.domain.interlocking.models import RouteRequest, SectionOccupation, SwitchDef
from app.domain.interlocking.route_catalog import RouteCatalog
from app.domain.interlocking.route_service import RouteService
from app.domain.interlocking.rule_engine import InterlockingRuleEngine
from app.domain.interlocking.signal_resolver import SignalAspectResolver
from app.domain.interlocking.section_occupation import SectionOccupationService
from app.domain.interlocking.switch_lock import SwitchLockService
from app.domain.line.services import PathTrackQuery
from app.domain.signal.models import TrainState


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_JsonDict = dict[str, Any]


def _tiny_line_map(
    *,
    axle_sections: list[_JsonDict] | None = None,
    segments: list[_JsonDict] | None = None,
    routes: list[_JsonDict] | None = None,
    switches: list[_JsonDict] | None = None,
) -> _JsonDict:
    """Build a minimal line_map dict for testing."""
    return {
        "axleSections": axle_sections or [],
        "logicalSections": [],
        "segments": segments or [],
        "routes": routes or [],
        "switches": switches or [],
        "signals": [],
    }


def _fake_track_query(segment_map: dict[int, _JsonDict] | None = None) -> Any:
    """Stub TrackQueryService with canned segment data."""

    class _Stub:
        def __init__(self, segs: dict[int, _JsonDict]) -> None:
            self._segs = segs

        def get_segment(self, seg_id: int) -> _JsonDict | None:
            return self._segs.get(seg_id)

        def get_next_segments(self, seg_id: int, direction: str) -> list[_JsonDict]:
            seg = self._segs.get(seg_id)
            if seg is None:
                return []
            if direction == "backward":
                key = "startForwardSegId"
            else:
                key = "endForwardSegId"
            next_id = seg.get(key)
            if next_id is None:
                return []
            next_seg = self._segs.get(int(next_id))
            return [next_seg] if next_seg else []

    return _Stub(segment_map or {})


def _make_train(**overrides: Any) -> TrainState:
    defaults: dict[str, Any] = {
        "train_id": "T001",
        "sim_time_ms": 120_000,
        "seg_id": 13,
        "offset_m": 80.0,
        "position_m": 500.0,
        "speed_mps": 12.0,
        "direction": "FORWARD",
        "length_m": 120.0,
    }
    defaults.update(overrides)
    return TrainState(**defaults)


# ---------------------------------------------------------------------------
# SectionOccupationService
# ---------------------------------------------------------------------------


class SectionOccupationServiceBasicTests(unittest.TestCase):
    """Tests that don't need topology walking."""

    def setUp(self) -> None:
        self.line_map = _tiny_line_map(
            axle_sections=[
                {"id": 1, "name": "JZ1", "segmentIds": [13]},
                {"id": 2, "name": "JZ2", "segmentIds": [14]},
                {"id": 3, "name": "JZ3", "segmentIds": [13, 14]},
            ],
            segments=[
                {"id": 13, "lengthM": 200.0, "startForwardSegId": None},
                {"id": 14, "lengthM": 150.0, "startForwardSegId": 13},
            ],
        )
        self.track = _fake_track_query(
            {
                13: {"id": 13, "lengthM": 200.0, "startForwardSegId": None},
                14: {"id": 14, "lengthM": 150.0, "startForwardSegId": 13},
            }
        )
        self.svc = SectionOccupationService(self.line_map)

    def test_single_train_occupies_its_segment_section(self) -> None:
        """Train on seg 13 → axle section JZ1 (containing seg 13) occupied."""
        train = _make_train(seg_id=13, offset_m=80.0)
        self.svc.update([train], self.track)
        self.assertTrue(self.svc.is_occupied("1"))
        self.assertEqual(self.svc.occupied_by("1"), ["T001"])

    def test_other_section_remains_free(self) -> None:
        """Train on seg 13 → axle section JZ2 (seg 14 only) stays free."""
        train = _make_train(seg_id=13, offset_m=80.0)
        self.svc.update([train], self.track)
        self.assertFalse(self.svc.is_occupied("2"))

    def test_logical_section_id_does_not_overwrite_same_numbered_axle_section(self) -> None:
        line_map = _tiny_line_map(
            axle_sections=[{"id": 85, "name": "JZ85", "segmentIds": [100]}],
            segments=[{"id": 100, "lengthM": 600.0}],
        )
        # The workbook uses independent numeric namespaces.  Logical 85 is a
        # different object and must not hide axle JZ85 from interlocking.
        line_map["logicalSections"] = [{"id": 85, "name": "15G"}]
        svc = SectionOccupationService(line_map)
        track = _fake_track_query({100: {"id": 100, "lengthM": 600.0}})

        svc.update([_make_train(seg_id=100, offset_m=300.0, length_m=20.0)], track)

        self.assertTrue(svc.is_occupied("85"))
        axle = next(item for item in svc.snapshot() if item["sectionId"] == "85")
        self.assertEqual(axle["sectionType"], "AXLE")
        self.assertTrue(axle["occupied"])

    def test_two_trains_same_section(self) -> None:
        """Two trains with segs in the same axle section → both listed."""
        t1 = _make_train(train_id="T001", seg_id=13, offset_m=50.0)
        t2 = _make_train(train_id="T002", seg_id=13, offset_m=120.0)
        self.svc.update([t1, t2], self.track)
        self.assertTrue(self.svc.is_occupied("1"))
        self.assertEqual(sorted(self.svc.occupied_by("1")), ["T001", "T002"])

    def test_two_trains_different_sections(self) -> None:
        """T001 on seg 13 → JZ1, T002 on seg 14 → JZ2."""
        t1 = _make_train(train_id="T001", seg_id=13)
        t2 = _make_train(train_id="T002", seg_id=14)
        self.svc.update([t1, t2], self.track)
        self.assertTrue(self.svc.is_occupied("1"))
        self.assertTrue(self.svc.is_occupied("2"))

    def test_occupancy_cleared_on_next_tick(self) -> None:
        """Occupancy from previous tick is cleared when train fully leaves."""
        t1 = _make_train(seg_id=13, offset_m=80.0, length_m=60.0)
        self.svc.update([t1], self.track)
        self.assertTrue(self.svc.is_occupied("1"))

        # next tick: train moved well into seg 14, tail no longer in seg 13
        # seg 14 is 150m long, offset 130, length 60 → tail at offset 70
        # (fully within seg 14)
        t2 = _make_train(train_id="T001", seg_id=14, offset_m=130.0, length_m=60.0)
        self.svc.update([t2], self.track)
        self.assertFalse(self.svc.is_occupied("1"), "train fully left seg 13")
        self.assertTrue(self.svc.is_occupied("2"))

    def test_all_occupied_sections(self) -> None:
        train = _make_train(seg_id=13)
        self.svc.update([train], self.track)
        occupied = self.svc.all_occupied_sections
        self.assertIn("1", occupied)
        self.assertNotIn("2", occupied)

    def test_snapshot_format(self) -> None:
        train = _make_train(seg_id=13)
        self.svc.update([train], self.track)
        snap = self.svc.snapshot()
        self.assertIsInstance(snap, list)
        jz1 = next(s for s in snap if s["sectionId"] == "1")
        self.assertTrue(jz1["occupied"])
        self.assertEqual(jz1["trainIds"], ["T001"])
        self.assertEqual(jz1["sectionType"], "AXLE")


class SectionOccupationServiceTailOverlapTests(unittest.TestCase):
    """Tests where the train tail extends into a previous segment."""

    def setUp(self) -> None:
        self.line_map = _tiny_line_map(
            axle_sections=[
                {"id": 1, "name": "JZ1", "segmentIds": [13]},
                {"id": 2, "name": "JZ2", "segmentIds": [14]},
            ],
            segments=[
                {"id": 13, "lengthM": 100.0, "startForwardSegId": None},
                {"id": 14, "lengthM": 200.0, "startForwardSegId": 13},
            ],
        )
        self.track = _fake_track_query(
            {
                13: {"id": 13, "lengthM": 100.0, "startForwardSegId": None},
                14: {"id": 14, "lengthM": 200.0, "startForwardSegId": 13},
            }
        )
        self.svc = SectionOccupationService(self.line_map)

    def test_tail_extends_into_previous_segment(self) -> None:
        """Train head at seg 14 offset 10 but length 120 → tail in seg 13."""
        # seg 14 at offset 10, only 10m of seg 14 behind the head.
        # Train is 120m long → 110m extends into seg 13.
        train = _make_train(
            train_id="T001",
            seg_id=14,
            offset_m=10.0,
            length_m=120.0,
            direction="FORWARD",
        )
        self.svc.update([train], self.track)
        # Both sections should be occupied
        self.assertTrue(self.svc.is_occupied("1"), "seg 13 section should be occupied by tail")
        self.assertTrue(self.svc.is_occupied("2"), "seg 14 section should be occupied by head")

    def test_tail_within_same_segment(self) -> None:
        """Train head at offset 130, length 120 → tail at offset 10 (same seg)."""
        train = _make_train(
            train_id="T001",
            seg_id=14,
            offset_m=130.0,
            length_m=120.0,
            direction="FORWARD",
        )
        self.svc.update([train], self.track)
        self.assertFalse(self.svc.is_occupied("1"), "tail is still in seg 14")
        self.assertTrue(self.svc.is_occupied("2"))

    def test_tail_follows_approved_turnout_branch_not_global_first_predecessor(self) -> None:
        """A train entering S43 through S44 must not project its tail into S41."""
        line_map = _tiny_line_map(
            axle_sections=[
                {"id": 41, "name": "JZ41", "segmentIds": [41]},
                {"id": 43, "name": "JZ43", "segmentIds": [43]},
                {"id": 44, "name": "JZ44", "segmentIds": [44]},
            ],
            segments=[
                {"id": 41, "lengthM": 100.0},
                {"id": 44, "lengthM": 100.0},
                {"id": 43, "lengthM": 20.0, "startForwardSegId": 41, "startDivergingSegId": 44},
            ],
        )
        track = _fake_track_query({
            41: {"id": 41, "lengthM": 100.0},
            44: {"id": 44, "lengthM": 100.0},
            43: {"id": 43, "lengthM": 20.0, "startForwardSegId": 41},
        })
        train = _make_train(seg_id=43, offset_m=20.0, length_m=60.0)
        train_with_path = TrainState(**{
            **train.__dict__,
            "path_track": PathTrackQuery(track, [44, 43]),
        })
        svc = SectionOccupationService(line_map)

        svc.update([train_with_path], track)

        self.assertEqual(svc.covered_segments_for("T001"), {43, 44})
        self.assertFalse(svc.is_occupied("41"))
        self.assertTrue(svc.is_occupied("43"))
        self.assertTrue(svc.is_occupied("44"))


class SectionOccupationServiceEmptyTests(unittest.TestCase):
    def test_no_trains_nothing_occupied(self) -> None:
        svc = SectionOccupationService(
            _tiny_line_map(
                axle_sections=[{"id": 1, "name": "JZ1", "segmentIds": [13]}],
                segments=[{"id": 13, "lengthM": 100.0}],
            )
        )
        svc.update([], _fake_track_query())
        self.assertFalse(svc.is_occupied("1"))
        self.assertEqual(len(svc.all_occupied_sections), 0)


# ---------------------------------------------------------------------------
# RouteCatalog
# ---------------------------------------------------------------------------


class RouteCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.line_map = _tiny_line_map(
            routes=[
                {
                    "id": 1,
                    "name": "R-GGZ-FSP",
                    "type": "0x0001",
                    "startSignalId": 5,
                    "endSignalId": 10,
                    "axleSectionIds": [1, 2],
                    "protectionSectionIds": [50],
                    "ciAreaId": 1,
                },
                {
                    "id": 2,
                    "name": "R-FSP-GGZ",
                    "type": "0x0001",
                    "startSignalId": 11,
                    "endSignalId": 6,
                    "axleSectionIds": [2, 3],
                    "protectionSectionIds": [],
                    "ciAreaId": 1,
                },
                {
                    "id": 3,
                    "name": "R-INDEPENDENT",
                    "type": "0x0001",
                    "startSignalId": 20,
                    "endSignalId": 25,
                    "axleSectionIds": [4, 5],
                    "protectionSectionIds": [],
                    "ciAreaId": 2,
                },
            ]
        )
        self.catalog = RouteCatalog(self.line_map)

    def test_loads_all_routes(self) -> None:
        self.assertEqual(len(self.catalog.route_ids), 3)

    def test_route_definition(self) -> None:
        r = self.catalog.get("1")
        self.assertIsNotNone(r)
        assert r is not None
        self.assertEqual(r.name, "R-GGZ-FSP")
        self.assertEqual(r.start_signal_id, 5)
        self.assertEqual(r.axle_section_ids, ["1", "2"])

    def test_conflicting_routes_detected(self) -> None:
        """Route 1 and 2 share axle section 2 → hostile."""
        self.assertTrue(self.catalog.are_hostile("1", "2"))
        self.assertTrue(self.catalog.are_hostile("2", "1"))

    def test_independent_route_not_conflicting(self) -> None:
        """Route 3 uses sections 4/5 → no overlap with 1 or 2."""
        self.assertFalse(self.catalog.are_hostile("1", "3"))
        self.assertFalse(self.catalog.are_hostile("2", "3"))

    def test_conflicts_with_returns_set(self) -> None:
        conflicts = self.catalog.conflicts_with("1")
        self.assertEqual(conflicts, {"2"})

    def test_by_start_signal(self) -> None:
        routes = self.catalog.by_start_signal(5)
        self.assertEqual(routes, ["1"])

    def test_empty_catalog(self) -> None:
        empty = RouteCatalog(_tiny_line_map())
        self.assertEqual(len(empty.route_ids), 0)
        self.assertIsNone(empty.get("nonexistent"))


# ---------------------------------------------------------------------------
# RouteCatalog — switch requirements
# ---------------------------------------------------------------------------


class RouteCatalogSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.line_map = _tiny_line_map(
            axle_sections=[
                {"id": 1, "name": "JZ1", "segmentIds": [13]},
                {"id": 2, "name": "JZ2", "segmentIds": [14]},
                {"id": 3, "name": "JZ3", "segmentIds": [15]},
            ],
            switches=[
                {"id": 1, "name": "W1", "normalSegId": 14, "reverseSegId": 15, "frogSegId": 13},
            ],
            routes=[
                {
                    "id": 1, "name": "R-NORMAL", "type": "0x0001",
                    "startSignalId": 5, "endSignalId": 10,
                    "axleSectionIds": [1, 2],  # → segs 13,14
                },
                {
                    "id": 2, "name": "R-REVERSE", "type": "0x0001",
                    "startSignalId": 15, "endSignalId": 20,
                    "axleSectionIds": [1, 3],  # → segs 13,15
                },
            ],
        )
        self.cat = RouteCatalog(self.line_map)

    def test_route_with_normal_switch(self) -> None:
        """Route covering segs 13+14 → needs W1 at NORMAL."""
        r = self.cat.get("1")
        self.assertEqual(r.required_switches, {"1": "NORMAL"})

    def test_route_with_reverse_switch(self) -> None:
        """Route covering segs 13+15 → needs W1 at REVERSE."""
        r = self.cat.get("2")
        self.assertEqual(r.required_switches, {"1": "REVERSE"})

    def test_switch_conflict_detected(self) -> None:
        """Routes 1 and 2 share switch 1 but need opposite positions."""
        self.assertTrue(self.cat.are_hostile("1", "2"))


# ---------------------------------------------------------------------------
# SwitchLockService
# ---------------------------------------------------------------------------


class SwitchLockServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.switch_defs = [
            SwitchDef("1", "W1", normal_seg_id=14, reverse_seg_id=15, frog_seg_id=13),
            SwitchDef("2", "W2", normal_seg_id=20, reverse_seg_id=21, frog_seg_id=19),
        ]
        self.svc = SwitchLockService(self.switch_defs)

    def test_default_position_is_normal(self) -> None:
        self.assertEqual(self.svc.get_position("1"), "NORMAL")

    def test_lock_succeeds(self) -> None:
        self.assertTrue(self.svc.lock("1", "NORMAL", "R-001"))
        self.assertTrue(self.svc.is_locked("1"))
        self.assertEqual(self.svc.locked_by("1"), "R-001")

    def test_lock_fails_when_locked_by_another_route_in_opposite(self) -> None:
        self.svc.lock("1", "NORMAL", "R-001")
        # Another route tries to lock same switch in REVERSE → fails
        self.assertFalse(self.svc.lock("1", "REVERSE", "R-002"))

    def test_lock_ok_when_same_position_by_different_route(self) -> None:
        self.svc.lock("1", "NORMAL", "R-001")
        # Another route needs same position → shares the lock
        self.assertTrue(self.svc.is_available_for("1", "NORMAL"))

    def test_unlock_releases_switch(self) -> None:
        self.svc.lock("1", "NORMAL", "R-001")
        self.svc.unlock("1", "R-001")
        self.assertFalse(self.svc.is_locked("1"))

    def test_unlock_wrong_route_does_nothing(self) -> None:
        self.svc.lock("1", "NORMAL", "R-001")
        self.svc.unlock("1", "R-999")
        self.assertTrue(self.svc.is_locked("1"))

    def test_faulted_switch_unavailable(self) -> None:
        self.svc.set_fault("1")
        self.assertFalse(self.svc.is_available_for("1", "NORMAL"))
        self.assertFalse(self.svc.is_available_for("1", "REVERSE"))

    def test_clear_fault_restores_availability(self) -> None:
        self.svc.set_fault("1")
        self.svc.clear_fault("1")
        self.assertTrue(self.svc.is_available_for("1", "NORMAL"))


# ---------------------------------------------------------------------------
# InterlockingRuleEngine
# ---------------------------------------------------------------------------


class InterlockingRuleEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        line_map = _tiny_line_map(
            axle_sections=[
                {"id": 1, "name": "JZ1", "segmentIds": [13]},
                {"id": 2, "name": "JZ2", "segmentIds": [14]},
                {"id": 3, "name": "JZ3", "segmentIds": [15]},
            ],
            segments=[
                {"id": 13, "lengthM": 100.0},
                {"id": 14, "lengthM": 100.0},
                {"id": 15, "lengthM": 100.0},
            ],
            switches=[
                {"id": 1, "name": "W1", "normalSegId": 14, "reverseSegId": 15, "frogSegId": 13},
            ],
            routes=[
                {
                    "id": 1, "name": "R-NORMAL", "type": "0x0001",
                    "startSignalId": 5, "endSignalId": 10,
                    "axleSectionIds": [1, 2],
                    "protectionSectionIds": [],
                },
                {
                    "id": 2, "name": "R-REVERSE", "type": "0x0001",
                    "startSignalId": 20, "endSignalId": 25,
                    "axleSectionIds": [1, 3],
                    "protectionSectionIds": [],
                },
            ],
        )
        self.catalog = RouteCatalog(line_map)
        self.section_occ = SectionOccupationService(line_map)
        self.switch_lock = SwitchLockService(
            [SwitchDef("1", "W1", normal_seg_id=14, reverse_seg_id=15, frog_seg_id=13)]
        )
        self.engine = InterlockingRuleEngine(self.catalog, self.section_occ, self.switch_lock)

    def test_all_clear_route_checks_ok(self) -> None:
        result = self.engine.check("1", "T001")
        self.assertTrue(result.ok)

    def test_route_not_found(self) -> None:
        result = self.engine.check("999", "T001")
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "ROUTE_NOT_FOUND")

    def test_section_occupied_blocks_route(self) -> None:
        self.section_occ.update(
            [_make_train(seg_id=14, offset_m=50.0, length_m=60.0)],
            _fake_track_query({14: {"id": 14, "lengthM": 100.0}}),
        )
        result = self.engine.check("1", "T002")
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "SECTION_OCCUPIED")

    def test_own_occupied_entry_section_allows_route(self) -> None:
        self.section_occ.update(
            [_make_train(train_id="T001", seg_id=14, offset_m=50.0, length_m=60.0)],
            _fake_track_query({14: {"id": 14, "lengthM": 100.0}}),
        )
        result = self.engine.check("1", "T001")
        self.assertTrue(result.ok)
    def test_conflict_route_locked_blocks(self) -> None:
        result = self.engine.check("1", "T001", locked_route_ids=frozenset(["2"]))
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "CONFLICT_ROUTE_LOCKED")

    def test_switch_unavailable_blocks(self) -> None:
        self.switch_lock.set_fault("1")
        result = self.engine.check("1", "T001")
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "SWITCH_UNAVAILABLE")


# ---------------------------------------------------------------------------
# RouteService — lifecycle
# ---------------------------------------------------------------------------


class RouteServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        line_map = _tiny_line_map(
            axle_sections=[
                {"id": 1, "name": "JZ1", "segmentIds": [13]},
                {"id": 2, "name": "JZ2", "segmentIds": [14]},
            ],
            switches=[
                {"id": 1, "name": "W1", "normalSegId": 14, "reverseSegId": 15, "frogSegId": 13},
            ],
            routes=[
                {
                    "id": 1, "name": "R-NORMAL", "type": "0x0001",
                    "startSignalId": 5, "endSignalId": 10,
                    "axleSectionIds": [1, 2],
                    "protectionSectionIds": [],
                },
            ],
        )
        self.catalog = RouteCatalog(line_map)
        self.section_occ = SectionOccupationService(line_map)
        self.switch_lock = SwitchLockService(
            [SwitchDef("1", "W1", normal_seg_id=14, reverse_seg_id=15, frog_seg_id=13)]
        )
        self.engine = InterlockingRuleEngine(self.catalog, self.section_occ, self.switch_lock)
        self.svc = RouteService(self.catalog, self.engine, self.section_occ, self.switch_lock)

    def test_request_locks_route(self) -> None:
        req = RouteRequest("REQ-1", "1", "T001")
        result = self.svc.request(req)
        self.assertTrue(result.accepted)
        self.assertEqual(result.state, "LOCKED")
        self.assertTrue(self.svc.is_locked("1"))

    def test_request_fails_when_section_occupied(self) -> None:
        self.section_occ.update(
            [_make_train(seg_id=14, offset_m=50.0, length_m=60.0)],
            _fake_track_query({14: {"id": 14, "lengthM": 100.0}}),
        )
        req = RouteRequest("REQ-1", "1", "T002")
        result = self.svc.request(req)
        self.assertFalse(result.accepted)
        self.assertEqual(result.failure_reason, "SECTION_OCCUPIED")

    def test_request_fails_when_conflicting_route_locked(self) -> None:
        # First request locks successfully
        self.svc.request(RouteRequest("REQ-1", "1", "T001"))
        # Second request for the same route should fail
        result = self.svc.request(RouteRequest("REQ-2", "1", "T002"))
        self.assertFalse(result.accepted)
        self.assertEqual(result.failure_reason, "CONFLICT_ROUTE_LOCKED")

    def test_remove_owner_releases_its_locked_routes(self) -> None:
        self.assertTrue(self.svc.request(RouteRequest("REQ-1", "1", "T001")).accepted)
        released = self.svc.release_routes_owned_by("T001")
        self.assertEqual(released, ["1"])
        self.assertFalse(self.svc.is_locked("1"))
        self.assertEqual(self.svc.release_routes_owned_by("T002"), [])
    def test_cancel_releases_route(self) -> None:
        self.svc.request(RouteRequest("REQ-1", "1", "T001"))
        self.svc.release("1", "CANCEL")
        self.assertFalse(self.svc.is_locked("1"))

    def test_release_unlocks_switches(self) -> None:
        self.svc.request(RouteRequest("REQ-1", "1", "T001"))
        self.svc.release("1", "AUTO")
        self.assertFalse(self.switch_lock.is_locked("1"))

    def test_auto_release_when_all_sections_cleared(self) -> None:
        """A route releases only after its train clears the final route section."""
        self.svc.request(RouteRequest("REQ-1", "1", "T001"))
        self.section_occ.update(
            [_make_train(train_id="T001", seg_id=13, offset_m=80.0, length_m=60.0)],
            _fake_track_query({13: {"id": 13, "lengthM": 100.0}}),
        )
        self.svc.update()
        self.assertTrue(self.svc.is_locked("1"))
        self.section_occ.update(
            [_make_train(train_id="T001", seg_id=14, offset_m=80.0, length_m=60.0)],
            _fake_track_query({14: {"id": 14, "lengthM": 100.0}}),
        )
        self.svc.update()
        self.assertTrue(self.svc.is_locked("1"))
        self.section_occ.update([], _fake_track_query())
        self.svc.update()
        self.assertFalse(self.svc.is_locked("1"))
    def test_update_only_releases_our_train_sections(self) -> None:
        """Another train behind does not retain a route after its owner clears it."""
        self.svc.request(RouteRequest("REQ-1", "1", "T001"))
        self.section_occ.update(
            [
                _make_train(train_id="T001", seg_id=13),
                _make_train(train_id="T002", seg_id=13),
            ],
            _fake_track_query({13: {"id": 13, "lengthM": 100.0}}),
        )
        self.svc.update()
        self.section_occ.update(
            [
                _make_train(train_id="T001", seg_id=14),
                _make_train(train_id="T002", seg_id=13),
            ],
            _fake_track_query({13: {"id": 13, "lengthM": 100.0}, 14: {"id": 14, "lengthM": 100.0}}),
        )
        self.svc.update()
        self.section_occ.update(
            [_make_train(train_id="T002", seg_id=13)],
            _fake_track_query({13: {"id": 13, "lengthM": 100.0}}),
        )
        self.svc.update()
        self.assertFalse(self.svc.is_locked("1"))

# ---------------------------------------------------------------------------
# SignalAspectResolver
# ---------------------------------------------------------------------------


    def test_approach_lock_uses_mapped_axle_section_not_same_numbered_id(self) -> None:
        """Approach-section 18 maps to JZ61, not coincidentally numbered JZ18."""
        line_map = {
            "axleSections": [
                {"id": 18, "name": "JZ18", "segmentIds": [18]},
                {"id": 61, "name": "JZ61", "segmentIds": [61]},
                {"id": 62, "name": "JZ62", "segmentIds": [62]},
            ],
            "logicalSections": [],
            "segments": [
                {"id": 18, "lengthM": 100.0},
                {"id": 61, "lengthM": 100.0},
                {"id": 62, "lengthM": 100.0},
            ],
            "routes": [{
                "id": 1, "name": "R-APPROACH", "type": "0x0001",
                "startSignalId": 5, "endSignalId": 10,
                "axleSectionIds": [62], "protectionSectionIds": [],
                "pointApproachSectionIds": [18],
            }],
            "switches": [],
            "pointApproachSections": [{"id": 18, "axleSectionIds": [61]}],
        }
        catalog = RouteCatalog(line_map)
        section_occ = SectionOccupationService(line_map)
        switch_lock = SwitchLockService([])
        rules = InterlockingRuleEngine(catalog, section_occ, switch_lock)
        service = RouteService(catalog, rules, section_occ, switch_lock)
        self.assertTrue(service.request(RouteRequest("REQ-1", "1", "T001")).accepted)

        section_occ.update(
            [_make_train(train_id="T001", seg_id=18, offset_m=50.0)],
            _fake_track_query({18: {"id": 18, "lengthM": 100.0}}),
        )
        service.update()
        self.assertEqual(service.state_of("1"), "LOCKED")

        section_occ.update(
            [_make_train(train_id="T001", seg_id=61, offset_m=50.0)],
            _fake_track_query({61: {"id": 61, "lengthM": 100.0}}),
        )
        service.update()
        self.assertEqual(service.state_of("1"), "APPROACH_LOCKED")
class SignalAspectResolverTests(unittest.TestCase):
    """测试信号机灯色解析逻辑。

    使用两条级联进路的场景：
    进路 1：信号3 → 信号1，区段 {1}
    进路 2：信号5 → 信号3，区段 {2}
    当两者都锁闭时：信号5 GREEN（前方空闲），信号3 YELLOW（终端信号1是RED）
    """

    def setUp(self) -> None:
        line_map = _tiny_line_map(
            axle_sections=[
                {"id": 1, "name": "JZ1", "segmentIds": [13]},
                {"id": 2, "name": "JZ2", "segmentIds": [14]},
            ],
            segments=[
                {"id": 13, "lengthM": 100.0},
                {"id": 14, "lengthM": 100.0},
            ],
            switches=[],
            routes=[
                {
                    "id": 1, "name": "R-1", "type": "0x0001",
                    "startSignalId": 3, "endSignalId": 1,
                    "axleSectionIds": [1], "protectionSectionIds": [],
                },
                {
                    "id": 2, "name": "R-2", "type": "0x0001",
                    "startSignalId": 5, "endSignalId": 3,
                    "axleSectionIds": [2], "protectionSectionIds": [],
                },
            ],
        )
        self.catalog = RouteCatalog(line_map)
        self.section_occ = SectionOccupationService(line_map)
        self.switch_lock = SwitchLockService([])
        self.engine = InterlockingRuleEngine(self.catalog, self.section_occ, self.switch_lock)
        self.route_svc = RouteService(self.catalog, self.engine, self.section_occ, self.switch_lock)
        self.resolver = SignalAspectResolver(
            self.catalog, self.route_svc, self.section_occ, self.switch_lock
        )

    def test_no_routes_all_red(self) -> None:
        """无进路锁闭时所有信号应为 RED。"""
        self.assertEqual(self.resolver.resolve(3), "RED")
        self.assertEqual(self.resolver.resolve(5), "RED")

    def test_single_route_locked_end_red_means_yellow(self) -> None:
        """只锁进路1 → 信号3 GREEN/YELLOW（看终端信号1）。"""
        self.route_svc.request(RouteRequest("REQ-1", "1", "T001"))
        # 进路1的终端是信号1，信号1没有锁闭进路 → RED
        # 终端RED → 始端YELLOW（规则5）
        self.assertEqual(self.resolver.resolve(3), "YELLOW")

    def test_two_routes_cascaded_gives_green(self) -> None:
        """进路1和2都锁闭 → 信号5 GREEN（前方全线空闲）。"""
        self.route_svc.request(RouteRequest("REQ-1", "1", "T001"))
        self.route_svc.request(RouteRequest("REQ-2", "2", "T002"))
        # 信号3的终端是信号1（无锁闭进路 → RED）
        # 但看看信号5：进路2已锁，终端是信号3
        # 信号3的终端信号1是RED → 信号3 YELLOW
        # 信号5的终端信号3是YELLOW → 信号5 GREEN（不是RED就放GREEN）
        self.assertEqual(self.resolver.resolve(5), "GREEN")

    def test_section_occupied_makes_red(self) -> None:
        """进路锁闭但前方区段被占用 → RED。"""
        self.route_svc.request(RouteRequest("REQ-1", "1", "T001"))
        # 占用区段1
        self.section_occ.update(
            [_make_train(train_id="T002", seg_id=13, offset_m=50.0, length_m=60.0)],
            _fake_track_query({13: {"id": 13, "lengthM": 100.0}}),
        )
        self.assertEqual(self.resolver.resolve(3), "RED")

    def test_own_entry_section_keeps_departure_signal_open(self) -> None:
        self.route_svc.request(RouteRequest("REQ-1", "1", "T001"))
        self.section_occ.update(
            [_make_train(train_id="T001", seg_id=13, offset_m=50.0, length_m=60.0)],
            _fake_track_query({13: {"id": 13, "lengthM": 100.0}}),
        )
        self.assertEqual(self.resolver.resolve(3), "YELLOW")

    def test_departure_signal_returns_red_after_owner_enters_route(self) -> None:
        self.route_svc.request(RouteRequest("REQ-1", "1", "T001"))
        self.section_occ.update(
            [_make_train(train_id="T001", seg_id=13, offset_m=50.0, length_m=60.0)],
            _fake_track_query({13: {"id": 13, "lengthM": 100.0}}),
        )
        self.route_svc.update()

        self.assertEqual(self.resolver.resolve(3), "RED")
    def test_signal_fault_forces_red(self) -> None:
        """信号故障 → 强制 RED，不管进路是否锁闭。"""
        self.route_svc.request(RouteRequest("REQ-1", "1", "T001"))
        self.resolver.set_fault("3")
        self.assertEqual(self.resolver.resolve(3), "RED")

    def test_clear_fault_restores(self) -> None:
        """清除信号故障 → 恢复正常灯色。"""
        self.route_svc.request(RouteRequest("REQ-1", "1", "T001"))
        self.resolver.set_fault("3")
        self.resolver.clear_fault("3")
        self.assertEqual(self.resolver.resolve(3), "YELLOW")

    def test_resolve_all_snapshot(self) -> None:
        """resolve_all() 返回所有进路的始端和终端信号灯色。"""
        snap = self.resolver.snapshot()
        ids = {s["signalId"] for s in snap}
        self.assertIn("3", ids)
        self.assertIn("1", ids)
        self.assertIn("5", ids)


if __name__ == "__main__":
    unittest.main()
