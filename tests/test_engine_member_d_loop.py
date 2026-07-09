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
