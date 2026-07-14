from __future__ import annotations

import struct
import unittest
from pathlib import Path

from app.adapters.cab import DriverCabHardwareController, MitsubishiPlcCabParser
from app.core.engine import DEPARTING, DWELLING, SimTrainState, SimulationEngine
from app.domain.vehicle.doors import DoorSide, DoorUnitStatus, TrainDoorSystem
from app.domain.vehicle.models import CommandSource, ControlCommand


ROOT = Path(__file__).resolve().parents[1]


def load_engine() -> SimulationEngine:
    engine = SimulationEngine.load_from_files(
        scenario_path=ROOT / "data" / "scenarios" / "line9_interactive.json",
        line_map_path=ROOT / "data" / "cache" / "line_map.json",
        stations_csv_path=ROOT / "data" / "line9" / "stations.csv",
    )
    engine.load()
    return engine


class TrainDoorSystemTests(unittest.TestCase):
    def test_line9_default_has_six_cars_and_eight_units_per_car(self) -> None:
        doors = TrainDoorSystem.line9_default()

        self.assertEqual(len(doors.cars), 6)
        self.assertTrue(all(len(car.doors) == 8 for car in doors.cars))
        self.assertTrue(doors.all_closed_and_locked)
        self.assertEqual([car.protocol_word() for car in doors.cars], [0] * 6)

    def test_protocol_encodes_each_car_and_side_independently(self) -> None:
        doors = TrainDoorSystem.line9_default()
        doors.set_permission(DoorSide.LEFT)

        self.assertTrue(doors.request_open(DoorSide.LEFT, "TEST"))
        self.assertEqual(doors.cars[0].protocol_word(), 0x00001111)
        self.assertEqual(doors.cars[5].protocol_word(), 0x00001111)
        doors.advance(1.0)
        self.assertTrue(all(
            door.status == DoorUnitStatus.OPEN
            for car in doors.cars
            for door in car.doors[:4]
        ))
        self.assertTrue(doors.request_close("TEST"))
        doors.advance(1.0)
        self.assertTrue(doors.all_closed_and_locked)

    def test_wrong_side_is_rejected(self) -> None:
        doors = TrainDoorSystem.line9_default()
        doors.set_permission(DoorSide.RIGHT)

        self.assertFalse(doors.request_open(DoorSide.LEFT, "TEST"))
        self.assertEqual(doors.last_rejection_reason, "DOOR_SIDE_NOT_PERMITTED")

    def test_open_command_reverses_a_closing_door(self) -> None:
        doors = TrainDoorSystem.line9_default()
        doors.set_permission(DoorSide.LEFT)
        self.assertTrue(doors.request_open(DoorSide.LEFT, "TEST"))
        doors.advance(1.0)
        self.assertTrue(doors.request_close("TEST"))

        self.assertTrue(doors.request_open(DoorSide.LEFT, "TEST"))
        self.assertTrue(all(
            door.status == DoorUnitStatus.OPENING
            for car in doors.cars
            for door in car.doors[:4]
        ))


