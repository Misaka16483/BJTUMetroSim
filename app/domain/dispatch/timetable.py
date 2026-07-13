"""运行图模型与生成服务.

模块7核心：根据车站列表 + 发车间隔配置 → 生成多列车运行图.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


JsonDict = dict[str, Any]


# ── 六时段发车间隔配置（秒） ──
# 与 TIME_PERIODS 一一对应
DEFAULT_HEADWAY_CONFIG: dict[str, float] = {
    "EARLY":    480.0,   # 起步期 8 分钟
    "AM_PEAK":  120.0,   # 早高峰 2 分钟
    "MIDDAY":   300.0,   # 平峰期 5 分钟
    "PM_PEAK":  150.0,   # 晚高峰 2.5 分钟
    "EVENING":  360.0,   # 回落期 6 分钟
    "NIGHT":    600.0,   # 深夜 10 分钟
}


@dataclass(frozen=True)
class HeadwayConfig:
    """时段-发车间隔映射."""
    period_headway_sec: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_HEADWAY_CONFIG))
    min_headway_sec: float = 90.0              # 最小追踪间隔（信号约束）
    default_headway_sec: float = 300.0         # 未知时段的默认间隔

    def headway_at(self, sim_time_ms: int) -> float:
        """根据仿真时刻返回当前应使用的发车间隔."""
        from app.domain.station.services import TIME_PERIODS
        sim_sec = (sim_time_ms // 1000) % 86400
        for name, start, end, _coeff in TIME_PERIODS:
            if start <= sim_sec < end:
                return max(self.period_headway_sec.get(name, self.default_headway_sec), self.min_headway_sec)
        return self.default_headway_sec


@dataclass(frozen=True)
class ScheduledStop:
    """运行图中一个停站."""
    station_code: str
    station_name: str
    station_index: int
    planned_arrival_s: float       # 计划到达秒（from midnight）
    planned_departure_s: float     # 计划发车秒
    distance_from_origin_m: float  # 距起点的累计里程
    is_skipped: bool = False       # 调度命令跳站


@dataclass
class TrainService:
    """一列车的一次完整运行任务."""
    service_id: str
    train_id: str
    line_id: str
    direction: str
    duty_id: str = ""
    stops: list[ScheduledStop] = field(default_factory=list)
    origin_station_code: str = ""
    terminal_station_code: str = ""

    @property
    def planned_run_time_s(self) -> float:
        """全程计划运行时间."""
        if not self.stops:
            return 0.0
        return self.stops[-1].planned_arrival_s - self.stops[0].planned_departure_s

    @property
    def stop_count(self) -> int:
        return len(self.stops)

    def planned_arrival_at_station(self, station_index: int) -> float | None:
        for stop in self.stops:
            if stop.station_index == station_index:
                return stop.planned_arrival_s
        return None

    def planned_departure_at_station(self, station_index: int) -> float | None:
        for stop in self.stops:
            if stop.station_index == station_index:
                return stop.planned_departure_s
        return None

    def to_dict(self) -> JsonDict:
        return {
            "serviceId": self.service_id,
            "trainId": self.train_id,
            "lineId": self.line_id,
            "direction": self.direction,
            "dutyId": self.duty_id,
            "originStationCode": self.origin_station_code,
            "terminalStationCode": self.terminal_station_code,
            "plannedRunTimeS": round(self.planned_run_time_s, 1),
            "stops": [
                {
                    "stationCode": s.station_code,
                    "stationName": s.station_name,
                    "stationIndex": s.station_index,
                    "plannedArrivalS": round(s.planned_arrival_s, 1),
                    "plannedDepartureS": round(s.planned_departure_s, 1),
                    "distanceFromOriginM": round(s.distance_from_origin_m, 1),
                    "isSkipped": s.is_skipped,
                }
                for s in self.stops
            ],
        }


@dataclass
class Timetable:
    """一条线路一个方向的一日运行图."""
    timetable_id: str
    line_id: str
    direction: str
    valid_from_s: float          # 生效起始秒（from midnight）
    valid_to_s: float            # 生效结束秒
    services: list[TrainService] = field(default_factory=list)
    headway_config: HeadwayConfig = field(default_factory=HeadwayConfig)

    @property
    def service_count(self) -> int:
        return len(self.services)

    def to_dict(self) -> JsonDict:
        return {
            "timetableId": self.timetable_id,
            "lineId": self.line_id,
            "direction": self.direction,
            "validFromS": round(self.valid_from_s, 1),
            "validToS": round(self.valid_to_s, 1),
            "serviceCount": self.service_count,
            "services": [svc.to_dict() for svc in self.services],
        }


@dataclass
class TrainDuty:
    """A physical trainset's ordered work for one simulated operating day."""
    duty_id: str
    train_id: str
    service_ids: list[str]
    planned_start_s: float
    planned_end_s: float
    lifecycle_state: str = "IN_DEPOT"
    active_service_id: str | None = None

    def to_dict(self) -> JsonDict:
        return {
            "dutyId": self.duty_id,
            "trainId": self.train_id,
            "serviceIds": list(self.service_ids),
            "plannedStartS": round(self.planned_start_s, 1),
            "plannedEndS": round(self.planned_end_s, 1),
            "lifecycleState": self.lifecycle_state,
            "activeServiceId": self.active_service_id,
        }


