from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import random
import time
import unittest

from app.domain.power.flow_solver import DCTractionPowerFlowSolver
from app.domain.power.line9_topology import build_line9_power_network
from app.domain.power.network_models import TrainElectricalLoad
from app.domain.power.services import PowerSection, PowerService, TrainPowerRequest


TOPOLOGY = Path(__file__).resolve().parents[1] / "data" / "scenarios" / "line9_power_topology.json"


def _topology_data() -> dict:
    return json.loads(TOPOLOGY.read_text(encoding="utf-8"))


class SupercapacitorStorageTests(unittest.TestCase):
    def test_topology_exposes_independent_storage_device(self) -> None:
        network = build_line9_power_network(_topology_data())
        storage = network.supercapacitor_storages["SCESS-0905"]

        self.assertEqual(storage.substation_id, "TS-0905")
        self.assertEqual(storage.rated_energy_kwh, 20.0)
        self.assertEqual(storage.max_charge_power_kw, 2000.0)
        self.assertEqual(storage.discharge_trigger_power_kw, 1000.0)
        self.assertNotEqual(storage.max_charge_power_kw, network.substations["TS-0905"].efs_capacity_kw)

        topology = network.topology_dict()["supercapacitorStorageSystems"][0]
        self.assertEqual(topology["dischargeTriggerPowerKw"], 1000.0)

    def test_braking_charges_storage_and_reduces_waste(self) -> None:
        data = _topology_data()
        without_storage = deepcopy(data)
        without_storage["supercapacitorStorageSystems"] = []
        braking = [
            TrainElectricalLoad(
                "BRAKING",
                "UP",
                7000.0,
                15.0,
                aux_power_kw=100.0,
                regen_power_available_kw=2200.0,
            )
        ]

        baseline = DCTractionPowerFlowSolver(build_line9_power_network(without_storage)).solve(braking, dt_sec=10.0)
        snapshot = DCTractionPowerFlowSolver(build_line9_power_network(data)).solve(braking, dt_sec=10.0)
        storage = snapshot.supercapacitor_flows[0]

        self.assertEqual(storage.state, "CHARGING")
        self.assertAlmostEqual(storage.charge_power_kw, 2000.0, places=6)
        self.assertGreater(storage.soc, 0.50)
        self.assertLess(snapshot.wasted_regen_kw, baseline.wasted_regen_kw)
        split = (
            snapshot.self_consumed_regen_kw
            + snapshot.absorbed_regen_kw
            + sum(item.charge_power_kw for item in snapshot.supercapacitor_flows)
            + snapshot.feedback_regen_kw
            + snapshot.wasted_regen_kw
            + snapshot.regen_transfer_losses_kw
        )
        self.assertAlmostEqual(snapshot.generated_regen_kw, split, places=6)

    def test_stored_energy_discharge_supplies_later_traction(self) -> None:
        solver = DCTractionPowerFlowSolver(build_line9_power_network(_topology_data()))
        solver.solve(
            [TrainElectricalLoad("BRAKING", "UP", 7000.0, 15.0, aux_power_kw=100.0, regen_power_available_kw=2200.0)],
            dt_sec=10.0,
        )
        snapshot = solver.solve(
            [TrainElectricalLoad("TRACTION", "UP", 7000.0, 15.0, aux_power_kw=100.0, traction_power_request_kw=1800.0)],
            dt_sec=10.0,
        )
        storage = snapshot.supercapacitor_flows[0]

        self.assertEqual(storage.state, "DISCHARGING")
        self.assertGreater(storage.discharge_power_kw, 0.0)
        self.assertLess(storage.soc, 0.763)
        self.assertLess(snapshot.power_balance_error_ratio, 0.01)

    def test_storage_respects_soc_limits_under_repeated_cycles(self) -> None:
        solver = DCTractionPowerFlowSolver(build_line9_power_network(_topology_data()))
        braking = [TrainElectricalLoad("B", "UP", 7000.0, 15.0, aux_power_kw=0.0, regen_power_available_kw=5000.0)]
        traction = [TrainElectricalLoad("T", "UP", 7000.0, 15.0, aux_power_kw=0.0, traction_power_request_kw=5000.0)]

        for _ in range(20):
            full = solver.solve(braking, dt_sec=10.0).supercapacitor_flows[0]
        self.assertAlmostEqual(full.soc, 0.90, places=6)
        self.assertEqual(full.state, "FULL")

        for _ in range(20):
            empty = solver.solve(traction, dt_sec=10.0).supercapacitor_flows[0]
        self.assertAlmostEqual(empty.soc, 0.20, places=6)
        self.assertEqual(empty.state, "EMPTY")

    def test_storage_keeps_base_load_on_rectifier_and_only_shaves_peak(self) -> None:
        solver = DCTractionPowerFlowSolver(build_line9_power_network(_topology_data()))

        low = solver.solve(
            [TrainElectricalLoad("LOW", "UP", 7000.0, 0.0, aux_power_kw=200.0)],
            dt_sec=30.0,
        )
        low_storage = low.supercapacitor_flows[0]
        self.assertEqual(low_storage.state, "STANDBY")
        self.assertEqual(low_storage.discharge_power_kw, 0.0)
        self.assertAlmostEqual(low_storage.stored_energy_kwh, 9.975, places=6)

        peak = solver.solve(
            [TrainElectricalLoad("PEAK", "UP", 7000.0, 15.0, aux_power_kw=100.0, traction_power_request_kw=1800.0)],
            dt_sec=10.0,
        )
        peak_storage = peak.supercapacitor_flows[0]
        rectifier_kw = sum(item.rectifier_power_kw for item in peak.substations)
        self.assertEqual(peak_storage.state, "DISCHARGING")
        self.assertGreater(peak_storage.discharge_power_kw, 0.0)
        self.assertLess(peak_storage.discharge_power_kw, 1000.0)
        self.assertGreater(rectifier_kw, 950.0)

    def test_host_substation_outage_disables_storage(self) -> None:
        network = build_line9_power_network(_topology_data())
        network.apply_substation_outage("TS-0905", big_bilateral=False)
        solver = DCTractionPowerFlowSolver(network)

        braking = solver.solve(
            [TrainElectricalLoad("B", "UP", 7000.0, 15.0, aux_power_kw=0.0, regen_power_available_kw=2200.0)],
            dt_sec=10.0,
        )
        flow = braking.supercapacitor_flows[0]
        self.assertEqual(flow.state, "OUT_OF_SERVICE")
        self.assertEqual(flow.status, "OUT_OF_SERVICE")
        self.assertEqual(flow.charge_power_kw, 0.0)
        self.assertEqual(flow.discharge_power_kw, 0.0)
        self.assertAlmostEqual(flow.soc, 0.50, places=9)

    def test_zero_duration_recalculation_does_not_change_soc_or_counters(self) -> None:
        solver = DCTractionPowerFlowSolver(build_line9_power_network(_topology_data()))
        before = solver.storage_checkpoint()
        snapshot = solver.solve(
            [TrainElectricalLoad("PEAK", "UP", 7000.0, 15.0, aux_power_kw=100.0, traction_power_request_kw=1800.0)],
            dt_sec=0.0,
        )

        self.assertEqual(solver.storage_checkpoint(), before)
        self.assertEqual(snapshot.supercapacitor_flows[0].cumulative_discharged_kwh, 0.0)

    def test_invalid_power_service_snapshot_rolls_back_storage_state(self) -> None:
        network = build_line9_power_network(_topology_data())
        service = PowerService(
            [PowerSection("PWR-09-UP", "Up", max_traction_power_kw=20_000.0, warning_power_kw=15_000.0)],
            network=network,
        )
        request = TrainPowerRequest(
            "PEAK",
            "PWR-09-UP",
            speed_mps=15.0,
            position_m=7000.0,
            direction="UP",
            aux_power_kw=100.0,
            traction_power_request_kw=1800.0,
        )
        assert service.solver is not None
        checkpoint = service.solver.storage_checkpoint()
        original_solve = service.solver.solve

        def failed_solve(*args, **kwargs):
            return replace(original_solve(*args, **kwargs), converged=False, power_balance_error_ratio=0.02)

        service.solver.solve = failed_solve  # type: ignore[method-assign]
        service.update([request], dt_sec=10.0, sim_time_ms=10_000)

        self.assertEqual(service.solver.storage_checkpoint(), checkpoint)
        self.assertIsNotNone(service.last_failed_network_snapshot)

    def test_direct_train_absorption_precedes_storage_charging(self) -> None:
        solver = DCTractionPowerFlowSolver(build_line9_power_network(_topology_data()))
        snapshot = solver.solve(
            [
                TrainElectricalLoad("BRAKING", "UP", 7000.0, 15.0, aux_power_kw=100.0, regen_power_available_kw=2200.0),
                TrainElectricalLoad("TRACTION", "UP", 7000.0, 15.0, aux_power_kw=100.0, traction_power_request_kw=800.0),
            ],
            dt_sec=1.0,
        )

        storage_charge_kw = snapshot.supercapacitor_flows[0].charge_power_kw
        self.assertGreater(snapshot.absorbed_regen_kw, 700.0)
        self.assertGreater(storage_charge_kw, 0.0)
        self.assertLess(storage_charge_kw, snapshot.generated_regen_kw - snapshot.self_consumed_regen_kw)
        path_types = [item.sink_type for item in snapshot.regen_paths]
        self.assertLess(path_types.index("TRAIN"), path_types.index("SUPERCAPACITOR"))

    def test_storage_charge_has_priority_over_feedback_at_same_substation(self) -> None:
        data = _topology_data()
        storage = data["supercapacitorStorageSystems"][0]
        storage["substationId"] = "TS-0901"
        storage["storageId"] = "SCESS-0901"
        solver = DCTractionPowerFlowSolver(build_line9_power_network(data))
        snapshot = solver.solve(
            [TrainElectricalLoad("B", "UP", 600.0, 15.0, aux_power_kw=0.0, regen_power_available_kw=2600.0)],
            dt_sec=1.0,
        )

        self.assertGreater(snapshot.supercapacitor_flows[0].charge_power_kw, 0.0)
        self.assertGreater(snapshot.feedback_regen_kw, 0.0)
        path_types = [item.sink_type for item in snapshot.regen_paths]
        self.assertLess(path_types.index("SUPERCAPACITOR"), path_types.index("SUBSTATION_FEEDBACK"))

    def test_snapshot_contract_exposes_storage_ledger(self) -> None:
        solver = DCTractionPowerFlowSolver(build_line9_power_network(_topology_data()))
        payload = solver.solve([], dt_sec=0.0).to_dict()

        storage = payload["supercapacitorStorageSystems"][0]
        self.assertEqual(storage["storageId"], "SCESS-0905")
        self.assertIn("availableChargeEnergyKwh", storage)
        self.assertIn("availableDischargeEnergyKwh", storage)
        self.assertIn("conversionLossesKw", storage)
        self.assertEqual(payload["regen"]["storageChargedKw"], 0.0)
        self.assertEqual(payload["regen"]["storageDischargedKw"], 0.0)

    def test_two_storage_randomized_multitrain_run_preserves_invariants_and_budget(self) -> None:
        data = _topology_data()
        second = deepcopy(data["supercapacitorStorageSystems"][0])
        second.update({"storageId": "SCESS-0908", "substationId": "TS-0908", "initialSoc": 0.6})
        data["supercapacitorStorageSystems"].append(second)
        solver = DCTractionPowerFlowSolver(build_line9_power_network(data))
        rng = random.Random(20260712)
        elapsed_ms: list[float] = []

        for tick in range(180):
            loads: list[TrainElectricalLoad] = []
            for index in range(6):
                direction = "UP" if index % 2 == 0 else "DOWN"
                mileage_m = rng.uniform(500.0, 15_800.0)
                speed_mps = rng.uniform(4.0, 22.0)
                aux_kw = rng.uniform(80.0, 180.0)
                phase = (tick + index) % 4
                traction_kw = rng.uniform(700.0, 2300.0) if phase in {0, 1} else 0.0
                regen_kw = rng.uniform(500.0, 2200.0) if phase == 2 else 0.0
                loads.append(TrainElectricalLoad(
                    f"T{index}",
                    direction,
                    mileage_m,
                    speed_mps,
                    aux_power_kw=aux_kw,
                    traction_power_request_kw=traction_kw,
                    regen_power_available_kw=regen_kw,
                ))

            started = time.perf_counter()
            snapshot = solver.solve(loads, dt_sec=0.25, sim_time_ms=tick * 250)
            elapsed_ms.append((time.perf_counter() - started) * 1000.0)

            self.assertTrue(snapshot.converged)
            self.assertLess(snapshot.power_balance_error_ratio, 0.01)
            regen_split = (
                snapshot.self_consumed_regen_kw
                + snapshot.absorbed_regen_kw
                + sum(item.charge_power_kw for item in snapshot.supercapacitor_flows)
                + snapshot.feedback_regen_kw
                + snapshot.wasted_regen_kw
                + snapshot.regen_transfer_losses_kw
            )
            self.assertAlmostEqual(snapshot.generated_regen_kw, regen_split, places=6)
            for flow in snapshot.supercapacitor_flows:
                config = solver.network.supercapacitor_storages[flow.storage_id]
                self.assertGreaterEqual(flow.soc, config.min_soc - 1e-9)
                self.assertLessEqual(flow.soc, config.max_soc + 1e-9)
                self.assertLessEqual(flow.charge_power_kw, config.max_charge_power_kw + 1e-6)
                self.assertLessEqual(flow.discharge_power_kw, config.max_discharge_power_kw + 1e-6)
                self.assertFalse(flow.charge_power_kw > 1e-9 and flow.discharge_power_kw > 1e-9)

        self.assertLess(sum(elapsed_ms) / len(elapsed_ms), 20.0)
        self.assertLess(max(elapsed_ms), 100.0)


if __name__ == "__main__":
    unittest.main()
