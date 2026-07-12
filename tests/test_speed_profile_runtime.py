from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from app.core.engine import SimulationEngine
from app.domain.control.models import AtoConfig, AtoTarget
from app.domain.control.services import ATOController
from app.domain.line.services import PathPlan, PathSegmentConstraint
from app.domain.vehicle.models import TrainState, VehicleConfig


ROOT = Path(__file__).resolve().parents[1]


def load_engine() -> SimulationEngine:
    engine = SimulationEngine.load_from_files(
        scenario_path=ROOT / "data" / "scenarios" / "line9_interactive.json",
        line_map_path=ROOT / "data" / "cache" / "line_map.json",
        stations_csv_path=ROOT / "data" / "line9" / "stations.csv",
    )
    engine.load()
    return engine


class SpeedProfileRuntimeTests(unittest.TestCase):
    def test_realtime_controller_falls_back_without_synchronous_dcdp(self) -> None:
        controller = ATOController(
            AtoConfig(use_dynamic_programming_profile=True),
            enable_synchronous_profile_optimization=False,
        )
        state = TrainState("T1", position_m=0.0, speed_mps=0.0)
        target = AtoTarget(target_position_m=1000.0, permitted_speed_mps=20.0)

        with patch(
            "app.domain.control.services.optimize_speed_profile_dcdp",
            side_effect=AssertionError("synchronous optimizer must not run"),
        ):
            speed = controller.target_speed_mps(state, target)

        self.assertGreater(speed, 0.0)
        self.assertEqual(controller.last_profile_mode, "BRAKING_CURVE")

    def test_vehicle_identity_does_not_change_shared_request_key(self) -> None:
        from app.domain.control.profile_runtime import build_speed_profile_request

        path = PathPlan(
            origin_platform_id=1,
            destination_platform_id=2,
            direction="forward",
            segment_ids=(1,),
            constraints=(PathSegmentConstraint(1, 0, 1000, 0, 1000, 20, 0),),
            total_length_m=1000,
            start_segment_id=1,
            start_offset_m=0,
            end_segment_id=1,
            end_offset_m=1000,
        )
        config = AtoConfig()
        first = build_speed_profile_request(path, 20.0, config, VehicleConfig(train_id="T1"))
        second = build_speed_profile_request(
            path,
            20.0,
            config,
            replace(VehicleConfig(train_id="T1"), train_id="T2"),
        )
        self.assertEqual(first.cache_key, second.cache_key)

    def test_three_train_first_tick_is_non_blocking_and_deduplicated(self) -> None:
        engine = load_engine()
        try:
            for train_id, direction, station in (
                ("T1", "UP", "GGZ"),
                ("T2", "DOWN", "GTG"),
                ("T3", "UP", "GGZ"),
            ):
                result = engine.add_train({
                    "trainId": train_id,
                    "initialStationCode": station,
                    "direction": direction,
                    "initialLoadPax": 100,
                })
                self.assertTrue(result["ok"])
            engine.clock.start()
            started = time.perf_counter()
            engine._tick()
            elapsed = time.perf_counter() - started

            self.assertLess(elapsed, 0.25)
            runtime = engine.speed_profile_service.snapshot()
            self.assertEqual(runtime["pendingProfileCount"], 2)
            self.assertTrue(runtime["workerAlive"])
            self.assertTrue(all(train.phase == "DWELLING" for train in engine.trains))
            self.assertTrue(all(not train._profile_triggered for train in engine.trains))
        finally:
            engine.speed_profile_service.shutdown()


if __name__ == "__main__":
    unittest.main()
