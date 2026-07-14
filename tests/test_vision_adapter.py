from __future__ import annotations

import socket
import unittest

from app.adapters.vision import (
    FIXED_LAYOUT,
    VisionFrameBuilder,
    VisionFrameParser,
    VisionFrameState,
    VisionSnapshotMapper,
    VisionTrainState,
    VisionUdpPublisher,
)
from app.adapters.vision.protocol import FIXED_FRAME_SIZE
from app.api_server import ApiHandler


class _FakeEngine:
    def __init__(self, snapshot: dict) -> None:
        self._snapshot = snapshot
        self.line_map = {"signals": [], "switches": []}

    def snapshot(self) -> dict:
        return self._snapshot


def _frame_state() -> VisionFrameState:
    return VisionFrameState(
        live_counter=42,
        signal_states=tuple([0x01] * 76 + [0x02]),
        switch_states=tuple([0x01] * 28 + [0x02]),
        speed_mmps=12345,
        dwell_time_s=8,
        run_state=0x11,
        acceleration_percent=65,
        section_distance_mm=456789,
        edge_id=20,
        direction=-1,
        other_trains=(VisionTrainState(1000, 21, 1, 222),),
    )


class VisionFrameProtocolTests(unittest.TestCase):
    def test_compact_layout_matches_captured_154_byte_payload(self) -> None:
        captured_payload = bytes.fromhex(
            """
            ad 0d 00 00 5c
            02 02 02 02 02 02 02 02 02 02 02 02 02 02 02 02
            02 02 02 02 02 02 02 02 02 02 02 02 02 02 02 02
            02 02 02 02 02 02 02 02 02 02 02 02 02 02 02 02
            02 02 02 02 02 02 02 02 02 02 02 02 02 02 02 02
            02 02 02 02 02 02 02 02 02 02 02 02 02 02 02 02
            02 02 02 02 02 02 02 02 02 02 02 02
            28
            01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01
            01 01 01 01 01 01 01 01 01 01 02 02 01 01 01 01
            01 01 01 01 01 01 01 01
            6a 5a 00 00 00 00 00 00 e5 0d e2 00 14 00 01 00
            """
        )
        state = VisionFrameState(
            live_counter=3501,
            signal_states=(0x02,) * 92,
            switch_states=(0x01,) * 26 + (0x02, 0x02) + (0x01,) * 12,
            speed_mmps=23146,
            dwell_time_s=0,
            run_state=0,
            acceleration_percent=0,
            section_distance_mm=14814693,
            edge_id=20,
            direction=1,
        )

        self.assertEqual(len(captured_payload), 154)
        self.assertEqual(VisionFrameBuilder().build(state), captured_payload)
        self.assertEqual(VisionFrameParser().parse(captured_payload), state)

    def test_compact_v13_frame_round_trip(self) -> None:
        state = _frame_state()
        frame = VisionFrameBuilder().build(state)

        self.assertEqual(len(frame), 137)  # 128-byte base plus one 9-byte other train
        self.assertEqual(frame[:4], (42).to_bytes(4, "little", signed=True))
        self.assertEqual(frame[4], 77)
        self.assertEqual(frame[82], 29)
        self.assertEqual(VisionFrameParser().parse(frame), state)

    def test_fixed_c_struct_layout_is_1556_bytes_and_round_trips(self) -> None:
        state = _frame_state()
        frame = VisionFrameBuilder(FIXED_LAYOUT).build(state)

        self.assertEqual(len(frame), FIXED_FRAME_SIZE)
        self.assertEqual(frame[4], 77)
        self.assertEqual(frame[260], 29)
        self.assertEqual(VisionFrameParser(FIXED_LAYOUT).parse(frame), state)


