from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DispatchContext:
    sim_time_ms: int
    train_id: str
    station_id: str | None = None
    rear_headway_sec: float | None = None
    front_headway_sec: float | None = None
    platform_crowding_level: str = "LOW"
    load_factor: float = 0.0
    left_behind_pax: int = 0
    power_traction_limit_ratio: float = 1.0
    disturbance_active: bool = False
    route_available: bool = True
    terminal_turnback: bool = False
    turnback_direction: str | None = None


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
    expected_impact: dict[str, float | str] | None = None


@dataclass(frozen=True)
class DispatchRuleConfig:
    min_headway_sec: float = 90.0
    max_headway_sec: float = 300.0
    power_stagger_threshold: float = 0.8
    overload_threshold: float = 0.95
    left_behind_threshold_pax: int = 80
    default_hold_sec: float = 20.0
    power_stagger_sec: float = 15.0


class RuleBasedDispatchService:
    def __init__(self, config: DispatchRuleConfig | None = None) -> None:
        self.config = config or DispatchRuleConfig()
        self._sequence = 0

    def decide(self, context: DispatchContext) -> DispatchDecision:
        self._sequence += 1
        decision_id = f"DD-{self._sequence:04d}"
        cfg = self.config
        # Terminal reversal is an operating-plan decision, not an ATO shortcut.
        # The engine performs it only after confirming a legal reverse route.
        if context.terminal_turnback:
            return DispatchDecision(
                decision_id,
                context.sim_time_ms,
                context.train_id,
                context.station_id,
                "TURNBACK",
                0.0,
                "TERMINAL_REVERSAL_" + (context.turnback_direction or "UNKNOWN"),
                expected_impact={"direction": context.turnback_direction or "UNKNOWN"},
            )

        if context.power_traction_limit_ratio < cfg.power_stagger_threshold:
            return DispatchDecision(
                decision_id,
                context.sim_time_ms,
                context.train_id,
                context.station_id,
                "STAGGER_DEPARTURE",
                cfg.power_stagger_sec,
                "POWER_LIMITED",
                expected_impact={"tractionLimitRatio": context.power_traction_limit_ratio},
            )
        if context.rear_headway_sec is not None and context.rear_headway_sec < cfg.min_headway_sec:
            return DispatchDecision(
                decision_id,
                context.sim_time_ms,
                context.train_id,
                context.station_id,
                "HOLD",
                cfg.default_hold_sec,
                "HEADWAY_TOO_SHORT",
                expected_impact={"rearHeadwaySec": context.rear_headway_sec},
            )
        if (
            context.front_headway_sec is not None
            and context.front_headway_sec > cfg.max_headway_sec
            and context.platform_crowding_level in {"HIGH", "CRITICAL"}
            and context.route_available
        ):
            return DispatchDecision(
                decision_id,
                context.sim_time_ms,
                context.train_id,
                context.station_id,
                "RELEASE",
                0.0,
                "HEADWAY_TOO_LONG_AND_PLATFORM_CROWDED",
                expected_impact={"frontHeadwaySec": context.front_headway_sec},
            )
        if context.load_factor >= cfg.overload_threshold and context.left_behind_pax >= cfg.left_behind_threshold_pax:
            return DispatchDecision(
                decision_id,
                context.sim_time_ms,
                context.train_id,
                context.station_id,
                "ADD_TRAIN_REQUEST",
                0.0,
                "OVERLOAD_AND_LEFT_BEHIND",
                applied=False,
                expected_impact={"leftBehindPax": float(context.left_behind_pax)},
            )
        if context.disturbance_active:
            return DispatchDecision(
                decision_id,
                context.sim_time_ms,
                context.train_id,
                context.station_id,
                "DWELL_EXTEND",
                cfg.default_hold_sec,
                "DISTURBANCE_RECOVERY",
            )
        return DispatchDecision(
            decision_id,
            context.sim_time_ms,
            context.train_id,
            context.station_id,
            "FOLLOW_TIMETABLE",
            0.0,
            "NO_ADJUSTMENT_NEEDED",
        )

