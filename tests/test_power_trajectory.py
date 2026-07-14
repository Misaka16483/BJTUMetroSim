from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from app.domain.power.joint_optimization import (
    BASELINE_CANDIDATE,
    JointExperimentConfig,
    JointPowerEvaluator,
)
from app.domain.power.trajectory import (
    EngineRunTrajectoryProvider,
    EngineSnapshotTrajectoryAdapter,
    InMemoryTrajectoryProvider,
    JsonlTrajectoryProvider,
    JsonlTrajectoryRecorder,
    TrainTrajectorySample,
    TrajectoryContractError,
    TrajectoryFrame,
    validate_trajectory_frames,
)


ROOT = Path(__file__).resolve().parents[1]
TOPOLOGY = ROOT / "data" / "scenarios" / "line9_power_topology.json"
TRACKING_METRICS = {
    "speedTrackingRmseMps": 0.0,
    "meanDepartureDeviationSec": 0.0,
    "maxDepartureDeviationSec": 0.0,
    "runtimeDeviationSec": 0.0,
    "stopPositionErrorM": 0.0,
    "maximumSpeedMps": 10.0,
}


def _sample(
    sim_time_ms: int,
    train_id: str,
    mileage_m: float,
    *,
    speed_mps: float = 10.0,
    traction_force_n: float = 5_000.0,
    electric_brake_force_n: float = 0.0,
) -> TrainTrajectorySample:
    return TrainTrajectorySample(
        sim_time_ms=sim_time_ms,
        train_id=train_id,
        direction="UP",
        mileage_m=mileage_m,
        speed_mps=speed_mps,
        acceleration_mps2=0.0,
        mass_kg=225_000.0,
        traction_force_n=traction_force_n,
        electric_brake_force_n=electric_brake_force_n,
        auxiliary_power_kw=150.0,
        traction_power_request_kw=60.0,
        regen_power_available_kw=0.0,
        permitted_speed_mps=20.0,
        resistance_force_n=traction_force_n - electric_brake_force_n,
        phase="CRUISING",
        source="TEST",
    )


def _frames(start_time_ms: int = 100_000) -> tuple[TrajectoryFrame, ...]:
    frames = []
    for offset_ms in range(0, 20_001, 5_000):
        distance_m = offset_ms / 1000.0 * 10.0
        sim_time_ms = start_time_ms + offset_ms
        frames.append(TrajectoryFrame(
            sim_time_ms,
            (
                _sample(sim_time_ms, "TRACE-001", 1_000.0 + distance_m),
                _sample(sim_time_ms, "TRACE-002", 2_000.0 + distance_m),
            ),
            source="TEST",
        ))
    return tuple(frames)


