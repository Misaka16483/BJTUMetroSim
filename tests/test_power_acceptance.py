from __future__ import annotations

import json
import time
import unittest
from dataclasses import replace
from pathlib import Path

from app.domain.power.flow_solver import DCTractionPowerFlowSolver
from app.domain.power.line9_topology import load_line9_power_network
from app.domain.power.network_models import TrainElectricalLoad
from app.core.engine import SimulationEngine


ROOT = Path(__file__).resolve().parents[1]
TOPOLOGY = ROOT / "data" / "scenarios" / "line9_power_topology.json"


def five_train_loads() -> list[TrainElectricalLoad]:
    return [
        TrainElectricalLoad(f"T{i}", "UP", 2200.0 + i * 400.0, 15.0, traction_force_n=100_000.0, aux_power_kw=100.0)
        for i in range(5)
    ]


class PowerAcceptanceTests(unittest.TestCase):
    def _advance_until_any_train_moves(
        self,
        engine: SimulationEngine,
        *,
        min_speed_mps: float = 1.0,
        max_ticks: int = 300,
    ) -> int:
        """Wait through the real dwell/interlocking startup before power checks.

        The Member C integration makes initial trains obey platform dwell,
        route locking, MA, and ATP before they draw meaningful traction power.
        Power acceptance tests should therefore start voltage/delay assertions
        from the first actual movement, not from the scenario clock zero.
        """
        for tick in range(1, max_ticks + 1):
            engine._tick()
            if any(item.speed_mps >= min_speed_mps for item in engine.trains):
                return tick
        self.fail("No train reached motion state before power acceptance timeout")

    def test_five_train_engine_runs_within_realtime_budget_after_warmup(self) -> None:
        engine = SimulationEngine.load_from_files(
            scenario_path=ROOT / "data" / "scenarios" / "line9_5train_power.json",
            line_map_path=ROOT / "data" / "cache" / "line_map.json",
            stations_csv_path=ROOT / "data" / "line9" / "stations.csv",
        )
        engine.load()
        engine.clock.start()
        durations_ms: list[float] = []
        for _ in range(240):
            started = time.perf_counter()
            engine._tick()
            durations_ms.append((time.perf_counter() - started) * 1000.0)
        snapshot = engine.snapshot()
        assert snapshot is not None
        p95_ms = sorted(durations_ms[20:])[int(len(durations_ms[20:]) * 0.95) - 1]
        self.assertEqual(len(snapshot.trains), 5)
        self.assertLess(p95_ms, 125.0)
        self.assertTrue(snapshot.power_network["solver"]["converged"])
        self.assertLess(snapshot.power_network["solver"]["powerBalanceErrorRatio"], 0.01)
        self.assertTrue(all(item["massKg"] > 225_000.0 for item in snapshot.trains))
        self.assertTrue(all(item["pantographVoltageV"] > 500.0 for item in snapshot.trains))
        self.assertTrue(all(item["status"] == "NORMAL" for item in snapshot.power_network["substations"]))

    def test_five_train_solver_meets_performance_and_balance_budget(self) -> None:
        durations_ms: list[float] = []
        snapshots = []
        for _ in range(20):
            solver = DCTractionPowerFlowSolver(load_line9_power_network(TOPOLOGY))
            started = time.perf_counter()
            snapshot = solver.solve(five_train_loads(), dt_sec=0.25)
            durations_ms.append((time.perf_counter() - started) * 1000.0)
            snapshots.append(snapshot)
        p95_ms = sorted(durations_ms)[int(len(durations_ms) * 0.95) - 1]
        self.assertLess(p95_ms, 50.0)
        self.assertTrue(all(item.converged for item in snapshots))
        self.assertTrue(all(item.power_balance_error_ratio < 0.01 for item in snapshots))

    def test_same_inputs_are_deterministic(self) -> None:
        first = DCTractionPowerFlowSolver(load_line9_power_network(TOPOLOGY)).solve(five_train_loads(), dt_sec=0.25)
        second = DCTractionPowerFlowSolver(load_line9_power_network(TOPOLOGY)).solve(five_train_loads(), dt_sec=0.25)
        self.assertEqual(
            [round(item.voltage_v, 6) for item in first.trains],
            [round(item.voltage_v, 6) for item in second.trains],
        )

    def test_full_five_train_scenario_is_deterministic(self) -> None:
        results = []
        for _ in range(2):
            engine = SimulationEngine.load_from_files(
                scenario_path=ROOT / "data" / "scenarios" / "line9_5train_power.json",
                line_map_path=ROOT / "data" / "cache" / "line_map.json",
                stations_csv_path=ROOT / "data" / "line9" / "stations.csv",
            )
            engine.load()
            engine.clock.start()
            for _tick in range(120):
                engine._tick()
            snapshot = engine.snapshot()
            assert snapshot is not None
            results.append([
                (
                    item["trainId"],
                    item["pathPositionM"],
                    item["speedMps"],
                    item["massKg"],
                    item["pantographVoltageV"],
                )
                for item in snapshot.trains
            ])
        self.assertEqual(results[0], results[1])

    def test_passenger_load_propagates_to_energy_voltage_limit_and_runtime(self) -> None:
        scenario = json.loads((ROOT / "data" / "scenarios" / "line9_5train_power.json").read_text(encoding="utf-8"))
        test_dir = ROOT / "outputs" / "test-runtime"
        test_dir.mkdir(parents=True, exist_ok=True)
        normal_results = []
        stressed_limits = []
        try:
            for passengers in (0, 600):
                for train in scenario["trains"]:
                    train["initialLoadPax"] = passengers
                scenario_path = test_dir / f"passenger-load-{passengers}.json"
                scenario_path.write_text(json.dumps(scenario, ensure_ascii=False), encoding="utf-8")
                engine = SimulationEngine.load_from_files(
                    scenario_path=scenario_path,
                    line_map_path=ROOT / "data" / "cache" / "line_map.json",
                    stations_csv_path=ROOT / "data" / "line9" / "stations.csv",
                )
                engine.load()
                engine.clock.start()
                peak_power_kw = 0.0
                min_voltage_v = 1000.0
                reach_tick = None
                for tick in range(900):
                    engine._tick()
                    snapshot = engine.snapshot()
                    assert snapshot is not None
                    peak_power_kw = max(
                        peak_power_kw,
                        sum(max(item["requestedPowerKw"], 0.0) for item in snapshot.trains),
                    )
                    min_voltage_v = min(min_voltage_v, snapshot.kpi["minTrainVoltageV"])
                    if reach_tick is None and snapshot.trains[0]["pathPositionM"] >= 1000.0:
                        reach_tick = tick + 1
                        break
                snapshot = engine.snapshot()
                assert snapshot is not None and reach_tick is not None
                normal_results.append({
                    "massKg": sum(item["massKg"] for item in snapshot.trains),
                    "peakPowerKw": peak_power_kw,
                    "minVoltageV": min_voltage_v,
                    "energyKwh": sum(item["energyKwh"] for item in snapshot.trains),
                    "runtimeSec": reach_tick * 0.25,
                })

                stressed = SimulationEngine.load_from_files(
                    scenario_path=scenario_path,
                    line_map_path=ROOT / "data" / "cache" / "line_map.json",
                    stations_csv_path=ROOT / "data" / "line9" / "stations.csv",
                )
                stressed.load()
                network = stressed.power_service.network
                assert network is not None
                network.substations = {
                    key: replace(value, no_load_voltage_v=740.0, internal_resistance_ohm=0.025)
                    for key, value in network.substations.items()
                }
                stressed.clock.start()
                self._advance_until_any_train_moves(stressed)
                min_limit = 1.0
                for _tick in range(200):
                    stressed._tick()
                    current = stressed.snapshot()
                    assert current is not None
                    min_limit = min(min_limit, current.kpi["minTractionLimitRatio"])
                stressed_limits.append(min_limit)
        finally:
            for passengers in (0, 600):
                (test_dir / f"passenger-load-{passengers}.json").unlink(missing_ok=True)

        low, high = normal_results
        self.assertGreater(high["massKg"], low["massKg"])
        self.assertNotEqual(high["peakPowerKw"], low["peakPowerKw"])
        self.assertNotEqual(high["minVoltageV"], low["minVoltageV"])
        self.assertGreater(high["energyKwh"], low["energyKwh"])
        self.assertGreater(high["runtimeSec"], low["runtimeSec"])
        self.assertTrue(all(item < 1.0 for item in stressed_limits))
        self.assertNotEqual(round(stressed_limits[0], 4), round(stressed_limits[1], 4))

    def test_regen_energy_split_is_conservative(self) -> None:
        solver = DCTractionPowerFlowSolver(load_line9_power_network(TOPOLOGY))
        snapshot = solver.solve(
            [
                TrainElectricalLoad("TRACTION", "UP", 2200.0, 18.0, traction_force_n=95_000.0, aux_power_kw=60.0),
                TrainElectricalLoad("BRAKING", "UP", 2600.0, 18.0, brake_force_n=80_000.0, aux_power_kw=60.0),
            ],
            dt_sec=0.25,
        )
        split = (
            snapshot.self_consumed_regen_kw
            + snapshot.absorbed_regen_kw
            + snapshot.feedback_regen_kw
            + snapshot.wasted_regen_kw
            + snapshot.regen_transfer_losses_kw
        )
        self.assertAlmostEqual(snapshot.generated_regen_kw, split, places=9)
        self.assertGreater(snapshot.absorbed_regen_kw, 0.0)

    def test_regen_absorption_feedback_and_waste_all_conserve_energy(self) -> None:
        network = load_line9_power_network(TOPOLOGY)
        network.operate_switch("SW-TIE-0902", "CLOSED")
        solver = DCTractionPowerFlowSolver(network)
        snapshot = solver.solve(
            [
                TrainElectricalLoad("TRACTION", "UP", 2200.0, 18.0, traction_force_n=95_000.0, aux_power_kw=60.0),
                TrainElectricalLoad("BRAKING-1", "UP", 2600.0, 22.0, brake_force_n=180_000.0, aux_power_kw=60.0),
                TrainElectricalLoad("BRAKING-2", "UP", 3000.0, 22.0, brake_force_n=180_000.0, aux_power_kw=60.0),
                TrainElectricalLoad("BRAKING-3", "UP", 3400.0, 22.0, brake_force_n=180_000.0, aux_power_kw=60.0),
            ],
            dt_sec=0.25,
        )
        split = (
            snapshot.self_consumed_regen_kw
            + snapshot.absorbed_regen_kw
            + snapshot.feedback_regen_kw
            + snapshot.wasted_regen_kw
            + snapshot.regen_transfer_losses_kw
        )
        self.assertAlmostEqual(snapshot.generated_regen_kw, split, places=9)
        self.assertGreater(snapshot.absorbed_regen_kw, 0.0)
        self.assertGreater(snapshot.feedback_regen_kw, 0.0)
        self.assertGreater(snapshot.wasted_regen_kw, 0.0)

    def test_tie_switch_changes_supply_path_and_voltage(self) -> None:
        network = load_line9_power_network(TOPOLOGY)
        load = [TrainElectricalLoad("T1", "UP", 2400.0, 18.0, traction_force_n=130_000.0, aux_power_kw=100.0)]
        before = DCTractionPowerFlowSolver(network).solve(load, dt_sec=0.25)
        network.operate_switch("SW-TIE-0902", "CLOSED")
        after = DCTractionPowerFlowSolver(network).solve(load, dt_sec=0.25)
        before_sources = {item.substation_id for item in before.substations if item.current_a > 1.0}
        after_sources = {item.substation_id for item in after.substations if item.current_a > 1.0}
        self.assertNotEqual(before_sources, after_sources)
        self.assertGreater(after.trains[0].voltage_v, before.trains[0].voltage_v)

    def test_n_minus_one_excludes_failed_source_and_remains_solvable(self) -> None:
        network = load_line9_power_network(TOPOLOGY)
        loads = [
            TrainElectricalLoad(f"T{i}", "UP", 6100.0 + i * 180.0, 18.0, traction_force_n=170_000.0, aux_power_kw=100.0)
            for i in range(3)
        ]
        baseline = DCTractionPowerFlowSolver(network).solve(loads, dt_sec=0.25)
        network.apply_substation_outage("TS-0905", big_bilateral=True)
        snapshot = DCTractionPowerFlowSolver(network).solve(loads, dt_sec=0.25)
        restored = DCTractionPowerFlowSolver(load_line9_power_network(TOPOLOGY)).solve(loads, dt_sec=0.25)
        failed = next(item for item in snapshot.substations if item.substation_id == "TS-0905")
        self.assertEqual(failed.current_a, 0.0)
        self.assertEqual(failed.status, "OUTAGE")
        self.assertTrue(snapshot.converged)
        self.assertGreater(min(item.voltage_v for item in snapshot.trains), 500.0)
        baseline_voltage = min(item.voltage_v for item in baseline.trains)
        outage_voltage = min(item.voltage_v for item in snapshot.trains)
        outage_limit = min(item.traction_limit_ratio for item in snapshot.trains)
        self.assertLess(outage_voltage, baseline_voltage)
        self.assertLess(outage_limit, 1.0)
        self.assertGreater(1.0 / outage_limit - 1.0, 0.0)
        self.assertAlmostEqual(
            min(item.voltage_v for item in restored.trains),
            baseline_voltage,
            places=6,
        )
        self.assertEqual(min(item.traction_limit_ratio for item in restored.trains), 1.0)

    def test_power_command_is_applied_only_at_tick_boundary(self) -> None:
        engine = SimulationEngine.load_from_files(
            scenario_path=ROOT / "data" / "scenarios" / "line9_5train_power.json",
            line_map_path=ROOT / "data" / "cache" / "line_map.json",
            stations_csv_path=ROOT / "data" / "line9" / "stations.csv",
        )
        engine.load()
        network = engine.power_service.network
        assert network is not None
        command = engine.queue_power_command(
            "OPERATE_SWITCH",
            {"switchId": "SW-TIE-0902", "state": "CLOSED"},
        )
        self.assertEqual(command["status"], "QUEUED")
        self.assertEqual(network.switches["SW-TIE-0902"].current_state, "OPEN")
        engine.clock.start()
        engine._tick()
        self.assertEqual(network.switches["SW-TIE-0902"].current_state, "CLOSED")
        snapshot = engine.snapshot()
        assert snapshot is not None
        self.assertEqual(snapshot.power_network["commandResults"][-1]["status"], "APPLIED")

    def test_n_minus_one_delay_accumulates_and_stops_after_recovery(self) -> None:
        engine = SimulationEngine.load_from_files(
            scenario_path=ROOT / "data" / "scenarios" / "line9_5train_power.json",
            line_map_path=ROOT / "data" / "cache" / "line_map.json",
            stations_csv_path=ROOT / "data" / "line9" / "stations.csv",
        )
        engine.load()
        network = engine.power_service.network
        assert network is not None
        network.substations = {
            key: replace(value, no_load_voltage_v=740.0, internal_resistance_ohm=0.025)
            for key, value in network.substations.items()
        }
        engine.clock.start()
        self._advance_until_any_train_moves(engine)
        delay_before = sum(item.power_constraint_delay_sec for item in engine.trains)

        engine.queue_power_command("SUBSTATION_OUTAGE", {"targetId": "TS-0905", "bigBilateral": True})
        for _tick in range(80):
            engine._tick()
        outage_snapshot = engine.snapshot()
        assert outage_snapshot is not None
        delay_during_outage = sum(item.power_constraint_delay_sec for item in engine.trains)
        self.assertGreater(delay_during_outage, delay_before)
        self.assertLess(outage_snapshot.kpi["minTractionLimitRatio"], 1.0)

        engine.queue_power_command("RESET_NETWORK", {})
        for _tick in range(20):
            engine._tick()
        recovered_snapshot = engine.snapshot()
        assert recovered_snapshot is not None
        delay_after_recovery = sum(item.power_constraint_delay_sec for item in engine.trains)
        self.assertAlmostEqual(delay_after_recovery, delay_during_outage, places=9)
        self.assertEqual(recovered_snapshot.kpi["minTractionLimitRatio"], 1.0)
        self.assertEqual(recovered_snapshot.power_network["commandResults"][-1]["status"], "APPLIED")


if __name__ == "__main__":
    unittest.main()
