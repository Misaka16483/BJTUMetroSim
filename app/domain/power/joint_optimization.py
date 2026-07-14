from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace
from pathlib import Path
from statistics import median
from typing import Any

from app.domain.power.flow_solver import DCTractionPowerFlowSolver
from app.domain.power.line9_topology import load_line9_power_network
from app.domain.power.network_models import TrainElectricalLoad
from app.domain.power.trajectory import (
    ProxyTrajectoryProvider,
    TrainTrajectorySample,
    TrajectoryFrame,
    TrajectoryProvider,
    assert_candidate_supported,
)
from app.domain.power.validation import validate_power_snapshot
from app.domain.vehicle.models import VehicleConfig
from app.domain.vehicle.services import SimpleVehicleModel


JsonDict = dict[str, Any]
BASELINE_CANDIDATE = {
    "departureSpreadSec": 0.0,
    "tractionTimingSec": 0.0,
    "brakeTimingSec": 0.0,
    "storageChargeLimitKw": 2000.0,
    "storageDischargeLimitKw": 2000.0,
    "storageTriggerKw": 3500.0,
}
VARIABLE_BOUNDS = {
    "departureSpreadSec": (0.0, 15.0),
    "tractionTimingSec": (-3.0, 3.0),
    "brakeTimingSec": (-3.0, 3.0),
    "storageChargeLimitKw": (500.0, 2000.0),
    "storageDischargeLimitKw": (500.0, 2000.0),
    "storageTriggerKw": (3400.0, 3800.0),
}
MODE_VARIABLES = {
    "TIMING_ONLY": tuple(list(VARIABLE_BOUNDS)[:3]),
    "STORAGE_ONLY": tuple(list(VARIABLE_BOUNDS)[3:]),
    "JOINT": tuple(VARIABLE_BOUNDS),
}


@dataclass(frozen=True)
class JointExperimentConfig:
    train_count: int = 12
    start_time_ms: int = 0
    horizon_sec: int = 240
    time_step_sec: float = 1.25
    cycle_sec: float = 120.0
    cycle_distance_m: float = 1550.0
    nominal_max_speed_mps: float = 20.0
    train_mass_kg: float = 225_000.0
    max_traction_force_n: float = 300_000.0
    max_service_brake_force_n: float = 300_000.0
    max_acceleration_mps2: float = 1.10
    max_service_deceleration_mps2: float = 1.00
    minimum_same_direction_spacing_m: float = 500.0
    max_terminal_soc_deviation: float = 0.05
    rectifier_efficiency: float = 0.96
    feedback_efficiency: float = 0.95
    electrical_substeps: int = 2
    min_voltage_v: float = 650.0
    max_substation_load_ratio: float = 1.20
    max_balance_error_ratio: float = 0.01
    max_speed_tracking_rmse_mps: float = 1.50
    max_departure_deviation_sec: float = 15.0
    max_runtime_deviation_sec: float = 15.0
    max_stop_position_error_m: float = 0.5
    max_dynamics_residual_n: float = 5_000.0

    def __post_init__(self) -> None:
        if self.train_count < 2:
            raise ValueError("train_count must be at least 2")
        if self.start_time_ms < 0:
            raise ValueError("start_time_ms must be non-negative")
        if self.horizon_sec <= 0 or self.time_step_sec <= 0:
            raise ValueError("horizon and time step must be positive")
        if self.horizon_sec % self.time_step_sec:
            raise ValueError("horizon_sec must be divisible by time_step_sec")
        if self.electrical_substeps < 1:
            raise ValueError("electrical_substeps must be positive")
        if min(
            self.max_runtime_deviation_sec,
            self.max_stop_position_error_m,
            self.max_dynamics_residual_n,
        ) < 0:
            raise ValueError("runtime, stop-position, and dynamics tolerances must be non-negative")
        if not 0 < self.rectifier_efficiency <= 1 or not 0 < self.feedback_efficiency <= 1:
            raise ValueError("grid conversion efficiencies must be in (0, 1]")


def normalize_candidate(candidate: JsonDict) -> JsonDict:
    normalized: JsonDict = {}
    for name, (lower, upper) in VARIABLE_BOUNDS.items():
        value = float(candidate.get(name, BASELINE_CANDIDATE[name]))
        normalized[name] = round(min(upper, max(lower, value)), 6)
    return normalized


