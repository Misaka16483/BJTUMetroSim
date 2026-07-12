from __future__ import annotations

import json
import sqlite3
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from app.core.clock import ClockState
from app.core.engine import SimulationEngine
from app.domain.power.network_models import TrainElectricalLoad
from app.infra.recorder import RunRecorder


ROOT = Path(__file__).resolve().parents[1]


class EnginePowerNetworkLoopTests(unittest.TestCase):
    def test_power_request_uses_actual_vehicle_command_during_braking(self) -> None:
        engine = SimulationEngine.load_from_files(
            scenario_path=ROOT / "data" / "scenarios" / "line9_single.json",
            line_map_path=ROOT / "data" / "cache" / "line_map.json",
            stations_csv_path=ROOT / "data" / "line9" / "stations.csv",
        )
        engine.load()
        result = engine.add_train({
            "trainId": "T0901",
            "initialStationCode": "GGZ",
            "direction": "UP",
        })
        self.assertTrue(result["ok"])
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
            stations_csv_path=ROOT / "data" / "line9" / "stations.csv",
        )
        engine.load()

        assert engine.power_service.solver is not None
        charged = engine.power_service.solver.solve(
            [TrainElectricalLoad(
                "BRAKING",
                "UP",
                7000.0,
                15.0,
                aux_power_kw=100.0,
                regen_power_available_kw=2200.0,
            )],
            dt_sec=10.0,
        ).supercapacitor_flows[0]
        self.assertGreater(charged.soc, 0.50)

        engine.apply_power_substation_outage("TS-0901")
        self.assertEqual(engine.power_service.network.substations["TS-0901"].status, "OUTAGE")

        engine.reset_power_network()
        self.assertEqual(engine.power_service.network.substations["TS-0901"].status, "IN_SERVICE")
        assert engine.power_service.solver is not None
        reset_storage = engine.power_service.solver.solve([], dt_sec=0.0).supercapacitor_flows[0]
        self.assertAlmostEqual(reset_storage.soc, 0.50, places=9)
        self.assertEqual(reset_storage.state, "STANDBY")
        self.assertEqual(reset_storage.cumulative_charged_kwh, 0.0)
        self.assertEqual(reset_storage.cumulative_discharged_kwh, 0.0)

    def test_engine_snapshot_and_recorder_include_power_network(self) -> None:
        test_dir = ROOT / "outputs" / "test-runtime"
        test_dir.mkdir(parents=True, exist_ok=True)
        db_path = test_dir / "engine_power.sqlite"
        db_path.unlink(missing_ok=True)
        recorder = RunRecorder(db_path)
        try:
            engine = SimulationEngine.load_from_files(
                scenario_path=ROOT / "data" / "scenarios" / "line9_5train_power.json",
                line_map_path=ROOT / "data" / "cache" / "line_map.json",
                stations_csv_path=ROOT / "data" / "line9" / "stations.csv",
                recorder=recorder,
            )
            engine.load()
            engine.trains[0].phase = "DEPARTING"
            engine.trains[0].dwell_remaining_sec = 0.0
            engine.trains[0].speed_mps = 18.0
            engine.set_manual_mode(engine.trains[0].train_id, True)
            engine.set_manual_command(engine.trains[0].train_id, 70.0, 0.0)
            engine.trains[1].phase = "DEPARTING"
            engine.trains[1].dwell_remaining_sec = 0.0
            engine.trains[1].speed_mps = 18.0
            engine.set_manual_mode(engine.trains[1].train_id, True)
            engine.set_manual_command(engine.trains[1].train_id, 0.0, 80.0)
            engine.queue_power_command("OPERATE_SWITCH", {"switchId": "SW-TIE-0902", "state": "CLOSED"})
            engine.clock.start()
            for _ in range(8):
                engine._tick()

            snapshot = engine.snapshot()
            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertIn("substations", snapshot.power_network)
            self.assertGreaterEqual(len(snapshot.power_network["substations"]), 10)
            self.assertIn("trainVoltages", snapshot.power_network)
            self.assertIn("supercapacitorStorageSystems", snapshot.power_network)
            self.assertEqual(len(snapshot.power_network["supercapacitorStorageSystems"]), 1)
            self.assertIn("storageChargedKw", snapshot.power_network["regen"])
            self.assertIn("storageDischargedKw", snapshot.power_network["regen"])
            self.assertIn("minTrainVoltageV", snapshot.kpi)

            with closing(sqlite3.connect(db_path)) as conn:
                train_voltage_count = conn.execute("SELECT COUNT(*) FROM train_voltage_records").fetchone()[0]
                substation_count = conn.execute("SELECT COUNT(*) FROM substation_power_records").fetchone()[0]
                storage_record_count = conn.execute("SELECT COUNT(*) FROM supercapacitor_power_records").fetchone()[0]
                regen_count = conn.execute("SELECT COUNT(*) FROM regen_energy_records").fetchone()[0]
                regen_path_count = conn.execute("SELECT COUNT(*) FROM regen_path_records").fetchone()[0]
                static_substation_count = conn.execute("SELECT COUNT(*) FROM traction_substations").fetchone()[0]
                static_feeder_count = conn.execute("SELECT COUNT(*) FROM feeder_arms").fetchone()[0]
                static_contact_count = conn.execute("SELECT COUNT(*) FROM contact_rail_sections").fetchone()[0]
                static_return_count = conn.execute("SELECT COUNT(*) FROM return_rail_sections").fetchone()[0]
                static_switch_count = conn.execute("SELECT COUNT(*) FROM power_switches").fetchone()[0]
                static_storage_count = conn.execute("SELECT COUNT(*) FROM supercapacitor_storage_systems").fetchone()[0]
                solver_count = conn.execute("SELECT COUNT(*) FROM power_solver_records").fetchone()[0]
                command_count = conn.execute("SELECT COUNT(*) FROM power_command_records").fetchone()[0]
                run_metadata = conn.execute("SELECT metadata_json FROM runs ORDER BY id DESC LIMIT 1").fetchone()[0]
                topology_detail = conn.execute(
                    "SELECT detail_json FROM traction_substations WHERE substation_id = 'TS-0901'"
                ).fetchone()[0]

            self.assertGreater(train_voltage_count, 0)
            self.assertGreater(substation_count, 0)
            self.assertGreater(storage_record_count, 0)
            self.assertGreater(regen_count, 0)
            self.assertGreater(regen_path_count, 0)
            self.assertGreaterEqual(static_substation_count, 10)
            self.assertGreater(static_feeder_count, 0)
            self.assertGreater(static_contact_count, 0)
            self.assertGreater(static_return_count, 0)
            self.assertGreater(static_switch_count, 0)
            self.assertEqual(static_storage_count, 1)
            self.assertGreater(solver_count, 0)
            self.assertEqual(command_count, 1)
            self.assertEqual(json.loads(run_metadata)["powerModelVersion"], "LINE9-DC750-V1.0")
            self.assertNotEqual(json.loads(topology_detail)["sourceId"], "UNSPECIFIED")
            export = engine.export_current_run()
            self.assertGreater(len(export["tables"]["train_voltage_records"]), 0)
            self.assertGreater(len(export["tables"]["power_solver_records"]), 0)
            self.assertGreater(len(export["tables"]["supercapacitor_power_records"]), 0)
            self.assertGreater(len(export["tables"]["regen_path_records"]), 0)
            replay_commands = recorder.replay_power_commands(engine._run_id)
            self.assertEqual(replay_commands[0]["commandType"], "OPERATE_SWITCH")
            self.assertEqual(replay_commands[0]["requestPayload"]["switchId"], "SW-TIE-0902")
            self.assertGreater(len(recorder.replay_events(engine._run_id, "train.state")), 0)
        finally:
            recorder.close()
            db_path.unlink(missing_ok=True)

    def test_solver_failure_pauses_engine_and_exposes_failure_without_publishing_bad_snapshot(self) -> None:
        engine = SimulationEngine.load_from_files(
            scenario_path=ROOT / "data" / "scenarios" / "line9_single.json",
            line_map_path=ROOT / "data" / "cache" / "line_map.json",
            stations_csv_path=ROOT / "MetroDynamicsJavaDemo" / "data" / "stations.csv",
        )
        engine.load()
        engine.add_train({"trainId": "T0901", "initialStationCode": "GGZ", "direction": "UP"})
        engine._update_power(sim_time_ms=0)
        valid = engine.power_service.last_network_snapshot
        assert engine.power_service.solver is not None
        original_solve = engine.power_service.solver.solve

        def failed_solve(*args, **kwargs):
            return replace(original_solve(*args, **kwargs), converged=False)

        engine.power_service.solver.solve = failed_solve  # type: ignore[method-assign]
        engine.clock.start()
        engine._tick()

        self.assertEqual(engine.clock.state, ClockState.PAUSED)
        self.assertIs(engine.power_service.last_network_snapshot, valid)
        snapshot = engine.snapshot()
        assert snapshot is not None
        self.assertEqual(snapshot.power_network["solverFailure"]["reasons"], ["NOT_CONVERGED"])

    def test_power_commands_support_delayed_execution_restore_and_replay(self) -> None:
        engine = SimulationEngine.load_from_files(
            scenario_path=ROOT / "data" / "scenarios" / "line9_single.json",
            line_map_path=ROOT / "data" / "cache" / "line_map.json",
            stations_csv_path=ROOT / "MetroDynamicsJavaDemo" / "data" / "stations.csv",
        )
        engine.load()
        now = engine._absolute_sim_time_ms()
        queued = engine.queue_power_command(
            "SUBSTATION_OUTAGE",
            {"targetId": "TS-0901", "bigBilateral": False, "applyAtSimTimeMs": now + 500},
        )
        engine._apply_power_commands(now)
        self.assertEqual(engine.power_service.network.substations["TS-0901"].status, "IN_SERVICE")
        self.assertEqual(engine.power_command_status(queued["commandId"])[0]["status"], "QUEUED")

        engine._apply_power_commands(now + 500)
        self.assertEqual(engine.power_service.network.substations["TS-0901"].status, "OUTAGE")
        self.assertEqual(engine.power_command_status(queued["commandId"])[0]["status"], "APPLIED")
        engine.queue_power_command("SUBSTATION_RESTORE", {"targetId": "TS-0901"})
        engine._apply_power_commands(now + 750)
        self.assertEqual(engine.power_service.network.substations["TS-0901"].status, "IN_SERVICE")

        records = [
            {"commandId": "OLD-1", "simTimeMs": 1000, "commandType": "SET_FEEDER_STATUS", "requestPayload": {"feederId": "FD-0901-UP-RIGHT", "status": "OPEN"}},
            {"commandId": "OLD-2", "simTimeMs": 1250, "commandType": "SET_FEEDER_STATUS", "requestPayload": {"feederId": "FD-0901-UP-RIGHT", "status": "CLOSED"}},
        ]
        replayed = engine.replay_power_commands(records, base_sim_time_ms=now + 1_000)
        self.assertEqual(len(replayed), 2)
        engine._apply_power_commands(now + 1_000)
        self.assertEqual(engine.power_service.network.feeders["FD-0901-UP-RIGHT"].status, "OPEN")
        engine._apply_power_commands(now + 1_250)
        self.assertEqual(engine.power_service.network.feeders["FD-0901-UP-RIGHT"].status, "CLOSED")


if __name__ == "__main__":
    unittest.main()
