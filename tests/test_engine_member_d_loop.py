from __future__ import annotations

import unittest

from app.core.engine import SimulationEngine


class EngineMemberDLoopTests(unittest.TestCase):
    def _engine(self) -> SimulationEngine:
        engine = SimulationEngine.load_from_files(
            "data/scenarios/line9_single.json",
            "data/cache/line_map.json",
            "MetroDynamicsJavaDemo/data/stations.csv",
        )
        engine.load()
        return engine

    def test_snapshot_uses_scenario_start_time(self) -> None:
        engine = self._engine()
        snapshot = engine.snapshot()

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.sim_time_str, "08:00:00")
        self.assertEqual(snapshot.sim_time_ms, 28_800_000)

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
        self.assertGreater(len(profile), 0)
        self.assertEqual(profile_meta["source"], "DCDP_STRICT")
        self.assertEqual(profile[-1]["speedMps"], 0.0)
        self.assertAlmostEqual(profile[-1]["positionM"], train["pathTotalLengthM"], delta=1.0)
        self.assertIn("localSpeedLimitMps", profile[0])
        self.assertIn("gradeRatio", profile[0])
        self.assertIn("segmentId", profile[0])

    def test_train_continues_to_next_station_path_plan_after_arrival(self) -> None:
        engine = self._engine()
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
        self.assertGreater(len(engine.export_speed_profile(train.train_id)), 0)
        self.assertEqual(engine.export_speed_profile_meta(train.train_id)["source"], "DCDP_STRICT")

    def test_station_stop_uses_station_code_for_passenger_flow(self) -> None:
        engine = self._engine()
        train = engine.trains[0]
        engine.station_service.ensure_platform("GGZ", "UP").waiting_pax = 80

        engine._process_station_stop(train, 28_800_000)

        self.assertEqual(train.current_station_code, "GGZ")
        self.assertGreater(train.onboard_pax, 0)
        self.assertGreater(train.dwell_remaining_sec, 30.0)


if __name__ == "__main__":
    unittest.main()
