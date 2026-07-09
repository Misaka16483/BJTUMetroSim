"""仿真引擎 — 成员A: 时钟驱动 + 域服务编排 + 事件发布 + 数据记录."""

from __future__ import annotations

import csv
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.clock import SimulationClock
from app.core.message_bus import Envelope, MessageBus
from app.core.scenario import ScenarioConfig, TrainConfig
from app.domain.control.models import AtoConfig, AtoTarget, OperationMode
from app.domain.control.services import ATOController
from app.domain.dispatch.services import DispatchContext, DispatchDecision, RuleBasedDispatchService
from app.domain.line.services import LineMapRepository, TrackQueryService
from app.domain.power.services import PowerSection, PowerService, TrainPowerRequest
from app.domain.station.services import (
    BoardingResult,
    DwellPlan,
    DwellTimeConfig,
    PassengerDemandProfile,
    PassengerFlowGenerator,
    StationService,
    TrainLoadState,
)
from app.domain.vehicle.models import ControlCommand, TrainState, VehicleConfig, CommandSource
from app.domain.vehicle.services import SimpleVehicleModel
from app.infra.recorder import RunRecorder


JsonDict = dict[str, Any]

# ── 列车运行阶段 ──
APPROACHING = "APPROACHING"     # 进站制动
DWELLING = "DWELLING"           # 停站上下客
DEPARTING = "DEPARTING"         # 出站加速
CRUISING = "CRUISING"           # 区间巡航
IDLE = "IDLE"                   # 尚未启动或已完成


@dataclass
class SimTrainState:
    """列车实时状态."""
    train_id: str
    line_id: str
    station_index: int           # 当前所在站序号 (0-based)
    direction: str               # "UP" or "DOWN"
    current_station_code: str = ""
    next_station_code: str = ""
    phase: str = IDLE
    speed_mps: float = 0.0
    permitted_speed_mps: float = 22.22
    distance_to_next_m: float = 0.0    # 剩余距离（每 tick 递减）
    target_distance_m: float = 0.0     # 当前区间总站间距（不变，到站后更新）
    dwell_remaining_sec: float = 0.0
    onboard_pax: int = 0
    capacity_pax: int = 600
    load_factor: float = 0.0
    current_station_name: str = ""
    next_station_name: str = ""
    segment_progress: float = 0.0  # 0→1 between current and next station
    last_dispatch_action: str = "FOLLOW_TIMETABLE"
    last_dispatch_reason: str = "NO_ADJUSTMENT_NEEDED"
    dispatch_hold_applied_station_index: int | None = None
    # ── 驾驶台反馈字段 ──
    traction_percent: float = 0.0
    brake_percent: float = 0.0
    energy_kwh: float = 0.0
    target_speed_mps: float = 0.0
    estimated_run_time_s: float = 0.0   # 预计区间运行时间，由速度曲线积分得出
    # ── profile 触发控制 ──
    _profile_triggered: bool = False

    def to_dict(self) -> JsonDict:
        return {
            "trainId": self.train_id,
            "lineId": self.line_id,
            "stationIndex": self.station_index,
            "direction": self.direction,
            "phase": self.phase,
            "currentStationCode": self.current_station_code,
            "nextStationCode": self.next_station_code,
            "speedMps": round(self.speed_mps, 2),
            "permittedSpeedMps": round(self.permitted_speed_mps, 2),
            "distanceToNextM": round(self.distance_to_next_m, 1),
            "targetDistanceM": round(self.target_distance_m, 1),
            "dwellRemainingSec": round(self.dwell_remaining_sec, 1),
            "onboardPax": self.onboard_pax,
            "capacityPax": self.capacity_pax,
            "loadFactor": round(self.load_factor, 3),
            "currentStation": self.current_station_name,
            "nextStation": self.next_station_name,
            "segmentProgress": round(self.segment_progress, 3),
            "lastDispatchAction": self.last_dispatch_action,
            "lastDispatchReason": self.last_dispatch_reason,
            "tractionPercent": round(self.traction_percent, 1),
            "brakePercent": round(self.brake_percent, 1),
            "energyKwh": round(self.energy_kwh, 2),
            "targetSpeedMps": round(self.target_speed_mps, 2),
            "estimatedRunTimeS": round(self.estimated_run_time_s, 1),
        }


@dataclass
class TickSnapshot:
    """每个 tick 的完整快照，供 API 读取."""
    tick: int = 0
    sim_time_ms: int = 0
    sim_time_str: str = "08:00:00"
    clock_state: str = "IDLE"
    trains: list[dict[str, Any]] = field(default_factory=list)
    stations: list[dict[str, Any]] = field(default_factory=list)
    power: list[dict[str, Any]] = field(default_factory=list)
    dispatch_decisions: list[dict[str, Any]] = field(default_factory=list)
    kpi: dict[str, Any] = field(default_factory=dict)


