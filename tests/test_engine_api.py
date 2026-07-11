from __future__ import annotations

import unittest
from pathlib import Path

from app.core.engine import SimulationEngine


ROOT = Path(__file__).resolve().parents[1]


class EngineStateContractTests(unittest.TestCase):
    def test_engine_start_and_state_contract_without_external_server(self) -> None:
        engine = SimulationEngine.load_from_files(
            scenario_path=ROOT / "data" / "scenarios" / "line9_5train_power.json",
            line_map_path=ROOT / "data" / "cache" / "line_map.json",
            stations_csv_path=ROOT / "MetroDynamicsJavaDemo" / "data" / "stations.csv",
        )
        engine.load()
        engine.clock.start()
        for _tick in range(24):
            engine._tick()
        snapshot = engine.snapshot()
        assert snapshot is not None
        self.assertEqual(snapshot.clock_state, "RUNNING")
        # Dynamic train management deliberately starts with no pre-created train.
        self.assertEqual(snapshot.trains, [])
        self.assertIn("solver", snapshot.power_network)


if __name__ == "__main__":
    unittest.main()
