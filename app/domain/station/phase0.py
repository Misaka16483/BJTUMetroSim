"""Phase 0: default station states and metric structure for Member D."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DefaultStationState:
    station_id: str
    station_name: str
    direction: str = "UP"
    platform_area_m2: float = 120.0
    waiting_pax: int = 0
    platform_density_pax_per_m2: float = 0.0
    crowding_level: str = "LOW"
    timestamp_ms: int = 0


@dataclass(frozen=True)
class StationMetricNames:
    WAITING_PAX: str = "memberD.waitingPax"
    CROWDING_LEVEL: str = "memberD.crowdingLevel"
    PLATFORM_DENSITY: str = "memberD.platformDensity"
    BOARDING_COUNT: str = "memberD.boardingCount"
    ALIGHTING_COUNT: str = "memberD.alightingCount"
    LEFT_BEHIND_PAX: str = "memberD.leftBehindPax"
    DWELL_SECONDS: str = "memberD.dwellSeconds"
    LOAD_FACTOR: str = "memberD.loadFactor"
    VEHICLE_LOAD_KG: str = "memberD.vehicleLoadKg"
    POWER_REQUESTED_KW: str = "memberD.requestedPowerKw"
    POWER_TRACTION_LIMIT: str = "memberD.tractionLimitRatio"
    POWER_ENERGY_KWH: str = "memberD.energyKwh"
    POWER_REGEN_KWH: str = "memberD.regenEnergyKwh"
    DISPATCH_DECISION_COUNT: str = "memberD.dispatchDecisionCount"
    TOTAL_WAITING_PAX: str = "memberD.totalWaitingPax"


def compute_crowding_level(density: float) -> str:
    if density >= 4.0:
        return "CRITICAL"
    if density >= 2.5:
        return "HIGH"
    if density >= 1.2:
        return "MEDIUM"
    return "LOW"


def generate_default_station_state(
    station_id: str,
    station_name: str,
    direction: str = "UP",
    platform_area_m2: float = 120.0,
    timestamp_ms: int = 0,
) -> DefaultStationState:
    return DefaultStationState(
        station_id=station_id,
        station_name=station_name,
        direction=direction,
        platform_area_m2=platform_area_m2,
        timestamp_ms=timestamp_ms,
    )


LINE9_STATIONS: list[dict[str, str]] = [
    {"id": "S-GGZ", "name": "郭公庄"},
    {"id": "S-FSP", "name": "丰台"},
    {"id": "S-KYL", "name": "科怡路"},
    {"id": "S-FTN", "name": "丰台科技园"},
    {"id": "S-FTD", "name": "丰台大街"},
    {"id": "S-QLZ", "name": "七里庄"},
    {"id": "S-LLQ", "name": "六里桥"},
    {"id": "S-LLE", "name": "六里桥东"},
    {"id": "S-BWR", "name": "北京西站"},
    {"id": "S-JBG", "name": "军事博物馆"},
    {"id": "S-BDZ", "name": "白堆子"},
    {"id": "S-BQS", "name": "白石桥南"},
    {"id": "S-GTG", "name": "国家图书馆"},
]
