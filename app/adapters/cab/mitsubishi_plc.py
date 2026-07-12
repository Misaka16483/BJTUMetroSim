from __future__ import annotations

import socket
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator

from app.adapters.binary import read_u16_le, set_bit, write_u16_le
from app.domain.control import DriverHandleMode, DriverInput


@dataclass(frozen=True)
class MitsubishiPlcCabInputState:
    """Complete decoded state of one 46-byte PLC -> host cab frame."""

    train_id: str
    identify_bytes: bytes
    total_len: int
    data_len: int
    year: int
    month: int
    day: int
    hour: int
    minute: int
    second: int
    verify_type: int
    verify_code: int
    status_byte_24: int
    status_byte_25: int
    high_breaker_closed_light: bool
    brake_release_fault_light: bool
    doors_closed_light: bool
    network_fault_light: bool
    auto_turnback_available: bool
    ato_available: bool
    wash_mode_entered: bool
    ato_active: bool
    auto_turnback_active: bool
    vehicle_speed_cmps: int
    control_byte_28: int
    door_control_byte_29: int
    emergency_brake_button_locked: bool
    bus_control_button_locked: bool
    forced_release_triggered: bool
    forced_air_pump_triggered: bool
    emergency_command_button_locked: bool
    parking_brake_apply_triggered: bool
    parking_brake_release_triggered: bool
    horn_triggered: bool
    open_left_door_triggered: bool
    open_right_door_triggered: bool
    close_left_door_triggered: bool
    close_right_door_triggered: bool
    external_lighting_mode: int
    door_mode: int
    command_byte_34: int
    mode_byte_35: int
    high_acceleration_button_locked: bool
    cab_lighting_button_locked: bool
    mode_upgrade_confirm_triggered: bool
    mode_downgrade_confirm_triggered: bool
    confirm_triggered: bool
    auto_turnback_triggered: bool
    traction_aux_reset_triggered: bool
    ato_start_triggered: bool
    wash_mode_switch_locked: bool
    key_switch_locked: bool
    vigilance_triggered: bool
    vigilance_release_allowed: bool
    direction_handle_code: int
    main_handle_code: int
    traction_percent_raw: int
    brake_percent_raw: int
    reserved_word: int

    @property
    def vehicle_speed_mps(self) -> float:
        return self.vehicle_speed_cmps / 100.0

    @property
    def direction(self) -> str:
        return {0: "NEUTRAL", 1: "FORWARD", 2: "REVERSE"}.get(self.direction_handle_code, "UNKNOWN")

    @property
    def external_lighting(self) -> str:
        return {0: "OFF", 1: "AUTO", 2: "LOW_BEAM", 4: "HIGH_BEAM"}.get(
            self.external_lighting_mode,
            "UNKNOWN",
        )

    @property
    def door_operation_mode(self) -> str:
        return {0: "SEMI_AUTO", 1: "MANUAL", 2: "AUTO"}.get(self.door_mode, "UNKNOWN")

    def to_driver_input(self) -> DriverInput:
        return DriverInput(
            train_id=self.train_id,
            handle_mode=MitsubishiPlcCabParser.parse_handle_mode(self.main_handle_code),
            traction_percent=MitsubishiPlcCabParser.clamp_percent(self.traction_percent_raw),
            brake_percent=MitsubishiPlcCabParser.clamp_percent(self.brake_percent_raw),
            emergency_brake=self.emergency_brake_button_locked,
            reported_speed_mps=self.vehicle_speed_mps,
            source="MITSUBISHI_PLC",
        )


