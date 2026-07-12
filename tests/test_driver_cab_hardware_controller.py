from __future__ import annotations

import struct
import threading
import time
import unittest
from pathlib import Path

from app.adapters.cab import DriverCabHardwareController, MitsubishiPlcCabParser
from app.core.engine import SimulationEngine


ROOT = Path(__file__).resolve().parents[1]


def _write_word(frame: bytearray, offset: int, value: int) -> None:
    frame[offset : offset + 2] = value.to_bytes(2, "little")


class _FailingClient:
    def __init__(self, connected: threading.Event) -> None:
        self.connected = connected

    def connect(self) -> None:
        self.connected.set()
        raise OSError("PLC unreachable")

    def close(self) -> None:
        pass


class _BlockingPlcClient:
    def __init__(self, connected: threading.Event) -> None:
        self.connected = connected
        self.closed = threading.Event()

    def connect(self) -> None:
        self.connected.set()

    def read_input_state(self, train_id: str) -> object:
        self.closed.wait(timeout=2.0)
        raise OSError(f"{train_id} PLC closed")

    def close(self) -> None:
        self.closed.set()


class _RecordingDisplayClient:
    def __init__(self, connected: threading.Event, frames: list[bytes]) -> None:
        self.connected = connected
        self.frames = frames

    def connect(self) -> None:
        self.connected.set()

    def send_frame(self, frame: bytes) -> None:
        self.frames.append(frame)

    def close(self) -> None:
        pass


class _FailingDisplayClient(_RecordingDisplayClient):
    def connect(self) -> None:
        raise OSError("display unreachable")


class _DisplayClientFactory:
    def __init__(self, fail_network_screen_once: bool = False) -> None:
        self.fail_network_screen_once = fail_network_screen_once
        self.attempts: dict[int, int] = {}
        self.connected = {8888: threading.Event(), 9999: threading.Event()}
        self.frames: dict[int, list[bytes]] = {8888: [], 9999: []}

    def __call__(self, _host: str, port: int, _timeout: float) -> _RecordingDisplayClient:
        self.attempts[port] = self.attempts.get(port, 0) + 1
        if self.fail_network_screen_once and port == 8888 and self.attempts[port] == 1:
            return _FailingDisplayClient(self.connected[port], self.frames[port])
        return _RecordingDisplayClient(self.connected[port], self.frames[port])


class DriverCabHardwareControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = SimulationEngine.load_from_files(
            scenario_path=ROOT / "data" / "scenarios" / "line9_single.json",
            line_map_path=ROOT / "data" / "cache" / "line_map.json",
            stations_csv_path=ROOT / "data" / "line9" / "stations.csv",
        )
        self.engine.add_train(
            {
                "trainId": "T0901",
                "initialStationCode": "GGZ",
                "direction": "UP",
            }
        )

    def test_decoded_plc_frame_controls_t0901_in_manual_mode(self) -> None:
        frame = bytearray(46)
        _write_word(frame, 38, 1)
        _write_word(frame, 40, 65)
        input_state = MitsubishiPlcCabParser().parse(bytes(frame), train_id="T0901")
        controller = DriverCabHardwareController(self.engine)

        result = controller.process_input_state(input_state)

        self.assertTrue(result["ok"])
        train = next(train for train in self.engine.trains if train.train_id == "T0901")
        self.assertEqual(train.operation_mode, "MANUAL")
        self.assertIsNotNone(train._manual_command)
        self.assertEqual(train._manual_command.traction_percent, 65.0)
        self.assertEqual(train._manual_command.brake_percent, 0.0)
        status = controller.status()["status"]
        self.assertEqual(status["controlState"], "ACTIVE")
        self.assertEqual(status["framesReceived"], 1)
        self.assertEqual(status["trainId"], "T0901")

    def test_emergency_button_reaches_engine_command(self) -> None:
        frame = bytearray(46)
        frame[28] = 0b0000_0001
        _write_word(frame, 38, 1)
        _write_word(frame, 40, 80)
        input_state = MitsubishiPlcCabParser().parse(bytes(frame), train_id="T0901")
        controller = DriverCabHardwareController(self.engine)

        result = controller.process_input_state(input_state)

        self.assertTrue(result["emergencyBrake"])
        train = next(train for train in self.engine.trains if train.train_id == "T0901")
        self.assertTrue(train._manual_command.emergency_brake)
        self.assertEqual(train._manual_command.traction_percent, 0.0)
        self.assertEqual(train._manual_command.brake_percent, 100.0)

    def test_ato_start_input_is_retained_for_frontend_display(self) -> None:
        controller = DriverCabHardwareController(self.engine)
        controller.process_input_state(MitsubishiPlcCabParser().parse(bytes(46), train_id="T0901"))
        frame = bytearray(46)
        frame[25] = 0b0000_0101
        frame[34] = 0b1110_0000

        result = controller.process_input_state(
            MitsubishiPlcCabParser().parse(bytes(frame), train_id="T0901")
        )

        self.assertTrue(result["ok"])
        self.assertFalse(result["manualMode"])
        status = controller.status()["status"]
        self.assertEqual(status["controlState"], "ATO_ACTIVE")
        self.assertTrue(status["lastInput"]["atoStart"])
        train = next(train for train in self.engine.trains if train.train_id == "T0901")
        self.assertEqual(train.operation_mode, "ATO")

        released = controller.process_input_state(
            MitsubishiPlcCabParser().parse(bytes(46), train_id="T0901")
        )

        self.assertTrue(released["ok"])
        self.assertEqual(released["message"], "ATO_ACTIVE")
        self.assertFalse(released["manualMode"])
        status = controller.status()["status"]
        self.assertEqual(status["controlState"], "ATO_ACTIVE")
        self.assertIsNone(status["lastError"])
        self.assertIsNone(status["lastCommand"])
        self.assertEqual(train.operation_mode, "ATO")

    def test_emergency_brake_overrides_ato_without_handle_movement(self) -> None:
        controller = DriverCabHardwareController(self.engine)
        ato_frame = bytearray(46)
        ato_frame[25] = 0b0000_0101
        ato_frame[34] = 0b1000_0000
        controller.process_input_state(
            MitsubishiPlcCabParser().parse(bytes(ato_frame), train_id="T0901")
        )
        emergency_frame = bytearray(46)
        emergency_frame[28] = 0b0000_0001

        result = controller.process_input_state(
            MitsubishiPlcCabParser().parse(bytes(emergency_frame), train_id="T0901")
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["emergencyBrake"])
        self.assertEqual(result["tractionPercent"], 0.0)
        self.assertEqual(result["brakePercent"], 100.0)
        train = next(train for train in self.engine.trains if train.train_id == "T0901")
        self.assertEqual(train.operation_mode, "MANUAL")
        self.assertTrue(train._manual_command.emergency_brake)
        status = controller.status()["status"]
        self.assertTrue(status["lastInput"]["emergencyBrake"])
        self.assertTrue(status["lastCommand"]["emergencyBrake"])

    def test_connect_runs_in_background_and_reports_error(self) -> None:
        attempted = threading.Event()
        display_factory = _DisplayClientFactory()
        controller = DriverCabHardwareController(
            self.engine,
            client_factory=lambda _host, _port, _timeout: _FailingClient(attempted),
            display_client_factory=display_factory,
            display_interval_s=0.01,
        )

        response = controller.connect()

        self.assertTrue(response["ok"])
        self.assertIn(response["status"]["state"], {"CONNECTING", "ERROR"})
        self.assertTrue(attempted.wait(timeout=1.0))
        deadline = time.monotonic() + 1.0
        while controller.status()["status"]["state"] != "ERROR" and time.monotonic() < deadline:
            time.sleep(0.01)
        status = controller.status()["status"]
        self.assertEqual(status["state"], "ERROR")
        self.assertIn("PLC unreachable", status["lastError"])
        controller.disconnect()

    def test_display_screen_addresses_are_saved_and_reported(self) -> None:
        attempted = threading.Event()
        display_factory = _DisplayClientFactory()
        controller = DriverCabHardwareController(
            self.engine,
            client_factory=lambda _host, _port, _timeout: _FailingClient(attempted),
            display_client_factory=display_factory,
            display_interval_s=0.01,
        )

        response = controller.connect(
            host="10.0.0.123",
            network_screen_host="10.0.0.122",
            signal_screen_host="10.0.0.121",
        )

        status = response["status"]
        self.assertEqual(status["host"], "10.0.0.123")
        self.assertEqual(status["networkScreenHost"], "10.0.0.122")
        self.assertEqual(status["networkScreenPort"], 8888)
        self.assertEqual(status["signalScreenHost"], "10.0.0.121")
        self.assertEqual(status["signalScreenPort"], 9999)
        self.assertTrue(attempted.wait(timeout=1.0))
        controller.disconnect()

    def test_display_screens_connect_send_live_frames_and_retry(self) -> None:
        plc_attempted = threading.Event()
        display_factory = _DisplayClientFactory(fail_network_screen_once=True)
        controller = DriverCabHardwareController(
            self.engine,
            client_factory=lambda _host, _port, _timeout: _FailingClient(plc_attempted),
            display_client_factory=display_factory,
            display_interval_s=0.01,
            display_reconnect_interval_s=0.01,
        )

        controller.connect()

        deadline = time.monotonic() + 1.0
        status = controller.status()["status"]
        while (
            (
                status["networkScreen"]["state"] != "CONNECTED"
                or status["signalScreen"]["state"] != "CONNECTED"
                or len(display_factory.frames[8888]) < 2
                or len(display_factory.frames[9999]) < 2
            )
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
            status = controller.status()["status"]

        self.assertGreaterEqual(display_factory.attempts[8888], 2)
        self.assertEqual(status["networkScreen"]["state"], "CONNECTED")
        self.assertEqual(status["signalScreen"]["state"], "CONNECTED")
        self.assertGreaterEqual(status["networkScreen"]["framesSent"], 2)
        self.assertGreaterEqual(status["signalScreen"]["framesSent"], 2)
        self.assertEqual(len(display_factory.frames[8888][0]), 572)
        self.assertEqual(len(display_factory.frames[9999][0]), 66)
        self.assertEqual(display_factory.frames[8888][0][36:39], bytes((1, 2, 13)))
        self.assertEqual(display_factory.frames[9999][0][36:39], bytes((1, 2, 13)))

        disconnected = controller.disconnect()["status"]

        self.assertEqual(disconnected["networkScreen"]["state"], "DISCONNECTED")
        self.assertEqual(disconnected["signalScreen"]["state"], "DISCONNECTED")

    def test_simulation_snapshot_maps_to_both_display_protocols(self) -> None:
        controller = DriverCabHardwareController(self.engine)
        train = dict(self.engine.snapshot().trains[0])
        train.update(
            {
                "speedMps": 12.5,
                "localSpeedLimitMps": 22.22,
                "tractionPercent": 60.0,
                "brakePercent": 0.0,
                "operationMode": "ATO",
                "distanceToNextM": 321.5,
            }
        )

        hmi_frame = controller._build_display_frame("networkScreen", train, 0.75)
        mmi_frame = controller._build_display_frame("signalScreen", train, 0.75)

        self.assertAlmostEqual(struct.unpack_from("<f", hmi_frame, 40)[0], 12.5)
        self.assertAlmostEqual(struct.unpack_from("<f", hmi_frame, 44)[0], 0.75)
        self.assertEqual(int.from_bytes(hmi_frame[52:54], "little"), 80)
        self.assertEqual(hmi_frame[54], 1)
        self.assertAlmostEqual(struct.unpack_from("<f", mmi_frame, 42)[0], 12.5)
        self.assertAlmostEqual(struct.unpack_from("<f", mmi_frame, 62)[0], 321.5)
        self.assertEqual(mmi_frame[54], 1)
        self.assertEqual(mmi_frame[55], 1)

    def test_each_hardware_endpoint_can_disconnect_and_reconnect_independently(self) -> None:
        plc_connected = threading.Event()
        display_factory = _DisplayClientFactory()
        controller = DriverCabHardwareController(
            self.engine,
            client_factory=lambda _host, _port, _timeout: _BlockingPlcClient(plc_connected),
            display_client_factory=display_factory,
            display_interval_s=0.01,
            display_reconnect_interval_s=0.01,
        )
        controller.connect()
        self.assertTrue(plc_connected.wait(timeout=1.0))
        deadline = time.monotonic() + 1.0
        status = controller.status()["status"]
        while (
            (
                status["state"] != "CONNECTED"
                or status["networkScreen"]["state"] != "CONNECTED"
                or status["signalScreen"]["state"] != "CONNECTED"
            )
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
            status = controller.status()["status"]

        hmi_disconnected = controller.disconnect_display("networkScreen")["status"]

        self.assertEqual(hmi_disconnected["state"], "CONNECTED")
        self.assertEqual(hmi_disconnected["networkScreen"]["state"], "DISCONNECTED")
        self.assertEqual(hmi_disconnected["signalScreen"]["state"], "CONNECTED")

        hmi_reconnected = controller.connect_display("networkScreen", host="10.0.0.122")["status"]
        self.assertEqual(hmi_reconnected["networkScreen"]["host"], "10.0.0.122")
        deadline = time.monotonic() + 1.0
        while (
            controller.status()["status"]["networkScreen"]["state"] != "CONNECTED"
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)

        plc_disconnected = controller.disconnect_plc()["status"]

        self.assertEqual(plc_disconnected["state"], "DISCONNECTED")
        self.assertEqual(plc_disconnected["networkScreen"]["state"], "CONNECTED")
        self.assertEqual(plc_disconnected["signalScreen"]["state"], "CONNECTED")
        controller.disconnect()


if __name__ == "__main__":
    unittest.main()
