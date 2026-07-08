from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PassengerDemandProfile:
    station_id: str
    direction: str
    start_sec: int
    end_sec: int
    arrival_rate_pax_per_min: float
    alighting_ratio: float = 0.12

    def active_at(self, sim_time_ms: int) -> bool:
        sim_sec = sim_time_ms / 1000
        return self.start_sec <= sim_sec < self.end_sec


@dataclass
class PlatformCrowdState:
    station_id: str
    direction: str
    waiting_pax: int = 0
    platform_area_m2: float = 120.0
    left_behind_pax: int = 0

    @property
    def platform_density_pax_per_m2(self) -> float:
        if self.platform_area_m2 <= 0:
            return 0.0
        return self.waiting_pax / self.platform_area_m2

    @property
    def crowding_level(self) -> str:
        density = self.platform_density_pax_per_m2
        if density >= 4.0:
            return "CRITICAL"
        if density >= 2.5:
            return "HIGH"
        if density >= 1.2:
            return "MEDIUM"
        return "LOW"


@dataclass
class TrainLoadState:
    train_id: str
    onboard_pax: int
    capacity_pax: int
    average_passenger_weight_kg: float = 65.0

    @property
    def load_factor(self) -> float:
        if self.capacity_pax <= 0:
            return 0.0
        return self.onboard_pax / self.capacity_pax

    @property
    def vehicle_load_kg(self) -> float:
        return self.onboard_pax * self.average_passenger_weight_kg


@dataclass(frozen=True)
class BoardingResult:
    station_id: str
    direction: str
    train_id: str
    arrivals: int
    boarding: int
    alighting: int
    waiting: int
    left_behind: int
    updated_load: TrainLoadState


@dataclass(frozen=True)
class DwellTimeConfig:
    base_dwell_sec: float = 30.0
    alpha_boarding_sec_per_pax: float = 0.08
    beta_alighting_sec_per_pax: float = 0.06
    gamma_density_sec_per_pax_m2: float = 2.0
    min_dwell_sec: float = 20.0
    max_dwell_sec: float = 90.0
    door_capacity_pax_per_sec: float = 3.0


@dataclass(frozen=True)
class DwellPlan:
    train_id: str
    station_id: str
    planned_dwell_sec: float
    estimated_dwell_sec: float
    dispatch_hold_sec: float
    door_fault_extra_sec: float
    can_depart: bool
    blocking_reason: str | None = None


class PassengerFlowGenerator:
    """Deterministic passenger arrival generator with fractional carry-over."""

    def __init__(self, profiles: list[PassengerDemandProfile]) -> None:
        self.profiles = profiles
        self._residual_by_key: dict[tuple[str, str], float] = {}

    def arrivals(self, station_id: str, direction: str, sim_time_ms: int, dt_sec: float) -> int:
        rate = sum(
            profile.arrival_rate_pax_per_min
            for profile in self.profiles
            if profile.station_id == station_id and profile.direction == direction and profile.active_at(sim_time_ms)
        )
        key = (station_id, direction)
        expected = rate * dt_sec / 60.0 + self._residual_by_key.get(key, 0.0)
        arrivals = int(expected)
        self._residual_by_key[key] = expected - arrivals
        return arrivals

    def alighting_ratio(self, station_id: str, direction: str, sim_time_ms: int) -> float:
        ratios = [
            profile.alighting_ratio
            for profile in self.profiles
            if profile.station_id == station_id and profile.direction == direction and profile.active_at(sim_time_ms)
        ]
        if not ratios:
            return 0.12
        return sum(ratios) / len(ratios)


class StationService:
    def __init__(
        self,
        flow_generator: PassengerFlowGenerator,
        dwell_config: DwellTimeConfig | None = None,
    ) -> None:
        self.flow_generator = flow_generator
        self.dwell_config = dwell_config or DwellTimeConfig()
        self.platforms: dict[tuple[str, str], PlatformCrowdState] = {}

    def ensure_platform(self, station_id: str, direction: str, platform_area_m2: float = 120.0) -> PlatformCrowdState:
        key = (station_id, direction)
        if key not in self.platforms:
            self.platforms[key] = PlatformCrowdState(station_id, direction, platform_area_m2=platform_area_m2)
        return self.platforms[key]

    def update_arrivals(self, sim_time_ms: int, dt_sec: float) -> dict[tuple[str, str], int]:
        arrivals_by_platform: dict[tuple[str, str], int] = {}
        active_keys = {
            (profile.station_id, profile.direction)
            for profile in self.flow_generator.profiles
            if profile.active_at(sim_time_ms)
        }
        for station_id, direction in active_keys:
            platform = self.ensure_platform(station_id, direction)
            arrivals = self.flow_generator.arrivals(station_id, direction, sim_time_ms, dt_sec)
            platform.waiting_pax += arrivals
            arrivals_by_platform[(station_id, direction)] = arrivals
        return arrivals_by_platform

    def process_train_stop(
        self,
        *,
        sim_time_ms: int,
        station_id: str,
        direction: str,
        train_load: TrainLoadState,
        dispatch_hold_sec: float = 0.0,
        door_fault_extra_sec: float = 0.0,
        platform_area_m2: float = 120.0,
    ) -> tuple[BoardingResult, DwellPlan]:
        platform = self.ensure_platform(station_id, direction, platform_area_m2)
        cfg = self.dwell_config
        alighting = min(
            train_load.onboard_pax,
            int(round(train_load.onboard_pax * self.flow_generator.alighting_ratio(station_id, direction, sim_time_ms))),
        )
        remaining_capacity = max(train_load.capacity_pax - (train_load.onboard_pax - alighting), 0)
        door_limit = max(int(cfg.door_capacity_pax_per_sec * cfg.base_dwell_sec), 0)
        boarding = min(platform.waiting_pax, remaining_capacity, door_limit)
        platform.waiting_pax -= boarding
        platform.left_behind_pax = platform.waiting_pax

        updated_load = TrainLoadState(
            train_id=train_load.train_id,
            onboard_pax=train_load.onboard_pax - alighting + boarding,
            capacity_pax=train_load.capacity_pax,
            average_passenger_weight_kg=train_load.average_passenger_weight_kg,
        )
        dwell_raw = (
            cfg.base_dwell_sec
            + cfg.alpha_boarding_sec_per_pax * boarding
            + cfg.beta_alighting_sec_per_pax * alighting
            + cfg.gamma_density_sec_per_pax_m2 * platform.platform_density_pax_per_m2
            + dispatch_hold_sec
            + door_fault_extra_sec
        )
        estimated = min(max(dwell_raw, cfg.min_dwell_sec), cfg.max_dwell_sec)
        can_depart = door_fault_extra_sec <= 0
        blocking_reason = None if can_depart else "DOOR_FAULT"
        result = BoardingResult(
            station_id=station_id,
            direction=direction,
            train_id=train_load.train_id,
            arrivals=0,
            boarding=boarding,
            alighting=alighting,
            waiting=platform.waiting_pax,
            left_behind=platform.left_behind_pax,
            updated_load=updated_load,
        )
        plan = DwellPlan(
            train_id=train_load.train_id,
            station_id=station_id,
            planned_dwell_sec=cfg.base_dwell_sec,
            estimated_dwell_sec=estimated,
            dispatch_hold_sec=dispatch_hold_sec,
            door_fault_extra_sec=door_fault_extra_sec,
            can_depart=can_depart,
            blocking_reason=blocking_reason,
        )
        return result, plan