@dataclass(frozen=True)
class MitsubishiPlcCabParser:
    """Parser for Mitsubishi PLC cab input frames.

    The protocol document defines a 46-byte PLC -> host frame with little-endian
    WORD fields. ``parse`` exposes every documented input field, while
    ``parse_driver_input`` preserves the smaller backend control-model view.
    TCP connection management is intentionally separate.
    """

    frame_size_bytes: int = 46

    def parse(self, frame: bytes, train_id: str = "T001") -> MitsubishiPlcCabInputState:
        if len(frame) != self.frame_size_bytes:
            raise ValueError(f"PLC cab frame must be {self.frame_size_bytes} bytes")

        bit = self._read_bit
        return MitsubishiPlcCabInputState(
            train_id=train_id,
            identify_bytes=frame[0:4],
            total_len=read_u16_le(frame, 4),
            data_len=read_u16_le(frame, 6),
            year=read_u16_le(frame, 8),
            month=read_u16_le(frame, 10),
            day=read_u16_le(frame, 12),
            hour=read_u16_le(frame, 14),
            minute=read_u16_le(frame, 16),
            second=read_u16_le(frame, 18),
            verify_type=read_u16_le(frame, 20),
            verify_code=read_u16_le(frame, 22),
            status_byte_24=frame[24],
            status_byte_25=frame[25],
            high_breaker_closed_light=bit(frame, 24, 1),
            brake_release_fault_light=bit(frame, 24, 2),
            doors_closed_light=bit(frame, 24, 5),
            network_fault_light=bit(frame, 24, 6),
            auto_turnback_available=bit(frame, 24, 7),
            ato_available=bit(frame, 25, 0),
            wash_mode_entered=bit(frame, 25, 1),
            ato_active=bit(frame, 25, 2),
            auto_turnback_active=bit(frame, 25, 3),
            vehicle_speed_cmps=read_u16_le(frame, 26),
            control_byte_28=frame[28],
            door_control_byte_29=frame[29],
            emergency_brake_button_locked=bit(frame, 28, 0),
            bus_control_button_locked=bit(frame, 28, 1),
            forced_release_triggered=bit(frame, 28, 2),
            forced_air_pump_triggered=bit(frame, 28, 3),
            emergency_command_button_locked=bit(frame, 28, 4),
            parking_brake_apply_triggered=bit(frame, 28, 5),
            parking_brake_release_triggered=bit(frame, 28, 6),
            horn_triggered=bit(frame, 28, 7),
            open_left_door_triggered=bit(frame, 29, 0),
            open_right_door_triggered=bit(frame, 29, 1),
            close_left_door_triggered=bit(frame, 29, 2),
            close_right_door_triggered=bit(frame, 29, 3),
            external_lighting_mode=read_u16_le(frame, 30),
            door_mode=read_u16_le(frame, 32),
            command_byte_34=frame[34],
            mode_byte_35=frame[35],
            high_acceleration_button_locked=bit(frame, 34, 0),
            cab_lighting_button_locked=bit(frame, 34, 1),
            mode_upgrade_confirm_triggered=bit(frame, 34, 2),
            mode_downgrade_confirm_triggered=bit(frame, 34, 3),
            confirm_triggered=bit(frame, 34, 4),
            auto_turnback_triggered=bit(frame, 34, 5),
            traction_aux_reset_triggered=bit(frame, 34, 6),
            ato_start_triggered=bit(frame, 34, 7),
            wash_mode_switch_locked=bit(frame, 35, 0),
            key_switch_locked=bit(frame, 35, 1),
            vigilance_triggered=bit(frame, 35, 2),
            vigilance_release_allowed=bit(frame, 35, 3),
            direction_handle_code=read_u16_le(frame, 36),
            main_handle_code=read_u16_le(frame, 38),
            traction_percent_raw=read_u16_le(frame, 40),
            brake_percent_raw=read_u16_le(frame, 42),
            reserved_word=read_u16_le(frame, 44),
        )

    def parse_driver_input(self, frame: bytes, train_id: str = "T001") -> DriverInput:
        return self.parse(frame, train_id=train_id).to_driver_input()

    @staticmethod
    def parse_handle_mode(handle_code: int) -> DriverHandleMode:
        if handle_code == 1:
            return DriverHandleMode.TRACTION
        if handle_code == 2:
            return DriverHandleMode.BRAKE
        if handle_code == 4:
            return DriverHandleMode.FAST_BRAKE
        return DriverHandleMode.NEUTRAL

    @staticmethod
    def clamp_percent(value: int) -> float:
        return float(min(max(value, 0), 100))

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
        return self.read_input_state(train_id=train_id).to_driver_input()

    def read_input_state(self, train_id: str = "T001") -> MitsubishiPlcCabInputState:
        return self._parser().parse(self.recv_frame(), train_id=train_id)

    def iter_driver_inputs(self, train_id: str = "T001", max_frames: int | None = None) -> Iterator[DriverInput]:
        count = 0
        while max_frames is None or count < max_frames:
            yield self.read_driver_input(train_id=train_id)
            count += 1

    def iter_input_states(
        self,
        train_id: str = "T001",
        max_frames: int | None = None,
    ) -> Iterator[MitsubishiPlcCabInputState]:
        count = 0
        while max_frames is None or count < max_frames:
            yield self.read_input_state(train_id=train_id)
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
