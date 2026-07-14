from __future__ import annotations

import unittest
from pathlib import Path

from app.core.engine import SimulationEngine


ROOT = Path(__file__).resolve().parents[1]


def load_engine(scenario_name: str) -> SimulationEngine:
    engine = SimulationEngine.load_from_files(
        scenario_path=ROOT / "data" / "scenarios" / scenario_name,
        line_map_path=ROOT / "data" / "cache" / "line_map.json",
        stations_csv_path=ROOT / "data" / "line9" / "stations.csv",
    )
    engine.load()
    return engine


class EngineMultiTrainManagementTests(unittest.TestCase):
    def test_interactive_scenario_starts_empty_and_accepts_multiple_trains(self) -> None:
        engine = load_engine("line9_single.json")
        self.assertEqual(engine.trains, [])

        first = engine.add_train({
            "trainId": "T-DYN-01",
            "initialStationCode": "GGZ",
            "direction": "UP",
            "initialLoadPax": 120,
        })
        second = engine.add_train({
            "trainId": "T-DYN-02",
            "initialStationCode": "GTG",
            "direction": "DOWN",
            "operationMode": "MANUAL",
            "initialLoadPax": 240,
        })

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual([item.train_id for item in engine.trains], ["T-DYN-01", "T-DYN-02"])
        self.assertEqual(engine.trains[1].operation_mode, "MANUAL")
        self.assertEqual(engine.snapshot().kpi["totalTrains"], 2)

    def test_dynamic_train_validation_and_removal(self) -> None:
        engine = load_engine("line9_single.json")
        valid = {
            "trainId": "T-DYN-01",
            "initialStationCode": "GGZ",
            "direction": "UP",
        }
        self.assertTrue(engine.add_train(valid)["ok"])
        self.assertEqual(engine.add_train(valid)["error"], "TRAIN_ID_EXISTS")
        self.assertEqual(engine.add_train({**valid, "trainId": "BAD-1", "direction": "SIDE"})["error"], "INVALID_DIRECTION")
        self.assertEqual(engine.add_train({**valid, "trainId": "BAD-2", "initialStationCode": "NONE"})["error"], "INVALID_INITIAL_STATION")
        self.assertEqual(engine.add_train({**valid, "trainId": "BAD-3", "initialLoadPax": 1461})["error"], "INVALID_INITIAL_LOAD")

        self.assertTrue(engine.remove_train("T-DYN-01")["ok"])
        self.assertEqual(engine.remove_train("T-DYN-01")["error"], "TRAIN_NOT_FOUND")
        self.assertEqual(engine.snapshot().kpi["totalTrains"], 0)

    def test_add_train_accepts_station_code_and_legacy_chinese_name(self) -> None:
        engine = load_engine("line9_single.json")

        by_code = engine.add_train({
            "trainId": "T-CODE",
            "initialStationCode": "GGZ",
            "direction": "UP",
        })
        by_name = engine.add_train({
            "trainId": "T-NAME",
            "initialStationCode": "国家图书馆站",
            "direction": "DOWN",
        })

        self.assertTrue(by_code["ok"])
        self.assertTrue(by_name["ok"])
        self.assertEqual(engine.trains[0].current_station_code, "GGZ")
        self.assertEqual(engine.trains[1].current_station_code, "GTG")

    def test_add_train_from_intermediate_station_enters_directional_interval(self) -> None:
        engine = load_engine("line9_single.json")
        result = engine.add_train({
            "trainId": "T-MIDDLE",
            "initialStationCode": "QLZ",
            "direction": "UP",
        })

        self.assertTrue(result["ok"])
        self.assertEqual(result["train"]["currentStationCode"], "QLZ")
        self.assertEqual(result["train"]["nextStationCode"], "LLQ")
        engine.clock.start()
        for _ in range(8):
            engine._tick()
        train = engine.snapshot().trains[0]
        self.assertEqual(train["currentStationCode"], "QLZ")
        self.assertEqual(train["nextStationCode"], "LLQ")
        self.assertGreater(train["pathTotalLengthM"], 0.0)

    def test_manual_commands_and_vehicle_parameters_are_isolated_per_train(self) -> None:
        engine = load_engine("line9_single.json")
        for train_id, load in (("T-DYN-01", 100), ("T-DYN-02", 200)):
            result = engine.add_train({
                "trainId": train_id,
                "initialStationCode": "GGZ",
                "direction": "UP",
                "initialLoadPax": load,
            })
            self.assertTrue(result["ok"])

        self.assertTrue(engine.set_manual_mode("T-DYN-01", True)["ok"])
        command = engine.set_manual_command("T-DYN-01", 35.0, 0.0)
        self.assertTrue(command["ok"])
        self.assertEqual(engine.trains[0].operation_mode, "MANUAL")
        self.assertEqual(engine.trains[1].operation_mode, "ATO")
        snapshot = engine.snapshot()
        assert snapshot is not None
        self.assertEqual(snapshot.trains[0]["operationMode"], "MANUAL")
        self.assertEqual(snapshot.trains[1]["operationMode"], "ATO")
        self.assertEqual(engine.set_manual_command("T-DYN-02", 35.0, 0.0)["error"], "NOT_IN_MANUAL_MODE")

        engine.set_train_vehicle_config("T-DYN-01", {
            "formation": "Tc-M-M-M-M-Tc",
            "carMassesKg": [40_000.0] * 6,
        })
        configured = engine._make_vehicle_config("T-DYN-01", engine.trains[0].onboard_pax)
        default = engine._make_vehicle_config("T-DYN-02", engine.trains[1].onboard_pax)
        self.assertEqual(configured.mass_kg, 246_500.0)
        self.assertEqual(default.mass_kg, 238_000.0)

    def test_auto_spawn_five_train_scenario_shares_one_power_flow_tick(self) -> None:
        engine = load_engine("line9_5train_power.json")
        self.assertEqual(len(engine.trains), 5)
        self.assertEqual(len({item.train_id for item in engine.trains}), 5)

        engine.clock.start()
        for _ in range(24):
            engine._tick()
        snapshot = engine.snapshot()
        assert snapshot is not None
        self.assertEqual(snapshot.kpi["totalTrains"], 5)
        self.assertEqual(len(snapshot.power_network["trainVoltages"]), 5)
        self.assertTrue(snapshot.power_network["solver"]["converged"])
        self.assertTrue(all(item["massKg"] > 225_000.0 for item in snapshot.trains))


if __name__ == "__main__":
    unittest.main()
