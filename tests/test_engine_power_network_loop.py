from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.core.engine import SimulationEngine
from app.infra.recorder import RunRecorder


ROOT = Path(__file__).resolve().parents[1]


class EnginePowerNetworkLoopTests(unittest.TestCase):
    def test_power_request_uses_actual_vehicle_command_during_braking(self) -> None:
        engine = SimulationEngine.load_from_files(
            scenario_path=ROOT / "data" / "scenarios" / "line9_single.json",
            line_map_path=ROOT / "data" / "cache" / "line_map.json",
            stations_csv_path=ROOT / "MetroDynamicsJavaDemo" / "data" / "stations.csv",
        )
        engine.load()
        train = engine.trains[0]
        train.phase = "DEPARTING"
        train.speed_mps = 10.0
        train.traction_percent = 0.0
        train.brake_percent = 80.0

        engine._update_power(sim_time_ms=12_345)
        snapshot = engine.power_service.last_network_snapshot

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.sim_time_ms, 12_345)
        self.assertLess(snapshot.trains[0].requested_power_kw, 0.0)
        self.assertGreater(snapshot.generated_regen_kw, 0.0)

    def test_fault_and_reset_restore_topology_atomically(self) -> None:
        engine = SimulationEngine.load_from_files(
            scenario_path=ROOT / "data" / "scenarios" / "line9_single.json",
            line_map_path=ROOT / "data" / "cache" / "line_map.json",
            stations_csv_path=ROOT / "MetroDynamicsJavaDemo" / "data" / "stations.csv",
        )
        engine.load()

        engine.apply_power_substation_outage("TS-0901")
        self.assertEqual(engine.power_service.network.substations["TS-0901"].status, "OUTAGE")

        engine.reset_power_network()
        self.assertEqual(engine.power_service.network.substations["TS-0901"].status, "IN_SERVICE")

    def test_engine_snapshot_and_recorder_include_power_network(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "engine_power.sqlite"
            recorder = RunRecorder(db_path)
            engine = SimulationEngine.load_from_files(
                scenario_path=ROOT / "data" / "scenarios" / "line9_single.json",
                line_map_path=ROOT / "data" / "cache" / "line_map.json",
                stations_csv_path=ROOT / "MetroDynamicsJavaDemo" / "data" / "stations.csv",
                recorder=recorder,
            )
            engine.load()
            try:
                engine.clock.start()
                for _ in range(8):
                    engine._tick()

                snapshot = engine.snapshot()
                self.assertIsNotNone(snapshot)
                assert snapshot is not None
                self.assertIn("substations", snapshot.power_network)
                self.assertGreaterEqual(len(snapshot.power_network["substations"]), 10)
                self.assertIn("trainVoltages", snapshot.power_network)
                self.assertIn("minTrainVoltageV", snapshot.kpi)

                with sqlite3.connect(db_path) as conn:
                    train_voltage_count = conn.execute("SELECT COUNT(*) FROM train_voltage_records").fetchone()[0]
                    substation_count = conn.execute("SELECT COUNT(*) FROM substation_power_records").fetchone()[0]
                    regen_count = conn.execute("SELECT COUNT(*) FROM regen_energy_records").fetchone()[0]
                    static_substation_count = conn.execute("SELECT COUNT(*) FROM traction_substations").fetchone()[0]
                    static_feeder_count = conn.execute("SELECT COUNT(*) FROM feeder_arms").fetchone()[0]
                    static_contact_count = conn.execute("SELECT COUNT(*) FROM contact_rail_sections").fetchone()[0]
                    static_return_count = conn.execute("SELECT COUNT(*) FROM return_rail_sections").fetchone()[0]
                    static_switch_count = conn.execute("SELECT COUNT(*) FROM power_switches").fetchone()[0]

                self.assertGreater(train_voltage_count, 0)
                self.assertGreater(substation_count, 0)
                self.assertGreater(regen_count, 0)
                self.assertGreaterEqual(static_substation_count, 10)
                self.assertGreater(static_feeder_count, 0)
                self.assertGreater(static_contact_count, 0)
                self.assertGreater(static_return_count, 0)
                self.assertGreater(static_switch_count, 0)
            finally:
                recorder.close()


if __name__ == "__main__":
    unittest.main()