class PowerTrajectoryContractTests(unittest.TestCase):
    def test_valid_trace_passes_contract_validation(self) -> None:
        report = validate_trajectory_frames(_frames(), allow_roster_changes=False)

        self.assertTrue(report.passed, report.issues)
        self.assertEqual(report.frame_count, 5)
        self.assertEqual(report.sample_count, 10)

    def test_validation_reports_time_force_and_position_failures(self) -> None:
        first = TrajectoryFrame(1_000, (_sample(1_000, "T1", 1_000.0),))
        broken = TrajectoryFrame(1_000, (
            _sample(
                1_000,
                "T1",
                5_000.0,
                traction_force_n=5_000.0,
                electric_brake_force_n=1_000.0,
            ),
        ))
        later = TrajectoryFrame(2_000, (_sample(2_000, "T1", 9_000.0),))

        report = validate_trajectory_frames((first, broken, later))
        codes = {issue.code for issue in report.issues}

        self.assertIn("NON_MONOTONIC_TIME", codes)
        self.assertIn("TRACTION_BRAKE_OVERLAP", codes)
        self.assertIn("POSITION_DISCONTINUITY", codes)

    def test_static_holding_brake_does_not_require_unmodelled_rail_reaction(self) -> None:
        sample = _sample(
            1_000,
            "T1",
            1_000.0,
            speed_mps=0.0,
            traction_force_n=0.0,
            electric_brake_force_n=60_000.0,
        )

        report = validate_trajectory_frames((TrajectoryFrame(1_000, (sample,)),))

        self.assertNotIn("DYNAMICS_NOT_CLOSED", {item.code for item in report.issues})

    def test_moving_force_imbalance_remains_invalid(self) -> None:
        sample = replace(_sample(
            1_000,
            "T1",
            1_000.0,
            speed_mps=5.0,
            traction_force_n=0.0,
            electric_brake_force_n=60_000.0,
        ), resistance_force_n=0.0)

        report = validate_trajectory_frames((TrajectoryFrame(1_000, (sample,)),))

        self.assertIn("DYNAMICS_NOT_CLOSED", {item.code for item in report.issues})

    def test_speed_ceiling_reaction_closes_force_balance_explicitly(self) -> None:
        sample = replace(
            _sample(
                1_000,
                "T1",
                1_000.0,
                speed_mps=5.0,
                traction_force_n=60_000.0,
                electric_brake_force_n=0.0,
            ),
            permitted_speed_mps=5.0,
            resistance_force_n=0.0,
            constraint_reaction_force_n=-60_000.0,
        )

        report = validate_trajectory_frames((TrajectoryFrame(1_000, (sample,)),))

        self.assertNotIn("DYNAMICS_NOT_CLOSED", {item.code for item in report.issues})

    def test_jsonl_round_trip_preserves_metadata_and_interpolates(self) -> None:
        frames = _frames()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trajectory.jsonl"
            with JsonlTrajectoryRecorder(path, metadata={"scenarioId": "TEST"}) as recorder:
                recorder.write(frames[0])
                recorder.write(frames[1])
                recorder.write_metadata({"trackingMetrics": TRACKING_METRICS})

            provider = JsonlTrajectoryProvider(path)
            midpoint = provider.frame_at(102_500, BASELINE_CANDIDATE)

        self.assertEqual(provider.metadata["trackingMetrics"], TRACKING_METRICS)
        self.assertEqual(provider.metadata["scenarioId"], "TEST")
        self.assertEqual(midpoint.source, "INTERPOLATED_REPLAY")
        self.assertAlmostEqual(midpoint.samples[0].mileage_m, 1_025.0)
        self.assertEqual(provider.tracking_metrics(BASELINE_CANDIDATE)["operationalMetricsAvailable"], 1.0)

    def test_replay_uses_nearest_real_frame_across_turnback_boundary(self) -> None:
        before_sample = replace(
            _sample(1_000, "T1", 1_000.0, speed_mps=0.0),
            direction="UP",
            turnback_count=0,
        )
        after_sample = replace(
            _sample(2_000, "T1", 900.0, speed_mps=0.0),
            direction="DOWN",
            turnback_count=1,
        )
        provider = InMemoryTrajectoryProvider((
            TrajectoryFrame(1_000, (before_sample,)),
            TrajectoryFrame(2_000, (after_sample,)),
        ))

        before_boundary = provider.frame_at(1_250, BASELINE_CANDIDATE).samples[0]
        after_boundary = provider.frame_at(1_750, BASELINE_CANDIDATE).samples[0]

        self.assertEqual(before_boundary.direction, "UP")
        self.assertEqual(before_boundary.mileage_m, 1_000.0)
        self.assertEqual(after_boundary.direction, "DOWN")
        self.assertEqual(after_boundary.mileage_m, 900.0)
        self.assertEqual(after_boundary.source, "NEAREST_EVENT_BOUNDARY")

    def test_snapshot_adapter_maps_public_fields_and_derives_acceleration(self) -> None:
        adapter = EngineSnapshotTrajectoryAdapter()
        base_train = {
            "trainId": "ENGINE-001",
            "direction": "UP",
            "headMileageM": 1_000.0,
            "speedMps": 10.0,
            "massKg": 225_000.0,
            "tractionForceN": 10_000.0,
            "electricBrakeForceN": 0.0,
            "pneumaticBrakeForceN": 0.0,
            "emergencyBrakeActive": True,
            "commandSource": "ATP_OVERRIDE",
            "auxiliaryPowerKw": 150.0,
            "tractionPowerRequestKw": 120.0,
            "regenPowerAvailableKw": 0.0,
            "localSpeedLimitMps": 16.0,
            "gradeRatio": 0.001,
            "phase": "DEPARTING",
            "currentStationCode": "GGZ",
            "nextStationCode": "FSP",
            "departureAuthorized": True,
            "interlockingHoldReason": None,
            "activeRouteIds": ["R-1"],
            "turnbackCount": 0,
        }
        first = adapter.frame_from_snapshot({
            "simTimeMs": 1_000,
            "tick": 1,
            "clockState": "RUNNING",
            "trains": [base_train],
        })
        second = adapter.frame_from_snapshot({
            "simTimeMs": 2_000,
            "tick": 2,
            "clockState": "RUNNING",
            "trains": [{**base_train, "headMileageM": 1_010.5, "speedMps": 11.0}],
        })

        self.assertEqual(first.samples[0].active_route_ids, ("R-1",))
        self.assertTrue(first.samples[0].departure_authorized)
        self.assertTrue(first.samples[0].emergency_brake_active)
        self.assertEqual(first.samples[0].command_source, "ATP_OVERRIDE")
        self.assertAlmostEqual(second.samples[0].acceleration_mps2, 1.0)
        self.assertEqual(second.samples[0].source, "ENGINE_SNAPSHOT")

    def test_engine_runner_caches_prepared_candidate(self) -> None:
        calls = []

        def runner(candidate, sample_times_ms):
            calls.append((dict(candidate), tuple(sample_times_ms)))
            return _frames()

        provider = EngineRunTrajectoryProvider(runner)
        provider.prepare(BASELINE_CANDIDATE, (102_500, 107_500))
        provider.prepare({**BASELINE_CANDIDATE, "storageChargeLimitKw": 750.0}, (102_500, 107_500))

        self.assertEqual(len(calls), 1)
        provider.prepare({**BASELINE_CANDIDATE, "tractionTimingSec": 1.0}, (102_500, 107_500))
        self.assertEqual(len(calls), 2)


