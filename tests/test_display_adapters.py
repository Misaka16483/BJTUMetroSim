from __future__ import annotations

import struct
import unittest

from app.adapters.hmi import NetworkScreenFrameBuilder, NetworkScreenState, TractionCutoffRequestParser
from app.adapters.mmi import SignalScreenFrameBuilder, SignalScreenState


class NetworkScreenAdapterTests(unittest.TestCase):
    def test_network_screen_builder_writes_572_byte_frame(self) -> None:
        frame = NetworkScreenFrameBuilder().build(
            NetworkScreenState(
                timestamp_ms=123456789,
                year=2025,
                month=7,
                day=16,
                hour=15,
                minute=11,
                second=3,
                curr_station_id=1,
                next_station_id=2,
                end_station_id=9,
                power_state=1,
                speed_mps=12.5,
                acceleration_mps2=0.75,
                power_pull=123,
                net_pressure=1500,
                speed_limit=80,
                level_pos=1,
                run_mode=0x21,
                master_voltage=1500,
                brake_pressures=[101, 102, 103, 104, 105, 106],
                usage_rates=[10, 20, 30, 40, 50, 60],
                train_no=9,
            )
        )

        self.assertEqual(len(frame), 572)
        self.assertEqual(frame[0:4], b"\x55\xaa\x55\xaa")
        self.assertEqual(int.from_bytes(frame[4:6], "little"), 572)
        self.assertEqual(int.from_bytes(frame[6:8], "little"), 548)
        self.assertEqual(int.from_bytes(frame[8:16], "little"), 123456789)
        self.assertEqual(int.from_bytes(frame[24:26], "little"), 2025)
        self.assertEqual(frame[36], 1)
        self.assertAlmostEqual(struct.unpack_from("<f", frame, 40)[0], 12.5)
        self.assertAlmostEqual(struct.unpack_from("<f", frame, 44)[0], 0.75)
        self.assertEqual(int.from_bytes(frame[156:158], "little"), 101)
        self.assertEqual(frame[168], 10)
        self.assertEqual(int.from_bytes(frame[570:572], "little"), 9)

    def test_traction_cutoff_request_parser_reads_car_bits(self) -> None:
        frame = bytearray(26)
        frame[0:4] = b"\x55\xaa\x55\xaa"
        frame[4:6] = (26).to_bytes(2, "little")
        frame[6:8] = (2).to_bytes(2, "little")
        frame[8:16] = (123456789).to_bytes(8, "little")
        frame[16:18] = (2).to_bytes(2, "little")
        frame[18:20] = (0x1234).to_bytes(2, "little")
        frame[20:22] = (9).to_bytes(2, "little")
        frame[22:24] = (7).to_bytes(2, "little")
        frame[24] = 0b0010_0101
        frame[25] = 0xA5

        request = TractionCutoffRequestParser().parse(bytes(frame))

        self.assertEqual(request.timestamp_ms, 123456789)
        self.assertEqual(request.identify_bytes, b"\x55\xaa\x55\xaa")
        self.assertEqual((request.total_len, request.data_len), (26, 2))
        self.assertEqual((request.verify_type, request.verify_code), (2, 0x1234))
        self.assertEqual(request.protocol_id, 9)
        self.assertEqual(request.msg_id, 7)
        self.assertEqual(request.pull_control_mask, 0b0010_0101)
        self.assertEqual(request.requested_car_numbers, [1, 3, 6])
        self.assertEqual(request.reserve, 0xA5)


class SignalScreenAdapterTests(unittest.TestCase):
    def test_signal_screen_builder_writes_66_byte_frame(self) -> None:
        frame = SignalScreenFrameBuilder().build(
            SignalScreenState(
                timestamp_ms=123456789,
                year=2025,
                month=7,
                day=16,
                hour=15,
                minute=11,
                second=3,
                curr_station_id=1,
                next_station_id=2,
                end_station_id=9,
                cm_state=1,
                mm_state=1,
                ctc_state=0,
                speed_mps=10.25,
                acceleration_mps2=-0.5,
                pull_switch=3,
                speed_limit=80,
                mode=4,
                pull_state=1,
                brake_state=2,
                urgency_stop_state=0,
                event_id=12,
                sig_state=5,
                train_no=9,
                next_station_distance_m=321.5,
            )
        )

        self.assertEqual(len(frame), 66)
        self.assertEqual(frame[0:4], b"\x55\xaa\x55\xaa")
        self.assertEqual(int.from_bytes(frame[4:6], "little"), 66)
        self.assertEqual(int.from_bytes(frame[6:8], "little"), 42)
        self.assertEqual(frame[36], 1)
        self.assertAlmostEqual(struct.unpack_from("<f", frame, 42)[0], 10.25)
        self.assertAlmostEqual(struct.unpack_from("<f", frame, 46)[0], -0.5)
        self.assertEqual(int.from_bytes(frame[50:52], "little"), 3)
        self.assertEqual(frame[54], 4)
        self.assertEqual(int.from_bytes(frame[60:62], "little"), 9)
        self.assertAlmostEqual(struct.unpack_from("<f", frame, 62)[0], 321.5)


if __name__ == "__main__":
    unittest.main()
