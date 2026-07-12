from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.domain.dispatch.services import DispatchContext, RuleBasedDispatchService
from app.domain.power.services import PowerSection, PowerService, TrainPowerRequest
from app.domain.station.services import (
    DwellTimeConfig,
    PassengerDemandProfile,
    PassengerFlowGenerator,
    StationService,
    TrainLoadState,
)
from app.infra.recorder import RunRecorder


class MemberDPhase2Tests(unittest.TestCase):
    def test_passenger_flow_boarding_load_and_dwell(self) -> None:
        station_service = StationService(
            PassengerFlowGenerator(
                [
                    PassengerDemandProfile(
                        station_id="S-GGZ",
                        direction="UP",
                        start_sec=0,
                        end_sec=1800,
                        arrival_rate_pax_per_min=120.0,
                        alighting_ratio=0.10,
                    )
                ]
            ),
            DwellTimeConfig(base_dwell_sec=30.0, door_capacity_pax_per_sec=4.0),
        )

        arrivals = station_service.update_arrivals(sim_time_ms=0, dt_sec=60.0)
        self.assertEqual(arrivals[("S-GGZ", "UP")], 120)

        load = TrainLoadState(train_id="T0901", onboard_pax=500, capacity_pax=600)
        boarding, dwell = station_service.process_train_stop(
            sim_time_ms=60_000,
            station_id="S-GGZ",
            direction="UP",
            train_load=load,
        )

        self.assertEqual(boarding.alighting, 50)
        self.assertEqual(boarding.boarding, 120)
        self.assertEqual(boarding.waiting, 0)
        self.assertEqual(boarding.updated_load.onboard_pax, 570)
        self.assertGreater(boarding.updated_load.load_factor, load.load_factor)
        self.assertGreater(dwell.estimated_dwell_sec, dwell.planned_dwell_sec)
        self.assertTrue(dwell.can_depart)

    def test_power_service_limits_traction_and_absorbs_regen(self) -> None:
        power = PowerService(
            [
                PowerSection(
                    power_section_id="PWR-0901",
                    name="test section",
                    max_traction_power_kw=1000.0,
                    warning_power_kw=800.0,
                    regen_absorb_limit_kw=200.0,
                )
            ]
        )

        states = power.update(
            [
                TrainPowerRequest("T1", "PWR-0901", speed_mps=10.0, traction_force_n=70_000.0),
                TrainPowerRequest("T2", "PWR-0901", speed_mps=10.0, traction_force_n=70_000.0),
                TrainPowerRequest("T3", "PWR-0901", speed_mps=10.0, brake_force_n=20_000.0),
            ],
            dt_sec=1.0,
        )

        state = states["PWR-0901"]
        self.assertEqual(state.source, "SELF_SIM")
        self.assertEqual(state.voltage_level, "UNDERVOLTAGE")
        self.assertLess(state.traction_limit_ratio, 1.0)
        self.assertGreater(state.absorbed_regen_kw, 0.0)
        self.assertGreater(state.energy_kwh, 0.0)
        self.assertGreater(state.regen_energy_kwh, 0.0)

    def test_rule_based_dispatch_priority(self) -> None:
        dispatch = RuleBasedDispatchService()

        power_decision = dispatch.decide(
            DispatchContext(
                sim_time_ms=1000,
                train_id="T0901",
                station_id="S-GGZ",
                power_traction_limit_ratio=0.7,
                rear_headway_sec=30.0,
            )
        )
        self.assertEqual(power_decision.action, "STAGGER_DEPARTURE")
        self.assertEqual(power_decision.reason, "POWER_LIMITED")

        hold_decision = dispatch.decide(
            DispatchContext(
                sim_time_ms=2000,
                train_id="T0902",
                station_id="S-GGZ",
                rear_headway_sec=45.0,
            )
        )
        self.assertEqual(hold_decision.action, "HOLD")
        self.assertEqual(hold_decision.reason, "HEADWAY_TOO_SHORT")

        release_decision = dispatch.decide(
            DispatchContext(
                sim_time_ms=3000,
                train_id="T0903",
                station_id="S-GGZ",
                front_headway_sec=360.0,
                platform_crowding_level="HIGH",
            )
        )
        self.assertEqual(release_decision.action, "RELEASE")

    def test_terminal_turnback_dispatch_priority(self) -> None:
        decision = RuleBasedDispatchService().decide(DispatchContext(
            sim_time_ms=4000,
            train_id="T0904",
            station_id="GGZ",
            terminal_turnback=True,
            turnback_direction="UP",
            power_traction_limit_ratio=0.5,
            rear_headway_sec=10.0,
        ))
        self.assertEqual(decision.action, "TURNBACK")
        self.assertEqual(decision.reason, "TERMINAL_REVERSAL_UP")
    def test_recorder_member_d_tables_and_power_source_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = RunRecorder(Path(tmp) / "run.sqlite")
            try:
                run_id = recorder.start_run("member-d", {"phase": 2})
                recorder.record_station_passenger(
                    run_id,
                    sim_time_ms=1000,
                    station_id="S-GGZ",
                    direction="UP",
                    arrivals=20,
                    boarding=10,
                    alighting=3,
                    waiting=7,
                    left_behind=0,
                    platform_density_pax_per_m2=0.2,
                    crowding_level="LOW",
                )
                recorder.record_train_load(
                    run_id,
                    sim_time_ms=1000,
                    train_id="T0901",
                    onboard_pax=120,
                    capacity_pax=600,
                    load_factor=0.2,
                    vehicle_load_kg=7800.0,
                )
                recorder.record_dwell(
                    run_id,
                    train_id="T0901",
                    station_id="S-GGZ",
                    arrival_ms=1000,
                    depart_ms=40_000,
                    planned_dwell_sec=30.0,
                    estimated_dwell_sec=39.0,
                    actual_dwell_sec=39.0,
                    reason="PASSENGER_BOARDING",
                )
                recorder.record_dispatch_decision(
                    run_id,
                    decision_id="DD-0001",
                    sim_time_ms=1000,
                    train_id="T0901",
                    station_id="S-GGZ",
                    action="HOLD",
                    duration_sec=20.0,
                    reason="HEADWAY_TOO_SHORT",
                )
                recorder.record_power(
                    run_id,
                    sim_time_ms=1000,
                    power_section_id="PWR-0901",
                    requested_power_kw=900.0,
                    available_power_kw=1000.0,
                    traction_limit_ratio=1.0,
                    voltage_level="NORMAL",
                    energy_kwh=0.25,
                    regen_energy_kwh=0.03,
                    source="SELF_SIM",
                )

                counts = {}
                for table in [
                    "station_passenger_records",
                    "train_load_records",
                    "dwell_records",
                    "dispatch_decisions",
                    "power_records",
                ]:
                    counts[table] = recorder.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                self.assertEqual(set(counts.values()), {1})

                with self.assertRaises(ValueError):
                    recorder.record_power(
                        run_id,
                        sim_time_ms=2000,
                        power_section_id="PWR-0901",
                        requested_power_kw=1.0,
                        available_power_kw=1.0,
                        traction_limit_ratio=1.0,
                        voltage_level="NORMAL",
                        energy_kwh=0.0,
                        regen_energy_kwh=0.0,
                        source="PLATFORM",
                    )
            finally:
                recorder.close()


if __name__ == "__main__":
    unittest.main()

