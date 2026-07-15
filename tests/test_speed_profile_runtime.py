from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
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

    def test_shared_capacity_demand_matches_vehicle_drive_model(self) -> None:
        from app.domain.control.speed_profile import (
            _candidate_commands,
            _demand_from_capacities,
        )
        from app.domain.vehicle.services import TractionDriveModel

        config = VehicleConfig(train_id="T1")
        drive = TractionDriveModel(config)
        for speed_mps in (0.0, 8.0, 20.0):
            traction_capacity_n = drive.traction_capacity_n(speed_mps)
            electric_brake_capacity_n = drive.electric_brake_capacity_n(speed_mps)
            for _, command in _candidate_commands("T1", speed_mps, config):
                expected = drive.demand(command, speed_mps)
                actual = _demand_from_capacities(
                    command,
                    config,
                    traction_capacity_n,
                    electric_brake_capacity_n,
                )
                self.assertEqual(actual, expected)

    def test_three_train_first_tick_is_non_blocking_and_deduplicated(self) -> None:
        engine = load_engine()
        try:
            # This test exercises first-run scheduling. Keep it independent of
            # profiles persisted by earlier tests or local simulation sessions.
            with tempfile.TemporaryDirectory() as cache_dir:
                engine.speed_profile_service.cache_dir = Path(cache_dir)
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
                for train in engine.trains:
                    self.assertEqual(engine.export_speed_profile(train.train_id), [])
                    self.assertEqual(
                        engine.export_speed_profile_meta(train.train_id)["source"],
                        "DCDP_PENDING",
                    )
        finally:
            engine.speed_profile_service.shutdown()

    def test_manual_train_primes_first_profile_when_added(self) -> None:
        engine = load_engine()
        try:
            with patch.object(engine, "_prime_path_profile", return_value=False) as prime:
                result = engine.add_train({
                    "trainId": "T-EAGER",
                    "initialStationCode": "GGZ",
                    "direction": "UP",
                    "initialLoadPax": 100,
                })

            self.assertTrue(result["ok"])
            prime.assert_called_once()
            self.assertEqual(engine.clock.current_tick, 0)
        finally:
            engine.speed_profile_service.shutdown()

    def test_runtime_profile_key_uses_reference_load_not_live_passenger_count(self) -> None:
        engine = load_engine()
        try:
            with patch.object(engine, "_prime_path_profile", return_value=False):
                result = engine.add_train({
                    "trainId": "T-REFERENCE",
                    "initialStationCode": "GGZ",
                    "direction": "UP",
                    "initialLoadPax": 50,
                })
            self.assertTrue(result["ok"])
            train = engine.trains[-1]
            assert train._path_plan is not None
            first = engine._build_runtime_profile_request(
                train,
                train._path_plan,
                train.permitted_speed_mps,
            )
            train.onboard_pax = 1_200
            second = engine._build_runtime_profile_request(
                train,
                train._path_plan,
                train.permitted_speed_mps,
            )

            self.assertEqual(first.cache_key, second.cache_key)
            expected_mass = engine._make_vehicle_config(
                train.train_id,
                engine.scenario.operation_plan.profile_reference_load_pax,
            ).mass_kg
            self.assertEqual(first.vehicle_config.mass_kg, expected_mass)
        finally:
            engine.speed_profile_service.shutdown()

    def test_installed_profile_prewarm_queues_following_interval(self) -> None:
        engine = load_engine()
        try:
            with patch.object(engine, "_prime_path_profile", return_value=False):
                result = engine.add_train({
                    "trainId": "T-LOOKAHEAD",
                    "initialStationCode": "GGZ",
                    "direction": "UP",
                    "initialLoadPax": 100,
                })
            self.assertTrue(result["ok"])
            train = engine.trains[-1]
            assert train._path_plan is not None

            with patch.object(engine.speed_profile_service, "request", return_value=None) as request:
                cache_key = engine._prewarm_following_interval_profile(
                    train,
                    train._path_plan,
                )

            self.assertIsNotNone(cache_key)
            request.assert_called_once()
            queued = request.call_args.args[0]
            self.assertEqual(queued.cache_key, cache_key)
            self.assertEqual(
                queued.path_plan.origin_platform_id,
                train._path_plan.destination_platform_id,
            )
            self.assertNotEqual(queued.path_plan.cache_key(), train._path_plan.cache_key())
        finally:
            engine.speed_profile_service.shutdown()


if __name__ == "__main__":
    unittest.main()
