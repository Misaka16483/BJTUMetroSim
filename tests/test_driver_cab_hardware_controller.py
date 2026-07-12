from __future__ import annotations

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
        self.assertEqual(status["controlState"], "ACTIVE")
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
        self.assertEqual(status["controlState"], "ACTIVE")
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
        controller = DriverCabHardwareController(
            self.engine,
            client_factory=lambda _host, _port, _timeout: _FailingClient(attempted),
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


if __name__ == "__main__":
    unittest.main()
