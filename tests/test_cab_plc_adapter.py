from __future__ import annotations

import unittest

from app.adapters.cab import MitsubishiPlcCabParser
from app.domain.control import CabControlService, DriverHandleMode


def _write_word(frame: bytearray, offset: int, value: int) -> None:
    frame[offset : offset + 2] = value.to_bytes(2, byteorder="little", signed=False)


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
        self.assertEqual(command.traction_level, 0)
        self.assertEqual(command.brake_level, 2)

    def test_emergency_button_overrides_handle(self) -> None:
        frame = bytearray(46)
        frame[28] = 0b0000_0001
        _write_word(frame, 38, 1)
        _write_word(frame, 40, 70)

        driver_input = MitsubishiPlcCabParser().parse_driver_input(bytes(frame))
        command = CabControlService().command_from_driver_input(driver_input)

        self.assertTrue(driver_input.emergency_brake)
        self.assertTrue(command.emergency_brake)
        self.assertEqual(command.traction_level, 0)

    def test_parser_rejects_wrong_frame_length(self) -> None:
        with self.assertRaises(ValueError):
            MitsubishiPlcCabParser().parse_driver_input(b"\x00" * 45)


if __name__ == "__main__":
    unittest.main()
