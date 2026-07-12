from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from enum import Enum


# ── 日型枚举 ──
class DayType(str, Enum):
    MON_THU = "MON_THU"       # 周一至周四 基准
    FRI = "FRI"               # 周五 +8%
    SAT = "SAT"               # 周六 -33%
    SUN = "SUN"               # 周日 -40%


# 日型系数
DAY_TYPE_COEFFICIENTS: dict[DayType, float] = {
    DayType.MON_THU: 1.00,
    DayType.FRI: 1.08,
    DayType.SAT: 0.67,
    DayType.SUN: 0.60,
}

# ── 六时段定义（秒 from midnight） ──
# 起步 5:00-7:00 | 早峰 7:00-9:00 | 平峰 9:00-17:00 | 晚峰 17:00-19:00 | 回落 19:00-22:00 | 深夜 22:00-24:00
TIME_PERIODS = [
    ("EARLY",    5*3600,  7*3600,  0.15),
    ("AM_PEAK",  7*3600,  9*3600,  1.45),
    ("MIDDAY",   9*3600, 17*3600,  0.55),
    ("PM_PEAK", 17*3600, 19*3600,  1.15),
    ("EVENING", 19*3600, 22*3600,  0.35),
    ("NIGHT",   22*3600, 24*3600,  0.08),
]


@dataclass(frozen=True)
class StationFlowConfig:
    """单站客流配置：基准到达率 + 各站下车比例."""
    station_id: str
    base_arrival_rate_pax_per_min: float          # 平峰小时基准到达率
    alighting_ratio: float = 0.12                 # 本站下车比例
    direction: str = "UP"


@dataclass(frozen=True)
class FlowScenario:
    """运行场景：日型 + 线路级系数."""
    day_type: DayType = DayType.MON_THU
    line_scale: float = 1.0                       # 线路级缩放系数，默认 1.0
    random_seed: int | None = None


@dataclass
class PlatformCrowdState:
    station_id: str
    direction: str
    waiting_pax: int = 0
    platform_area_m2: float = 120.0
    left_behind_pax: int = 0
    _total_arrived_pax: int = 0                   # 累计到达乘客（KPI用）
    _total_waited_sec: float = 0.0                # 累计等待秒数（KPI用）

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


