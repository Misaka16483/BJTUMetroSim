"""调度KPI统计：准点率、平均等待时间、满载率、延误恢复时间."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


JsonDict = dict[str, Any]


@dataclass
class TrainArrivalRecord:
    """一次到站的记录."""
    train_id: str
    station_index: int
    station_code: str
    planned_arrival_s: float
    actual_arrival_s: float
    delay_s: float       # actual - planned, >0 表示晚点
    direction: str = "UP"


@dataclass
class DispatchKpiSnapshot:
    """一次仿真运行的KPI汇总."""
    # 准点率
    total_stops: int = 0
    on_time_stops: int = 0               # 偏差 < 120s
    on_time_rate: float = 0.0

    # 平均等待时间
    total_boarded_pax: int = 0
    total_waited_sec: float = 0.0        # 所有乘客等待秒数总和
    avg_wait_sec: float = 0.0

    # 满载率
    max_load_factor: float = 0.0
    avg_load_factor: float = 0.0
    overload_events: int = 0             # 满载率 > 120% 次数

    # 延误恢复
    first_delay_time_s: float | None = None
    recovery_time_s: float | None = None # 准点率回到 90% 的耗时

    # 追踪间隔
    headway_violations: int = 0          # 低于最小追踪间隔次数

    def to_dict(self) -> JsonDict:
        return {
            "totalStops": self.total_stops,
            "onTimeStops": self.on_time_stops,
            "onTimeRate": round(self.on_time_rate, 4),
            "totalBoardedPax": self.total_boarded_pax,
            "avgWaitSec": round(self.avg_wait_sec, 1),
            "maxLoadFactor": round(self.max_load_factor, 3),
            "avgLoadFactor": round(self.avg_load_factor, 3),
            "overloadEvents": self.overload_events,
            "firstDelayTimeS": (
                round(self.first_delay_time_s, 1) if self.first_delay_time_s is not None else None
            ),
            "recoveryTimeS": (
                round(self.recovery_time_s, 1) if self.recovery_time_s is not None else None
            ),
            "headwayViolations": self.headway_violations,
        }


class DispatchKpiTracker:
    """根据仿真过程数据实时计算调度KPI.

    评估指标（来自需求调研）：
    - 准点率：偏差 < 120s 的车次占比, 合格线 > 95%
    - 平均等待时间：高峰 < 180s, 平峰 < 360s
    - 满载率：高峰 < 120%
    - 延误恢复时间：初始延误 120s 后恢复 < 20 min
    """

    ON_TIME_THRESHOLD_S: float = 120.0     # 准点阈值
    OVERLOAD_THRESHOLD: float = 1.20       # 超载阈值
    RECOVERY_RATE: float = 0.90            # 恢复判定准点率
    MIN_HEADWAY_SEC: float = 90.0          # 最小追踪间隔

    def __init__(self) -> None:
        self.arrivals: list[TrainArrivalRecord] = []
        self._load_samples: list[float] = []      # 满载率采样
        self._overload_count: int = 0
        self._headway_violations: int = 0
        self._first_delay_time_s: float | None = None
        self._recovery_time_s: float | None = None
        self._total_boarded: int = 0
        self._total_waited: float = 0.0

    def record_arrival(
        self,
        train_id: str,
        station_index: int,
        station_code: str,
        planned_arrival_s: float,
        actual_arrival_s: float,
        direction: str = "UP",
    ) -> None:
        delay = actual_arrival_s - planned_arrival_s
        record = TrainArrivalRecord(
            train_id=train_id,
            station_index=station_index,
            station_code=station_code,
            planned_arrival_s=planned_arrival_s,
            actual_arrival_s=actual_arrival_s,
            delay_s=delay,
            direction=direction,
        )
        self.arrivals.append(record)

        # 记录首次延误
        if self._first_delay_time_s is None and delay > self.ON_TIME_THRESHOLD_S:
            self._first_delay_time_s = actual_arrival_s

    def record_load(self, load_factor: float) -> None:
        self._load_samples.append(load_factor)
        if load_factor > self.OVERLOAD_THRESHOLD:
            self._overload_count += 1

    def record_headway_violation(self) -> None:
        self._headway_violations += 1

    def record_boarding(self, boarded: int, total_wait_sec: float) -> None:
        """记录一次上车事件：boarding 人，这些人总共等待了 total_wait_sec 秒."""
        self._total_boarded += boarded
        self._total_waited += total_wait_sec

    def snapshot(self, current_time_s: float) -> DispatchKpiSnapshot:
        """计算当前KPI快照."""
        total = len(self.arrivals)
        on_time = sum(1 for r in self.arrivals if abs(r.delay_s) < self.ON_TIME_THRESHOLD_S)
        on_time_rate = on_time / total if total > 0 else 1.0

        # 延误恢复：从首次延误时刻起，准点率何时回到 90%
        recovery = self._compute_recovery(current_time_s, on_time_rate)

        avg_load = sum(self._load_samples) / len(self._load_samples) if self._load_samples else 0.0
        max_load = max(self._load_samples) if self._load_samples else 0.0

        avg_wait = self._total_waited / self._total_boarded if self._total_boarded > 0 else 0.0

        return DispatchKpiSnapshot(
            total_stops=total,
            on_time_stops=on_time,
            on_time_rate=on_time_rate,
            total_boarded_pax=self._total_boarded,
            total_waited_sec=self._total_waited,
            avg_wait_sec=avg_wait,
            max_load_factor=max_load,
            avg_load_factor=avg_load,
            overload_events=self._overload_count,
            first_delay_time_s=self._first_delay_time_s,
            recovery_time_s=recovery,
            headway_violations=self._headway_violations,
        )

    def _compute_recovery(self, current_time_s: float, current_rate: float) -> float | None:
        """计算延误恢复时间."""
        if self._first_delay_time_s is None:
            return None
        if current_rate >= self.RECOVERY_RATE and self._recovery_time_s is None:
            self._recovery_time_s = current_time_s - self._first_delay_time_s
        return self._recovery_time_s

    def reset(self) -> None:
        self.arrivals.clear()
        self._load_samples.clear()
        self._overload_count = 0
        self._headway_violations = 0
        self._first_delay_time_s = None
        self._recovery_time_s = None
        self._total_boarded = 0
        self._total_waited = 0.0
