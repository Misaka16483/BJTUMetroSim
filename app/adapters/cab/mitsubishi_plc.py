from __future__ import annotations

import socket
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator

from app.adapters.binary import set_bit, write_u16_le
from app.domain.control import DriverHandleMode, DriverInput


@dataclass(frozen=True)
class MitsubishiPlcCabParser:
    """Parser for Mitsubishi PLC cab input frames.

    The protocol document defines a 46-byte PLC -> host frame with little-endian
    WORD fields. This parser only translates the driver controls needed by the
    backend control model; TCP connection management is intentionally separate.
    """

    frame_size_bytes: int = 46
    emergency_button_byte_offset: int = 28
    emergency_button_bit_offset: int = 0
    speed_word_offset: int = 26
    handle_word_offset: int = 38
    traction_percent_word_offset: int = 40
    brake_percent_word_offset: int = 42

    def parse_driver_input(self, frame: bytes, train_id: str = "T001") -> DriverInput:
        if len(frame) != self.frame_size_bytes:
            raise ValueError(f"PLC cab frame must be {self.frame_size_bytes} bytes")

        handle_code = self._read_word(frame, self.handle_word_offset)
        traction_percent = self._read_word(frame, self.traction_percent_word_offset)
        brake_percent = self._read_word(frame, self.brake_percent_word_offset)
        speed_cmps = self._read_word(frame, self.speed_word_offset)
        emergency_brake = self._read_bit(
            frame,
            self.emergency_button_byte_offset,
            self.emergency_button_bit_offset,
        )

        return DriverInput(
            train_id=train_id,
            handle_mode=self._parse_handle_mode(handle_code),
            traction_percent=self._clamp_percent(traction_percent),
            brake_percent=self._clamp_percent(brake_percent),
            emergency_brake=emergency_brake,
            reported_speed_mps=speed_cmps / 100.0,
            source="MITSUBISHI_PLC",
        )

    @staticmethod
    def _parse_handle_mode(handle_code: int) -> DriverHandleMode:
        if handle_code == 1:
            return DriverHandleMode.TRACTION
        if handle_code == 2:
            return DriverHandleMode.BRAKE
        if handle_code == 4:
            return DriverHandleMode.FAST_BRAKE
        return DriverHandleMode.NEUTRAL

    @staticmethod
    def _clamp_percent(value: int) -> float:
        return float(min(max(value, 0), 100))

    @staticmethod
    def _read_word(frame: bytes, offset: int) -> int:
        return int.from_bytes(frame[offset : offset + 2], byteorder="little", signed=False)

    @staticmethod
    def _read_bit(frame: bytes, byte_offset: int, bit_offset: int) -> bool:
        return bool(frame[byte_offset] & (1 << bit_offset))


@dataclass(frozen=True)
class MitsubishiPlcCabOutputState:
    high_breaker_closed_light: bool = False
    brake_release_fault_light: bool = False
    door_open_light: bool = False
    doors_closed_light: bool = False
    network_fault_light: bool = False
    auto_turnback_available: bool = False
    ato_available: bool = False
    wash_mode_entered: bool = False
    ato_active: bool = False
    auto_turnback_active: bool = False
    vehicle_speed_cmps: int | None = None
    year: int | None = None
    month: int | None = None
    day: int | None = None
    hour: int | None = None
    minute: int | None = None
    second: int | None = None
    verify_type: int = 0
    verify_code: int = 0