class EngineDoorInterlockTests(unittest.TestCase):
    def test_cm_door_command_requires_a_stopped_platform_and_permitted_side(self) -> None:
        engine = load_engine()
        added = engine.add_train({
            "trainId": "T-DOOR",
            "initialStationCode": "GGZ",
            "initialSegmentId": 13,
            "direction": "UP",
            "operationMode": "MANUAL",
        })
        self.assertTrue(added["ok"])
        train = engine.trains[0]

        wrong_side = engine.set_door_command("T-DOOR", "OPEN", "RIGHT")
        self.assertFalse(wrong_side["ok"])
        self.assertEqual(wrong_side["error"], "DOOR_SIDE_NOT_PERMITTED")

        accepted = engine.set_door_command("T-DOOR", "OPEN", "LEFT")
        self.assertTrue(accepted["ok"])
        self.assertFalse(train.door_system.all_closed_and_locked)

        train.door_system.request_close("TEST", transition_sec=0.0)
        train.door_system.advance(0.0)
        train.phase = DEPARTING
        rejected = engine.set_door_command("T-DOOR", "OPEN", "LEFT")
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["error"], "DOOR_OPEN_REQUIRES_PLATFORM_STOP")

    def test_ato_rejects_external_open_command(self) -> None:
        engine = load_engine()
        self.assertTrue(engine.add_train({
            "trainId": "T-ATO-DOOR",
            "initialStationCode": "GGZ",
            "initialSegmentId": 13,
            "direction": "UP",
        })["ok"])

        result = engine.set_door_command("T-ATO-DOOR", "OPEN", "LEFT")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "DOOR_MANUAL_COMMAND_REQUIRES_CM")

    def test_ato_opens_and_closes_doors_during_station_dwell(self) -> None:
        engine = load_engine()
        self.assertTrue(engine.add_train({
            "trainId": "T-AUTO-DWELL",
            "initialStationCode": "GGZ",
            "initialSegmentId": 13,
            "direction": "UP",
        })["ok"])
        train = engine.trains[0]
        engine.clock.start()

        for _ in range(30):
            engine._tick()
            if train.door_system.aggregate_state == "OPEN":
                break
        self.assertEqual(train.door_system.aggregate_state, "OPEN")
        self.assertGreater(train.dwell_remaining_sec, 0.0)

        for _ in range(400):
            engine._tick()
            if train.door_system.all_closed_and_locked and train.phase != DWELLING:
                break
        self.assertTrue(train.door_system.all_closed_and_locked)
        self.assertNotEqual(train.door_notice, "WAITING_MANUAL_CLOSE")

    def test_cm_waits_for_manual_open_and_close(self) -> None:
        engine = load_engine()
        self.assertTrue(engine.add_train({
            "trainId": "T-CM-DWELL",
            "initialStationCode": "GGZ",
            "initialSegmentId": 13,
            "direction": "UP",
            "operationMode": "MANUAL",
        })["ok"])
        train = engine.trains[0]
        engine.clock.start()

        for _ in range(5):
            engine._tick()
        self.assertTrue(train._passenger_service_pending)
        self.assertEqual(train.door_notice, "WAITING_MANUAL_OPEN")

        self.assertTrue(engine.set_door_command("T-CM-DWELL", "OPEN", "LEFT")["ok"])
        for _ in range(30):
            engine._tick()
            if train.door_system.aggregate_state == "OPEN":
                break
        self.assertEqual(train.door_system.aggregate_state, "OPEN")
        for _ in range(400):
            engine._tick()
            if train.dwell_remaining_sec == 0.0:
                break
        self.assertEqual(train.phase, DWELLING)
        self.assertEqual(train.door_notice, "WAITING_MANUAL_CLOSE")

        self.assertTrue(engine.set_door_command("T-CM-DWELL", "CLOSE")["ok"])
        for _ in range(30):
            engine._tick()
            if train.door_system.all_closed_and_locked and train.phase != DWELLING:
                break
        self.assertTrue(train.door_system.all_closed_and_locked)
        self.assertNotEqual(train.phase, DWELLING)

    def test_plc_open_button_is_edge_triggered(self) -> None:
        engine = load_engine()
        self.assertTrue(engine.add_train({
            "trainId": "T-PLC-DOOR",
            "initialStationCode": "GGZ",
            "initialSegmentId": 13,
            "direction": "UP",
            "operationMode": "MANUAL",
        })["ok"])
        train = engine.trains[0]
        controller = DriverCabHardwareController(engine, train_id=train.train_id)
        frame = bytearray(46)
        frame[29] = 0b0000_0001
        state = MitsubishiPlcCabParser().parse(bytes(frame), train_id=train.train_id)

        first = controller.process_input_state(state)
        train.door_system.transition_remaining_sec = 0.4
        second = controller.process_input_state(state)

        self.assertTrue(first["doorCommand"]["ok"])
        self.assertNotIn("doorCommand", second)
        self.assertEqual(train.door_system.transition_remaining_sec, 0.4)

    def test_hmi_frame_contains_six_independent_door_words(self) -> None:
        engine = load_engine()
        self.assertTrue(engine.add_train({
            "trainId": "T-HMI-DOOR",
            "initialStationCode": "GGZ",
            "initialSegmentId": 13,
            "direction": "UP",
        })["ok"])
        train = engine.trains[0]
        train.door_system.cars[1].doors[4].status = DoorUnitStatus.OPEN
        controller = DriverCabHardwareController(engine, train_id=train.train_id)

        frame = controller._build_display_frame("networkScreen", train.to_dict(), 0.0)
        words = struct.unpack_from("<6I", frame, 60)

        self.assertEqual(words[0], 0)
        self.assertEqual(words[1], 0x00010000)
        self.assertEqual(words[2:], (0, 0, 0, 0))

    def test_open_door_removes_traction_for_ato_and_cm_commands(self) -> None:
        train = SimTrainState("T-SAFE", "9", 0, "UP", phase=DWELLING)
        train.door_system.set_permission(DoorSide.LEFT)
        self.assertTrue(train.door_system.request_open(DoorSide.LEFT, "TEST"))

        for source in (CommandSource.ATO, CommandSource.MANUAL):
            command = ControlCommand(
                train_id=train.train_id,
                traction_percent=75.0,
                source=source,
            )
            protected = SimulationEngine._enforce_door_interlock(train, command)
            self.assertEqual(protected.traction_percent, 0.0)
            self.assertGreaterEqual(protected.brake_percent, 20.0)


if __name__ == "__main__":
    unittest.main()
