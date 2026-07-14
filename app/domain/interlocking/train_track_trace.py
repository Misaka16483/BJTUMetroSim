"""Ordered physical Seg history used to trace a train's rear end.

``PathTrackQuery`` describes one approved path.  A train body, however, can
span the end of the preceding path, a platform segment, and the beginning of
the following path.  This object keeps that *actual* ordered traversal so
section occupancy never has to choose a turnout branch from global topology.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Iterator


def _normalise_direction(direction: str) -> str:
    value = str(direction).upper()
    if value in {"FORWARD", "UP"}:
        return "FORWARD"
    if value in {"BACKWARD", "DOWN"}:
        return "BACKWARD"
    raise ValueError(f"unsupported train direction: {direction!r}")


@dataclass(frozen=True)
class TrainTrackTrace:
    """A cursor into the exact physical Seg sequence travelled by a train.

    ``segment_ids`` are ordered in the direction named by ``trace_direction``.
    They intentionally allow duplicate IDs: a train can legitimately revisit a
    Seg after changing ends or travelling through a loop.  ``head_index`` is
    therefore required to identify the precise current occurrence.

    The main engine should retain enough leading and trailing entries to cover
    the full vehicle length, and create a new cursor with
    :meth:`with_active_head` whenever its active PathPlan changes.  At a
    terminal reversal that cursor must move to the physical former tail before
    the engine publishes the new opposite TrainState.direction.  The class is
    also a compatible read-only track query for callers that only need
    ``get_segment``.
    """

    track: Any
    segment_ids: tuple[int, ...]
    head_index: int
    trace_direction: str = "FORWARD"

    def __init__(
        self,
        track: Any,
        segment_ids: Iterable[int],
        *,
        head_index: int | None = None,
        trace_direction: str = "FORWARD",
    ) -> None:
        ids = tuple(int(segment_id) for segment_id in segment_ids)
        if not ids:
            raise ValueError("train track trace requires at least one segment")
        cursor = len(ids) - 1 if head_index is None else int(head_index)
        if cursor < 0 or cursor >= len(ids):
            raise ValueError("head_index is outside the train track trace")
        object.__setattr__(self, "track", track)
        object.__setattr__(self, "segment_ids", ids)
        object.__setattr__(self, "head_index", cursor)
        object.__setattr__(self, "trace_direction", _normalise_direction(trace_direction))

    @property
    def head_segment_id(self) -> int:
        """The exact Seg occurrence currently containing the train head."""
        return self.segment_ids[self.head_index]

    def get_segment(self, seg_id: int) -> dict[str, Any] | None:
        """Expose immutable Seg metadata like :class:`PathTrackQuery`."""
        return self.track.get_segment(int(seg_id))

    def with_head(self, head_index: int) -> "TrainTrackTrace":
        """Return this trace with its cursor moved to a known Seg occurrence."""
        return self.with_active_head(head_index)

    def with_active_head(self, head_index: int) -> "TrainTrackTrace":
        """Record an engine-selected active vehicle head occurrence.

        This is the hand-off required for a terminal reversal: the engine
        selects the former tail occurrence, then publishes a TrainState with
        the opposite direction.  No topology search or position teleportation
        occurs inside the occupancy subsystem.
        """
        return TrainTrackTrace(
            self.track,
            self.segment_ids,
            head_index=head_index,
            trace_direction=self.trace_direction,
        )

    def with_head_segment(
        self,
        seg_id: int,
        *,
        occurrence: int | None = None,
    ) -> "TrainTrackTrace":
        """Move the cursor to ``seg_id`` without silently guessing duplicates.

        For duplicate Seg IDs callers must pass the zero-based ``occurrence``
        in traversal order.  This forces the engine to record a genuine visit
        rather than accidentally attaching a tail to an older one.
        """
        matches = [index for index, value in enumerate(self.segment_ids) if value == int(seg_id)]
        if not matches:
            raise ValueError(f"segment {seg_id} is not in this train track trace")
        if occurrence is None:
            if len(matches) != 1:
                raise ValueError(f"segment {seg_id} occurs more than once; specify occurrence")
            return self.with_active_head(matches[0])
        if occurrence < 0 or occurrence >= len(matches):
            raise ValueError(f"occurrence {occurrence} is outside segment {seg_id} visits")
        return self.with_active_head(matches[occurrence])

    def rear_segment_ids(self, head_seg_id: int, train_direction: str) -> Iterator[int]:
        """Yield the exact Seg occurrences from head towards tail.

        The cursor must agree with the TrainState.  A disagreement is an
        integration error, not a reason to fall back to a globally selected
        turnout branch.
        """
        if int(head_seg_id) != self.head_segment_id:
            raise ValueError(
                "TrainState.seg_id does not match TrainTrackTrace head "
                f"({head_seg_id} != {self.head_segment_id})"
            )
        direction = _normalise_direction(train_direction)
        step = -1 if direction == self.trace_direction else 1
        index = self.head_index
        while 0 <= index < len(self.segment_ids):
            yield self.segment_ids[index]
            index += step

    def get_next_segments(self, seg_id: int, direction: str = "forward") -> list[dict[str, Any]]:
        """Compatibility query for a single unambiguous occurrence.

        Occupancy code uses :meth:`rear_segment_ids` so repeated Seg IDs retain
        their identity.  This method is provided for consumers of the existing
        ``PathTrackQuery`` protocol and deliberately refuses ambiguous repeats.
        """
        indices = [index for index, value in enumerate(self.segment_ids) if value == int(seg_id)]
        if len(indices) != 1:
            return []
        query_direction = _normalise_direction(direction)
        step = 1 if query_direction == self.trace_direction else -1
        next_index = indices[0] + step
        if not 0 <= next_index < len(self.segment_ids):
            return []
        segment = self.get_segment(self.segment_ids[next_index])
        return [segment] if segment is not None else []