class MitsubishiPlcCabOutputFrameBuilder:
    strict_frame_size_bytes = 26
    speed_extension_frame_size_bytes = 28

    def build(self, state: MitsubishiPlcCabOutputState) -> bytes:
        frame_size = self.speed_extension_frame_size_bytes if state.vehicle_speed_cmps is not None else self.strict_frame_size_bytes
        frame = bytearray(frame_size)
        frame[0:4] = b"\x55\xaa\x55\xaa"
        write_u16_le(frame, 4, frame_size)
        write_u16_le(frame, 6, frame_size - 24)
        self._write_time(frame, state)
        write_u16_le(frame, 20, state.verify_type)
        write_u16_le(frame, 22, state.verify_code)
        self._write_status_bits(frame, state)
        if state.vehicle_speed_cmps is not None:
            write_u16_le(frame, 26, state.vehicle_speed_cmps)
        return bytes(frame)

    @staticmethod
    def _write_time(frame: bytearray, state: MitsubishiPlcCabOutputState) -> None:
        now = datetime.now()
        write_u16_le(frame, 8, state.year if state.year is not None else now.year)
        write_u16_le(frame, 10, state.month if state.month is not None else now.month)
        write_u16_le(frame, 12, state.day if state.day is not None else now.day)
        write_u16_le(frame, 14, state.hour if state.hour is not None else now.hour)
        write_u16_le(frame, 16, state.minute if state.minute is not None else now.minute)
        write_u16_le(frame, 18, state.second if state.second is not None else now.second)

    @staticmethod
    def _write_status_bits(frame: bytearray, state: MitsubishiPlcCabOutputState) -> None:
        set_bit(frame, 24, 1, state.high_breaker_closed_light)
        set_bit(frame, 24, 2, state.brake_release_fault_light)
        set_bit(frame, 24, 4, state.door_open_light)
        set_bit(frame, 24, 5, state.doors_closed_light)
        set_bit(frame, 24, 6, state.network_fault_light)
        set_bit(frame, 24, 7, state.auto_turnback_available)
        set_bit(frame, 25, 0, state.ato_available)
        set_bit(frame, 25, 1, state.wash_mode_entered)
        set_bit(frame, 25, 2, state.ato_active)
        set_bit(frame, 25, 3, state.auto_turnback_active)


@dataclass
class MitsubishiPlcTcpClient:
    """TCP client for the driver cab PLC server.

    The PLC opens one TCP server per port and pushes 46-byte cab input frames
    every 100 ms after a host connects. This client only owns transport and
    framing; protocol translation stays in ``MitsubishiPlcCabParser``.
    """

    host: str = "192.168.100.123"
    port: int = 8001
    timeout_s: float = 3.0
    parser: MitsubishiPlcCabParser | None = None

    def __post_init__(self) -> None:
        if not self.host:
            raise ValueError("host must not be empty")
        if self.port <= 0 or self.port > 65535:
            raise ValueError("port must be between 1 and 65535")
        if self.timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        if self.parser is None:
            self.parser = MitsubishiPlcCabParser()
        self._socket: socket.socket | None = None

    def connect(self) -> None:
        if self._socket is not None:
            return
        self._socket = socket.create_connection((self.host, self.port), timeout=self.timeout_s)
        self._socket.settimeout(self.timeout_s)

    def close(self) -> None:
        if self._socket is None:
            return
        self._socket.close()
        self._socket = None

    def __enter__(self) -> MitsubishiPlcTcpClient:
        self.connect()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def recv_frame(self) -> bytes:
        parser = self._parser()
        return self._recv_exactly(parser.frame_size_bytes)

    def read_driver_input(self, train_id: str = "T001") -> DriverInput:
        return self._parser().parse_driver_input(self.recv_frame(), train_id=train_id)

    def iter_driver_inputs(self, train_id: str = "T001", max_frames: int | None = None) -> Iterator[DriverInput]:
        count = 0
        while max_frames is None or count < max_frames:
            yield self.read_driver_input(train_id=train_id)
            count += 1

    def send_frame(self, frame: bytes) -> None:
        self._require_socket().sendall(frame)

    def send_output_state(
        self,
        state: MitsubishiPlcCabOutputState,
        builder: MitsubishiPlcCabOutputFrameBuilder | None = None,
    ) -> None:
        output_builder = builder or MitsubishiPlcCabOutputFrameBuilder()
        self.send_frame(output_builder.build(state))

    def _recv_exactly(self, size_bytes: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size_bytes
        sock = self._require_socket()
        while remaining > 0:
            chunk = sock.recv(remaining)
            if not chunk:
                raise ConnectionError("PLC connection closed before a complete frame was received")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _require_socket(self) -> socket.socket:
        if self._socket is None:
            raise RuntimeError("PLC client is not connected")
        return self._socket

    def _parser(self) -> MitsubishiPlcCabParser:
        if self.parser is None:
            raise RuntimeError("PLC parser is not configured")
        return self.parser
