from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from app.domain.station.services import (
    DayType,
    DwellTimeConfig,
    FlowScenario,
    PoissonPassengerFlowGenerator,
    StationFlowConfig,
    TIME_PERIODS,
)


JsonDict = dict[str, Any]
PASSENGER_PROFILE_SCHEMA_VERSION = "PASSENGER-PROFILE-V1"
DEFAULT_LINE9_PROFILE_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "passenger_profiles"
    / "line9_calibrated_synthetic_v1.json"
)


class PassengerProfileError(ValueError):
    pass


@dataclass(frozen=True)
class PassengerProfile:
    profile_id: str
    schema_version: str
    line_id: str
    quality: str
    station_configs: tuple[StationFlowConfig, ...]
    flow_scenario: FlowScenario
    dwell_config: DwellTimeConfig
    use_poisson: bool
    train_capacity_pax: int
    average_passenger_mass_kg: float
    metadata: JsonDict

    def estimated_daily_arrivals(
        self,
        *,
        station_codes: Iterable[str] | None = None,
    ) -> float:
        selected = set(station_codes) if station_codes is not None else None
        generator = PoissonPassengerFlowGenerator(
            list(self.station_configs),
            self.flow_scenario,
            use_poisson=False,
        )
        total = 0.0
        for _name, start_sec, end_sec, _coefficient in TIME_PERIODS:
            midpoint_ms = ((start_sec + end_sec) // 2) * 1000
            duration_min = (end_sec - start_sec) / 60.0
            for config in self.station_configs:
                if selected is not None and config.station_id not in selected:
                    continue
                total += generator.arrival_rate_pax_per_min(
                    config.station_id,
                    config.direction,
                    midpoint_ms,
                ) * duration_min
        return total


def load_passenger_profile(path: str | Path = DEFAULT_LINE9_PROFILE_PATH) -> PassengerProfile:
    profile_path = Path(path)
    with profile_path.open("r", encoding="utf-8") as stream:
        payload = json.load(stream)
    if payload.get("schemaVersion") != PASSENGER_PROFILE_SCHEMA_VERSION:
        raise PassengerProfileError("unsupported passenger profile schema")

    direction_factors = payload.get("directionPeriodFactors", {})
    configs: list[StationFlowConfig] = []
    station_codes: list[str] = []
    for station in payload.get("stations", []):
        station_code = str(station.get("stationCode", ""))
        station_codes.append(station_code)
        platform_area_m2 = float(station.get("effectivePlatformAreaM2", 120.0))
        directions = station.get("directions", {})
        for direction in ("UP", "DOWN"):
            if direction not in directions:
                raise PassengerProfileError(f"{station_code} is missing {direction} demand")
            demand = directions[direction]
            factors = tuple(
                (str(name), float(value))
                for name, value in direction_factors.get(direction, {}).items()
            )
            configs.append(StationFlowConfig(
                station_id=station_code,
                base_arrival_rate_pax_per_min=float(demand["baseArrivalRatePaxPerMin"]),
                alighting_ratio=float(demand["alightingRatio"]),
                direction=direction,
                platform_area_m2=platform_area_m2,
                period_factors=factors,
            ))

    expected_codes = tuple(str(item) for item in payload.get("expectedStationCodes", ()))
    if expected_codes and tuple(station_codes) != expected_codes:
        raise PassengerProfileError("station order does not match expectedStationCodes")
    if len(station_codes) != len(set(station_codes)):
        raise PassengerProfileError("passenger profile contains duplicate stations")
    if not configs:
        raise PassengerProfileError("passenger profile contains no station demand")

    scenario = payload.get("scenario", {})
    dwell = payload.get("dwell", {})
    rolling_stock = payload.get("rollingStock", {})
    profile = PassengerProfile(
        profile_id=str(payload["profileId"]),
        schema_version=str(payload["schemaVersion"]),
        line_id=str(payload["lineId"]),
        quality=str(payload.get("quality", "SYNTHETIC")),
        station_configs=tuple(configs),
        flow_scenario=FlowScenario(
            day_type=DayType(str(scenario.get("dayType", DayType.MON_THU.value))),
            line_scale=float(scenario.get("lineScale", 1.0)),
            random_seed=int(scenario["randomSeed"]) if scenario.get("randomSeed") is not None else None,
        ),
        dwell_config=DwellTimeConfig(
            base_dwell_sec=float(dwell.get("baseDwellSec", 30.0)),
            alpha_boarding_sec_per_pax=float(dwell.get("alphaBoardingSecPerPax", 0.08)),
            beta_alighting_sec_per_pax=float(dwell.get("betaAlightingSecPerPax", 0.06)),
            gamma_density_sec_per_pax_m2=float(dwell.get("gammaDensitySecPerPaxM2", 2.0)),
            min_dwell_sec=float(dwell.get("minDwellSec", 20.0)),
            max_dwell_sec=float(dwell.get("maxDwellSec", 90.0)),
            door_capacity_pax_per_sec=float(dwell.get("doorCapacityPaxPerSec", 3.0)),
        ),
        use_poisson=bool(scenario.get("usePoisson", True)),
        train_capacity_pax=int(rolling_stock.get("trainCapacityPax", 1_460)),
        average_passenger_mass_kg=float(rolling_stock.get("averagePassengerMassKg", 65.0)),
        metadata=dict(payload.get("calibration", {})),
    )
    if profile.train_capacity_pax <= 0 or profile.average_passenger_mass_kg <= 0:
        raise PassengerProfileError("rolling-stock passenger assumptions must be positive")
    return profile
