from __future__ import annotations

import unittest

from app.adapters.cab import (
    MitsubishiPlcCabOutputFrameBuilder,
    MitsubishiPlcCabOutputState,
    MitsubishiPlcCabParser,
    MitsubishiPlcTcpClient,
)
from app.domain.control import CabControlService, DriverHandleMode


def _write_word(frame: bytearray, offset: int, value: int) -> None:
    frame[offset : offset + 2] = value.to_bytes(2, byteorder="little", signed=False)


class _ChunkedSocket:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    def recv(self, size: int) -> bytes:
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        if len(chunk) <= size:
            return chunk
        self.chunks.insert(0, chunk[size:])
        return chunk[:size]


class MitsubishiPlcCabParserTests(unittest.TestCase):
    def test_parser_reads_traction_handle_and_percent(self) -> None:
        frame = bytearray(46)
        _write_word(frame, 26, 1234)
        _write_word(frame, 38, 1)
        _write_word(frame, 40, 60)

        driver_input = MitsubishiPlcCabParser().parse_driver_input(bytes(frame))

        self.assertEqual(driver_input.handle_mode, DriverHandleMode.TRACTION)
        self.assertEqual(driver_input.traction_percent, 60.0)
        self.assertEqual(driver_input.reported_speed_mps, 12.34)
        self.assertFalse(driver_input.emergency_brake)

    def test_parser_reads_brake_handle_and_command_conversion(self) -> None:
        frame = bytearray(46)
        _write_word(frame, 38, 2)
        _write_word(frame, 42, 40)

        driver_input = MitsubishiPlcCabParser().parse_driver_input(bytes(frame))
        command = CabControlService().command_from_driver_input(driver_input)

        self.assertEqual(driver_input.handle_mode, DriverHandleMode.BRAKE)
        self.assertEqual(command.traction_percent, 0)
        self.assertEqual(command.brake_percent, 40.0)

    def test_emergency_button_overrides_handle(self) -> None:
        frame = bytearray(46)
        frame[28] = 0b0000_0001
        _write_word(frame, 38, 1)
        _write_word(frame, 40, 70)

        driver_input = MitsubishiPlcCabParser().parse_driver_input(bytes(frame))
        command = CabControlService().command_from_driver_input(driver_input)

        self.assertTrue(driver_input.emergency_brake)
        self.assertTrue(command.emergency_brake)
        self.assertEqual(command.traction_percent, 0)

    def test_parser_rejects_wrong_frame_length(self) -> None:
        with self.assertRaises(ValueError):
            MitsubishiPlcCabParser().parse_driver_input(b"\x00" * 45)

    def test_tcp_client_reads_complete_frame_from_chunked_plc_stream(self) -> None:
        frame = bytearray(46)
        _write_word(frame, 26, 456)
        _write_word(frame, 38, 1)
        _write_word(frame, 40, 80)
        client = MitsubishiPlcTcpClient()
        client._socket = _ChunkedSocket([bytes(frame[:8]), bytes(frame[8:31]), bytes(frame[31:])])
        driver_input = client.read_driver_input(train_id="T009")

        self.assertEqual(driver_input.train_id, "T009")
        self.assertEqual(driver_input.handle_mode, DriverHandleMode.TRACTION)
        self.assertEqual(driver_input.traction_percent, 80.0)
        self.assertEqual(driver_input.reported_speed_mps, 4.56)

    def test_output_builder_creates_strict_26_byte_status_frame(self) -> None:
        frame = MitsubishiPlcCabOutputFrameBuilder().build(
            MitsubishiPlcCabOutputState(
                year=2025,
                month=7,
                day=16,
                hour=15,
                minute=11,
                second=3,
                door_open_light=True,
                doors_closed_light=True,
                ato_available=True,
                ato_active=True,
            )
        )

        self.assertEqual(len(frame), 26)
        self.assertEqual(frame[0:4], b"\x55\xaa\x55\xaa")
        self.assertEqual(int.from_bytes(frame[4:6], "little"), 26)
        self.assertEqual(int.from_bytes(frame[6:8], "little"), 2)
        self.assertEqual(frame[24], 0b0011_0000)
        self.assertEqual(frame[25], 0b0000_0101)

    def test_output_builder_extends_frame_when_speed_feedback_is_present(self) -> None:
        frame = MitsubishiPlcCabOutputFrameBuilder().build(
            MitsubishiPlcCabOutputState(vehicle_speed_cmps=1234)
        )

        self.assertEqual(len(frame), 28)
        self.assertEqual(int.from_bytes(frame[4:6], "little"), 28)
        self.assertEqual(int.from_bytes(frame[6:8], "little"), 4)
        self.assertEqual(int.from_bytes(frame[26:28], "little"), 1234)


if __name__ == "__main__":
    unittest.main()
