from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.adapters.binary import (
    TcpFrameClient,
    write_display_header,
    write_float_le,
    write_u8,
    write_u16_le,
)


@dataclass(frozen=True)
class SignalScreenState:
    timestamp_ms: int | None = None
    year: int | None = None
    month: int | None = None
    day: int | None = None
    hour: int | None = None
    minute: int | None = None
    second: int | None = None
    curr_station_id: int = 0
    next_station_id: int = 0
    end_station_id: int = 0
    cm_state: int = 0
    mm_state: int = 0
    ctc_state: int = 0
    speed_mps: float = 0.0
    acceleration_mps2: float = 0.0
    pull_switch: int = 0
    speed_limit: int = 0
    mode: int = 0
    pull_state: int = 0
    brake_state: int = 0
    urgency_stop_state: int = 0
    event_id: int = 0
    sig_state: int = 0
    train_no: int = 0
    next_station_distance_m: float = 0.0


class SignalScreenFrameBuilder:
    """Builder for the 66-byte MMI signal screen frame.

    The source table overlaps _nRunDir/_nReserve with _nSpeed at byte 42. The
    frame here follows the continuous numeric display layout from _nSpeed at 42,
    because it matches the remaining offsets and total length.
    """

    frame_size_bytes = 66
    data_size_bytes = 42

    def build(self, state: SignalScreenState) -> bytes:
        now = datetime.now()
        timestamp_ms = state.timestamp_ms if state.timestamp_ms is not None else int(now.timestamp() * 1000)
        frame = bytearray(self.frame_size_bytes)
        write_display_header(frame, self.frame_size_bytes, self.data_size_bytes, timestamp_ms)
        self._write_time(frame, state, now)
        write_u8(frame, 36, state.curr_station_id)
        write_u8(frame, 37, state.next_station_id)
        write_u8(frame, 38, state.end_station_id)
        write_u8(frame, 39, state.cm_state)
        write_u8(frame, 40, state.mm_state)
        write_u8(frame, 41, state.ctc_state)
        write_float_le(frame, 42, state.speed_mps)
        write_float_le(frame, 46, state.acceleration_mps2)
        write_u16_le(frame, 50, state.pull_switch)
        write_u16_le(frame, 52, state.speed_limit)
        write_u8(frame, 54, state.mode)
        write_u8(frame, 55, state.pull_state)
        write_u8(frame, 56, state.brake_state)
        write_u8(frame, 57, state.urgency_stop_state)
        write_u8(frame, 58, state.event_id)
        write_u8(frame, 59, state.sig_state)
        write_u16_le(frame, 60, state.train_no)
        write_float_le(frame, 62, state.next_station_distance_m)
        return bytes(frame)

    @staticmethod
    def _write_time(frame: bytearray, state: SignalScreenState, now: datetime) -> None:
        write_u16_le(frame, 24, state.year if state.year is not None else now.year)
        write_u16_le(frame, 26, state.month if state.month is not None else now.month)
        write_u16_le(frame, 28, state.day if state.day is not None else now.day)
        write_u16_le(frame, 30, state.hour if state.hour is not None else now.hour)
        write_u16_le(frame, 32, state.minute if state.minute is not None else now.minute)
        write_u16_le(frame, 34, state.second if state.second is not None else now.second)


@dataclass
class SignalScreenClient:
    host: str = "192.168.100.121"
    port: int = 9999
    timeout_s: float = 3.0
    builder: SignalScreenFrameBuilder = field(default_factory=SignalScreenFrameBuilder)

    def send_state(self, state: SignalScreenState) -> None:
        frame = self.builder.build(state)
        with TcpFrameClient(self.host, self.port, self.timeout_s) as client:
            client.send_frame(frame)
