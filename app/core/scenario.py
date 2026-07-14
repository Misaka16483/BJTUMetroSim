"""场景配置加载器 — 成员A: SimulationEngine 的输入来源."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


JsonDict = dict[str, Any]


@dataclass(frozen=True)
class TrainConfig:
    train_id: str
    line_id: str
    initial_station_code: str
    direction: str  # "UP" or "DOWN"
    capacity_pax: int = 1460
    initial_load_pax: int = 0


@dataclass(frozen=True)
class OperationPlanConfig:
    enabled: bool = False
    direction: str = "UP"
    start_time_ms: int | None = None
    end_time_ms: int | None = None
    max_duties: int = 8
    ready_lead_sec: float = 30.0
    turnback_layover_sec: float = 60.0
    profile_prewarm_enabled: bool = True
    profile_prewarm_timeout_sec: float = 180.0
    profile_reference_load_pax: int = 700
    runtime_recovery_margin_sec: float = 5.0
    door_cycle_allowance_sec: float = 2.0
    measurement_start_time_ms: int | None = None
    measurement_end_time_ms: int | None = None
    clearance_end_time_ms: int | None = None
    clearance_sec: float = 300.0
    stuck_threshold_sec: float = 300.0
    max_schedule_deviation_sec: float = 120.0


@dataclass
class ScenarioConfig:
    line_id: str
    name: str
    start_time_ms: int  # e.g. 8:00:00 = 8 * 3600 * 1000
    tick_seconds: float = 1.0
    use_dynamic_programming_profile: bool = True
    auto_spawn_trains: bool = False
    line_scope_file: str | None = None
    passenger_demand_scale: float = 1.0
    passenger_use_poisson: bool = True
    trains: list[TrainConfig] = field(default_factory=list)
    operation_plan: OperationPlanConfig = field(default_factory=OperationPlanConfig)

    @classmethod
    def from_dict(cls, data: JsonDict) -> ScenarioConfig:
        passenger_demand_scale = float(data.get("passengerDemandScale", 1.0))
        if passenger_demand_scale < 0.0:
            raise ValueError("passengerDemandScale must be non-negative")
        operation = data.get("operationPlan", {})
        return cls(
            line_id=data["lineId"],
            name=data["name"],
            start_time_ms=data["startTimeMs"],
            tick_seconds=data.get("tickSeconds", 1.0),
            use_dynamic_programming_profile=bool(data.get("useDynamicProgrammingProfile", True)),
            auto_spawn_trains=bool(data.get("autoSpawnTrains", False)),
            line_scope_file=data.get("lineScopeFile"),
            passenger_demand_scale=passenger_demand_scale,
            passenger_use_poisson=bool(data.get("passengerUsePoisson", True)),
            operation_plan=OperationPlanConfig(
                enabled=bool(operation.get("enabled", False)),
                direction=str(operation.get("direction", "UP")).upper(),
                start_time_ms=(
                    int(operation["startTimeMs"])
                    if operation.get("startTimeMs") is not None else None
                ),
                end_time_ms=(
                    int(operation["endTimeMs"])
                    if operation.get("endTimeMs") is not None else None
                ),
                max_duties=max(1, int(operation.get("maxDuties", 8))),
                ready_lead_sec=max(0.0, float(operation.get("readyLeadSec", 30.0))),
                turnback_layover_sec=max(0.0, float(operation.get("turnbackLayoverSec", 60.0))),
                profile_prewarm_enabled=bool(operation.get("profilePrewarmEnabled", True)),
                profile_prewarm_timeout_sec=max(
                    0.0, float(operation.get("profilePrewarmTimeoutSec", 180.0))
                ),
                profile_reference_load_pax=max(
                    0, int(operation.get("profileReferenceLoadPax", 700))
                ),
                runtime_recovery_margin_sec=max(
                    0.0, float(operation.get("runtimeRecoveryMarginSec", 5.0))
                ),
                door_cycle_allowance_sec=max(
                    0.0, float(operation.get("doorCycleAllowanceSec", 2.0))
                ),
                measurement_start_time_ms=(
                    int(operation["measurementStartTimeMs"])
                    if operation.get("measurementStartTimeMs") is not None else None
                ),
                measurement_end_time_ms=(
                    int(operation["measurementEndTimeMs"])
                    if operation.get("measurementEndTimeMs") is not None else None
                ),
                clearance_end_time_ms=(
                    int(operation["clearanceEndTimeMs"])
                    if operation.get("clearanceEndTimeMs") is not None else None
                ),
                clearance_sec=max(0.0, float(operation.get("clearanceSec", 300.0))),
                stuck_threshold_sec=max(
                    1.0, float(operation.get("stuckThresholdSec", 300.0))
                ),
                max_schedule_deviation_sec=max(
                    0.0, float(operation.get("maxScheduleDeviationSec", 120.0))
                ),
            ),
            trains=[
                TrainConfig(
                    train_id=item["trainId"],
                    line_id=item["lineId"],
                    initial_station_code=item["initialStationCode"],
                    direction=item["direction"],
                    capacity_pax=item.get("capacityPax", 1460),
                    initial_load_pax=item.get("initialLoadPax", 0),
                )
                for item in data.get("trains", [])
            ],
        )

    @classmethod
    def load(cls, path: str | Path) -> ScenarioConfig:
        with Path(path).open("r", encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))
