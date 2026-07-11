"""调度决策服务 — 规则引擎 + 多车间隔管理."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.dispatch.timetable import HeadwayConfig, ScheduledStop, TrainService


@dataclass(frozen=True)
class DispatchContext:
    sim_time_ms: int
    train_id: str
    station_id: str | None = None
    station_index: int = 0
    rear_headway_sec: float | None = None
    front_headway_sec: float | None = None
    platform_crowding_level: str = "LOW"
    load_factor: float = 0.0
    left_behind_pax: int = 0
    power_traction_limit_ratio: float = 1.0
    disturbance_active: bool = False
    route_available: bool = True
    onboard_pax: int = 0
    capacity_pax: int = 600


@dataclass(frozen=True)
class DispatchDecision:
    decision_id: str
    sim_time_ms: int
    train_id: str
    station_id: str | None
    action: str
    duration_sec: float
    reason: str
    applied: bool = True
    expected_impact: dict[str, float] | None = None


@dataclass(frozen=True)
class DispatchRuleConfig:
    min_headway_sec: float = 90.0
    max_headway_sec: float = 300.0
    power_stagger_threshold: float = 0.8
    overload_threshold: float = 1.20       # 满载率超载阈值
    left_behind_threshold_pax: int = 80
    default_hold_sec: float = 20.0
    power_stagger_sec: float = 15.0
    departure_delay_threshold_s: float = 60.0  # 晚点超过此值尝试追回


@dataclass
class TrainDispatchState:
    """一列车的调度状态追踪."""
    train_id: str
    last_departure_s: float = 0.0
    current_station_index: int = 0
    direction: str = "UP"
    service: TrainService | None = None


class RuleBasedDispatchService:
    """规则引擎调度器 — 支持多车间隔 + 满载拒载 + 供电限流."""

    def __init__(self, config: DispatchRuleConfig | None = None) -> None:
        self.config = config or DispatchRuleConfig()
        self._sequence = 0
        self._train_states: dict[str, TrainDispatchState] = {}
        self._departure_history: list[tuple[str, float, int]] = []  # [(train_id, sim_time_s, station_index), ...]

    def register_train(self, train_id: str, service: TrainService | None = None) -> None:
        """注册一列车进入调度系统."""
        self._train_states[train_id] = TrainDispatchState(
            train_id=train_id,
            service=service,
        )

    def unregister_train(self, train_id: str) -> None:
        self._train_states.pop(train_id, None)

    def update_train_position(self, train_id: str, station_index: int, direction: str) -> None:
        state = self._train_states.get(train_id)
        if state is not None:
            state.current_station_index = station_index
            state.direction = direction

    def record_departure(self, train_id: str, sim_time_s: float, station_index: int) -> None:
        """记录发车时刻，用于计算追踪间隔."""
        self._departure_history.append((train_id, sim_time_s, station_index))
        # 清理旧记录（保留最近 100 条）
        if len(self._departure_history) > 100:
            self._departure_history = self._departure_history[-100:]

    def compute_headway(self, train_id: str, station_index: int) -> tuple[float | None, float | None]:
        """计算前车间隔和后车间隔.

        Returns:
            (front_headway_sec, rear_headway_sec)
            front_headway: 本车与前车的间隔
            rear_headway: 本车与后车的间隔
        """
        same_station = [
            (tid, t, idx)
            for tid, t, idx in self._departure_history
            if idx == station_index and tid != train_id
        ]
        if not same_station:
            return None, None

        # 按发车时间排序
        same_station.sort(key=lambda x: x[1])
        current_dep = next(
            (t for tid, t, idx in self._departure_history
             if tid == train_id and idx == station_index),
            None,
        )

        front, rear = None, None
        for i, (tid, t, idx) in enumerate(same_station):
            if current_dep is not None and t < current_dep:
                front = current_dep - t if current_dep is not None else None
            if current_dep is not None and t > current_dep:
                rear = t - current_dep
                break

        return front, rear

    def decide(self, context: DispatchContext) -> DispatchDecision:
        self._sequence += 1
        decision_id = f"DD-{self._sequence:04d}"
        cfg = self.config

        # 规则1：供电限流 → 错峰发车
        if context.power_traction_limit_ratio < cfg.power_stagger_threshold:
            return DispatchDecision(
                decision_id, context.sim_time_ms, context.train_id, context.station_id,
                "STAGGER_DEPARTURE", cfg.power_stagger_sec, "POWER_LIMITED",
                expected_impact={"tractionLimitRatio": context.power_traction_limit_ratio},
            )

        # 规则2：后车间隔太近 → 扣车等待
        if context.rear_headway_sec is not None and context.rear_headway_sec < cfg.min_headway_sec:
            return DispatchDecision(
                decision_id, context.sim_time_ms, context.train_id, context.station_id,
                "HOLD", cfg.default_hold_sec, "HEADWAY_TOO_SHORT",
                expected_impact={"rearHeadwaySec": context.rear_headway_sec},
            )

        # 规则3：前车间隔过长 + 站台拥挤 → 提前发车
        if (
            context.front_headway_sec is not None
            and context.front_headway_sec > cfg.max_headway_sec
            and context.platform_crowding_level in {"HIGH", "CRITICAL"}
            and context.route_available
        ):
            return DispatchDecision(
                decision_id, context.sim_time_ms, context.train_id, context.station_id,
                "RELEASE", 0.0, "HEADWAY_TOO_LONG_AND_PLATFORM_CROWDED",
                expected_impact={"frontHeadwaySec": context.front_headway_sec},
            )

        # 规则4：超载 + 滞留 → 请求加车（不直接执行，通知上层）
        if (
            context.load_factor >= cfg.overload_threshold
            and context.left_behind_pax >= cfg.left_behind_threshold_pax
        ):
            return DispatchDecision(
                decision_id, context.sim_time_ms, context.train_id, context.station_id,
                "ADD_TRAIN_REQUEST", 0.0, "OVERLOAD_AND_LEFT_BEHIND",
                applied=False,
                expected_impact={
                    "leftBehindPax": float(context.left_behind_pax),
                    "loadFactor": context.load_factor,
                },
            )

        # 规则5：扰动场景 → 延长停站
        if context.disturbance_active:
            return DispatchDecision(
                decision_id, context.sim_time_ms, context.train_id, context.station_id,
                "DWELL_EXTEND", cfg.default_hold_sec, "DISTURBANCE_RECOVERY",
            )

        # 默认：按图行车
        return DispatchDecision(
            decision_id, context.sim_time_ms, context.train_id, context.station_id,
            "FOLLOW_TIMETABLE", 0.0, "NO_ADJUSTMENT_NEEDED",
        )