class VisionSnapshotMapperTests(unittest.TestCase):
    def test_snapshot_maps_protocol_order_train_position_and_safe_defaults(self) -> None:
        primary = {
            "trainId": "T1",
            "phase": "CRUISING",
            "direction": "UP",
            "headMileageM": 6000.0,
            "speedMps": 12.5,
            "tractionPercent": 40.0,
            "brakePercent": 0.0,
            "dwellRemainingSec": 0.0,
        }
        other = {
            "trainId": "T2",
            "phase": "CRUISING",
            "direction": "DOWN",
            "headMileageM": 10000.0,
            "speedMps": 8.0,
        }
        snapshot = {
            "trains": [primary, other],
            "interlocking": {
                "signals": [{"signalId": "501", "aspect": "GREEN"}],
                "switches": [{"switchId": "601", "actualPosition": "REVERSE"}],
            },
        }
        mapper = VisionSnapshotMapper(
            _FakeEngine(snapshot),
            signal_source_map={"0121": 501},
            switch_source_map={"0101": 601},
        )

        state = mapper.build_state(snapshot, 7)

        self.assertEqual(state.signal_states[0], 0x02)
        self.assertTrue(all(value == 0x01 for value in state.signal_states[1:]))
        self.assertEqual(len(state.signal_states), 92)
        self.assertEqual(state.switch_states[0], 0x02)
        self.assertTrue(all(value == 0x01 for value in state.switch_states[1:]))
        self.assertEqual(len(state.switch_states), 40)
        self.assertEqual(state.speed_mmps, 12500)
        self.assertEqual(state.run_state, 0x11)
        self.assertEqual(state.acceleration_percent, 40)
        self.assertEqual(state.edge_id, 21)
        self.assertEqual(state.section_distance_mm, 725160)
        self.assertEqual(state.direction, 1)
        self.assertEqual(len(state.other_trains), 1)
        self.assertEqual(state.other_trains[0].edge_id, 34)
        self.assertEqual(state.other_trains[0].direction, -1)
        self.assertEqual(state.other_trains[0].speed_cmps, 800)


class VisionUdpPublisherTests(unittest.TestCase):
    def tearDown(self) -> None:
        if ApiHandler.vision_publisher is not None:
            ApiHandler.vision_publisher.disconnect()
            ApiHandler.vision_publisher = None

    def test_api_factory_applies_runtime_connection_configuration(self) -> None:
        engine = _FakeEngine({"trains": [], "interlocking": {"signals": [], "switches": []}})
        handler = object.__new__(ApiHandler)
        handler.engine = engine

        publisher = handler._vision_publisher({
            "remoteHost": "127.0.0.1",
            "remotePort": 18303,
            "localPort": 0,
            "intervalMs": 250,
            "layout": "fixed",
            "primaryTrainId": "T0901",
            "signalSourceMap": {"0121": 501},
            "switchSourceMap": {"0101": 601},
        })

        self.assertIsNotNone(publisher)
        status = publisher.status()["status"]
        self.assertEqual(status["state"], "DISCONNECTED")
        self.assertEqual(status["remoteHost"], "127.0.0.1")
        self.assertEqual(status["remotePort"], 18303)
        self.assertEqual(status["localPort"], 0)
        self.assertEqual(status["intervalMs"], 250)
        self.assertEqual(status["layout"], "fixed")
        self.assertEqual(status["mapping"]["mappedSignalCount"], 1)
        self.assertEqual(status["mapping"]["mappedSwitchCount"], 1)
        self.assertEqual(status["logs"][0]["event"], "READY")

        cleared = publisher.clear_logs()["status"]["logs"]
        self.assertEqual(len(cleared), 1)
        self.assertEqual(cleared[0]["event"], "LOGS_CLEARED")

    def test_capture_confirmed_default_source_and_destination_ports_are_8303(self) -> None:
        engine = _FakeEngine({"trains": [], "interlocking": {"signals": [], "switches": []}})
        publisher = VisionUdpPublisher(engine)

        status = publisher.status()["status"]

        self.assertEqual(status["remotePort"], 8303)
        self.assertEqual(status["localPort"], 8303)

    def test_udp_loopback_sends_a_parseable_snapshot_frame(self) -> None:
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            receiver.bind(("127.0.0.1", 0))
        except PermissionError:
            receiver.close()
            self.skipTest("sandbox does not permit local UDP sockets")
        receiver.settimeout(1.0)
        remote_port = receiver.getsockname()[1]
        engine = _FakeEngine({"trains": [], "interlocking": {"signals": [], "switches": []}})
        publisher = VisionUdpPublisher(
            engine,
            remote_host="127.0.0.1",
            remote_port=remote_port,
            local_port=0,
        )
        try:
            expected = publisher.send_once()
            received, _ = receiver.recvfrom(4096)
        finally:
            publisher.stop()
            receiver.close()

        self.assertEqual(received, expected)
        decoded = VisionFrameParser().parse(received)
        self.assertEqual(len(decoded.signal_states), 92)
        self.assertEqual(len(decoded.switch_states), 40)
        status = publisher.status()["status"]
        self.assertEqual(status["framesSent"], 1)
        self.assertEqual(status["state"], "DISCONNECTED")
        events = [entry["event"] for entry in status["logs"]]
        self.assertIn("CONNECTED", events)
        self.assertIn("FIRST_FRAME_SENT", events)
        self.assertIn("DISCONNECTED", events)


if __name__ == "__main__":
    unittest.main()
