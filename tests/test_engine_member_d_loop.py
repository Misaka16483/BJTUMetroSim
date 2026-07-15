from __future__ import annotations

from dataclasses import replace
import unittest
from unittest.mock import patch

from app.core.engine import SimulationEngine


class EngineMemberDLoopTests(unittest.TestCase):
    def _engine(self) -> SimulationEngine:
        engine = SimulationEngine.load_from_files(
            "data/scenarios/line9_single.json",
            "data/cache/line_map.json",
            "data/line9/stations.csv",
        )
        engine.load()
        result = engine.add_train({
            "trainId": "T0901",
            "initialStationCode": "GGZ",
            "direction": "UP",
        })
        self.assertTrue(result["ok"])
        return engine

    def test_snapshot_uses_scenario_start_time(self) -> None:
        engine = self._engine()
        snapshot = engine.snapshot()

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.sim_time_str, "06:00:00")
        self.assertEqual(snapshot.sim_time_ms, 21_600_000)

    def test_member_d_state_enters_tick_snapshot(self) -> None:
        engine = self._engine()
        engine.clock.start()
        for _ in range(8):
            engine._tick()
        snapshot = engine.snapshot()

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertGreater(snapshot.kpi["totalWaitingPax"], 0)
        self.assertGreaterEqual(snapshot.kpi["maxPlatformDensity"], 0)
        self.assertGreater(len(snapshot.power), 0)
        self.assertIn("tractionLimitRatio", snapshot.power[0])
        self.assertIn("waitingPax", snapshot.stations[0])
        self.assertIn("lastDispatchAction", snapshot.kpi)
        train = snapshot.trains[0]
        self.assertIn("tractionPowerRequestKw", train)
        self.assertIn("regenPowerAvailableKw", train)
        self.assertIn("regenAcceptedKwh", train)
        self.assertIn("regenWastedKwh", train)
        self.assertIn("selfConsumedKw", snapshot.power_network["regen"])
        self.assertIn("tractionPowerDeliveredKw", snapshot.power_network["trainVoltages"][0])

    def test_global_passenger_service_has_distinct_bidirectional_platforms(self) -> None:
        engine = self._engine()
        engine.clock.start()
        engine._tick()
        snapshot = engine.snapshot()

        assert snapshot is not None
        ggz = [item for item in snapshot.stations if item["code"] == "GGZ"]
        self.assertEqual({item["direction"] for item in ggz}, {"UP", "DOWN"})
        absolute_time_ms = engine._absolute_sim_time_ms()
        generator = engine.station_service.flow_generator
        self.assertGreater(generator.arrival_rate_pax_per_min("GGZ", "UP", absolute_time_ms), 0)
        self.assertEqual(generator.arrival_rate_pax_per_min("GGZ", "DOWN", absolute_time_ms), 0)
        self.assertEqual(generator.arrival_rate_pax_per_min("GTG", "UP", absolute_time_ms), 0)

    def test_speed_multiplier_is_snapshot_state_and_validated(self) -> None:
        engine = self._engine()
        self.assertEqual(engine.set_speed_multiplier(20), 20)
        snapshot = engine.snapshot()
        assert snapshot is not None
        self.assertEqual(snapshot.speed_multiplier, 20)
        with self.assertRaisesRegex(ValueError, "SPEED_MULTIPLIER_OUT_OF_RANGE"):
            engine.set_speed_multiplier(241)

    def test_fast_forward_keeps_dcdp_profile_computation_enabled(self) -> None:
        engine = self._engine()
        engine.set_speed_multiplier(60)
        controller = engine._ato_for_train("T0901")
        self.assertTrue(controller.allow_profile_compute)
        engine.set_speed_multiplier(10)
        self.assertTrue(engine._ato_for_train("T0901").allow_profile_compute)
        engine.set_speed_multiplier(1)
        self.assertTrue(engine._ato_for_train("T0901").allow_profile_compute)

    def test_runtime_scenario_cannot_disable_dcdp(self) -> None:
        template = self._engine()
        engine = SimulationEngine(
            replace(template.scenario, use_dynamic_programming_profile=False),
            template.line_map,
            template.station_catalog,
            line_scope=template.line_scope,
        )
        self.assertFalse(engine.scenario.use_dynamic_programming_profile)
        self.assertTrue(engine._ato_config.use_dynamic_programming_profile)

    def test_fast_forward_holds_departure_until_dcdp_profile_is_ready(self) -> None:
        engine = self._engine()
        engine.set_speed_multiplier(10)
        train = engine.trains[0]
        train.phase = "DWELLING"
        train._passenger_service_pending = False
        train.dwell_remaining_sec = 0.0
        train._profile_triggered = False
        train.door_system.transition_remaining_sec = 0.0
        for car in train.door_system.cars:
            for door in car.doors:
                door.status = type(door.status).CLOSED_LOCKED

        with patch.object(engine, "_prime_path_profile", return_value=False):
            handled, prepared = engine._prepare_train_step(
                train,
                engine._absolute_sim_time_ms(),
            )

        self.assertTrue(handled)
        self.assertIsNone(prepared)
        self.assertEqual(train.phase, "DWELLING")
        self.assertEqual(train.speed_mps, 0.0)
        self.assertFalse(train._profile_triggered)
        self.assertEqual(train.door_notice, "WAITING_SPEED_PROFILE")
        self.assertEqual(train.last_dispatch_action, "HOLD")
        self.assertEqual(train.last_dispatch_reason, "DCDP_PROFILE_PENDING")

    def test_60x_samples_power_once_per_simulated_second(self) -> None:
        engine = self._engine()
        engine.set_speed_multiplier(60)
        self.assertTrue(engine._should_solve_power(21_600_250))
        engine._last_power_solve_sim_time_ms = 21_600_250
        self.assertFalse(engine._should_solve_power(21_601_000))
        self.assertTrue(engine._should_solve_power(21_601_250))
        engine.set_speed_multiplier(10)
        self.assertTrue(engine._should_solve_power(21_601_500))

    def test_approaching_phase_is_latched_until_station_arrival(self) -> None:
        engine = self._engine()
        train = engine.trains[0]
        train.phase = "DEPARTING"
        train.speed_mps = 8.0
        train.distance_to_next_m = 120.0
        train.local_speed_limit_mps = 22.0
        engine._update_running_phase(train, brake_percent=30.0, braking_profile=False)
        self.assertEqual(train.phase, "APPROACHING")

        # ATO may coast or give a brief traction correction while braking; this
        # must not relabel the same station approach as a fresh departure.
        engine._update_running_phase(train, brake_percent=0.0, braking_profile=False)
        self.assertEqual(train.phase, "APPROACHING")

        train.phase = "DEPARTING"
        train.distance_to_next_m = 1_000.0
        engine._update_running_phase(train, brake_percent=30.0, braking_profile=False)
        self.assertEqual(train.phase, "DEPARTING")

    def test_initial_station_uses_simulated_door_sequence_and_history(self) -> None:
        engine = self._engine()
        train = engine.trains[0]
        self.assertEqual(train.door_state, "CLOSED")
        self.assertEqual(train.door_notice, "PREPARE_OPEN")
        self.assertEqual(train.door_side, "LEFT")

        engine.clock.start()
        for _ in range(6):
            engine._tick()

        self.assertEqual(train.door_state, "OPEN")
        self.assertEqual(train.door_notice, "OPEN")
        self.assertLess(train.dwell_remaining_sec, 30.0)
        history = engine.station_passenger_history("GGZ")
        self.assertGreaterEqual(len(history["history"]["UP"]), 2)

    def test_engine_exports_path_plan_context_for_interval(self) -> None:
        engine = self._engine()
        engine.clock.start()
        engine._tick()
        snapshot = engine.snapshot()

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        train = snapshot.trains[0]
        self.assertGreater(train["pathTotalLengthM"], 0)
        self.assertGreater(train["pathSegmentCount"], 0)
        self.assertGreater(train["pathConstraintCount"], 0)
        self.assertIsNotNone(train["currentSegmentId"])
        self.assertGreater(train["localSpeedLimitMps"], 0)

        profile = engine.export_speed_profile("T0901")
        profile_meta = engine.export_speed_profile_meta("T0901")
        self.assertIn(profile_meta["source"], {"DCDP_PENDING", "DCDP_STRICT"})
        if profile_meta["source"] == "DCDP_PENDING":
            self.assertEqual(profile_meta["status"], "DCDP_PENDING")
            self.assertEqual(profile, [])
        else:
            self.assertGreater(len(profile), 0)
            self.assertEqual(profile[-1]["speedMps"], 0.0)
            self.assertAlmostEqual(profile[-1]["positionM"], train["pathTotalLengthM"], delta=1.0)
            self.assertIn("localSpeedLimitMps", profile[0])
            self.assertIn("gradeRatio", profile[0])
            self.assertIn("segmentId", profile[0])

    def test_train_continues_to_next_station_path_plan_after_arrival(self) -> None:
        engine = self._engine()
        engine._ato_config = replace(
            engine._ato_config,
            use_dynamic_programming_profile=False,
        )
        engine.clock.tick_seconds = 1.0
        engine.clock.start()

        for _ in range(180):
            engine._tick()
            if engine.trains[0].station_index == 1:
                break

        train = engine.trains[0]
        self.assertEqual(train.station_index, 1)
        self.assertEqual(train.current_station_code, "FSP")
        self.assertEqual(train.next_station_code, "KYL")
        self.assertGreater(train.target_distance_m, 0)

        engine._tick()
        train = engine.trains[0]
        self.assertGreater(train.path_total_length_m, 0)
        self.assertGreater(train.path_segment_count, 0)
        self.assertEqual(train._path_origin_station_index, 1)
        self.assertEqual(train._path_destination_station_index, 2)

    def test_station_stop_uses_station_code_for_passenger_flow(self) -> None:
        engine = self._engine()
        train = engine.trains[0]
        engine.station_service.ensure_platform("GGZ", "UP").waiting_pax = 80

        engine._process_station_stop(train, 28_800_000)

        self.assertEqual(train.current_station_code, "GGZ")
        self.assertEqual(train.last_boarding, 0)
        self.assertEqual(train.dwell_remaining_sec, 30.0)
        self.assertEqual(train.door_state, "OPEN")
        self.assertEqual(train.door_side, "LEFT")
        for step in range(10):
            engine._advance_open_door_passengers(train, 28_800_250 + step * 250, 0.25)
            train.dwell_remaining_sec = max(0.0, train.dwell_remaining_sec - 0.25)
        self.assertGreater(train.onboard_pax, 0)
        self.assertGreater(train.last_boarding, 0)

    def test_open_door_exchange_is_continuous_and_fills_available_capacity(self) -> None:
        engine = self._engine()
        train = engine.trains[0]
        engine.station_service.ensure_platform("GGZ", "UP").waiting_pax = 200
        engine._process_station_stop(train, 28_800_000)
        for step in range(120):
            engine._advance_open_door_passengers(train, 28_800_250 + step * 250, 0.25)
            train.dwell_remaining_sec = max(0.0, train.dwell_remaining_sec - 0.25)

        self.assertEqual(train.last_boarding, 200)
        self.assertEqual(train.onboard_pax, 200)
        self.assertEqual(engine.station_service.ensure_platform("GGZ", "UP").waiting_pax, 0)

    def test_open_door_exchange_smoothly_distributes_alighting(self) -> None:
        engine = self._engine()
        train = engine.trains[0]
        train.current_station_code = "FSP"
        train.onboard_pax = 200
        engine.station_service.ensure_platform("FSP", "UP").waiting_pax = 500
        engine._process_station_stop(train, 28_800_000)
        rates: list[tuple[float, float]] = []
        for step in range(120):
            engine._advance_open_door_passengers(train, 28_800_250 + step * 250, 0.25)
            rates.append((train.current_boarding_rate_pax_per_sec, train.current_alighting_rate_pax_per_sec))
            train.dwell_remaining_sec = max(0.0, train.dwell_remaining_sec - 0.25)

        self.assertEqual(train.last_alighting, 8)  # FSP UP calibrated synthetic ratio: 4%.
        self.assertGreater(train.last_boarding, 90)
        self.assertGreater(max(rate for rate, _ in rates), rates[0][0])
        self.assertGreater(max(rate for _, rate in rates), 0.0)


if __name__ == "__main__":
    unittest.main()
