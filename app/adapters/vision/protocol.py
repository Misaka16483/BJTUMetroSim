from __future__ import annotations

from dataclasses import dataclass, field
import struct
from typing import Iterable


COMPACT_LAYOUT = "compact"
FIXED_LAYOUT = "fixed"
SUPPORTED_LAYOUTS = frozenset({COMPACT_LAYOUT, FIXED_LAYOUT})

MAX_SIGNALS = 255
MAX_SWITCHES = 127
MAX_OTHER_TRAINS = 128
FIXED_FRAME_SIZE = 1556


@dataclass(frozen=True)
class VisionTrainState:
    """Train fields used by the Version 1.3 vision packet."""

    section_distance_mm: int = 0
    edge_id: int = 0
    direction: int = 1
    speed_cmps: int = 0


@dataclass(frozen=True)
class VisionFrameState:
    """Protocol-neutral values before compact/fixed binary serialization."""

    live_counter: int = 0
    signal_states: tuple[int, ...] = ()
    switch_states: tuple[int, ...] = ()
    speed_mmps: int = 0
    dwell_time_s: int = 0
    run_state: int = 0x13
    acceleration_percent: int = 0
    section_distance_mm: int = 0
    edge_id: int = 0
    direction: int = 1
    other_trains: tuple[VisionTrainState, ...] = field(default_factory=tuple)


class VisionFrameBuilder:
    """Encode the two packet layouts found in the supplied documents.

    ``compact`` follows the task document's ``BuildCommunicationPacket`` call:
    arrays contain only the number of values announced by their count byte.
    ``fixed`` follows the ``strTCMS2VIEW`` appendix and emits the full C arrays.
    All multi-byte fields are explicitly little-endian, matching the Windows C
    sample and avoiding host-native alignment/byte-order differences.
    """

    def __init__(self, layout: str = COMPACT_LAYOUT) -> None:
        normalized = str(layout).lower()
        if normalized not in SUPPORTED_LAYOUTS:
            raise ValueError(f"unsupported vision frame layout: {layout}")
        self.layout = normalized

    def build(self, state: VisionFrameState) -> bytes:
        self._validate(state)
        fixed = self.layout == FIXED_LAYOUT
        frame = bytearray(struct.pack("<i", state.live_counter))
        self._write_counted_u8(frame, state.signal_states, MAX_SIGNALS, fixed)
        self._write_counted_u8(frame, state.switch_states, MAX_SWITCHES, fixed)
        frame.extend(
            struct.pack(
                "<ihBbi hbB".replace(" ", ""),
                state.speed_mmps,
                state.dwell_time_s,
                state.run_state,
                state.acceleration_percent,
                state.section_distance_mm,
                state.edge_id,
                state.direction,
                len(state.other_trains),
            )
        )
        self._write_other_trains(frame, state.other_trains, fixed)
        if fixed and len(frame) != FIXED_FRAME_SIZE:
            raise AssertionError(f"fixed vision frame must be {FIXED_FRAME_SIZE} bytes")
        return bytes(frame)

    @staticmethod
    def _write_counted_u8(frame: bytearray, values: tuple[int, ...], maximum: int, fixed: bool) -> None:
        frame.append(len(values))
        frame.extend(values)
        if fixed:
            frame.extend(bytes(maximum - len(values)))

    @staticmethod
    def _write_other_trains(
        frame: bytearray,
        trains: tuple[VisionTrainState, ...],
        fixed: bool,
    ) -> None:
        count = MAX_OTHER_TRAINS if fixed else len(trains)
        padded = trains + (VisionTrainState(),) * (count - len(trains))
        for train in padded:
            frame.extend(struct.pack("<i", train.section_distance_mm))
        for train in padded:
            frame.extend(struct.pack("<h", train.edge_id))
        for train in padded:
            frame.extend(struct.pack("<b", train.direction))
        for train in padded:
            frame.extend(struct.pack("<h", train.speed_cmps))

    @staticmethod
    def _validate(state: VisionFrameState) -> None:
        _check_range("live_counter", state.live_counter, -(2**31), 2**31 - 1)
        _check_values("signal_states", state.signal_states, MAX_SIGNALS, 0, 255)
        _check_values("switch_states", state.switch_states, MAX_SWITCHES, 0, 255)
        _check_range("speed_mmps", state.speed_mmps, -(2**31), 2**31 - 1)
        _check_range("dwell_time_s", state.dwell_time_s, -(2**15), 2**15 - 1)
        _check_range("run_state", state.run_state, 0, 255)
        _check_range("acceleration_percent", state.acceleration_percent, -128, 127)
        _check_range("section_distance_mm", state.section_distance_mm, -(2**31), 2**31 - 1)
        _check_range("edge_id", state.edge_id, -(2**15), 2**15 - 1)
        _check_range("direction", state.direction, -128, 127)
        if len(state.other_trains) > MAX_OTHER_TRAINS:
            raise ValueError(f"other_trains supports at most {MAX_OTHER_TRAINS} values")
        for index, train in enumerate(state.other_trains):
            _check_range(f"other_trains[{index}].section_distance_mm", train.section_distance_mm, -(2**31), 2**31 - 1)
            _check_range(f"other_trains[{index}].edge_id", train.edge_id, -(2**15), 2**15 - 1)
            _check_range(f"other_trains[{index}].direction", train.direction, -128, 127)
            _check_range(f"other_trains[{index}].speed_cmps", train.speed_cmps, -(2**15), 2**15 - 1)


