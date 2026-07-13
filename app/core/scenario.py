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
    capacity_pax: int = 600
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


@dataclass
class ScenarioConfig:
    line_id: str
    name: str
    start_time_ms: int  # e.g. 8:00:00 = 8 * 3600 * 1000
    tick_seconds: float = 1.0
    use_dynamic_programming_profile: bool = True
    auto_spawn_trains: bool = False
    line_scope_file: str | None = None
    trains: list[TrainConfig] = field(default_factory=list)
    operation_plan: OperationPlanConfig = field(default_factory=OperationPlanConfig)

    @classmethod
    def from_dict(cls, data: JsonDict) -> ScenarioConfig:
        operation = data.get("operationPlan", {})
        return cls(
            line_id=data["lineId"],
            name=data["name"],
            start_time_ms=data["startTimeMs"],
            tick_seconds=data.get("tickSeconds", 1.0),
            use_dynamic_programming_profile=bool(data.get("useDynamicProgrammingProfile", True)),
            auto_spawn_trains=bool(data.get("autoSpawnTrains", False)),
            line_scope_file=data.get("lineScopeFile"),
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
            ),
            trains=[
                TrainConfig(
                    train_id=item["trainId"],
                    line_id=item["lineId"],
                    initial_station_code=item["initialStationCode"],
                    direction=item["direction"],
                    capacity_pax=item.get("capacityPax", 600),
                    initial_load_pax=item.get("initialLoadPax", 0),
                )
                for item in data.get("trains", [])
            ],
        )

    @classmethod
    def load(cls, path: str | Path) -> ScenarioConfig:
        with Path(path).open("r", encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))