class ReplayJointPowerEvaluationTests(unittest.TestCase):
    def _evaluator(self, provider: InMemoryTrajectoryProvider) -> JointPowerEvaluator:
        return JointPowerEvaluator(
            TOPOLOGY,
            JointExperimentConfig(
                train_count=2,
                start_time_ms=100_000,
                horizon_sec=20,
                time_step_sec=10.0,
                electrical_substeps=2,
                max_terminal_soc_deviation=0.15,
            ),
            trajectory_provider=provider,
        )

    def test_replay_provider_drives_storage_evaluation_at_absolute_time(self) -> None:
        evaluator = self._evaluator(InMemoryTrajectoryProvider(
            _frames(),
            tracking_metrics=TRACKING_METRICS,
        ))

        result = evaluator.evaluate(BASELINE_CANDIDATE)

        self.assertTrue(result["feasible"], result)
        self.assertEqual(result["trajectorySource"], "MEMORY_REPLAY")
        self.assertEqual(result["metrics"]["operationalMetricsAvailable"], 1.0)

    def test_static_replay_rejects_changed_timing_variables(self) -> None:
        evaluator = self._evaluator(InMemoryTrajectoryProvider(
            _frames(),
            tracking_metrics=TRACKING_METRICS,
        ))

        with self.assertRaises(TrajectoryContractError):
            evaluator.evaluate({**BASELINE_CANDIDATE, "tractionTimingSec": -1.0})

    def test_missing_timetable_metrics_is_explicitly_infeasible(self) -> None:
        result = self._evaluator(InMemoryTrajectoryProvider(_frames())).evaluate(BASELINE_CANDIDATE)

        self.assertFalse(result["feasible"])
        self.assertFalse(result["constraints"]["operationalMetrics"])
        self.assertEqual(result["constraintViolations"]["operationalMetrics"], 1.0)

    def test_replay_evaluation_preserves_raw_frame_force_peak(self) -> None:
        frames = list(_frames())
        peak = replace(
            frames[1].samples[0],
            traction_force_n=300_000.0,
            resistance_force_n=300_000.0,
        )
        frames[1] = replace(
            frames[1],
            samples=(peak, frames[1].samples[1]),
        )
        evaluator = self._evaluator(InMemoryTrajectoryProvider(
            tuple(frames),
            tracking_metrics=TRACKING_METRICS,
        ))

        result = evaluator.evaluate(BASELINE_CANDIDATE)

        self.assertEqual(result["metrics"]["maxTractionForceN"], 300_000.0)

    def test_replay_physics_includes_explicit_speed_constraint_reaction(self) -> None:
        evaluator = self._evaluator(InMemoryTrajectoryProvider(
            _frames(),
            tracking_metrics=TRACKING_METRICS,
        ))
        sample = replace(
            _sample(100_000, "T1", 1_000.0, traction_force_n=60_000.0),
            resistance_force_n=0.0,
            constraint_reaction_force_n=-60_000.0,
        )

        _, physical = evaluator._frame_to_loads_and_physics(
            TrajectoryFrame(100_000, (sample,))
        )

        self.assertAlmostEqual(physical["maxDynamicsResidualN"], 0.0)

    def test_idle_depot_samples_are_not_moving_block_obstacles(self) -> None:
        evaluator = self._evaluator(InMemoryTrajectoryProvider(
            _frames(),
            tracking_metrics=TRACKING_METRICS,
        ))
        active_a = _sample(100_000, "ACTIVE-1", 1_000.0)
        active_b = _sample(100_000, "ACTIVE-2", 2_000.0)
        stored = replace(
            _sample(100_000, "STORED", 1_000.1, speed_mps=0.0),
            phase="IDLE",
        )

        _, physical = evaluator._frame_to_loads_and_physics(
            TrajectoryFrame(100_000, (active_a, active_b, stored))
        )

        self.assertAlmostEqual(physical["minSameDirectionSpacingM"], 1_000.0)


if __name__ == "__main__":
    unittest.main()