class TimetableService:
    """运行图生成器.

    输入：车站列表（含区间里程和运行时分）、发车间隔配置
    输出：Timetable（多条 TrainService）
    """

    def __init__(
        self,
        headway_config: HeadwayConfig | None = None,
        default_run_time_per_interval_s: float = 150.0,
        base_dwell_sec: float = 30.0,
    ) -> None:
        self.headway_config = headway_config or HeadwayConfig()
        self.default_run_time_s = default_run_time_per_interval_s
        self.base_dwell_sec = base_dwell_sec
        self._service_counter: int = 0

    def reset(self) -> None:
        self._service_counter = 0

    def generate(
        self,
        timetable_id: str,
        line_id: str,
        direction: str,
        stations: list[JsonDict],
        start_time_s: float,           # 首班车发车秒
        end_time_s: float,             # 末班车发车秒
        interval_distance_m: list[float] | None = None,
    ) -> Timetable:
        """生成指定参数下的运行图.

        Args:
            stations: 按里程排序的车站列表 [{code, name, mileageM, dwellSeconds, ...}, ...]
            start_time_s: 首班车计划发车时刻 (秒 from midnight)
            end_time_s: 末班车计划发车时刻 (秒 from midnight)
            interval_distance_m: 区间距离列表, 长度 = len(stations)-1, 默认从车站里程计算
        """
        n = len(stations)
        if n < 2:
            return Timetable(
                timetable_id=timetable_id, line_id=line_id, direction=direction,
                valid_from_s=start_time_s, valid_to_s=end_time_s,
            )

        # 计算站间运行时分
        if interval_distance_m is None:
            mileages = [float(s.get("mileageM", 0)) for s in stations]
            interval_distance_m = [
                abs(mileages[i + 1] - mileages[i]) for i in range(n - 1)
            ]

        # 每段区间运行时间（从 csv dwellSeconds 或默认值估算）
        interval_run_times: list[float] = []
        for i, dist in enumerate(interval_distance_m):
            # 假设平均速度 60km/h = 16.67 m/s
            est_time = max(dist / 16.67, self.default_run_time_s * 0.5)
            interval_run_times.append(est_time)

        # 一站的总停+行时间
        cumulative_times: list[float] = [0.0]
        for i in range(n - 1):
            dwell = float(stations[i].get("dwellSeconds", self.base_dwell_sec))
            cumulative_times.append(cumulative_times[-1] + dwell + interval_run_times[i])

        # 首站到各站的累计里程
        mileages = [float(s.get("mileageM", 0)) for s in stations]
        origin_m = mileages[0]
        cumulative_distance = [0.0] + [abs(mileages[i] - origin_m) for i in range(1, n)]

        # 生成车次
        services: list[TrainService] = []
        current_departure = start_time_s

        while current_departure < end_time_s:
            # 当前发车时刻对应的 headway
            current_ms = int(current_departure * 1000)
            headway = self.headway_config.headway_at(current_ms)

            self._service_counter += 1
            service_id = f"SVC-{self._service_counter:04d}"
            train_id = f"T{self._service_counter:03d}"

            stops: list[ScheduledStop] = []
            for i, station in enumerate(stations):
                arrival = current_departure + cumulative_times[i] if i > 0 else current_departure
                dwell = float(station.get("dwellSeconds", self.base_dwell_sec)) if i < n - 1 else 0
                departure = arrival + dwell if i < n - 1 else arrival
                stops.append(ScheduledStop(
                    station_code=str(station.get("code", "")),
                    station_name=str(station.get("name", "")),
                    station_index=int(station.get("stationIndex", i)),
                    planned_arrival_s=arrival,
                    planned_departure_s=departure,
                    distance_from_origin_m=cumulative_distance[i],
                ))

            services.append(TrainService(
                service_id=service_id,
                train_id=train_id,
                line_id=line_id,
                direction=direction,
                stops=stops,
                origin_station_code=str(stations[0].get("code", "")),
                terminal_station_code=str(stations[-1].get("code", "")),
            ))

            current_departure += headway

        return Timetable(
            timetable_id=timetable_id,
            line_id=line_id,
            direction=direction,
            valid_from_s=start_time_s,
            valid_to_s=end_time_s,
            services=services,
            headway_config=self.headway_config,
        )
