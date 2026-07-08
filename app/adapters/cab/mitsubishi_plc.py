from __future__ import annotations

from dataclasses import dataclass

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
