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
    def test_parser_decodes_complete_plc_input_frame(self) -> None:
        frame = bytearray(46)
        frame[0:4] = b"\xaa\x55\xaa\x55"
        for offset, value in (
            (4, 46),
            (6, 22),
            (8, 2025),
            (10, 7),
            (12, 16),
            (14, 15),
            (16, 11),
            (18, 3),
            (20, 2),
            (22, 0x1234),
            (26, 1234),
            (30, 4),
            (32, 2),
            (36, 2),
            (38, 4),
            (40, 150),
            (42, 130),
            (44, 0xBEEF),
        ):
            _write_word(frame, offset, value)
        frame[24] = 0b1110_0110
        frame[25] = 0b0000_1111
        frame[28] = 0xFF
        frame[29] = 0x0F
        frame[34] = 0xFF
        frame[35] = 0x0F

        state = MitsubishiPlcCabParser().parse(bytes(frame), train_id="T009")

        self.assertEqual(state.train_id, "T009")
        self.assertEqual(state.identify_bytes, b"\xaa\x55\xaa\x55")
        self.assertEqual((state.total_len, state.data_len), (46, 22))
        self.assertEqual((state.year, state.month, state.day), (2025, 7, 16))
        self.assertEqual((state.hour, state.minute, state.second), (15, 11, 3))
        self.assertEqual((state.verify_type, state.verify_code), (2, 0x1234))
        self.assertEqual((state.status_byte_24, state.status_byte_25), (0b1110_0110, 0x0F))
        self.assertTrue(
            all(
                (
                    state.high_breaker_closed_light,
                    state.brake_release_fault_light,
                    state.doors_closed_light,
                    state.network_fault_light,
                    state.auto_turnback_available,
                    state.ato_available,
                    state.wash_mode_entered,
                    state.ato_active,
                    state.auto_turnback_active,
                )
            )
        )
        self.assertEqual(state.vehicle_speed_cmps, 1234)
        self.assertAlmostEqual(state.vehicle_speed_mps, 12.34)
        self.assertEqual((state.control_byte_28, state.door_control_byte_29), (0xFF, 0x0F))
        self.assertTrue(
            all(
                (
                    state.emergency_brake_button_locked,
                    state.bus_control_button_locked,
                    state.forced_release_triggered,
                    state.forced_air_pump_triggered,
                    state.emergency_command_button_locked,
                    state.parking_brake_apply_triggered,
                    state.parking_brake_release_triggered,
                    state.horn_triggered,
                    state.open_left_door_triggered,
                    state.open_right_door_triggered,
                    state.close_left_door_triggered,
                    state.close_right_door_triggered,
                )
            )
        )
        self.assertEqual((state.external_lighting_mode, state.external_lighting), (4, "HIGH_BEAM"))
        self.assertEqual((state.door_mode, state.door_operation_mode), (2, "AUTO"))
        self.assertEqual((state.command_byte_34, state.mode_byte_35), (0xFF, 0x0F))
        self.assertTrue(
            all(
                (
                    state.high_acceleration_button_locked,
                    state.cab_lighting_button_locked,
                    state.mode_upgrade_confirm_triggered,
                    state.mode_downgrade_confirm_triggered,
                    state.confirm_triggered,
                    state.auto_turnback_triggered,
                    state.traction_aux_reset_triggered,
                    state.ato_start_triggered,
                    state.wash_mode_switch_locked,
                    state.key_switch_locked,
                    state.vigilance_triggered,
                    state.vigilance_release_allowed,
                )
            )
        )
        self.assertEqual((state.direction_handle_code, state.direction), (2, "REVERSE"))
        self.assertEqual(state.main_handle_code, 4)
        self.assertEqual((state.traction_percent_raw, state.brake_percent_raw), (150, 130))
        self.assertEqual(state.reserved_word, 0xBEEF)
        driver_input = state.to_driver_input()
        self.assertEqual(driver_input.handle_mode, DriverHandleMode.FAST_BRAKE)
        self.assertEqual((driver_input.traction_percent, driver_input.brake_percent), (100.0, 100.0))

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