class PoissonPassengerFlowGenerator:
    """基于泊松分布的客流生成器，支持多时段 + 日型系数."""

    def __init__(
        self,
        station_configs: list[StationFlowConfig],
        scenario: FlowScenario | None = None,
        use_poisson: bool = True,
    ) -> None:
        self._station_configs = station_configs
        self._scenario = scenario or FlowScenario()
        self._use_poisson = use_poisson
        self._rng = __import__("numpy").random.default_rng(self._scenario.random_seed)

    @property
    def day_coefficient(self) -> float:
        return DAY_TYPE_COEFFICIENTS.get(self._scenario.day_type, 1.0)

    def _station_config(self, station_id: str, direction: str) -> StationFlowConfig | None:
        for cfg in self._station_configs:
            if cfg.station_id == station_id and cfg.direction == direction:
                return cfg
        return None

    def _period_multiplier(self, sim_time_ms: int) -> float:
        """根据仿真时刻返回六时段系数."""
        sim_sec = (sim_time_ms // 1000) % 86400
        for _name, start, end, coeff in TIME_PERIODS:
            if start <= sim_sec < end:
                return coeff
        return 0.0  # 0:00-5:00 无运营

    def arrival_rate_pax_per_min(
        self, station_id: str, direction: str, sim_time_ms: int
    ) -> float:
        """计算某站某方向当前时刻的到达率 (pax/min)."""
        cfg = self._station_config(station_id, direction)
        if cfg is None:
            return 0.0
        period_coeff = self._period_multiplier(sim_time_ms)
        if period_coeff <= 0:
            return 0.0
        return (
            cfg.base_arrival_rate_pax_per_min
            * period_coeff
            * self.day_coefficient
            * self._scenario.line_scale
        )

    def arrivals(
        self, station_id: str, direction: str, sim_time_ms: int, dt_sec: float
    ) -> int:
        """生成 dt_sec 内的进站乘客数."""
        rate = self.arrival_rate_pax_per_min(station_id, direction, sim_time_ms)
        if rate <= 0:
            return 0
        lam = rate * dt_sec / 60.0
        if self._use_poisson:
            return int(self._rng.poisson(lam))
        else:
            # 确定性模式：取整 + 余数累积
            key = (station_id, direction)
            residual = getattr(self, "_residual_by_key", {}).get(key, 0.0)
            expected = lam + residual
            result = int(expected)
            if not hasattr(self, "_residual_by_key"):
                self._residual_by_key: dict[tuple[str, str], float] = {}
            self._residual_by_key[key] = expected - result
            return result

    def alighting_ratio(self, station_id: str, direction: str, _sim_time_ms: int = 0) -> float:
        """获取某站下车比例."""
        cfg = self._station_config(station_id, direction)
        if cfg is None:
            return 0.12
        return cfg.alighting_ratio


class StationService:
    def __init__(
        self,
        flow_generator: PoissonPassengerFlowGenerator,
        dwell_config: DwellTimeConfig | None = None,
    ) -> None:
        self.flow_generator = flow_generator
        self.dwell_config = dwell_config or DwellTimeConfig()
        self.platforms: dict[tuple[str, str], PlatformCrowdState] = {}
        for config in getattr(flow_generator, "_station_configs", []):
            self.ensure_platform(config.station_id, config.direction)
        # KPI 累积量
        self._total_boarded: int = 0
        self._total_alighted: int = 0

    def ensure_platform(
        self, station_id: str, direction: str, platform_area_m2: float = 120.0
    ) -> PlatformCrowdState:
        key = (station_id, direction)
        if key not in self.platforms:
            self.platforms[key] = PlatformCrowdState(
                station_id, direction, platform_area_m2=platform_area_m2
            )
        return self.platforms[key]

    def update_arrivals(
        self, sim_time_ms: int, dt_sec: float
    ) -> dict[tuple[str, str], int]:
        """每 tick 更新所有站台的进站客流."""
        arrivals_by_platform: dict[tuple[str, str], int] = {}
        # 遍历所有已有站台（不局限于 active profile）
        for key in list(self.platforms.keys()):
            station_id, direction = key
            platform = self.platforms[key]
            arrivals = self.flow_generator.arrivals(station_id, direction, sim_time_ms, dt_sec)
            if arrivals > 0:
                platform.waiting_pax += arrivals
                platform._total_arrived_pax += arrivals
                arrivals_by_platform[key] = arrivals
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

        # 下车
        alighting_ratio = self.flow_generator.alighting_ratio(station_id, direction, sim_time_ms)
        alighting = min(
            train_load.onboard_pax,
            max(int(round(train_load.onboard_pax * alighting_ratio)), 0),
        )
        self._total_alighted += alighting

        # 上车（受剩余容量 + 车门通过能力 + 候车人数三重约束）
        remaining_capacity = max(train_load.capacity_pax - (train_load.onboard_pax - alighting), 0)
        door_limit = max(int(cfg.door_capacity_pax_per_sec * cfg.base_dwell_sec), 0)
        boarding = min(platform.waiting_pax, remaining_capacity, door_limit)

        platform.waiting_pax -= boarding
        platform.left_behind_pax = platform.waiting_pax
        self._total_boarded += boarding

        updated_load = TrainLoadState(
            train_id=train_load.train_id,
            onboard_pax=train_load.onboard_pax - alighting + boarding,
            capacity_pax=train_load.capacity_pax,
            average_passenger_weight_kg=train_load.average_passenger_weight_kg,
        )

        # 停站时间计算
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

    def exchange_open_door_passengers(
        self,
        *,
        station_id: str,
        direction: str,
        train_load: TrainLoadState,
        requested_alighting: int,
        requested_boarding: int,
        platform_area_m2: float = 120.0,
    ) -> BoardingResult:
        """Apply one short passenger-exchange slice while a train door is open."""
        platform = self.ensure_platform(station_id, direction, platform_area_m2)
        alighting = min(train_load.onboard_pax, max(0, int(requested_alighting)))
        remaining_capacity = max(train_load.capacity_pax - (train_load.onboard_pax - alighting), 0)
        boarding = min(platform.waiting_pax, remaining_capacity, max(0, int(requested_boarding)))
        platform.waiting_pax -= boarding
        platform.left_behind_pax = platform.waiting_pax
        self._total_alighted += alighting
        self._total_boarded += boarding
        updated_load = TrainLoadState(
            train_id=train_load.train_id,
            onboard_pax=train_load.onboard_pax - alighting + boarding,
            capacity_pax=train_load.capacity_pax,
            average_passenger_weight_kg=train_load.average_passenger_weight_kg,
        )
        return BoardingResult(
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


# ── 兼容旧版 member_d_demo 接口 ──

@dataclass(frozen=True, init=False)
class PassengerDemandProfile:
    """旧版客流需求配置（兼容 member_d_demo）。"""
    station_id: str
    direction: str
    start_sec: int
    end_sec: int
    arrival_rate_pax_per_min: float
    alighting_ratio: float = 0.12

    def __init__(
        self,
        station_id: str | None = None,
        direction: str = "UP",
        start_sec: int | None = None,
        end_sec: int | None = None,
        arrival_rate_pax_per_min: float | None = None,
        alighting_ratio: float = 0.12,
        *,
        station_code: str | None = None,
        start_time_s: int | None = None,
        end_time_s: int | None = None,
        hourly_arrival_rate: float | None = None,
    ) -> None:
        resolved_station_id = station_id or station_code
        if not resolved_station_id:
            raise ValueError("station_id or station_code is required")
        resolved_rate = arrival_rate_pax_per_min
        if resolved_rate is None and hourly_arrival_rate is not None:
            resolved_rate = hourly_arrival_rate / 60.0
        object.__setattr__(self, "station_id", resolved_station_id)
        object.__setattr__(self, "direction", direction)
        object.__setattr__(self, "start_sec", int(start_sec if start_sec is not None else start_time_s or 0))
        object.__setattr__(self, "end_sec", int(end_sec if end_sec is not None else end_time_s or 86400))
        object.__setattr__(self, "arrival_rate_pax_per_min", float(resolved_rate or 0.0))
        object.__setattr__(self, "alighting_ratio", float(alighting_ratio))

    @property
    def station_code(self) -> str:
        return self.station_id

    @property
    def start_time_s(self) -> int:
        return self.start_sec

    @property
    def end_time_s(self) -> int:
        return self.end_sec

    @property
    def hourly_arrival_rate(self) -> float:
        return self.arrival_rate_pax_per_min * 60.0

    def active_at(self, sim_time_ms: int) -> bool:
        sim_sec = sim_time_ms / 1000.0
        return self.start_sec <= sim_sec < self.end_sec


class PassengerFlowGenerator(PoissonPassengerFlowGenerator):
    """
    旧版客流生成器兼容封装。
    member_d_demo 仍通过此接口创建 StationService，内部转译为 StationFlowConfig。
    """

    def __init__(self, profiles: list[PassengerDemandProfile]) -> None:
        self.profiles = profiles
        self._station_configs = [
            StationFlowConfig(
                station_id=profile.station_id,
                direction=profile.direction,
                base_arrival_rate_pax_per_min=profile.arrival_rate_pax_per_min,
                alighting_ratio=profile.alighting_ratio,
            )
            for profile in profiles
        ]
        super().__init__(
            self._station_configs,
            FlowScenario(day_type=DayType.MON_THU),
            use_poisson=False,
        )
        self._residual_by_key: dict[tuple[str, str], float] = {}

    def arrivals(self, station_id: str, direction: str, sim_time_ms: int, dt_sec: float) -> int:
        rate = sum(
            profile.arrival_rate_pax_per_min
            for profile in self.profiles
            if profile.station_id == station_id
            and profile.direction == direction
            and profile.active_at(sim_time_ms)
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
            if profile.station_id == station_id
            and profile.direction == direction
            and profile.active_at(sim_time_ms)
        ]
        return sum(ratios) / len(ratios) if ratios else 0.12



