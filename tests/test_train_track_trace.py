"""Focused regression tests for route-authorized train-body tracing."""

from __future__ import annotations

import unittest
from typing import Any

from app.domain.interlocking.section_occupation import SectionOccupationService
from app.domain.interlocking.train_track_trace import TrainTrackTrace
from app.domain.signal.models import TrainState


class _Track:
    def __init__(self, segments: dict[int, dict[str, Any]]) -> None:
        self._segments = segments

    def get_segment(self, seg_id: int) -> dict[str, Any] | None:
        return self._segments.get(int(seg_id))

    def get_next_segments(self, seg_id: int, direction: str = "forward") -> list[dict[str, Any]]:
        return []


def _line_map(*segment_ids: int) -> dict[str, Any]:
    return {
        "segments": [{"id": segment_id, "lengthM": 100.0} for segment_id in segment_ids],
        "axleSections": [
            {"id": segment_id, "name": f"JZ{segment_id}", "segmentIds": [segment_id]}
            for segment_id in segment_ids
        ],
        "logicalSections": [],
    }


def _train(
    train_id: str,
    seg_id: int,
    offset_m: float,
    length_m: float,
    trace: TrainTrackTrace,
    direction: str = "FORWARD",
) -> TrainState:
    return TrainState(
        train_id=train_id,
        seg_id=seg_id,
        offset_m=offset_m,
        position_m=1.0,
        speed_mps=1.0,
        length_m=length_m,
        direction=direction,
        path_track=trace,
    )


class TrainTrackTraceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.track = _Track({segment_id: {"id": segment_id, "lengthM": 100.0} for segment_id in range(10, 60)})

    def test_cross_path_trace_keeps_previous_path_and_platform_under_tail(self) -> None:
        """Tail coverage spans previous PathPlan + platform + next PathPlan."""
        svc = SectionOccupationService(_line_map(10, 11, 12))
        # 10 is the old PathPlan end, 11 the current platform, and 12 belongs
        # to the newly approved PathPlan.  The head is only 10m into S12.
        trace = TrainTrackTrace(self.track, [10, 11, 12])
        train = _train("T1", 12, 10.0, 150.0, trace)

        svc.update([train], self.track)

        self.assertEqual(svc.covered_segments_for("T1"), {10, 11, 12})
        self.assertTrue(svc.is_axle_occupied(10))
        self.assertTrue(svc.is_axle_occupied(11))
        self.assertTrue(svc.is_axle_occupied(12))

    def test_turnout_tail_uses_authorized_branch_instead_of_global_topology(self) -> None:
        svc = SectionOccupationService(_line_map(41, 43, 44))
        # The global map could also have reached S43 from S41.  This trace is
        # the actually locked and travelled S44 -> S43 route.
        trace = TrainTrackTrace(self.track, [44, 43])
        train = _train("T1", 43, 10.0, 80.0, trace)

        svc.update([train], self.track)

        self.assertEqual(svc.covered_segments_for("T1"), {43, 44})
        self.assertFalse(svc.is_axle_occupied(41))

    def test_tail_sections_clear_one_by_one_as_the_train_advances(self) -> None:
        svc = SectionOccupationService(_line_map(10, 11, 12))
        trace = TrainTrackTrace(self.track, [10, 11, 12])

        svc.update([_train("T1", 12, 10.0, 150.0, trace)], self.track)
        self.assertEqual(svc.covered_segments_for("T1"), {10, 11, 12})

        svc.update([_train("T1", 12, 60.0, 150.0, trace)], self.track)
        self.assertEqual(svc.covered_segments_for("T1"), {11, 12})

        svc.update([_train("T1", 12, 100.0, 80.0, trace)], self.track)
        self.assertEqual(svc.covered_segments_for("T1"), {12})

    def test_duplicate_segment_visits_require_an_explicit_cursor_occurrence(self) -> None:
        trace = TrainTrackTrace(self.track, [50, 51, 50], head_index=2)

        self.assertEqual(list(trace.rear_segment_ids(50, "FORWARD")), [50, 51, 50])
        with self.assertRaisesRegex(ValueError, "occurs more than once"):
            trace.with_head_segment(50)
        self.assertEqual(trace.with_head_segment(50, occurrence=0).head_index, 0)

    def test_terminal_reversal_uses_engine_selected_active_head_and_direction(self) -> None:
        svc = SectionOccupationService(_line_map(10, 11, 12))
        arriving = TrainTrackTrace(self.track, [10, 11, 12])
        # The engine changes active ends at the terminal: S10 is now the head
        # and the train is travelling backward along the same physical trace.
        departing = arriving.with_active_head(0)
        train = _train("T1", 10, 90.0, 80.0, departing, direction="BACKWARD")

        svc.update([train], self.track)

        self.assertEqual(svc.covered_segments_for("T1"), {10, 11})

    def test_multiple_traces_keep_trains_on_separate_turnout_branches(self) -> None:
        svc = SectionOccupationService(_line_map(41, 43, 44, 45))
        via_44 = _train("T1", 43, 10.0, 80.0, TrainTrackTrace(self.track, [44, 43]))
        via_45 = _train("T2", 43, 10.0, 80.0, TrainTrackTrace(self.track, [45, 43]))

        svc.update([via_44, via_45], self.track)

        self.assertEqual(svc.covered_segments_for("T1"), {43, 44})
        self.assertEqual(svc.covered_segments_for("T2"), {43, 45})
        self.assertEqual(sorted(svc.axle_occupied_by(43)), ["T1", "T2"])
        self.assertEqual(svc.axle_occupied_by(44), ["T1"])
        self.assertEqual(svc.axle_occupied_by(45), ["T2"])


if __name__ == "__main__":
    unittest.main()