class VisionFrameParser:
    """Decode frames for dry-run, loopback testing and packet inspection."""

    def __init__(self, layout: str = COMPACT_LAYOUT) -> None:
        self.builder = VisionFrameBuilder(layout)
        self.layout = self.builder.layout

    def parse(self, frame: bytes) -> VisionFrameState:
        if self.layout == FIXED_LAYOUT and len(frame) != FIXED_FRAME_SIZE:
            raise ValueError(f"fixed vision frame must be {FIXED_FRAME_SIZE} bytes")
        view = memoryview(frame)
        offset = 0

        def take(fmt: str) -> tuple[int, ...]:
            nonlocal offset
            size = struct.calcsize(fmt)
            if offset + size > len(view):
                raise ValueError("vision frame is truncated")
            values = struct.unpack_from(fmt, view, offset)
            offset += size
            return values

        live_counter = take("<i")[0]
        signal_count = take("<B")[0]
        signal_width = MAX_SIGNALS if self.layout == FIXED_LAYOUT else signal_count
        signal_values = take(f"<{signal_width}B") if signal_width else ()
        signal_states = tuple(signal_values[:signal_count])
        switch_count = take("<B")[0]
        switch_width = MAX_SWITCHES if self.layout == FIXED_LAYOUT else switch_count
        switch_values = take(f"<{switch_width}B") if switch_width else ()
        switch_states = tuple(switch_values[:switch_count])
        (
            speed_mmps,
            dwell_time_s,
            run_state,
            acceleration_percent,
            section_distance_mm,
            edge_id,
            direction,
            other_count,
        ) = take("<ihBbihbB")
        other_width = MAX_OTHER_TRAINS if self.layout == FIXED_LAYOUT else other_count
        distances = take(f"<{other_width}i") if other_width else ()
        edge_ids = take(f"<{other_width}h") if other_width else ()
        directions = take(f"<{other_width}b") if other_width else ()
        speeds = take(f"<{other_width}h") if other_width else ()
        if offset != len(view):
            raise ValueError(f"vision frame has {len(view) - offset} trailing bytes")
        other_trains = tuple(
            VisionTrainState(distances[i], edge_ids[i], directions[i], speeds[i])
            for i in range(other_count)
        )
        state = VisionFrameState(
            live_counter=live_counter,
            signal_states=signal_states,
            switch_states=switch_states,
            speed_mmps=speed_mmps,
            dwell_time_s=dwell_time_s,
            run_state=run_state,
            acceleration_percent=acceleration_percent,
            section_distance_mm=section_distance_mm,
            edge_id=edge_id,
            direction=direction,
            other_trains=other_trains,
        )
        self.builder._validate(state)
        return state


def _check_values(name: str, values: Iterable[int], maximum: int, low: int, high: int) -> None:
    values_tuple = tuple(values)
    if len(values_tuple) > maximum:
        raise ValueError(f"{name} supports at most {maximum} values")
    for index, value in enumerate(values_tuple):
        _check_range(f"{name}[{index}]", value, low, high)


def _check_range(name: str, value: int, low: int, high: int) -> None:
    if not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name} must be an integer between {low} and {high}")
