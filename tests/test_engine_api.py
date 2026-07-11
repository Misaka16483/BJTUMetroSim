from __future__ import annotations

import unittest
from pathlib import Path

from app.core.engine import SimulationEngine


ROOT = Path(__file__).resolve().parents[1]


class EngineStateContractTests(unittest.TestCase):
    def test_start_is_idempotent_while_running(self) -> None:
        engine = SimulationEngine.load_from_files(
            scenario_path=ROOT / "data" / "scenarios" / "line9_single.json",
            line_map_path=ROOT / "data" / "cache" / "line_map.json",
            stations_csv_path=ROOT / "data" / "line9" / "stations.csv",
        )
        self.assertEqual(engine.start(), "STARTED")
        self.assertEqual(engine.snapshot().clock_state, "RUNNING")
        self.assertEqual(engine.start(), "ALREADY_RUNNING")
        self.assertEqual(engine.snapshot().clock_state, "RUNNING")
        engine.stop()

    def test_stop_resets_runtime_but_preserves_configured_roster_for_restart(self) -> None:
        engine = SimulationEngine.load_from_files(
            scenario_path=ROOT / "data" / "scenarios" / "line9_single.json",
            line_map_path=ROOT / "data" / "cache" / "line_map.json",
            stations_csv_path=ROOT / "data" / "line9" / "stations.csv",
        )
        engine.remove_train("T0901")
        self.assertTrue(engine.add_train({"trainId": "UP-1", "initialStationCode": "GGZ", "direction": "UP"})["ok"])
        engine.start()
        engine.stop()
        stopped = engine.snapshot()
        self.assertEqual(stopped.clock_state, "STOPPED")
        self.assertEqual(stopped.tick, 0)
        self.assertEqual([train["trainId"] for train in stopped.trains], ["UP-1"])
        self.assertEqual(stopped.trains[0]["speedMps"], 0)
        self.assertEqual(stopped.trains[0]["energyKwh"], 0)
        self.assertTrue(engine.add_train({"trainId": "DOWN-1", "initialStationCode": "GTG", "direction": "DOWN"})["ok"])
        engine.start()
        self.assertEqual({train["trainId"] for train in engine.snapshot().trains}, {"UP-1", "DOWN-1"})
        engine.stop()

    def test_add_train_validates_station_code_direction_and_terminus(self) -> None:
        engine = SimulationEngine.load_from_files(
            scenario_path=ROOT / "data" / "scenarios" / "line9_single.json",
            line_map_path=ROOT / "data" / "cache" / "line_map.json",
            stations_csv_path=ROOT / "data" / "line9" / "stations.csv",
        )
        engine.load()
        invalid_station = engine.add_train({
            "trainId": "BAD-STATION", "initialStationCode": "不存在车站", "direction": "UP",
        })
        self.assertEqual(invalid_station["error"], "INVALID_INITIAL_STATION")

        invalid_direction = engine.add_train({
            "trainId": "BAD-DIRECTION", "initialStationCode": "GGZ", "direction": "SIDEWAYS",
        })
        self.assertEqual(invalid_direction["error"], "INVALID_DIRECTION")

        up_terminal = engine.add_train({
            "trainId": "BAD-UP-END", "initialStationCode": "GTG", "direction": "UP",
        })
        self.assertEqual(up_terminal["error"], "INITIAL_STATION_MUST_MATCH_DIRECTION_ORIGIN")

        down_terminal = engine.add_train({
            "trainId": "BAD-DOWN-END", "initialStationCode": "GGZ", "direction": "DOWN",
        })
        self.assertEqual(down_terminal["error"], "INITIAL_STATION_MUST_MATCH_DIRECTION_ORIGIN")

        valid = engine.add_train({
            "trainId": "T-VALID", "initialStationCode": "GGZ", "direction": "UP",
        })
        self.assertTrue(valid["ok"])
        self.assertEqual(valid["train"]["currentStationCode"], "GGZ")
        self.assertEqual(valid["train"]["nextStationCode"], "FSP")

        engine.trains[0].station_index = len(engine._station_list) - 1
        engine.trains[0].direction = "UP"
        engine._turn_train_at_terminal(engine.trains[0])
        self.assertEqual(engine.trains[0].direction, "DOWN")
        self.assertEqual(engine.trains[0].phase, "DWELLING")

    def test_engine_start_and_state_contract_without_external_server(self) -> None:
        engine = SimulationEngine.load_from_files(
            scenario_path=ROOT / "data" / "scenarios" / "line9_5train_power.json",
            line_map_path=ROOT / "data" / "cache" / "line_map.json",
            stations_csv_path=ROOT / "data" / "line9" / "stations.csv",
        )
        engine.load()
        engine.clock.start()
        for _tick in range(24):
            engine._tick()
        snapshot = engine.snapshot()
        assert snapshot is not None
        self.assertEqual(snapshot.clock_state, "RUNNING")
        self.assertEqual(len(snapshot.trains), 5)
        self.assertTrue(all("pantographVoltageV" in item for item in snapshot.trains))
        self.assertIn("solver", snapshot.power_network)


if __name__ == "__main__":
    unittest.main()
