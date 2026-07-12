from __future__ import annotations

import unittest
from pathlib import Path

from app.domain.power.flow_solver import DCTractionPowerFlowSolver
from app.domain.power.line9_topology import load_line9_power_network
from app.domain.power.network_models import TrainElectricalLoad


ROOT = Path(__file__).resolve().parents[1]


def _network():
    return load_line9_power_network(ROOT / "data" / "scenarios" / "line9_power_topology.json")


class DCTractionPowerFlowSolverTests(unittest.TestCase):
    def test_single_train_midpoint_bilateral_supply_is_balanced(self) -> None:
        network = _network()
        solver = DCTractionPowerFlowSolver(network)

        snapshot = solver.solve(
            [
                TrainElectricalLoad(
                    train_id="T1",
                    direction="UP",
                    mileage_m=(1660.52 + 3429.32) / 2,
                    speed_mps=18.0,
                    traction_force_n=95_000.0,
                    aux_power_kw=60.0,
                )
            ],
            dt_sec=1.0,
        )

        train = snapshot.trains[0]
        self.assertGreater(train.voltage_v, 650.0)
        self.assertEqual(train.traction_limit_ratio, 1.0)
        left = next(item for item in snapshot.substations if item.substation_id == "TS-0902")
        right = next(item for item in snapshot.substations if item.substation_id == "TS-0903")
        self.assertAlmostEqual(left.current_a, right.current_a, delta=300.0)

    def test_single_train_near_one_side_loads_nearer_substation_more(self) -> None:
        network = _network()
        solver = DCTractionPowerFlowSolver(network)

        snapshot = solver.solve(
            [
                TrainElectricalLoad(
                    train_id="T1",
                    direction="UP",
                    mileage_m=1800.0,
                    speed_mps=18.0,
                    traction_force_n=95_000.0,
                    aux_power_kw=60.0,
                )
            ],
            dt_sec=1.0,
        )

        left = next(item for item in snapshot.substations if item.substation_id == "TS-0902")
        right = next(item for item in snapshot.substations if item.substation_id == "TS-0903")
        self.assertGreater(left.current_a, right.current_a)

    def test_multiple_trains_raise_load_and_create_warning(self) -> None:
        network = _network()
        solver = DCTractionPowerFlowSolver(network)

        snapshot = solver.solve(
            [
                TrainElectricalLoad(f"T{i}", "UP", 2_200.0 + i * 160.0, 20.0, traction_force_n=145_000.0, aux_power_kw=150.0)
                for i in range(4)
            ],
            dt_sec=1.0,
        )

        self.assertLess(min(train.voltage_v for train in snapshot.trains), 750.0)
        self.assertTrue(any(item["type"] in {"SUBSTATION_WARNING", "FEEDER_WARNING", "LIMITED"} for item in snapshot.alerts))

    def test_regen_can_be_absorbed_by_traction_train(self) -> None:
        network = _network()
        solver = DCTractionPowerFlowSolver(network)

        snapshot = solver.solve(
            [
                TrainElectricalLoad("T1", "UP", 2200.0, 18.0, traction_force_n=95_000.0, aux_power_kw=60.0),
                TrainElectricalLoad("T2", "UP", 2600.0, 18.0, brake_force_n=80_000.0, aux_power_kw=60.0),
            ],
            dt_sec=1.0,
        )

        self.assertGreater(snapshot.generated_regen_kw, 0.0)
        self.assertGreater(snapshot.absorbed_regen_kw, 0.0)
        self.assertLess(snapshot.wasted_regen_kw, snapshot.generated_regen_kw)
        transfer = next(item for item in snapshot.regen_paths if item.sink_type == "TRAIN")
        self.assertEqual(transfer.source_train_id, "T2")
        self.assertEqual(transfer.sink_id, "T1")
        self.assertAlmostEqual(transfer.generated_kw, transfer.delivered_kw + transfer.losses_kw, places=9)
        traction = next(item for item in snapshot.trains if item.train_id == "T1")
        self.assertAlmostEqual(
            traction.voltage_v * traction.current_a / 1000.0,
            traction.requested_power_kw,
            delta=traction.requested_power_kw * 0.01,
        )

    def test_regen_supplies_nearby_stopped_train_auxiliary_load(self) -> None:
        solver = DCTractionPowerFlowSolver(_network())

        snapshot = solver.solve(
            [
                TrainElectricalLoad(
                    "STOPPED",
                    "UP",
                    2200.0,
                    0.0,
                    aux_power_kw=150.0,
                ),
                TrainElectricalLoad(
                    "BRAKING",
                    "UP",
                    2450.0,
                    18.0,
                    aux_power_kw=150.0,
                    regen_power_available_kw=1200.0,
                ),
            ],
            dt_sec=1.0,
        )

        transfer = next(
            item
            for item in snapshot.regen_paths
            if item.sink_type == "TRAIN" and item.sink_id == "STOPPED"
        )
        self.assertGreater(transfer.delivered_kw, 0.0)
        self.assertLessEqual(transfer.delivered_kw, 150.0)
        self.assertAlmostEqual(
            snapshot.generated_regen_kw,
            snapshot.self_consumed_regen_kw
            + snapshot.absorbed_regen_kw
            + snapshot.feedback_regen_kw
            + snapshot.regen_transfer_losses_kw
            + snapshot.wasted_regen_kw,
            places=6,
        )

    def test_regen_is_not_absorbed_across_disconnected_supply_paths(self) -> None:
        network = _network()
        solver = DCTractionPowerFlowSolver(network)

        snapshot = solver.solve(
            [
                TrainElectricalLoad("TRACTION", "UP", 2_200.0, 18.0, traction_force_n=95_000.0, aux_power_kw=60.0),
                TrainElectricalLoad("REGEN", "UP", 13_000.0, 18.0, brake_force_n=80_000.0, aux_power_kw=60.0),
            ],
            dt_sec=1.0,
        )

        self.assertEqual(snapshot.absorbed_regen_kw, 0.0)
        self.assertEqual(snapshot.feedback_regen_kw, 0.0)
        self.assertAlmostEqual(
            snapshot.generated_regen_kw,
            snapshot.self_consumed_regen_kw + snapshot.wasted_regen_kw,
            places=9,
        )
        self.assertEqual({item.sink_type for item in snapshot.regen_paths}, {"TRAIN_AUXILIARY", "WASTE"})

    def test_explicit_teacher_curve_power_overrides_force_speed_estimate(self) -> None:
        load = TrainElectricalLoad(
            "T1",
            "UP",
            2200.0,
            18.0,
            traction_force_n=180_000.0,
            aux_power_kw=100.0,
            traction_power_request_kw=1234.0,
        )
        self.assertEqual(load.traction_power_kw, 1234.0)
        self.assertEqual(load.traction_demand_kw, 1334.0)

    def test_regen_accounting_includes_self_consumption_and_per_train_acceptance(self) -> None:
        snapshot = DCTractionPowerFlowSolver(_network()).solve(
            [
                TrainElectricalLoad(
                    "TRACTION",
                    "UP",
                    2200.0,
                    18.0,
                    aux_power_kw=100.0,
                    traction_power_request_kw=900.0,
                ),
                TrainElectricalLoad(
                    "REGEN",
                    "UP",
                    2600.0,
                    18.0,
                    aux_power_kw=120.0,
                    regen_power_available_kw=800.0,
                ),
            ],
            dt_sec=1.0,
        )
        regen_train = next(item for item in snapshot.trains if item.train_id == "REGEN")
        self.assertEqual(snapshot.generated_regen_kw, 800.0)
        self.assertEqual(snapshot.self_consumed_regen_kw, 120.0)
        self.assertAlmostEqual(
            snapshot.generated_regen_kw,
            snapshot.self_consumed_regen_kw
            + snapshot.absorbed_regen_kw
            + snapshot.feedback_regen_kw
            + snapshot.regen_transfer_losses_kw
            + snapshot.wasted_regen_kw,
            places=6,
        )
        self.assertAlmostEqual(
            regen_train.regen_power_available_kw,
            regen_train.regen_power_accepted_kw + regen_train.regen_power_wasted_kw,
            places=6,
        )

    def test_multitrain_result_is_independent_of_input_order(self) -> None:
        loads = [
            TrainElectricalLoad("T1", "UP", 2200.0, 18.0, aux_power_kw=100.0, traction_power_request_kw=900.0),
            TrainElectricalLoad("T2", "UP", 2600.0, 18.0, aux_power_kw=120.0, regen_power_available_kw=800.0),
            TrainElectricalLoad("T3", "UP", 3000.0, 16.0, aux_power_kw=100.0, traction_power_request_kw=700.0),
        ]
        first = DCTractionPowerFlowSolver(_network()).solve(loads, dt_sec=1.0)
        second = DCTractionPowerFlowSolver(_network()).solve(list(reversed(loads)), dt_sec=1.0)
        first_trains = sorted(
            (item.train_id, round(item.voltage_v, 6), round(item.regen_power_accepted_kw, 6))
            for item in first.trains
        )
        second_trains = sorted(
            (item.train_id, round(item.voltage_v, 6), round(item.regen_power_accepted_kw, 6))
            for item in second.trains
        )
        self.assertEqual(first_trains, second_trains)
        self.assertAlmostEqual(first.wasted_regen_kw, second.wasted_regen_kw, places=9)

    def test_tie_switch_changes_regen_connectivity(self) -> None:
        network = _network()
        loads = [
            TrainElectricalLoad("TRACTION", "UP", 700.0, 18.0, traction_force_n=95_000.0, aux_power_kw=60.0),
            TrainElectricalLoad("REGEN", "UP", 4_000.0, 18.0, brake_force_n=80_000.0, aux_power_kw=60.0),
        ]
        isolated = DCTractionPowerFlowSolver(network).solve(loads, dt_sec=1.0)
        network.operate_switch("SW-TIE-0903", "CLOSED")
        connected = DCTractionPowerFlowSolver(network).solve(loads, dt_sec=1.0)

        self.assertEqual(isolated.absorbed_regen_kw, 0.0)
        self.assertGreater(connected.absorbed_regen_kw, 0.0)
        self.assertTrue(any(item.sink_type == "TRAIN" for item in connected.regen_paths))

    def test_reachable_feedback_is_assigned_to_device_and_signed_path(self) -> None:
        network = _network()
        solver = DCTractionPowerFlowSolver(network)

        snapshot = solver.solve(
            [TrainElectricalLoad("REGEN", "UP", 700.0, 18.0, brake_force_n=80_000.0, aux_power_kw=60.0)],
            dt_sec=1.0,
        )

        feedback = next(item for item in snapshot.regen_paths if item.sink_type == "SUBSTATION_FEEDBACK")
        self.assertEqual(feedback.sink_id, "TS-0901")
        self.assertAlmostEqual(feedback.generated_kw, feedback.delivered_kw + feedback.losses_kw, places=9)
        substation = next(item for item in snapshot.substations if item.substation_id == "TS-0901")
        feeder = next(item for item in snapshot.feeders if item.feeder_id == feedback.source_feeder_id)
        self.assertLess(substation.current_a, 0.0)
        self.assertLess(substation.power_kw, 0.0)
        self.assertGreater(substation.feedback_power_kw, 0.0)
        self.assertLess(feeder.current_a, 0.0)
        self.assertLess(feeder.power_kw, 0.0)

    def test_regen_without_absorber_is_limited_and_wasted(self) -> None:
        network = _network()
        solver = DCTractionPowerFlowSolver(network)

        snapshot = solver.solve(
            [
                TrainElectricalLoad("T1", "UP", 6_900.0, 22.0, brake_force_n=180_000.0, aux_power_kw=20.0),
                TrainElectricalLoad("T2", "UP", 7_300.0, 22.0, brake_force_n=180_000.0, aux_power_kw=20.0),
            ],
            dt_sec=1.0,
        )

        self.assertGreater(snapshot.wasted_regen_kw, 0.0)
        self.assertTrue(any(train.regen_limit_ratio < 1.0 for train in snapshot.trains))
        self.assertTrue(any(item["type"] in {"REGEN_LIMITED", "OVERVOLTAGE", "REGEN_WASTED"} for item in snapshot.alerts))

    def test_n_minus_one_outage_generates_outage_alert(self) -> None:
        network = _network()
        network.apply_substation_outage("TS-0905")
        solver = DCTractionPowerFlowSolver(network)

        snapshot = solver.solve(
            [
                TrainElectricalLoad(f"T{i}", "UP", 6_100.0 + i * 180.0, 20.0, traction_force_n=140_000.0, aux_power_kw=120.0)
                for i in range(3)
            ],
            dt_sec=1.0,
        )

        self.assertTrue(any(item["type"] == "SUBSTATION_OUTAGE" and item["targetId"] == "TS-0905" for item in snapshot.alerts))

    def test_deenergized_contact_section_isolates_positive_train_load(self) -> None:
        network = _network()
        network.set_contact_section_status("CR-09-02-UP", "DEENERGIZED")
        solver = DCTractionPowerFlowSolver(network)

        snapshot = solver.solve(
            [TrainElectricalLoad("T1", "UP", 2_400.0, 18.0, traction_force_n=95_000.0, aux_power_kw=60.0)],
            dt_sec=1.0,
        )

        train = snapshot.trains[0]
        self.assertEqual(train.voltage_v, 0.0)
        self.assertEqual(train.traction_limit_ratio, 0.0)
        self.assertTrue(any(item["type"] == "POWER_SECTION_ISOLATED" for item in snapshot.alerts))
        section = next(item for item in snapshot.contact_rail_flows if item.section_id == "CR-09-02-UP")
        self.assertEqual(section.status, "DEENERGIZED")

    def test_contact_rail_flow_has_signed_current_and_dimensioned_power(self) -> None:
        network = _network()
        snapshot = DCTractionPowerFlowSolver(network).solve(
            [TrainElectricalLoad("T1", "UP", 1_800.0, 18.0, traction_force_n=95_000.0, aux_power_kw=60.0)],
            dt_sec=1.0,
        )

        section = next(item for item in snapshot.contact_rail_flows if item.section_id == "CR-09-02-UP")
        self.assertNotEqual(section.current_a, 0.0)
        self.assertAlmostEqual(section.power_kw, 750.0 * section.current_a / 1000.0, places=9)
        self.assertGreater(section.load_ratio, 0.0)


if __name__ == "__main__":
    unittest.main()