class SimulationEngine:
    """Phase 1 仿真引擎：单车为主，预留多车接口."""

    # ── 速度曲线参数 ──
    CRUISE_SPEED_MPS = 22.22  # 80 km/h 巡航

    def __init__(
        self,
        scenario: ScenarioConfig,
        line_map: JsonDict,
        station_catalog: list[JsonDict],  # [{code, name, mileageM, ...}, ...]
        recorder: RunRecorder | None = None,
    ) -> None:
        self.scenario = scenario
        self.line_map = line_map
        self.station_catalog = station_catalog
        self.recorder = recorder

        # 核心组件
        self.clock = SimulationClock(tick_seconds=scenario.tick_seconds)
        self.bus = MessageBus()
        self.track_query = TrackQueryService(line_map)

        # ── 构建车站运行索引 ──
        self._station_list: list[JsonDict] = self._build_station_list()
        self._station_distances: list[float] = self._build_station_distances()

        # ── 列车状态 ──
        self.trains: list[SimTrainState] = []
        self._run_id: int | None = None

        # ── 域服务 ──
        self.station_service = self._build_station_service()
        self.power_service: PowerService = self._build_power_service()
        self.dispatch_service = RuleBasedDispatchService()
        self._last_arrivals_by_platform: dict[tuple[str, str], int] = {}
        self._last_power_states: dict[str, Any] = {}
        self._last_dispatch_decisions: list[DispatchDecision] = []

        # ── 物理模型：ATO 制动曲线 + 合成规划曲线 ──
        self.ato = ATOController(AtoConfig(
            target_cruise_speed_mps=self.CRUISE_SPEED_MPS,
            use_dynamic_programming_profile=False,  # 制动曲线（即时）
        ))
        self._dcdp_curve_data: dict[str, list[dict[str, Any]]] = {}     # 规划曲线（合成）
        self._profile_run_times: dict[str, float] = {}                  # 预计区间运行时间

        # ── 线程安全 ──
        self._lock = threading.Lock()
        self._snapshot: TickSnapshot | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # ═══════════════════════════════════════════════════════════
    #  公共接口
    # ═══════════════════════════════════════════════════════════

    def load(self) -> None:
        """加载场景，初始化列车状态."""
        self.clock.load()
        self._last_arrivals_by_platform = {}
        self._last_power_states = self._empty_power_states()
        self._last_dispatch_decisions = []
        self.trains = [self._create_train(cfg) for cfg in self.scenario.trains]
        self._snapshot = self._build_snapshot()

        if self.recorder is not None:
            self._run_id = self.recorder.start_run(
                self.scenario.name,
                {
                    "phase": 1,
                    "lineId": self.scenario.line_id,
                    "startTimeMs": self.scenario.start_time_ms,
                    "trainCount": len(self.scenario.trains),
                },
            )

    def start(self) -> None:
        """启动仿真（后台线程）."""
        if self.clock.state.value != "LOADED":
            self.load()
        self.clock.start()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def pause(self) -> None:
        self.clock.pause()
        self._snapshot = self._build_snapshot()

    def resume(self) -> None:
        self.clock.resume()
        self._snapshot = self._build_snapshot()

    def stop(self) -> None:
        self._stop_event.set()
        if self.clock.state.value not in ("STOPPED", "IDLE"):
            self.clock.stop()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        # 刷新快照以反映当前状态
        self._snapshot = self._build_snapshot()

    def snapshot(self) -> TickSnapshot | None:
        """线程安全读取当前快照."""
        with self._lock:
            return self._snapshot

    # ═══════════════════════════════════════════════════════════
    #  仿真主循环
    # ═══════════════════════════════════════════════════════════

    def _run_loop(self) -> None:
        """后台仿真线程."""
        while not self._stop_event.is_set():
            if self.clock.state.value == "PAUSED":
                time.sleep(0.1)
                continue
            if self.clock.state.value != "RUNNING":
                break
            loop_start = time.perf_counter()
            self._tick()
            elapsed = time.perf_counter() - loop_start
            sleep_sec = max(0.06, self.clock.tick_seconds - elapsed)
            time.sleep(sleep_sec)

    def _tick(self) -> None:
        """单步仿真."""
        self.clock.step()
        tick = self.clock.current_tick
        sim_time_ms = self._absolute_sim_time_ms()

        # 1) 更新列车位置与速度 (ATO + 物理模型驱动)
        for train in self.trains:
            self._advance_train(train, sim_time_ms)

        # 2) 客流到达（每 tick 累积）
        self._last_arrivals_by_platform = self.station_service.update_arrivals(
            sim_time_ms,
            dt_sec=self.clock.tick_seconds,
        )

        # 3) 供电更新
        power_states = self._update_power(sim_time_ms)
        self._last_power_states = power_states

        # 4) 调度决策
        decisions = self._make_dispatch_decisions(sim_time_ms, power_states)
        self._last_dispatch_decisions = decisions

        # 5) 发布事件
        for train in self.trains:
            self.bus.publish(
                "train.state",
                train.to_dict(),
                source="engine",
                tick=tick,
            )
        self.bus.publish("clock.tick", {"tick": tick, "simTimeMs": sim_time_ms}, source="engine", tick=tick)

        # 6) 记录到 SQLite
        if self.recorder is not None and self._run_id is not None:
            for train in self.trains:
                self.recorder.record_event(
                    self._run_id,
                    "train.state",
                    train.to_dict(),
                    tick=tick,
                )
            for decision in decisions:
                self.recorder.record_dispatch_decision(
                    self._run_id,
                    decision_id=decision.decision_id,
                    sim_time_ms=decision.sim_time_ms,
                    train_id=decision.train_id,
                    station_id=decision.station_id,
                    action=decision.action,
                    duration_sec=decision.duration_sec,
                    reason=decision.reason,
                    expected_impact=decision.expected_impact,
                    applied=decision.applied,
                    detail={"tick": tick},
                )
            for state in power_states.values():
                self.recorder.record_power(
                    self._run_id,
                    sim_time_ms=sim_time_ms,
                    power_section_id=state.power_section_id,
                    requested_power_kw=state.requested_power_kw,
                    available_power_kw=state.available_power_kw,
                    traction_limit_ratio=state.traction_limit_ratio,
                    voltage_level=state.voltage_level,
                    energy_kwh=state.energy_kwh,
                    regen_energy_kwh=state.regen_energy_kwh,
                    absorbed_regen_kw=state.absorbed_regen_kw,
                    wasted_regen_kw=state.wasted_regen_kw,
                    source=state.source,
                    quality=state.quality,
                    detail={"tick": tick},
                )

        # 7) 更新快照
        self._snapshot = self._build_snapshot()

    # ═══════════════════════════════════════════════════════════
    #  列车推进逻辑
    # ═══════════════════════════════════════════════════════════

    def _advance_train(self, train: SimTrainState, sim_time_ms: int) -> None:
        """每 tick 推进一辆列车 — ATO 决策 + 牛顿物理模型."""
        dt = self.clock.tick_seconds
        stations = self._station_list
        n = len(stations)

        if train.station_index >= n - 1 and train.direction == "UP":
            train.phase = IDLE
            train.speed_mps = 0
            return
        if train.station_index <= 0 and train.direction == "DOWN":
            train.phase = IDLE
            train.speed_mps = 0
            return

        next_idx = train.station_index + 1 if train.direction == "UP" else train.station_index - 1
        if next_idx < 0 or next_idx >= n:
            train.phase = IDLE
            train.speed_mps = 0
            return

        next_stn = stations[next_idx]
        dist = abs(self._station_distances[next_idx] - self._station_distances[train.station_index])

        # ── DWELLING ──
        if train.phase in (DWELLING, IDLE):
            train.speed_mps = 0
            train.distance_to_next_m = dist
            train.segment_progress = 0
            train.traction_percent = 0.0
            train.brake_percent = 0.0 if train.phase == IDLE else 20.0
            train.target_speed_mps = 0.0

            # 从 CSV 读取当前区间线路限速
            limit_kmh = int(self._station_list[train.station_index].get("speedLimitToNextKmh", 80))
            train.permitted_speed_mps = limit_kmh / 3.6

            # 生成下一区间的合成规划曲线（即时，无阻塞）
            if not train._profile_triggered:
                train._profile_triggered = True
                self._generate_synthetic_profile(train.train_id, train.station_index, train.direction, train.permitted_speed_mps)

            if train.dwell_remaining_sec > 0:
                train.dwell_remaining_sec = max(0, train.dwell_remaining_sec - dt)
                return
            train.dwell_remaining_sec = 0
            # 应用预计运行时间
            train.estimated_run_time_s = self._profile_run_times.get(train.train_id, 0.0)
            train.phase = DEPARTING
            # fall through to physics

        # ── 物理模型推进 ──
        try:
            cur_mileage = self._station_distances[train.station_index]
            next_mileage = self._station_distances[next_idx]
            interval_m = abs(next_mileage - cur_mileage)
            position_m = cur_mileage + train.segment_progress * interval_m
            target_position_m = next_mileage if train.direction == "UP" else cur_mileage

            state = TrainState(
                train_id=train.train_id,
                position_m=position_m,
                speed_mps=train.speed_mps,
                sim_time_s=self.clock.sim_time_seconds,
                net_energy_kwh=train.energy_kwh,
            )

            # ── ATO 目标：profile 作为跟踪目标 + 线路限速作为上限 ──
            profile_result = self._lookup_profile_speed(train.train_id, position_m)
            if profile_result is not None:
                profile_speed, profile_mode = profile_result
            else:
                profile_speed, profile_mode = None, ""
            effective_limit = (
                min(train.permitted_speed_mps, profile_speed)
                if profile_speed is not None
                else train.permitted_speed_mps
            )
            target = AtoTarget(
                target_position_m=target_position_m,
                permitted_speed_mps=effective_limit,
            )
            cmd = self.ato.decide(state, target)

            # ── Profile feedforward：加速段全牵引 ──
            if profile_mode == "MAX_TRACTION" and train.speed_mps < effective_limit - 0.2:
                cmd = ControlCommand(
                    train_id=train.train_id,
                    traction_percent=100.0,
                    source=CommandSource.ATO,
                )

            vcfg = VehicleConfig(train_id=train.train_id)
            vm = SimpleVehicleModel(vcfg)
            result = vm.step(state, cmd, dt)

            # ── 写回 ──
            train.speed_mps = max(0.0, result.speed_mps)
            train.traction_percent = cmd.traction_percent
            train.brake_percent = cmd.brake_percent
            train.target_speed_mps = self.ato.last_target_speed_mps
            train.energy_kwh = result.net_energy_kwh

            # ── 位置更新 ──
            new_progress = (result.position_m - cur_mileage) / interval_m if interval_m > 0 else 1.0

            if new_progress >= 1.0:
                # 到站
                train.speed_mps = 0
                train.segment_progress = 0
                train.station_index = next_idx
                train.current_station_code = str(next_stn.get("code", ""))
                train.current_station_name = next_stn.get("name", "")
                train.phase = DWELLING
                train.dispatch_hold_applied_station_index = None
                train.traction_percent = 0.0
                train.brake_percent = 20.0
                train.target_speed_mps = 0.0

                # 清除当前曲线 + 触发标志
                self._dcdp_curve_data.pop(train.train_id, None)
                self._profile_run_times.pop(train.train_id, None)
                train._profile_triggered = False  # 下一站允许触发
                self.ato.reset()  # 重置 PID 积分 + profile cache

                new_next_idx = next_idx + 1 if train.direction == "UP" else next_idx - 1
                if 0 <= new_next_idx < n:
                    new_next_stn = stations[new_next_idx]
                    train.next_station_code = str(new_next_stn.get("code", ""))
                    train.next_station_name = new_next_stn.get("name", "")
                    train.target_distance_m = abs(
                        self._station_distances[new_next_idx] - self._station_distances[next_idx]
                    )
                    train.distance_to_next_m = train.target_distance_m
                else:
                    train.next_station_code = ""
                    train.next_station_name = ""
                    train.target_distance_m = 0
                    train.distance_to_next_m = 0

                self._process_station_stop(train, sim_time_ms)
            else:
                train.segment_progress = new_progress
                train.distance_to_next_m = target_position_m - result.position_m if train.direction == "UP" else result.position_m - target_position_m
                # Phase 更新
                if train.speed_mps >= self.CRUISE_SPEED_MPS * 0.95:
                    train.phase = CRUISING
                elif cmd.brake_percent > 5 and train.speed_mps > 0.5:
                    train.phase = APPROACHING
                else:
                    train.phase = DEPARTING

        except Exception:
            train.traction_percent = 0.0
            train.brake_percent = 0.0
            train.target_speed_mps = 0.0
            train.speed_mps = max(0, train.speed_mps - 0.8 * dt)
            train.distance_to_next_m = max(0, train.distance_to_next_m - train.speed_mps * dt)
            train.segment_progress = 1 - (train.distance_to_next_m / dist) if dist > 0 else 1.0


    def _lookup_profile_speed(self, train_id: str, position_m: float) -> tuple[float, str] | None:
        """从规划曲线中线性插值当前位置的目标速度和运行模式."""
        points = self._dcdp_curve_data.get(train_id)
        if not points or len(points) < 2:
            return None
        for i in range(len(points) - 1):
            p0, p1 = points[i], points[i + 1]
            if p0["positionM"] <= position_m <= p1["positionM"]:
                seg = p1["positionM"] - p0["positionM"]
                if seg <= 0:
                    return (p0["speedMps"], p0.get("mode", ""))
                t = (position_m - p0["positionM"]) / seg
                return (p0["speedMps"] + t * (p1["speedMps"] - p0["speedMps"]), p0.get("mode", ""))
        if position_m < points[0]["positionM"]:
            return (points[0]["speedMps"], points[0].get("mode", ""))
        return (points[-1]["speedMps"], points[-1].get("mode", ""))

    def _generate_synthetic_profile(self, train_id: str, station_index: int, direction: str, permitted_speed_mps: float) -> None:
        """为给定区间即时生成合成规划曲线（加速→巡航→惰行→制动）."""
        import math
        stations = self._station_list
        n = len(stations)
        next_idx = station_index + 1 if direction == "UP" else station_index - 1
        if next_idx < 0 or next_idx >= n:
            return

        cur_mileage = self._station_distances[station_index]
        next_mileage = self._station_distances[next_idx]
        dist = abs(next_mileage - cur_mileage)
        if dist < 10:
            return

        ACCEL = 0.54   # m/s² (100kN/180t - 3000N 阻力)
        DECEL = 0.8    # m/s²
        CRUISE = min(self.CRUISE_SPEED_MPS, permitted_speed_mps)  # 不超过线路限速

        accel_to_cruise = CRUISE ** 2 / (2 * ACCEL)
        decel_from_cruise = CRUISE ** 2 / (2 * DECEL)
        cruise_dist = dist - accel_to_cruise - decel_from_cruise

        step = 20.0
        points: list[dict[str, Any]] = []
        pos = 0.0

        # 加速段
        while pos < min(accel_to_cruise, dist) and cruise_dist >= 0:
            v = math.sqrt(2 * ACCEL * max(pos, 0.1))
            points.append({"positionM": round(cur_mileage + pos, 1),
                           "speedMps": round(min(v, CRUISE), 2),
                           "mode": "MAX_TRACTION"})
            pos += step

        # 巡航段
        cruise_start = pos
        while pos < cruise_start + max(cruise_dist, 0):
            points.append({"positionM": round(cur_mileage + pos, 1),
                           "speedMps": round(CRUISE, 2),
                           "mode": "CRUISE"})
            pos += step

        # 惰行 + 制动段
        while pos < dist:
            remaining = max(dist - pos, 0.1)
            v = math.sqrt(2 * DECEL * remaining)
            mode = "COAST" if v > CRUISE * 0.5 else "MAX_BRAKE"
            points.append({"positionM": round(cur_mileage + pos, 1),
                           "speedMps": round(min(v, CRUISE), 2),
                           "mode": mode})
            pos += step

        points.append({"positionM": round(cur_mileage + dist, 1),
                       "speedMps": 0.0, "mode": "STOP"})

        if cruise_dist < 0:
            # 距离太短无法巡航：纯加速+制动
            mid = dist / 2
            points.clear()
            pos = 0.0
            while pos < mid:
                v = math.sqrt(2 * ACCEL * max(pos, 0.1))
                points.append({"positionM": round(cur_mileage + pos, 1),
                               "speedMps": round(v, 2), "mode": "MAX_TRACTION"})
                pos += step
            while pos <= dist:
                remaining = max(dist - pos, 0.1)
                v = math.sqrt(2 * DECEL * remaining)
                points.append({"positionM": round(cur_mileage + pos, 1),
                               "speedMps": round(min(v, points[-1]["speedMps"] if points else CRUISE), 2),
                               "mode": "MAX_BRAKE"})
                pos += step
            points.append({"positionM": round(cur_mileage + dist, 1),
                           "speedMps": 0.0, "mode": "STOP"})

        self._dcdp_curve_data[train_id] = points

        # 从合成速度曲线积分预计运行时间
        total_time = 0.0
        for i in range(len(points) - 1):
            dp = points[i+1]["positionM"] - points[i]["positionM"]
            v_avg = (points[i]["speedMps"] + points[i+1]["speedMps"]) / 2
            if v_avg > 0.001:
                total_time += dp / v_avg
        self._profile_run_times[train_id] = total_time

        print(f"[Profile] Synthetic: {stations[station_index].get('name','')} → "
              f"{stations[next_idx].get('name','')} ({dist:.0f}m), "
              f"{len(points)} pts, max={max(pt['speedMps'] for pt in points)*3.6:.0f} km/h, "
              f"est={total_time:.0f}s")

    def export_speed_profile(self, train_id: str) -> list[dict[str, Any]]:
        """返回指定列车的 DCDP 规划速度曲线数据."""
        return self._dcdp_curve_data.get(train_id, [])

    # ═══════════════════════════════════════════════════════════
    #  域服务调用
    # ═══════════════════════════════════════════════════════════

    def _process_station_stop(self, train: SimTrainState, sim_time_ms: int) -> None:
        """处理列车停站上下客."""
        try:
            result, dwell_plan = self.station_service.process_train_stop(
                sim_time_ms=sim_time_ms,
                station_id=train.current_station_code,
                direction=train.direction,
                train_load=TrainLoadState(
                    train_id=train.train_id,
                    onboard_pax=train.onboard_pax,
                    capacity_pax=train.capacity_pax,
                ),
                platform_area_m2=120.0,
            )
            # 取较大的停站时间（客流驱动 or 默认值）
            effective_dwell = max(
                dwell_plan.estimated_dwell_sec,
                float(self._station_list[train.station_index].get("dwellSeconds", 30)),
            )
            train.dwell_remaining_sec = effective_dwell
            train.onboard_pax = result.updated_load.onboard_pax
            train.load_factor = result.updated_load.load_factor

            # 记录到 SQLite
            if self.recorder is not None and self._run_id is not None:
                self.recorder.record_station_passenger(
                    self._run_id,
                    sim_time_ms=sim_time_ms,
                    station_id=result.station_id,
                    direction=result.direction,
                    boarding=result.boarding,
                    alighting=result.alighting,
                    waiting=result.waiting,
                    left_behind=result.left_behind,
                )
                self.recorder.record_train_load(
                    self._run_id,
                    sim_time_ms=sim_time_ms,
                    train_id=result.train_id,
                    onboard_pax=result.updated_load.onboard_pax,
                    capacity_pax=result.updated_load.capacity_pax,
                    load_factor=result.updated_load.load_factor,
                    vehicle_load_kg=result.updated_load.vehicle_load_kg,
                    detail={"stationId": result.station_id},
                )
                self.recorder.record_dwell(
                    self._run_id,
                    train_id=train.train_id,
                    station_id=result.station_id,
                    arrival_ms=sim_time_ms,
                    depart_ms=sim_time_ms + int(effective_dwell * 1000),
                    planned_dwell_sec=dwell_plan.planned_dwell_sec,
                    estimated_dwell_sec=dwell_plan.estimated_dwell_sec,
                    actual_dwell_sec=effective_dwell,
                    reason=dwell_plan.blocking_reason or "PASSENGER_BOARDING",
                )
        except Exception:
            # 客流服务失败时使用默认停站时间
            train.dwell_remaining_sec = 30.0

    def _update_power(self, sim_time_ms: int) -> dict[str, Any]:
        """更新供电状态."""
        if not self.power_service.sections:
            return {}
        requests: list[TrainPowerRequest] = []
        for train in self.trains:
            traction_force_n = 0.0
            brake_force_n = 0.0
            if train.phase == DEPARTING:
                traction_force_n = 95_000.0
            elif train.phase == CRUISING:
                traction_force_n = 35_000.0
            elif train.phase == APPROACHING:
                brake_force_n = 45_000.0
            requests.append(
                TrainPowerRequest(
                    train_id=train.train_id,
                    power_section_id=self._power_section_for_train(train),
                    speed_mps=train.speed_mps,
                    traction_force_n=traction_force_n,
                    brake_force_n=brake_force_n,
                )
            )
        return self.power_service.update(requests, dt_sec=self.clock.tick_seconds)

    def _make_dispatch_decisions(
        self, sim_time_ms: int, power_states: dict[str, Any]
    ) -> list[DispatchDecision]:
        """生成调度决策."""
        decisions: list[DispatchDecision] = []
        for train in self.trains:
            if train.phase != DWELLING:
                continue
            platform = self.station_service.ensure_platform(train.current_station_code, train.direction)
            ps = power_states.get(self._power_section_for_train(train))
            limit_ratio = ps.traction_limit_ratio if ps and hasattr(ps, "traction_limit_ratio") else 1.0
            context = DispatchContext(
                sim_time_ms=sim_time_ms,
                train_id=train.train_id,
                station_id=train.current_station_code,
                platform_crowding_level=platform.crowding_level,
                load_factor=train.load_factor,
                left_behind_pax=platform.left_behind_pax,
                power_traction_limit_ratio=limit_ratio,
            )
            decision = self.dispatch_service.decide(context)
            train.last_dispatch_action = decision.action
            train.last_dispatch_reason = decision.reason
            if (
                decision.applied
                and decision.duration_sec > 0
                and decision.action in {"HOLD", "STAGGER_DEPARTURE", "DWELL_EXTEND"}
                and train.dispatch_hold_applied_station_index != train.station_index
            ):
                train.dwell_remaining_sec += decision.duration_sec
                train.dispatch_hold_applied_station_index = train.station_index
            decisions.append(decision)
        return decisions

    # ═══════════════════════════════════════════════════════════
    #  快照构建
    # ═══════════════════════════════════════════════════════════

    def _build_snapshot(self) -> TickSnapshot:
        total_sec = self._absolute_sim_time_ms() // 1000
        h = (total_sec // 3600) % 24
        m = (total_sec % 3600) // 60
        s = total_sec % 60
        time_str = f"{h:02d}:{m:02d}:{s:02d}"

        active = [t for t in self.trains if t.phase != IDLE]
        return TickSnapshot(
            tick=self.clock.current_tick,
            sim_time_ms=self._absolute_sim_time_ms(),
            sim_time_str=time_str,
            clock_state=self.clock.state.value,
            trains=[t.to_dict() for t in self.trains],
            stations=[
                self._station_snapshot(stn)
                for stn in self._station_list
            ],
            power=[self._power_snapshot(state) for state in self._last_power_states.values()],
            dispatch_decisions=[self._dispatch_snapshot(item) for item in self._last_dispatch_decisions],
            kpi={
                "activeTrains": len(active),
                "totalTrains": len(self.trains),
                "avgSpeed": (
                    round(sum(t.speed_mps for t in active) / len(active), 2) if active else 0
                ),
                "totalOnboardPax": sum(t.onboard_pax for t in self.trains),
                "totalWaitingPax": sum(p.waiting_pax for p in self.station_service.platforms.values()),
                "maxPlatformDensity": round(
                    max((p.platform_density_pax_per_m2 for p in self.station_service.platforms.values()), default=0.0),
                    3,
                ),
                "totalTractionEnergyKwh": round(
                    sum(state.energy_kwh for state in self._last_power_states.values()),
                    3,
                ),
                "minTractionLimitRatio": round(
                    min((state.traction_limit_ratio for state in self._last_power_states.values()), default=1.0),
                    3,
                ),
                "lastDispatchAction": self._last_dispatch_decisions[-1].action
                if self._last_dispatch_decisions else "FOLLOW_TIMETABLE",
            },
        )

    # ═══════════════════════════════════════════════════════════
    #  初始化辅助
    # ═══════════════════════════════════════════════════════════

    def _build_station_list(self) -> list[JsonDict]:
        """构建按里程排序的车站列表."""
        stations = sorted(self.station_catalog, key=lambda s: float(s.get("mileageM", 0)))
        return stations

    def _build_station_distances(self) -> list[float]:
        """各站累计里程 (m)."""
        return [float(stn.get("mileageM", 0)) for stn in self._station_list]

    def _create_train(self, cfg: TrainConfig) -> SimTrainState:
        """根据配置初始化一辆列车."""
        # 查找起点站序号
        idx = next(
            (i for i, stn in enumerate(self._station_list) if stn.get("code") == cfg.initial_station_code),
            0,
        )
        stn = self._station_list[idx]
        next_idx = min(idx + 1, len(self._station_list) - 1) if cfg.direction == "UP" else max(idx - 1, 0)
        next_stn = self._station_list[next_idx]
        dist = abs(
            self._station_distances[next_idx] - self._station_distances[idx]
        )

        return SimTrainState(
            train_id=cfg.train_id,
            line_id=cfg.line_id,
            station_index=idx,
            direction=cfg.direction,
            current_station_code=str(stn.get("code", "")),
            next_station_code=str(next_stn.get("code", "")),
            phase=DWELLING,
            speed_mps=0.0,
            target_distance_m=dist,
            dwell_remaining_sec=5.0,  # 初始等待5秒后发车
            distance_to_next_m=dist,
            onboard_pax=cfg.initial_load_pax,
            capacity_pax=cfg.capacity_pax,
            current_station_name=stn.get("name", ""),
            next_station_name=next_stn.get("name", ""),
        )

    def _build_station_service(self) -> StationService:
        """构建客流服务."""
        return StationService(
            PassengerFlowGenerator(
                [
                    PassengerDemandProfile("GGZ", "UP", 7 * 3600, 9 * 3600, 60.0, alighting_ratio=0.08),
                    PassengerDemandProfile("FSP", "UP", 7 * 3600, 9 * 3600, 72.0, alighting_ratio=0.14),
                    PassengerDemandProfile("KYL", "UP", 7 * 3600, 9 * 3600, 48.0, alighting_ratio=0.16),
                    PassengerDemandProfile("FTN", "UP", 7 * 3600, 9 * 3600, 55.0, alighting_ratio=0.15),
                    PassengerDemandProfile("FTD", "UP", 7 * 3600, 9 * 3600, 40.0, alighting_ratio=0.12),
                    PassengerDemandProfile("QLZ", "UP", 7 * 3600, 9 * 3600, 65.0, alighting_ratio=0.18),
                    PassengerDemandProfile("LLQ", "UP", 7 * 3600, 9 * 3600, 90.0, alighting_ratio=0.20),
                    PassengerDemandProfile("LLE", "UP", 7 * 3600, 9 * 3600, 50.0, alighting_ratio=0.14),
                    PassengerDemandProfile("BWR", "UP", 7 * 3600, 9 * 3600, 120.0, alighting_ratio=0.25),
                    PassengerDemandProfile("JBG", "UP", 7 * 3600, 9 * 3600, 80.0, alighting_ratio=0.22),
                    PassengerDemandProfile("BDZ", "UP", 7 * 3600, 9 * 3600, 35.0, alighting_ratio=0.12),
                    PassengerDemandProfile("BQS", "UP", 7 * 3600, 9 * 3600, 45.0, alighting_ratio=0.15),
                    PassengerDemandProfile("GTG", "UP", 7 * 3600, 9 * 3600, 70.0, alighting_ratio=0.30),
                ]
            ),
            DwellTimeConfig(base_dwell_sec=30.0, door_capacity_pax_per_sec=3.0),
        )

    def _build_power_service(self) -> PowerService:
        return PowerService(
            [
                PowerSection(
                    power_section_id="PWR-09-UP",
                    name="Line 9 Up traction section",
                    max_traction_power_kw=900.0,
                    warning_power_kw=650.0,
                    regen_absorb_limit_kw=180.0,
                ),
                PowerSection(
                    power_section_id="PWR-09-DOWN",
                    name="Line 9 Down traction section",
                    max_traction_power_kw=900.0,
                    warning_power_kw=650.0,
                    regen_absorb_limit_kw=180.0,
                ),
            ],
            traction_efficiency=0.88,
            regen_efficiency=0.65,
        )

    def _empty_power_states(self) -> dict[str, Any]:
        return self.power_service.update([], dt_sec=0.0)

    def _power_section_for_train(self, train: SimTrainState) -> str:
        return "PWR-09-UP" if train.direction == "UP" else "PWR-09-DOWN"

    def _traction_limit_for_train(self, train: SimTrainState) -> float:
        state = self._last_power_states.get(self._power_section_for_train(train))
        if state is None:
            return 1.0
        return max(0.0, min(1.0, float(state.traction_limit_ratio)))

    def _absolute_sim_time_ms(self) -> int:
        return self.scenario.start_time_ms + int(self.clock.sim_time_seconds * 1000)

    def _station_snapshot(self, station: JsonDict) -> JsonDict:
        code = str(station.get("code", ""))
        direction = "UP"
        platform = self.station_service.ensure_platform(code, direction)
        arrivals = self._last_arrivals_by_platform.get((code, direction), 0)
        return {
            "name": station.get("name", ""),
            "code": code,
            "waitingPax": platform.waiting_pax,
            "leftBehindPax": platform.left_behind_pax,
            "arrivalsLastTick": arrivals,
            "platformDensity": round(platform.platform_density_pax_per_m2, 3),
            "crowdingLevel": platform.crowding_level,
            "direction": direction,
        }

    @staticmethod
    def _power_snapshot(state: Any) -> JsonDict:
        return {
            "powerSectionId": state.power_section_id,
            "requestedPowerKw": round(state.requested_power_kw, 3),
            "availablePowerKw": round(state.available_power_kw, 3),
            "tractionLimitRatio": round(state.traction_limit_ratio, 3),
            "voltageLevel": state.voltage_level,
            "energyKwh": round(state.energy_kwh, 4),
            "regenEnergyKwh": round(state.regen_energy_kwh, 4),
            "absorbedRegenKw": round(state.absorbed_regen_kw, 3),
            "wastedRegenKw": round(state.wasted_regen_kw, 3),
            "source": state.source,
            "quality": state.quality,
        }

    @staticmethod
    def _dispatch_snapshot(decision: DispatchDecision) -> JsonDict:
        return {
            "decisionId": decision.decision_id,
            "simTimeMs": decision.sim_time_ms,
            "trainId": decision.train_id,
            "stationId": decision.station_id,
            "action": decision.action,
            "durationSec": decision.duration_sec,
            "reason": decision.reason,
            "applied": decision.applied,
            "expectedImpact": decision.expected_impact or {},
        }

    @classmethod
    def load_from_files(
        cls,
        scenario_path: str | Path,
        line_map_path: str | Path,
        stations_csv_path: str | Path,
        recorder: RunRecorder | None = None,
    ) -> SimulationEngine:
        """便捷工厂: 从文件构建引擎."""
        scenario = ScenarioConfig.load(scenario_path)
        line_map = LineMapRepository(line_map_path).load()
        with Path(stations_csv_path).open("r", encoding="utf-8-sig", newline="") as handle:
            station_catalog = [
                {
                    "id": int(row["id"]),
                    "code": row["code"],
                    "name": row["name"],
                    "mileageM": float(row["mileage_m"]),
                    "speedLimitToNextKmh": int(row["speed_limit_to_next_kmh"]),
                    "dwellSeconds": int(row["dwell_s"]),
                }
                for row in csv.DictReader(handle)
            ]
        return cls(scenario, line_map, station_catalog, recorder)
