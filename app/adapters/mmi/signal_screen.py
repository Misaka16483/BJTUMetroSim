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
    run_dir: int = 0
    reserve: int = 0
    speed_kmh: float = 0.0
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
    """Builder for the 68-byte MMI signal screen frame seen on the wire.

    Working traffic keeps _nRunDir/_nReserve at 42..43 and starts speed at 44.
    Its _uTotalLen field is the legacy value 62 despite a 68-byte TCP payload.
    """

    frame_size_bytes = 68
    header_total_size_bytes = 62
    data_size_bytes = 42

    def build(self, state: SignalScreenState) -> bytes:
        now = datetime.now()
        timestamp_ms = state.timestamp_ms if state.timestamp_ms is not None else int(now.timestamp() * 1000)
        frame = bytearray(self.frame_size_bytes)
        write_display_header(frame, self.header_total_size_bytes, self.data_size_bytes, timestamp_ms)
        self._write_time(frame, state, now)
        write_u8(frame, 36, state.curr_station_id)
        write_u8(frame, 37, state.next_station_id)
        write_u8(frame, 38, state.end_station_id)
        write_u8(frame, 39, state.cm_state)
        write_u8(frame, 40, state.mm_state)
        write_u8(frame, 41, state.ctc_state)
        write_u8(frame, 42, state.run_dir)
        write_u8(frame, 43, state.reserve)
        write_float_le(frame, 44, state.speed_kmh)
        write_float_le(frame, 48, state.acceleration_mps2)
        write_u16_le(frame, 52, state.pull_switch)
        write_u16_le(frame, 54, state.speed_limit)
        write_u8(frame, 56, state.mode)
        write_u8(frame, 57, state.pull_state)
        write_u8(frame, 58, state.brake_state)
        write_u8(frame, 59, state.urgency_stop_state)
        write_u8(frame, 60, state.event_id)
        write_u8(frame, 61, state.sig_state)
        write_u16_le(frame, 62, state.train_no)
        write_float_le(frame, 64, state.next_station_distance_m)
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
