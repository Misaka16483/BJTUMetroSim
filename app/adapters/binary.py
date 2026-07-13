from __future__ import annotations

import select
import socket
import struct
from dataclasses import dataclass
from typing import TypeVar


T = TypeVar("T")


def require_frame_size(frame: bytes | bytearray, expected_size: int, frame_name: str) -> None:
    if len(frame) != expected_size:
        raise ValueError(f"{frame_name} must be {expected_size} bytes")


def write_u8(frame: bytearray, offset: int, value: int) -> None:
    _require_range(value, 0, 0xFF, "u8")
    frame[offset] = value


def write_u16_le(frame: bytearray, offset: int, value: int) -> None:
    _require_range(value, 0, 0xFFFF, "u16")
    struct.pack_into("<H", frame, offset, value)


def write_u32_le(frame: bytearray, offset: int, value: int) -> None:
    _require_range(value, 0, 0xFFFFFFFF, "u32")
    struct.pack_into("<I", frame, offset, value)


def write_u64_le(frame: bytearray, offset: int, value: int) -> None:
    _require_range(value, 0, 0xFFFFFFFFFFFFFFFF, "u64")
    struct.pack_into("<Q", frame, offset, value)


def write_float_le(frame: bytearray, offset: int, value: float) -> None:
    struct.pack_into("<f", frame, offset, float(value))


def read_u8(frame: bytes | bytearray, offset: int) -> int:
    return frame[offset]


def read_u16_le(frame: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<H", frame, offset)[0]


def read_u64_le(frame: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<Q", frame, offset)[0]


def set_bit(frame: bytearray, byte_offset: int, bit_offset: int, enabled: bool) -> None:
    if enabled:
        frame[byte_offset] |= 1 << bit_offset
    else:
        frame[byte_offset] &= ~(1 << bit_offset)


def write_u8_array(frame: bytearray, offset: int, values: list[int], count: int, default: int = 0) -> None:
    padded = _padded(values, count, default)
    for index, value in enumerate(padded):
        write_u8(frame, offset + index, value)


def write_u16_array_le(frame: bytearray, offset: int, values: list[int], count: int, default: int = 0) -> None:
    padded = _padded(values, count, default)
    for index, value in enumerate(padded):
        write_u16_le(frame, offset + index * 2, value)


def write_u32_array_le(frame: bytearray, offset: int, values: list[int], count: int, default: int = 0) -> None:
    padded = _padded(values, count, default)
    for index, value in enumerate(padded):
        write_u32_le(frame, offset + index * 4, value)


def write_float_array_le(frame: bytearray, offset: int, values: list[float], count: int, default: float = 0.0) -> None:
    padded = _padded(values, count, default)
    for index, value in enumerate(padded):
        write_float_le(frame, offset + index * 4, value)


def write_display_header(
    frame: bytearray,
    total_len: int,
    data_len: int,
    timestamp_ms: int,
    msg_id: int = 0,
    protocol_id: int = 0,
    verify_type: int = 0,
    verify_code: int = 0,
) -> None:
    frame[0:4] = b"\x55\xaa\x55\xaa"
    write_u16_le(frame, 4, total_len)
    write_u16_le(frame, 6, data_len)
    write_u64_le(frame, 8, timestamp_ms)
    write_u16_le(frame, 16, verify_type)
    write_u16_le(frame, 18, verify_code)
    write_u16_le(frame, 20, protocol_id)
    write_u16_le(frame, 22, msg_id)


@dataclass
class TcpFrameClient:
    host: str
    port: int
    timeout_s: float = 3.0

    def __post_init__(self) -> None:
        if not self.host:
            raise ValueError("host must not be empty")
        if self.port <= 0 or self.port > 65535:
            raise ValueError("port must be between 1 and 65535")
        if self.timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
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

    def __enter__(self) -> TcpFrameClient:
        self.connect()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def send_frame(self, frame: bytes) -> None:
        if self._socket is None:
            raise RuntimeError("TCP client is not connected")
        self._socket.sendall(frame)

    def receive_available(self, max_bytes: int = 65536) -> bytes:
        """Read all bytes currently waiting without blocking the send loop."""
        if self._socket is None:
            raise RuntimeError("TCP client is not connected")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        chunks: list[bytes] = []
        remaining = max_bytes
        while remaining > 0:
            readable, _, _ = select.select([self._socket], [], [], 0)
            if not readable:
                break
            chunk = self._socket.recv(min(4096, remaining))
            if not chunk:
                raise ConnectionError("TCP peer closed the connection")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)


def _padded(values: list[T], count: int, default: T) -> list[T]:
    if len(values) > count:
        raise ValueError(f"expected at most {count} values")
    return values + [default] * (count - len(values))


def _require_range(value: int, minimum: int, maximum: int, field_name: str) -> None:
    if value < minimum or value > maximum:
        raise ValueError(f"{field_name} must be between {minimum} and {maximum}")