class JointPowerEvaluator:
    """Deterministic multi-train evaluator using the production DC solver."""

    def __init__(
        self,
        topology_path: str | Path,
        config: JointExperimentConfig | None = None,
        trajectory_provider: TrajectoryProvider | None = None,
    ) -> None:
        self.topology_path = Path(topology_path)
        self.config = config or JointExperimentConfig()
        self.vehicle_config = VehicleConfig(
            train_id="JOINT-PROFILE",
            mass_kg=self.config.train_mass_kg,
            max_speed_mps=self.config.nominal_max_speed_mps,
            max_traction_force_n=self.config.max_traction_force_n,
            max_service_brake_force_n=self.config.max_service_brake_force_n,
            auxiliary_power_kw=150.0,
        )
        self.vehicle_dynamics = SimpleVehicleModel(self.vehicle_config)
        self.trajectory_provider: TrajectoryProvider = trajectory_provider or ProxyTrajectoryProvider(
            self._proxy_frame_at,
            self._tracking_metrics,
        )

    def evaluate(
        self,
        candidate: JsonDict,
        *,
        time_step_sec: float | None = None,
        storage_enabled: bool = True,
    ) -> JsonDict:
        candidate = normalize_candidate(candidate)
        assert_candidate_supported(self.trajectory_provider, candidate, BASELINE_CANDIDATE)
        dt = float(time_step_sec or self.config.time_step_sec)
        if self.config.horizon_sec % dt:
            raise ValueError("evaluation step must divide horizon")
        network = load_line9_power_network(self.topology_path)
        if storage_enabled and len(network.supercapacitor_storages) != 1:
            raise ValueError("joint experiment requires exactly one supercapacitor system")
        storage_id: str | None = None
        storage = None
        if storage_enabled:
            storage_id, storage = next(iter(network.supercapacitor_storages.items()))
            network.supercapacitor_storages[storage_id] = replace(
                storage,
                max_charge_power_kw=candidate["storageChargeLimitKw"],
                max_discharge_power_kw=candidate["storageDischargeLimitKw"],
                discharge_trigger_power_kw=candidate["storageTriggerKw"],
            )
        else:
            network.supercapacitor_storages.clear()
        solver = DCTractionPowerFlowSolver(network)
        initial_storage_kwh = storage.rated_energy_kwh * storage.initial_soc if storage else 0.0
        metrics: JsonDict = {
            "gridImportKwh": 0.0,
            "gridExportKwh": 0.0,
            "acGridImportKwh": 0.0,
            "acGridExportKwh": 0.0,
            "netGridEnergyKwh": 0.0,
            "socCorrectedGridImportKwh": 0.0,
            "socCorrectedAcGridImportKwh": 0.0,
            "netAcGridEnergyKwh": 0.0,
            "wastedRegenKwh": 0.0,
            "generatedRegenKwh": 0.0,
            "absorbedRegenKwh": 0.0,
            "storageChargedKwh": 0.0,
            "storageDischargedKwh": 0.0,
            "lossesKwh": 0.0,
            "peakRectifierPowerKw": 0.0,
            "peakSubstationRectifierPowerKw": 0.0,
            "aggregateAcGridPeakKw": 0.0,
            "minVoltageV": float("inf"),
            "maxSubstationLoadRatio": 0.0,
            "maxBalanceErrorRatio": 0.0,
            "maxRegenBalanceErrorKw": 0.0,
            "failedSteps": 0,
            "maxTractionForceN": 0.0,
            "maxBrakeForceN": 0.0,
            "maxAccelerationMps2": 0.0,
            "maxDecelerationMps2": 0.0,
            "maxDynamicsResidualN": 0.0,
            "minSameDirectionSpacingM": float("inf"),
        }
        step_count = int(self.config.horizon_sec / dt)
        sub_dt = dt / self.config.electrical_substeps
        sample_times_ms = tuple(
            self.config.start_time_ms + round((step * dt + (substep + 0.5) * sub_dt) * 1000.0)
            for step in range(step_count)
            for substep in range(self.config.electrical_substeps)
        )
        self.trajectory_provider.prepare(candidate, sample_times_ms)
        for step in range(step_count):
            for substep in range(self.config.electrical_substeps):
                sim_time_sec = step * dt + (substep + 0.5) * sub_dt
                sim_time_ms = self.config.start_time_ms + round(sim_time_sec * 1000.0)
                loads, physical = self._loads_and_physics_at(candidate, sim_time_ms)
                snapshot = solver.solve(loads, dt_sec=sub_dt, sim_time_ms=sim_time_ms)
                validation = validate_power_snapshot(
                    snapshot,
                    balance_error_limit_ratio=self.config.max_balance_error_ratio,
                )
                hours = sub_dt / 3600.0
                rectifier_kw = sum(max(item.rectifier_power_kw, 0.0) for item in snapshot.substations)
                substation_peak_kw = max((max(item.rectifier_power_kw, 0.0) for item in snapshot.substations), default=0.0)
                ac_import_kw = rectifier_kw / self.config.rectifier_efficiency
                ac_export_kw = snapshot.feedback_regen_kw * self.config.feedback_efficiency
                metrics["gridImportKwh"] += rectifier_kw * hours
                metrics["gridExportKwh"] += snapshot.feedback_regen_kw * hours
                metrics["acGridImportKwh"] += ac_import_kw * hours
                metrics["acGridExportKwh"] += ac_export_kw * hours
                metrics["wastedRegenKwh"] += snapshot.wasted_regen_kw * hours
                metrics["generatedRegenKwh"] += snapshot.generated_regen_kw * hours
                metrics["absorbedRegenKwh"] += snapshot.absorbed_regen_kw * hours
                metrics["storageChargedKwh"] += sum(
                    item.charge_power_kw for item in snapshot.supercapacitor_flows
                ) * hours
                metrics["storageDischargedKwh"] += sum(
                    item.discharge_power_kw for item in snapshot.supercapacitor_flows
                ) * hours
                metrics["lossesKwh"] += snapshot.losses_kw * hours
                metrics["peakRectifierPowerKw"] = max(metrics["peakRectifierPowerKw"], rectifier_kw)
                metrics["peakSubstationRectifierPowerKw"] = max(
                    metrics["peakSubstationRectifierPowerKw"], substation_peak_kw
                )
                metrics["aggregateAcGridPeakKw"] = max(metrics["aggregateAcGridPeakKw"], ac_import_kw)
                metrics["minVoltageV"] = min(
                    metrics["minVoltageV"],
                    *(item.voltage_v for item in snapshot.trains),
                )
                metrics["maxSubstationLoadRatio"] = max(
                    metrics["maxSubstationLoadRatio"],
                    *(item.load_ratio for item in snapshot.substations),
                )
                metrics["maxBalanceErrorRatio"] = max(
                    metrics["maxBalanceErrorRatio"], snapshot.power_balance_error_ratio
                )
                metrics["maxRegenBalanceErrorKw"] = max(
                    metrics["maxRegenBalanceErrorKw"], validation.metrics["regenBalanceErrorKw"]
                )
                for name in (
                    "maxTractionForceN",
                    "maxBrakeForceN",
                    "maxAccelerationMps2",
                    "maxDecelerationMps2",
                    "maxDynamicsResidualN",
                ):
                    metrics[name] = max(metrics[name], physical[name])
                metrics["minSameDirectionSpacingM"] = min(
                    metrics["minSameDirectionSpacingM"], physical["minSameDirectionSpacingM"]
                )
                if not validation.passed:
                    metrics["failedSteps"] += 1

        final_storage_kwh = solver.storage_checkpoint()[0][storage_id] if storage_id else 0.0
        stored_delta_kwh = final_storage_kwh - initial_storage_kwh
        storage_credit_kwh = 0.0
        storage_credit_ac_kwh = 0.0
        if storage is not None and stored_delta_kwh >= 0.0:
            storage_credit_kwh = stored_delta_kwh * storage.discharge_efficiency
            storage_credit_ac_kwh = storage_credit_kwh / self.config.rectifier_efficiency
        elif storage is not None:
            storage_credit_kwh = stored_delta_kwh / storage.charge_efficiency
            storage_credit_ac_kwh = storage_credit_kwh / self.config.rectifier_efficiency
        metrics["initialStorageKwh"] = initial_storage_kwh
        metrics["finalStorageKwh"] = final_storage_kwh
        metrics["storageEnergyDeltaKwh"] = stored_delta_kwh
        metrics["socCorrectedGridImportKwh"] = metrics["gridImportKwh"] - storage_credit_kwh
        metrics["netGridEnergyKwh"] = metrics["socCorrectedGridImportKwh"] - metrics["gridExportKwh"]
        metrics["socCorrectedAcGridImportKwh"] = metrics["acGridImportKwh"] - storage_credit_ac_kwh
        metrics["netAcGridEnergyKwh"] = metrics["socCorrectedAcGridImportKwh"] - metrics["acGridExportKwh"]
        metrics["wastedRegenRatio"] = (
            metrics["wastedRegenKwh"] / metrics["generatedRegenKwh"]
            if metrics["generatedRegenKwh"] > 1e-9 else 0.0
        )
        metrics["terminalSocDeviation"] = (
            abs(final_storage_kwh - initial_storage_kwh) / storage.rated_energy_kwh
            if storage is not None else 0.0
        )
        tracking = dict(self.trajectory_provider.tracking_metrics(candidate))
        metrics.update(tracking)
        constraints = {
            "allStepsValid": metrics["failedSteps"] == 0,
            "minimumVoltage": metrics["minVoltageV"] >= self.config.min_voltage_v,
            "substationCapacity": metrics["maxSubstationLoadRatio"] <= self.config.max_substation_load_ratio,
            "powerBalance": metrics["maxBalanceErrorRatio"] < self.config.max_balance_error_ratio,
            "speedTracking": metrics["speedTrackingRmseMps"] <= self.config.max_speed_tracking_rmse_mps,
            "departureDeviation": metrics["maxDepartureDeviationSec"] <= self.config.max_departure_deviation_sec,
            "runtimePreserved": metrics["runtimeDeviationSec"] <= self.config.max_runtime_deviation_sec,
            "stopPositionPreserved": metrics["stopPositionErrorM"] <= self.config.max_stop_position_error_m,
            "maximumSpeed": metrics["maximumSpeedMps"] <= self.config.nominal_max_speed_mps + 1e-9,
            "tractionForce": metrics["maxTractionForceN"] <= self.config.max_traction_force_n,
            "serviceBrakeForce": metrics["maxBrakeForceN"] <= self.config.max_service_brake_force_n,
            "acceleration": metrics["maxAccelerationMps2"] <= self.config.max_acceleration_mps2,
            "deceleration": metrics["maxDecelerationMps2"] <= self.config.max_service_deceleration_mps2,
            "dynamicsClosure": metrics["maxDynamicsResidualN"] <= self.config.max_dynamics_residual_n,
            "trainSeparation": metrics["minSameDirectionSpacingM"] >= self.config.minimum_same_direction_spacing_m,
            "terminalSoc": metrics["terminalSocDeviation"] <= self.config.max_terminal_soc_deviation,
            "operationalMetrics": metrics.get("operationalMetricsAvailable", 0.0) >= 1.0,
        }
        violations = {
            "failedSteps": float(metrics["failedSteps"]),
            "minimumVoltage": max(0.0, self.config.min_voltage_v - metrics["minVoltageV"]) / 50.0,
            "substationCapacity": max(0.0, metrics["maxSubstationLoadRatio"] - self.config.max_substation_load_ratio),
            "powerBalance": max(0.0, metrics["maxBalanceErrorRatio"] - self.config.max_balance_error_ratio) * 100.0,
            "speedTracking": max(0.0, metrics["speedTrackingRmseMps"] - self.config.max_speed_tracking_rmse_mps),
            "departureDeviation": max(0.0, metrics["maxDepartureDeviationSec"] - self.config.max_departure_deviation_sec) / 5.0,
            "runtimePreserved": max(0.0, metrics["runtimeDeviationSec"] - self.config.max_runtime_deviation_sec) / 5.0,
            "stopPositionPreserved": max(0.0, metrics["stopPositionErrorM"] - self.config.max_stop_position_error_m),
            "maximumSpeed": max(0.0, metrics["maximumSpeedMps"] - self.config.nominal_max_speed_mps),
            "tractionForce": max(0.0, metrics["maxTractionForceN"] - self.config.max_traction_force_n) / 50_000.0,
            "serviceBrakeForce": max(0.0, metrics["maxBrakeForceN"] - self.config.max_service_brake_force_n) / 50_000.0,
            "acceleration": max(0.0, metrics["maxAccelerationMps2"] - self.config.max_acceleration_mps2),
            "deceleration": max(0.0, metrics["maxDecelerationMps2"] - self.config.max_service_deceleration_mps2),
            "dynamicsClosure": max(0.0, metrics["maxDynamicsResidualN"] - self.config.max_dynamics_residual_n) / max(self.config.max_dynamics_residual_n, 1.0),
            "trainSeparation": max(0.0, self.config.minimum_same_direction_spacing_m - metrics["minSameDirectionSpacingM"]) / 100.0,
            "terminalSoc": max(0.0, metrics["terminalSocDeviation"] - self.config.max_terminal_soc_deviation) / 0.05,
            "operationalMetrics": 0.0 if metrics.get("operationalMetricsAvailable", 0.0) >= 1.0 else 1.0,
        }
        objectives = {
            "netAcGridEnergyKwh": metrics["netAcGridEnergyKwh"],
            "aggregateAcGridPeakKw": metrics["aggregateAcGridPeakKw"],
            "wastedRegenRatio": metrics["wastedRegenRatio"],
        }
        return {
            "candidate": candidate,
            "objectives": objectives,
            "constraints": constraints,
            "constraintViolations": violations,
            "totalConstraintViolation": sum(violations.values()),
            "feasible": all(constraints.values()),
            "metrics": metrics,
            "timeStepSec": dt,
            "startTimeMs": self.config.start_time_ms,
            "stepCount": step_count,
            "electricalSolveCount": step_count * self.config.electrical_substeps,
            "electricalTimeStepSec": sub_dt,
            "storageEnabled": storage_enabled,
            "trajectorySource": self.trajectory_provider.source,
        }

    def _profile_parameters(self, candidate: JsonDict) -> tuple[float, float, float]:
        traction_end = 25.0 + candidate["tractionTimingSec"]
        brake_start = 75.0 + candidate["brakeTimingSec"]
        area_coefficient = 0.5 * traction_end + (brake_start - traction_end) + 0.5 * (105.0 - brake_start)
        max_speed = self.config.cycle_distance_m / area_coefficient
        return traction_end, brake_start, max_speed

    def _speed_and_distance(self, candidate: JsonDict, phase_sec: float) -> tuple[float, float]:
        traction_end, brake_start, max_speed = self._profile_parameters(candidate)
        phase = min(max(phase_sec, 0.0), self.config.cycle_sec)
        if phase <= traction_end:
            speed = max_speed * phase / traction_end
            distance = 0.5 * max_speed * phase * phase / traction_end
        elif phase <= brake_start:
            speed = max_speed
            distance = 0.5 * max_speed * traction_end + max_speed * (phase - traction_end)
        elif phase <= 105.0:
            brake_duration = 105.0 - brake_start
            elapsed = phase - brake_start
            speed = max_speed * (1.0 - elapsed / brake_duration)
            distance = (
                0.5 * max_speed * traction_end
                + max_speed * (brake_start - traction_end)
                + max_speed * (elapsed - 0.5 * elapsed * elapsed / brake_duration)
            )
        else:
            speed = 0.0
            distance = self.config.cycle_distance_m
        return speed, distance

    def _speed_acceleration_distance(
        self,
        candidate: JsonDict,
        phase_sec: float,
    ) -> tuple[float, float, float]:
        traction_end, brake_start, max_speed = self._profile_parameters(candidate)
        speed, distance = self._speed_and_distance(candidate, phase_sec)
        phase = min(max(phase_sec, 0.0), self.config.cycle_sec)
        if phase < traction_end:
            acceleration = max_speed / traction_end
        elif phase < brake_start:
            acceleration = 0.0
        elif phase < 105.0:
            acceleration = -max_speed / (105.0 - brake_start)
        else:
            acceleration = 0.0
        return speed, acceleration, distance

    def _train_kinematics(
        self,
        candidate: JsonDict,
        index: int,
        sim_time_sec: float,
    ) -> tuple[str, float, float, float]:
        first_m, last_m = 313.0, 16_048.92
        span_m = last_m - first_m
        group = index % 3 - 1
        shift_sec = group * candidate["departureSpreadSec"]
        elapsed = sim_time_sec - shift_sec + index * self.config.cycle_sec / self.config.train_count
        cycle_index = math.floor(elapsed / self.config.cycle_sec)
        phase = elapsed - cycle_index * self.config.cycle_sec
        speed_mps, distance_in_cycle = self._speed_and_distance(candidate, phase)
        cumulative_m = cycle_index * self.config.cycle_distance_m + distance_in_cycle
        base_m = span_m * ((index + 0.5) / self.config.train_count)
        direction = "UP" if index % 2 == 0 else "DOWN"
        if direction == "UP":
            mileage_m = first_m + (base_m + cumulative_m) % span_m
        else:
            mileage_m = first_m + (base_m - cumulative_m) % span_m
        return direction, mileage_m, speed_mps, phase

    def _loads_at(
        self,
        candidate: JsonDict,
        sim_time_sec: float,
        *,
        interval_sec: float = 0.0,
    ) -> list[TrainElectricalLoad]:
        sample_count = 4 if interval_sec > 0.0 else 1
        sample_times = [
            sim_time_sec + (sample + 0.5) * interval_sec / sample_count
            for sample in range(sample_count)
        ] if interval_sec > 0.0 else [sim_time_sec]
        frames = [
            self.trajectory_provider.frame_at(round(sample_time * 1000.0), candidate)
            for sample_time in sample_times
        ]
        load_sets = [self._frame_to_loads_and_physics(frame)[0] for frame in frames]
        by_train = [{item.train_id: item for item in loads} for loads in load_sets]
        midpoint = frames[len(frames) // 2]
        loads: list[TrainElectricalLoad] = []
        for sample in midpoint.samples:
            items = [values[sample.train_id] for values in by_train]
            loads.append(TrainElectricalLoad(
                train_id=sample.train_id,
                direction=sample.direction,
                mileage_m=sample.mileage_m,
                speed_mps=sample.speed_mps,
                aux_power_kw=sum(item.aux_power_kw for item in items) / sample_count,
                traction_power_request_kw=sum(item.traction_power_kw for item in items) / sample_count,
                regen_power_available_kw=sum(item.raw_regen_power_kw for item in items) / sample_count,
            ))
        return loads

    def _loads_and_physics_at(
        self,
        candidate: JsonDict,
        sim_time_ms: int,
    ) -> tuple[list[TrainElectricalLoad], JsonDict]:
        frame = self.trajectory_provider.frame_at(sim_time_ms, candidate)
        return self._frame_to_loads_and_physics(frame)

    def _frame_to_loads_and_physics(
        self,
        frame: TrajectoryFrame,
    ) -> tuple[list[TrainElectricalLoad], JsonDict]:
        loads: list[TrainElectricalLoad] = []
        states: list[tuple[str, float]] = []
        physical: JsonDict = {
            "maxTractionForceN": 0.0,
            "maxBrakeForceN": 0.0,
            "maxAccelerationMps2": 0.0,
            "maxDecelerationMps2": 0.0,
            "maxDynamicsResidualN": 0.0,
            "minSameDirectionSpacingM": float("inf"),
        }
        for sample in frame.samples:
            if sample.resistance_force_n is not None:
                resistance_n = sample.resistance_force_n
            else:
                vehicle = SimpleVehicleModel(VehicleConfig(
                    train_id=sample.train_id,
                    mass_kg=sample.mass_kg,
                    max_speed_mps=max(sample.permitted_speed_mps or self.config.nominal_max_speed_mps, 0.1),
                    max_traction_force_n=max(self.config.max_traction_force_n, sample.traction_force_n, 1.0),
                    max_service_brake_force_n=max(self.config.max_service_brake_force_n, sample.total_brake_force_n, 1.0),
                    auxiliary_power_kw=sample.auxiliary_power_kw,
                ))
                resistance_n = vehicle.running_resistance_n(
                    sample.speed_mps,
                    sample.traction_force_n,
                    sample.total_brake_force_n,
                )
            gradient_force_n = sample.mass_kg * 9.80665 * sample.grade_ratio
            residual_n = abs(
                sample.traction_force_n
                - sample.total_brake_force_n
                - resistance_n
                - gradient_force_n
                - sample.mass_kg * sample.acceleration_mps2
            )
            traction_kw = sample.traction_power_request_kw
            if traction_kw is None:
                traction_kw = sample.traction_force_n * sample.speed_mps / 1000.0 / 0.88
            regen_kw = sample.regen_power_available_kw
            if regen_kw is None:
                regen_kw = sample.electric_brake_force_n * sample.speed_mps / 1000.0 * 0.80
            loads.append(TrainElectricalLoad(
                train_id=sample.train_id,
                direction=sample.direction,
                mileage_m=sample.mileage_m,
                speed_mps=sample.speed_mps,
                aux_power_kw=sample.auxiliary_power_kw,
                traction_power_request_kw=traction_kw,
                regen_power_available_kw=regen_kw,
            ))
            physical["maxTractionForceN"] = max(physical["maxTractionForceN"], sample.traction_force_n)
            physical["maxBrakeForceN"] = max(physical["maxBrakeForceN"], sample.total_brake_force_n)
            physical["maxAccelerationMps2"] = max(physical["maxAccelerationMps2"], sample.acceleration_mps2)
            physical["maxDecelerationMps2"] = max(physical["maxDecelerationMps2"], -sample.acceleration_mps2)
            physical["maxDynamicsResidualN"] = max(physical["maxDynamicsResidualN"], residual_n)
            states.append((sample.direction, sample.mileage_m))
        first_m, last_m = 313.0, 16_048.92
        span_m = last_m - first_m
        for direction in ("UP", "DOWN"):
            positions = sorted(mileage for item_direction, mileage in states if item_direction == direction)
            if len(positions) < 2:
                continue
            gaps = [right - left for left, right in zip(positions, positions[1:])]
            gaps.append(span_m - positions[-1] + positions[0])
            physical["minSameDirectionSpacingM"] = min(
                physical["minSameDirectionSpacingM"], min(gaps)
            )
        return loads, physical

    def _proxy_frame_at(self, candidate: JsonDict, sim_time_ms: int) -> TrajectoryFrame:
        sim_time_sec = (sim_time_ms - self.config.start_time_ms) / 1000.0
        samples: list[TrainTrajectorySample] = []
        for index in range(self.config.train_count):
            direction, mileage_m, speed_mps, phase = self._train_kinematics(candidate, index, sim_time_sec)
            _, acceleration, _ = self._speed_acceleration_distance(candidate, phase)
            resistance_n = self.vehicle_dynamics.running_resistance_n(speed_mps)
            traction_force_n = 0.0
            brake_force_n = 0.0
            if acceleration >= 0.0 and speed_mps > 0.0:
                traction_force_n = self.config.train_mass_kg * acceleration + resistance_n
            elif acceleration < 0.0:
                brake_force_n = max(-self.config.train_mass_kg * acceleration - resistance_n, 0.0)
            if phase < 25.0 + candidate["tractionTimingSec"]:
                phase_name = "DEPARTING"
            elif phase < 75.0 + candidate["brakeTimingSec"]:
                phase_name = "CRUISING"
            elif phase < 105.0:
                phase_name = "APPROACHING"
            else:
                phase_name = "DWELLING"
            samples.append(TrainTrajectorySample(
                sim_time_ms=sim_time_ms,
                train_id=f"JOINT-{index + 1:03d}",
                direction=direction,
                mileage_m=mileage_m,
                speed_mps=speed_mps,
                acceleration_mps2=acceleration,
                mass_kg=self.config.train_mass_kg,
                traction_force_n=traction_force_n,
                electric_brake_force_n=brake_force_n,
                auxiliary_power_kw=self.vehicle_config.auxiliary_power_kw,
                permitted_speed_mps=self.config.nominal_max_speed_mps,
                resistance_force_n=resistance_n,
                phase=phase_name,
                departure_authorized=True,
                source="ANALYTIC_PROXY_V2",
            ))
        return TrajectoryFrame(sim_time_ms, tuple(samples), source="ANALYTIC_PROXY_V2")

    def _tracking_metrics(self, candidate: JsonDict) -> JsonDict:
        squared_error = 0.0
        samples = 0
        baseline = normalize_candidate(BASELINE_CANDIDATE)
        for second in range(round(self.config.cycle_sec)):
            speed, _ = self._speed_and_distance(candidate, float(second))
            nominal, _ = self._speed_and_distance(baseline, float(second))
            squared_error += (speed - nominal) ** 2
            samples += 1
        _, final_distance = self._speed_and_distance(candidate, self.config.cycle_sec)
        _, _, maximum_speed = self._profile_parameters(candidate)
        departure_offsets = [
            abs((index % 3 - 1) * candidate["departureSpreadSec"])
            for index in range(self.config.train_count)
        ]
        return {
            "speedTrackingRmseMps": math.sqrt(squared_error / samples),
            "meanDepartureDeviationSec": sum(departure_offsets) / len(departure_offsets),
            "maxDepartureDeviationSec": max(departure_offsets),
            "runtimeDeviationSec": 0.0,
            "stopPositionErrorM": abs(final_distance - self.config.cycle_distance_m),
            "maximumSpeedMps": maximum_speed,
            "operationalMetricsAvailable": 1.0,
        }


def _dominates(first: JsonDict, second: JsonDict) -> bool:
    if first["feasible"] != second["feasible"]:
        return first["feasible"]
    if not first["feasible"]:
        return first["totalConstraintViolation"] < second["totalConstraintViolation"]
    a = tuple(first["objectives"].values())
    b = tuple(second["objectives"].values())
    return all(x <= y for x, y in zip(a, b)) and any(x < y for x, y in zip(a, b))


def nondominated_fronts(items: list[JsonDict]) -> list[list[int]]:
    dominates: list[list[int]] = [[] for _ in items]
    dominated_count = [0 for _ in items]
    fronts: list[list[int]] = [[]]
    for i, first in enumerate(items):
        for j, second in enumerate(items):
            if i == j:
                continue
            if _dominates(first, second):
                dominates[i].append(j)
            elif _dominates(second, first):
                dominated_count[i] += 1
        if dominated_count[i] == 0:
            fronts[0].append(i)
    current = 0
    while current < len(fronts) and fronts[current]:
        next_front: list[int] = []
        for i in fronts[current]:
            for j in dominates[i]:
                dominated_count[j] -= 1
                if dominated_count[j] == 0:
                    next_front.append(j)
        if next_front:
            fronts.append(next_front)
        current += 1
    return fronts


def crowding_distances(items: list[JsonDict], front: list[int]) -> dict[int, float]:
    distances = {index: 0.0 for index in front}
    if len(front) <= 2:
        return {index: float("inf") for index in front}
    objective_names = tuple(items[front[0]]["objectives"])
    for name in objective_names:
        ordered = sorted(front, key=lambda index: items[index]["objectives"][name])
        distances[ordered[0]] = distances[ordered[-1]] = float("inf")
        low = items[ordered[0]]["objectives"][name]
        high = items[ordered[-1]]["objectives"][name]
        if high <= low:
            continue
        for position in range(1, len(ordered) - 1):
            previous_value = items[ordered[position - 1]]["objectives"][name]
            next_value = items[ordered[position + 1]]["objectives"][name]
            distances[ordered[position]] += (next_value - previous_value) / (high - low)
    return distances


def relative_utility(item: JsonDict, baseline: JsonDict) -> float:
    return sum(
        item["objectives"][name] / max(baseline["objectives"][name], 1e-9)
        for name in baseline["objectives"]
    ) / len(baseline["objectives"])


class Nsga2JointOptimizer:
    def __init__(self, evaluator: JointPowerEvaluator) -> None:
        self.evaluator = evaluator

    def run(
        self,
        mode: str,
        *,
        seed: int,
        population_size: int,
        generations: int,
        storage_enabled: bool = True,
    ) -> JsonDict:
        mode = mode.upper()
        variables = MODE_VARIABLES[mode]
        rng = random.Random(seed)
        cache: dict[tuple[float, ...], JsonDict] = {}
        trials: list[JsonDict] = []

        def evaluate(candidate: JsonDict) -> JsonDict:
            normalized = normalize_candidate(candidate)
            key = tuple(normalized[name] for name in VARIABLE_BOUNDS)
            if key not in cache:
                result = self.evaluator.evaluate(normalized, storage_enabled=storage_enabled)
                result["trialIndex"] = len(trials)
                cache[key] = result
                trials.append(result)
            return cache[key]

        baseline = evaluate(BASELINE_CANDIDATE)
        population = [dict(BASELINE_CANDIDATE)]
        if "tractionTimingSec" in variables:
            for traction_timing, brake_timing in ((-1.0, 1.0), (-2.0, 2.0), (-3.0, 3.0)):
                population.append({
                    **BASELINE_CANDIDATE,
                    "tractionTimingSec": traction_timing,
                    "brakeTimingSec": brake_timing,
                })
        if "storageTriggerKw" in variables:
            population.append({**BASELINE_CANDIDATE, "storageTriggerKw": 3600.0})
        population = population[:population_size]
        while len(population) < population_size:
            population.append(self._random_candidate(variables, rng))
        generation_summary: list[JsonDict] = []
        for generation in range(generations + 1):
            evaluated = [evaluate(item) for item in population]
            fronts = nondominated_fronts(evaluated)
            feasible_items = [item for item in evaluated if item["feasible"]]
            generation_summary.append({
                "generation": generation,
                "feasibleCount": len(feasible_items),
                "frontSize": len(fronts[0]),
                "bestRelativeUtility": (
                    min(relative_utility(item, baseline) for item in feasible_items)
                    if feasible_items else None
                ),
            })
            if generation == generations:
                break
            rank: dict[int, int] = {}
            crowding: dict[int, float] = {}
            for front_rank, front in enumerate(fronts):
                for index in front:
                    rank[index] = front_rank
                crowding.update(crowding_distances(evaluated, front))

            def tournament() -> JsonDict:
                left, right = rng.sample(range(len(evaluated)), 2)
                left_key = (rank[left], -crowding[left])
                right_key = (rank[right], -crowding[right])
                return evaluated[left if left_key < right_key else right]["candidate"]

            offspring: list[JsonDict] = []
            while len(offspring) < population_size:
                first = tournament()
                second = tournament()
                offspring.extend(self._crossover_mutate(first, second, variables, rng))
            combined = evaluated + [evaluate(item) for item in offspring[:population_size]]
            population = self._environmental_selection(combined, population_size)

        all_fronts = nondominated_fronts(trials)
        pareto = [trials[index] for index in all_fronts[0] if trials[index]["feasible"]]
        if not pareto:
            raise RuntimeError(f"NO_FEASIBLE_{mode}_SOLUTION")
        recommended = min(pareto, key=lambda item: (relative_utility(item, baseline), item["trialIndex"]))
        return {
            "mode": mode,
            "seed": seed,
            "algorithm": "NSGA2-CONSTRAINT-DOMINATION",
            "storageEnabled": storage_enabled,
            "populationSize": population_size,
            "generations": generations,
            "evaluationCount": len(trials),
            "feasibleCount": sum(item["feasible"] for item in trials),
            "baseline": baseline,
            "recommended": recommended,
            "recommendedRelativeUtility": relative_utility(recommended, baseline),
            "paretoFront": pareto,
            "generationSummary": generation_summary,
            "trials": trials,
        }

    @staticmethod
    def _random_candidate(variables: tuple[str, ...], rng: random.Random) -> JsonDict:
        candidate = dict(BASELINE_CANDIDATE)
        for name in variables:
            low, high = VARIABLE_BOUNDS[name]
            candidate[name] = rng.uniform(low, high)
        return candidate

    @staticmethod
    def _crossover_mutate(
        first: JsonDict,
        second: JsonDict,
        variables: tuple[str, ...],
        rng: random.Random,
    ) -> list[JsonDict]:
        children = [dict(BASELINE_CANDIDATE), dict(BASELINE_CANDIDATE)]
        for name in variables:
            low, high = VARIABLE_BOUNDS[name]
            alpha = rng.uniform(-0.10, 1.10)
            values = (
                alpha * first[name] + (1.0 - alpha) * second[name],
                alpha * second[name] + (1.0 - alpha) * first[name],
            )
            for child, value in zip(children, values):
                if rng.random() < 1.0 / len(variables):
                    value += rng.gauss(0.0, 0.10 * (high - low))
                child[name] = min(high, max(low, value))
        return children

    @staticmethod
    def _environmental_selection(items: list[JsonDict], count: int) -> list[JsonDict]:
        selected: list[JsonDict] = []
        for front in nondominated_fronts(items):
            if len(selected) + len(front) <= count:
                selected.extend(items[index]["candidate"] for index in front)
                continue
            distances = crowding_distances(items, front)
            ordered = sorted(front, key=lambda index: (-distances[index], items[index]["trialIndex"]))
            selected.extend(items[index]["candidate"] for index in ordered[:count - len(selected)])
            break
        return selected


def summarize_repeats(results: list[JsonDict]) -> JsonDict:
    utilities = [item["recommendedRelativeUtility"] for item in results]
    improvements = [(1.0 - value) * 100.0 for value in utilities]
    return {
        "repeatCount": len(results),
        "medianImprovementPercent": median(improvements),
        "minImprovementPercent": min(improvements),
        "maxImprovementPercent": max(improvements),
        "totalEvaluations": sum(item["evaluationCount"] for item in results),
        "feasibleRate": (
            sum(item["feasibleCount"] for item in results)
            / max(sum(item["evaluationCount"] for item in results), 1)
        ),
    }


def run_random_search(
    evaluator: JointPowerEvaluator,
    mode: str,
    *,
    seed: int,
    evaluation_count: int,
    storage_enabled: bool = True,
) -> JsonDict:
    mode = mode.upper()
    variables = MODE_VARIABLES[mode]
    rng = random.Random(seed)
    candidates = [dict(BASELINE_CANDIDATE)]
    candidates.extend(
        Nsga2JointOptimizer._random_candidate(variables, rng)
        for _ in range(evaluation_count - 1)
    )
    trials = [evaluator.evaluate(item, storage_enabled=storage_enabled) for item in candidates]
    for index, trial in enumerate(trials):
        trial["trialIndex"] = index
    baseline = trials[0]
    front = [trials[index] for index in nondominated_fronts(trials)[0] if trials[index]["feasible"]]
    recommended = min(front, key=lambda item: (relative_utility(item, baseline), item["trialIndex"]))
    return {
        "mode": mode,
        "seed": seed,
        "algorithm": "RANDOM_SEARCH",
        "storageEnabled": storage_enabled,
        "evaluationCount": len(trials),
        "feasibleCount": sum(item["feasible"] for item in trials),
        "baseline": baseline,
        "recommended": recommended,
        "recommendedRelativeUtility": relative_utility(recommended, baseline),
        "paretoFront": front,
    }
