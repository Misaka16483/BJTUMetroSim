from __future__ import annotations

import math
from pathlib import Path
import random
import unittest

from app.domain.power.flow_solver import DCTractionPowerFlowSolver
from app.domain.power.line9_topology import load_line9_power_network
from app.domain.power.network_models import TrainElectricalLoad


ROOT = Path(__file__).resolve().parents[1]
TOPOLOGY = ROOT / "data" / "scenarios" / "line9_power_topology.json"


def _network():
    return load_line9_power_network(TOPOLOGY)


def _assert_finite(test: unittest.TestCase, *values: float) -> None:
    for value in values:
        test.assertTrue(math.isfinite(value), f"non-finite power result: {value!r}")


class PowerValidationPropertyTests(unittest.TestCase):
    def test_topology_structure_and_parameter_ranges_are_consistent(self) -> None:
        network = _network()

        self.assertEqual(len(network.substations), 10)
        self.assertEqual(len(network.feeders), 36)
        self.assertEqual(len(network.contact_sections), 18)
        self.assertEqual(len(network.return_sections), 18)
        self.assertEqual(len(network.switches), 9)
        self.assertEqual(len(network.supercapacitor_storages), 1)
        self.assertEqual(network.quality, "ENGINEERING_ESTIMATE")
        self.assertEqual(network.model_version, "LINE9-DC750-V1.0")

        mileages = [item.mileage_m for item in network.ordered_substations]
        self.assertEqual(mileages, sorted(mileages))
        self.assertEqual(len(mileages), len(set(mileages)))
        for item in network.substations.values():
            _assert_finite(
                self,
                item.mileage_m,
                item.no_load_voltage_v,
                item.internal_resistance_ohm,
                item.rated_current_a,
                item.overload_current_a,
            )
            self.assertGreater(item.no_load_voltage_v, network.nominal_voltage_v)
            self.assertGreater(item.internal_resistance_ohm, 0.0)
            self.assertGreater(item.rated_current_a, 0.0)
            self.assertGreater(item.overload_current_a, item.rated_current_a)
            self.assertNotEqual(item.source_id, "UNSPECIFIED")
            self.assertTrue(item.parameter_sources)

        for feeder in network.feeders.values():
            self.assertIn(feeder.substation_id, network.substations)
            self.assertIn(feeder.direction, {"UP", "DOWN"})
            self.assertIn(feeder.side, {"LEFT", "RIGHT"})
            self.assertNotEqual(feeder.from_mileage_m, feeder.to_mileage_m)
            self.assertGreater(feeder.cable_resistance_ohm, 0.0)
            self.assertGreater(feeder.short_time_current_a, feeder.continuous_current_a)

        for section in [*network.contact_sections.values(), *network.return_sections.values()]:
            self.assertIn(section.direction, {"UP", "DOWN"})
            self.assertGreater(section.to_mileage_m, section.from_mileage_m)
            self.assertGreater(section.resistance_ohm_per_km, 0.0)

        for switch in network.switches.values():
            self.assertIn(switch.from_node_id, network.substations)
            self.assertIn(switch.to_node_id, network.substations)
            self.assertEqual(switch.normal_state, "OPEN")
            self.assertEqual(switch.current_state, switch.normal_state)

        for storage in network.supercapacitor_storages.values():
            self.assertIn(storage.substation_id, network.substations)
            self.assertGreater(storage.rated_energy_kwh, 0.0)
            self.assertGreater(storage.max_charge_power_kw, 0.0)
            self.assertGreater(storage.max_discharge_power_kw, 0.0)
            self.assertLess(storage.min_soc, storage.initial_soc)
            self.assertLess(storage.initial_soc, storage.max_soc)
            self.assertLessEqual(storage.max_soc, 1.0)
            self.assertTrue(0.0 < storage.charge_efficiency <= 1.0)
            self.assertTrue(0.0 < storage.discharge_efficiency <= 1.0)

    def test_no_load_snapshot_is_finite_and_has_zero_network_power(self) -> None:
        snapshot = DCTractionPowerFlowSolver(_network()).solve([], dt_sec=0.0)

        self.assertTrue(snapshot.converged)
        self.assertEqual(snapshot.losses_kw, 0.0)
        self.assertEqual(snapshot.generated_regen_kw, 0.0)
        self.assertEqual(snapshot.power_balance_error_kw, 0.0)
        for item in snapshot.substations:
            _assert_finite(self, item.voltage_v, item.current_a, item.power_kw, item.energy_kwh)
            self.assertEqual(item.current_a, 0.0)
            self.assertEqual(item.power_kw, 0.0)
            self.assertEqual(item.voltage_v, 825.0)

    def test_single_source_matches_closed_form_voltage_current_and_path_loss(self) -> None:
        network = _network()
        target_feeder = "FD-0901-UP-RIGHT"
        for feeder_id in list(network.feeders):
            if feeder_id != target_feeder:
                network.set_feeder_status(feeder_id, "OPEN")
        load = TrainElectricalLoad(
            "REFERENCE",
            "UP",
            700.0,
            15.0,
            aux_power_kw=40.0,
            traction_power_request_kw=450.0,
        )
        solver = DCTractionPowerFlowSolver(network)
        source_paths = solver._load_source_paths(load)
        self.assertEqual(len(source_paths), 1)
        substation_id, _feeder_id, path_resistance = source_paths[0]
        substation = network.substations[substation_id]
        total_resistance = path_resistance + substation.internal_resistance_ohm
        requested_w = load.traction_demand_kw * 1000.0
        discriminant = substation.no_load_voltage_v**2 - 4.0 * total_resistance * requested_w
        self.assertGreater(discriminant, 0.0)
        expected_voltage = (substation.no_load_voltage_v + math.sqrt(discriminant)) / 2.0
        expected_current = requested_w / expected_voltage

        snapshot = solver.solve([load], dt_sec=1.0)
        actual = snapshot.trains[0]
        self.assertAlmostEqual(actual.voltage_v, expected_voltage, delta=0.5)
        self.assertLess(abs(actual.current_a - expected_current) / expected_current, 0.001)
        self.assertAlmostEqual(
            snapshot.losses_kw,
            actual.current_a**2 * total_resistance / 1000.0,
            delta=max(snapshot.losses_kw * 0.01, 1e-6),
        )

    def test_symmetric_bilateral_midpoint_splits_current_equally(self) -> None:
        midpoint = (1660.52 + 3429.32) / 2.0
        snapshot = DCTractionPowerFlowSolver(_network()).solve(
            [TrainElectricalLoad(
                "MIDPOINT",
                "UP",
                midpoint,
                18.0,
                aux_power_kw=0.0,
                traction_power_request_kw=1500.0,
            )],
            dt_sec=1.0,
        )
        left = next(item for item in snapshot.substations if item.substation_id == "TS-0902")
        right = next(item for item in snapshot.substations if item.substation_id == "TS-0903")
        mean_current = (left.current_a + right.current_a) / 2.0
        self.assertGreater(mean_current, 0.0)
        self.assertLess(abs(left.current_a - right.current_a) / mean_current, 0.01)
        self.assertAlmostEqual(left.voltage_v, right.voltage_v, delta=0.1)

    def test_load_and_single_source_distance_have_physical_monotonicity(self) -> None:
        load_results = []
        for power_kw in (300.0, 600.0, 900.0):
            snapshot = DCTractionPowerFlowSolver(_network()).solve(
                [TrainElectricalLoad(
                    "LOAD",
                    "UP",
                    2400.0,
                    15.0,
                    aux_power_kw=0.0,
                    traction_power_request_kw=power_kw,
                )],
                dt_sec=1.0,
            )
            load_results.append((snapshot.trains[0].voltage_v, snapshot.trains[0].current_a, snapshot.losses_kw))
        self.assertGreater(load_results[0][0], load_results[1][0])
        self.assertGreater(load_results[1][0], load_results[2][0])
        self.assertLess(load_results[0][1], load_results[1][1])
        self.assertLess(load_results[1][1], load_results[2][1])
        self.assertLess(load_results[0][2], load_results[1][2])
        self.assertLess(load_results[1][2], load_results[2][2])

        distance_results = []
        for mileage_m in (450.0, 700.0, 1000.0):
            network = _network()
            for feeder_id in list(network.feeders):
                if feeder_id != "FD-0901-UP-RIGHT":
                    network.set_feeder_status(feeder_id, "OPEN")
            snapshot = DCTractionPowerFlowSolver(network).solve(
                [TrainElectricalLoad(
                    "DISTANCE",
                    "UP",
                    mileage_m,
                    15.0,
                    aux_power_kw=0.0,
                    traction_power_request_kw=450.0,
                )],
                dt_sec=1.0,
            )
            distance_results.append((snapshot.trains[0].voltage_v, snapshot.losses_kw))
        self.assertGreater(distance_results[0][0], distance_results[1][0])
        self.assertGreater(distance_results[1][0], distance_results[2][0])
        self.assertLess(distance_results[0][1], distance_results[1][1])
        self.assertLess(distance_results[1][1], distance_results[2][1])

    def test_substation_and_train_power_dimensions_are_consistent(self) -> None:
        snapshot = DCTractionPowerFlowSolver(_network()).solve(
            [
                TrainElectricalLoad("T1", "UP", 2200.0, 18.0, aux_power_kw=100.0, traction_power_request_kw=900.0),
                TrainElectricalLoad("T2", "UP", 2700.0, 18.0, aux_power_kw=100.0, regen_power_available_kw=700.0),
            ],
            dt_sec=1.0,
        )
        for item in snapshot.substations:
            _assert_finite(self, item.voltage_v, item.current_a, item.power_kw)
            self.assertAlmostEqual(item.power_kw, item.voltage_v * item.current_a / 1000.0, places=9)
        for item in snapshot.trains:
            _assert_finite(self, item.voltage_v, item.current_a, item.requested_power_kw)
            if item.requested_power_kw > 0.0:
                self.assertAlmostEqual(
                    item.voltage_v * item.current_a / 1000.0,
                    item.requested_power_kw,
                    delta=max(item.requested_power_kw * 0.01, 1e-6),
                )

    def test_storage_energy_is_invariant_to_time_step_partition(self) -> None:
        braking = [TrainElectricalLoad(
            "BRAKING",
            "UP",
            7000.0,
            15.0,
            aux_power_kw=100.0,
            regen_power_available_kw=1200.0,
        )]
        final_states = []
        for dt_sec, count in ((10.0, 1), (1.0, 10), (0.1, 100)):
            solver = DCTractionPowerFlowSolver(_network())
            snapshot = None
            for tick in range(count):
                snapshot = solver.solve(braking, dt_sec=dt_sec, sim_time_ms=int(tick * dt_sec * 1000))
            assert snapshot is not None
            final_states.append(snapshot.supercapacitor_flows[0])
        baseline = final_states[0]
        for item in final_states[1:]:
            self.assertAlmostEqual(item.stored_energy_kwh, baseline.stored_energy_kwh, places=9)
            self.assertAlmostEqual(item.cumulative_charged_kwh, baseline.cumulative_charged_kwh, places=9)

    def test_seeded_random_multitrain_sequence_preserves_hard_invariants(self) -> None:
        network = _network()
        solver = DCTractionPowerFlowSolver(network)
        rng = random.Random(20260713)

        for tick in range(300):
            loads = []
            for index in range(12):
                phase = (tick // 12 + index) % 5
                traction_kw = rng.uniform(400.0, 2200.0) if phase in {0, 1, 2} else 0.0
                regen_kw = rng.uniform(300.0, 1800.0) if phase == 3 else 0.0
                loads.append(TrainElectricalLoad(
                    f"R{index:02d}",
                    "UP" if index % 2 == 0 else "DOWN",
                    rng.uniform(313.0, 16048.92),
                    rng.uniform(0.0, 22.0),
                    aux_power_kw=rng.uniform(60.0, 180.0),
                    traction_power_request_kw=traction_kw,
                    regen_power_available_kw=regen_kw,
                ))
            snapshot = solver.solve(loads, dt_sec=0.25, sim_time_ms=tick * 250)
            self.assertTrue(snapshot.converged)
            self.assertLess(snapshot.power_balance_error_ratio, 0.01)
            _assert_finite(
                self,
                snapshot.losses_kw,
                snapshot.generated_regen_kw,
                snapshot.wasted_regen_kw,
                snapshot.power_balance_error_kw,
            )
            regen_split = (
                snapshot.self_consumed_regen_kw
                + snapshot.absorbed_regen_kw
                + sum(item.charge_power_kw for item in snapshot.supercapacitor_flows)
                + snapshot.feedback_regen_kw
                + snapshot.wasted_regen_kw
                + snapshot.regen_transfer_losses_kw
            )
            self.assertAlmostEqual(snapshot.generated_regen_kw, regen_split, places=6)
            for train in snapshot.trains:
                _assert_finite(
                    self,
                    train.voltage_v,
                    train.current_a,
                    train.traction_limit_ratio,
                    train.regen_limit_ratio,
                )
                self.assertGreaterEqual(train.voltage_v, 0.0)
                self.assertLessEqual(train.voltage_v, 1000.0)
                if train.requested_power_kw > 0.0:
                    self.assertLessEqual(train.voltage_v, 900.0)
                self.assertTrue(0.0 <= train.traction_limit_ratio <= 1.0)
                self.assertTrue(0.0 <= train.regen_limit_ratio <= 1.0)
            for flow in snapshot.supercapacitor_flows:
                config = network.supercapacitor_storages[flow.storage_id]
                self.assertGreaterEqual(flow.soc, config.min_soc - 1e-9)
                self.assertLessEqual(flow.soc, config.max_soc + 1e-9)
                self.assertLessEqual(flow.charge_power_kw, config.max_charge_power_kw + 1e-6)
                self.assertLessEqual(flow.discharge_power_kw, config.max_discharge_power_kw + 1e-6)
                self.assertFalse(flow.charge_power_kw > 1e-9 and flow.discharge_power_kw > 1e-9)


if __name__ == "__main__":
    unittest.main()
