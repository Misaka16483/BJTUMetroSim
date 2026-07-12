"""仿真引擎 — 成员A: 时钟驱动 + 域服务编排 + 事件发布 + 数据记录."""

from __future__ import annotations

import csv
from collections import deque
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.clock import SimulationClock
from app.core.message_bus import Envelope, MessageBus
from app.core.scenario import ScenarioConfig, TrainConfig
from app.domain.control.models import AtoConfig, AtoTarget, OperationMode
from app.domain.control.movement_authority import MovementAuthority, MovementAuthorityService, TrainPosition
from app.domain.control.services import ATOController
from app.domain.dispatch.services import DispatchContext, DispatchDecision, RuleBasedDispatchService
from app.domain.interlocking.route_catalog import RouteCatalog
from app.domain.interlocking.route_chain_planner import RouteChainPlanner
from app.domain.interlocking.route_service import RouteService
from app.domain.interlocking.rule_engine import InterlockingRuleEngine
from app.domain.interlocking.section_occupation import SectionOccupationService
from app.domain.interlocking.signal_resolver import SignalAspectResolver
from app.domain.interlocking.switch_lock import SwitchLockService
from app.domain.interlocking.models import RouteRequest
from app.domain.power.line9_topology import load_line9_power_network
from app.domain.line.services import LineMapRepository, PathPlan, PathPlanner, PathTrackQuery, TrackQueryService
from app.domain.power.services import PowerSection, PowerService, TrainPowerRequest
from app.domain.signal.models import TrainState as InterlockingTrainState
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
from app.domain.vehicle.services import (
    BrakeBlendService,
    SimpleVehicleModel,
    TractionDriveModel,
    VehicleForceDemand,
)
from app.infra.recorder import RunRecorder


JsonDict = dict[str, Any]

# ── 列车运行阶段 ──
APPROACHING = "APPROACHING"     # 进站制动
DWELLING = "DWELLING"           # 停站上下客
DEPARTING = "DEPARTING"         # 出站加速
CRUISING = "CRUISING"           # 区间巡航
IDLE = "IDLE"                   # 尚未启动或已完成
WAITING_ROUTE = "WAITING_ROUTE"


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
    turnback_count: int = 0
    # ── 驾驶模式 ──
    operation_mode: str = "ATO"  # "ATO" or "MANUAL"
    # ── 驾驶台反馈字段 ──
    traction_percent: float = 0.0
    brake_percent: float = 0.0
    energy_kwh: float = 0.0
    target_speed_mps: float = 0.0
    estimated_run_time_s: float = 0.0   # 预计区间运行时间，由速度曲线积分得出
    path_position_m: float = 0.0
    path_total_length_m: float = 0.0
    current_segment_id: int | None = None
    current_segment_offset_m: float = 0.0
    movement_authority_end_m: float = 0.0
    movement_authority_reason: str = "UNAVAILABLE"
    movement_authority_speed_mps: float = 0.0
    movement_authority_service_brake_start_m: float = 0.0
    movement_authority_emergency_brake_start_m: float = 0.0
    local_speed_limit_mps: float = 22.22
    grade_ratio: float = 0.0
    path_segment_count: int = 0
    path_constraint_count: int = 0
    mass_kg: float = 225_000.0
    traction_force_n: float = 0.0
    electric_brake_force_n: float = 0.0
    pneumatic_brake_force_n: float = 0.0
    requested_power_kw: float = 0.0
    pantograph_voltage_v: float = 750.0
    traction_limit_ratio: float = 1.0
    regen_limit_ratio: float = 1.0
    power_limited_duration_sec: float = 0.0
    power_constraint_delay_sec: float = 0.0
    # ── profile 触发控制 ──
    _profile_triggered: bool = False
    _path_plan: PathPlan | None = field(default=None, repr=False, compare=False)
    _path_origin_station_index: int | None = field(default=None, repr=False, compare=False)
    _path_destination_station_index: int | None = field(default=None, repr=False, compare=False)
    _route_chain_ids: tuple[str, ...] = field(default=(), repr=False, compare=False)
    _route_retry_at_ms: int = field(default=0, repr=False, compare=False)
    _route_failure_reason: str | None = field(default=None, repr=False, compare=False)
    _initial_platform_id: int | None = field(default=None, repr=False, compare=False)
    # ── 手动驾驶 per-train 指令 ──
    _manual_command: ControlCommand | None = field(default=None, repr=False, compare=False)

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
            \
            "tractionPercent": round(self.traction_percent, 1),
            "brakePercent": round(self.brake_percent, 1),
            "energyKwh": round(self.energy_kwh, 2),
            "targetSpeedMps": round(self.target_speed_mps, 2),
            "estimatedRunTimeS": round(self.estimated_run_time_s, 1),
            "pathPositionM": round(self.path_position_m, 1),
            "pathTotalLengthM": round(self.path_total_length_m, 1),
            "currentSegmentId": self.current_segment_id,
            "currentSegmentOffsetM": round(self.current_segment_offset_m, 1),
            "movementAuthorityEndM": round(self.movement_authority_end_m, 1),
            "movementAuthorityReason": self.movement_authority_reason,
            "movementAuthoritySpeedMps": round(self.movement_authority_speed_mps, 2),
            "movementAuthorityServiceBrakeStartM": round(self.movement_authority_service_brake_start_m, 1),
            "movementAuthorityEmergencyBrakeStartM": round(self.movement_authority_emergency_brake_start_m, 1),
            "localSpeedLimitMps": round(self.local_speed_limit_mps, 2),
            "gradeRatio": round(self.grade_ratio, 7),
            "pathSegmentCount": self.path_segment_count,
            "pathConstraintCount": self.path_constraint_count,
            "massKg": round(self.mass_kg, 1),
            "tractionForceN": round(self.traction_force_n, 1),
            "electricBrakeForceN": round(self.electric_brake_force_n, 1),
            "pneumaticBrakeForceN": round(self.pneumatic_brake_force_n, 1),
            "requestedPowerKw": round(self.requested_power_kw, 3),
            "pantographVoltageV": round(self.pantograph_voltage_v, 2),
            "tractionLimitRatio": round(self.traction_limit_ratio, 4),
            "regenLimitRatio": round(self.regen_limit_ratio, 4),
            "powerLimitedDurationSec": round(self.power_limited_duration_sec, 3),
            "powerConstraintDelaySec": round(self.power_constraint_delay_sec, 3),
            "operationMode": self.operation_mode,
            "routeChainIds": list(self._route_chain_ids),
            "routeFailureReason": self._route_failure_reason,
            "routeRetryAtMs": self._route_retry_at_ms or None,
        }


@dataclass(frozen=True)
class PreparedTrainStep:
    train: SimTrainState
    next_idx: int
    next_station: JsonDict
    path_plan: PathPlan
    state: TrainState
    command: ControlCommand
    vehicle_config: VehicleConfig
    demand: VehicleForceDemand
    gradient_force_n: float
    movement_authority: MovementAuthority


@dataclass(frozen=True)
class PowerCommand:
    command_id: str
    command_type: str
    payload: JsonDict


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
    power_network: dict[str, Any] = field(default_factory=dict)
    dispatch_decisions: list[dict[str, Any]] = field(default_factory=list)
    kpi: dict[str, Any] = field(default_factory=dict)
    # 联锁数据（成员C Phase 2）
    interlocking: dict[str, Any] = field(default_factory=dict)


class SimulationEngine:
    """Phase 1 仿真引擎：单车为主，预留多车接口."""

    # ── 速度曲线参数 ──
    CRUISE_SPEED_MPS = 22.22  # 80 km/h 巡航
    ROUTE_LOOKAHEAD_DISTANCE_M = 200.0
    STATION_CAPTURE_DISTANCE_M = 10.0

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
        # Real-time playback is independent from the physical simulation timestep.
        self._tick_interval_seconds = scenario.tick_seconds
        self.bus = MessageBus()
        self.track_query = TrackQueryService(line_map)
        self.path_planner = PathPlanner(line_map)

        # ── 构建车站运行索引 ──
        self._station_list: list[JsonDict] = self._build_station_list()
        self._station_distances: list[float] = self._build_station_distances()
        self._station_platform_ids: dict[int, tuple[int, ...]] = self._build_station_platform_ids()
        self._platform_id_by_segment = {
            int(platform['segmentId']): int(platform['id'])
            for platform in line_map.get('platforms', [])
            if platform.get('id') is not None and platform.get('segmentId') is not None
        }

        # ── 列车状态 ──
        self.trains: list[SimTrainState] = []
        self._run_id: int | None = None

        # ── 域服务 ──
        self.station_service = self._build_station_service()
        self.power_service: PowerService = self._build_power_service()
        self.dispatch_service = RuleBasedDispatchService()

        # ── 联锁服务（成员C Phase 2 集成）──
        self._init_interlocking()

        self._last_arrivals_by_platform: dict[tuple[str, str], int] = {}
        self._last_power_states: dict[str, Any] = {}
        self._last_dispatch_decisions: list[DispatchDecision] = []
        self._pending_dispatch_decisions: list[DispatchDecision] = []
        self._power_commands: deque[PowerCommand] = deque()
        self._power_command_sequence = 0
        self._power_command_results: list[JsonDict] = []
        self._recorded_power_command_ids: set[str] = set()

        # ── 物理模型：PathPlan-aware ATO + DCDP 规划曲线 ──
        self._ato_config = AtoConfig(
            target_cruise_speed_mps=self.CRUISE_SPEED_MPS,
            expected_deceleration_mps2=0.6,
            use_dynamic_programming_profile=scenario.use_dynamic_programming_profile,
            profile_position_step_m=10.0,
            profile_speed_step_mps=1.0,
            profile_max_states_per_stage=700,
        )
        self.ato = ATOController(self._ato_config)
        self._ato_by_train: dict[str, ATOController] = {}
        self._dcdp_curve_data: dict[str, list[dict[str, Any]]] = {}     # 规划曲线
        self._dcdp_curve_meta: dict[str, dict[str, Any]] = {}
        self._profile_run_times: dict[str, float] = {}                  # 预计区间运行时间

        # ── 用户配置的车辆参数（per-train） ──
        self._vehicle_config_by_train: dict[str, VehicleConfig] = {}

        # ── 手动驾驶模式（per-train） ──
        self._manual_mode_by_train: dict[str, bool] = {}

        # ── 线程安全 ──
        self._lock = threading.Lock()
        self._power_lock = threading.RLock()
        self._snapshot: TickSnapshot | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # ═══════════════════════════════════════════════════════════
    #  公共接口
    # ═══════════════════════════════════════════════════════════

    def set_vehicle_config(self, data: JsonDict) -> VehicleConfig:
        """接收前端传来的车辆参数并保存（默认用于所有新车）."""
        train_id = data.get("trainId", self.trains[0].train_id if self.trains else "T0901")
        vcfg = VehicleConfig.from_user_config(train_id, data)
        self._vehicle_config_by_train[train_id] = vcfg
        for train in self.trains:
            if train.train_id == train_id:
                train.mass_kg = vcfg.mass_kg
                train.capacity_pax = 600
        return vcfg

    def set_train_vehicle_config(self, train_id: str, data: JsonDict) -> VehicleConfig:
        """为指定列车设置车辆参数."""
        vcfg = VehicleConfig.from_user_config(train_id, data)
        self._vehicle_config_by_train[train_id] = vcfg
        for train in self.trains:
            if train.train_id == train_id:
                train.mass_kg = vcfg.mass_kg
        return vcfg

    def set_manual_mode(self, train_id: str, enabled: bool) -> dict:
        """切换指定列车的 MANUAL/ATO 模式."""
        for train in self.trains:
            if train.train_id == train_id:
                train.operation_mode = "MANUAL" if enabled else "ATO"
                if not enabled:
                    train._manual_command = None
                self._manual_mode_by_train[train_id] = enabled
                return {"ok": True, "trainId": train_id, "manualMode": enabled}
        return {"ok": False, "error": "TRAIN_NOT_FOUND"}

    def set_manual_command(self, train_id: str, traction_percent: float, brake_percent: float) -> dict:
        """接收指定列车的手动驾驶指令."""
        for train in self.trains:
            if train.train_id == train_id:
                if train.operation_mode != "MANUAL":
                    return {"ok": False, "error": "NOT_IN_MANUAL_MODE"}
                train._manual_command = ControlCommand(
                    train_id=train_id,
                    traction_percent=max(0.0, min(100.0, traction_percent)),
                    brake_percent=max(0.0, min(100.0, brake_percent)),
                    source=CommandSource.MANUAL,
                )
                return {"ok": True, "trainId": train_id, "tractionPercent": train._manual_command.traction_percent, "brakePercent": train._manual_command.brake_percent}
        return {"ok": False, "error": "TRAIN_NOT_FOUND"}

    def _make_vehicle_config(self, train_id: str) -> VehicleConfig:
        """构建当前应使用的车辆配置."""
        vcfg = self._vehicle_config_by_train.get(train_id)
        if vcfg is not None:
            return VehicleConfig(
                train_id=train_id,
                formation=vcfg.formation,
                car_masses_kg=vcfg.car_masses_kg,
                head_car_length_m=vcfg.head_car_length_m,
                middle_car_length_m=vcfg.middle_car_length_m,
                wheel_radius_m=vcfg.wheel_radius_m,
                max_speed_mps=vcfg.max_speed_mps,
                max_traction_force_n=vcfg.max_traction_force_n,
                max_service_brake_force_n=vcfg.max_service_brake_force_n,
                emergency_brake_force_n=vcfg.emergency_brake_force_n,
                basic_resistance_n=vcfg.basic_resistance_n,
                motor_count=vcfg.motor_count,
                gear_ratio=vcfg.gear_ratio,
                drivetrain_efficiency=vcfg.drivetrain_efficiency,
                regen_efficiency=vcfg.regen_efficiency,
                auxiliary_power_kw=vcfg.auxiliary_power_kw,
                nominal_line_voltage_v=vcfg.nominal_line_voltage_v,
            )
        return VehicleConfig(train_id=train_id)

    def _manual_override(self, ato_cmd: ControlCommand, train_id: str) -> ControlCommand:
        """如果该列车处于手动模式，用其手动指令替代 ATO 指令."""
        for train in self.trains:
            if train.train_id == train_id:
                if train.operation_mode != "MANUAL" or train._manual_command is None:
                    return ato_cmd
                mc = train._manual_command
                return ControlCommand(
                    train_id=train_id,
                    traction_percent=mc.traction_percent,
                    brake_percent=mc.brake_percent,
                    source=CommandSource.MANUAL,
                )
        return ato_cmd

    def add_train(self, payload: JsonDict) -> JsonDict:
        """动态添加一列车。"""
        # A new train after Stop begins a fresh initialized simulation instead
        # of being discarded by the subsequent start() call.
        if self.clock.state.value in ("IDLE", "STOPPED"):
            self.load()
        train_id = str(payload.get("trainId", ""))
        if not train_id:
            return {"ok": False, "error": "MISSING_TRAIN_ID"}
        for t in self.trains:
            if t.train_id == train_id:
                return {"ok": False, "error": "TRAIN_ID_EXISTS"}

        initial_station_code = str(payload.get("initialStationCode", self._station_list[0].get("code", "GGZ")))
        initial_segment_id = payload.get("initialSegmentId")
        direction = str(payload.get("direction", "UP"))
        operation_mode = str(payload.get("operationMode", "ATO"))
        if operation_mode not in ("ATO", "MANUAL"):
            operation_mode = "ATO"

        cfg = TrainConfig(
            train_id=train_id,
            line_id="9",
            initial_station_code=initial_station_code,
            direction=direction,
            capacity_pax=int(payload.get("capacityPax", 600)),
            initial_load_pax=int(payload.get("initialLoadPax", 0)),
        )
        train = self._create_train(cfg)
        if initial_segment_id is not None:
            try:
                platform_id = self._platform_id_by_segment.get(int(initial_segment_id))
            except (TypeError, ValueError):
                platform_id = None
            if platform_id not in self._station_platform_ids.get(train.station_index, ()):
                return {'ok': False, 'error': 'INITIAL_SEGMENT_NOT_PLATFORM'}
            if direction not in self.available_initial_directions(initial_station_code, int(initial_segment_id)):
                return {'ok': False, 'error': 'INITIAL_SEGMENT_DIRECTION_UNAVAILABLE'}
            train._initial_platform_id = platform_id

        # Resolve the physical start position before admitting the train. Route
        # locking only prevents a conflicting train from moving; it cannot undo
        # two train bodies that were spawned on top of each other.
        next_index = train.station_index + (1 if direction == "UP" else -1)
        if not 0 <= next_index < len(self._station_list):
            return {"ok": False, "error": "INITIAL_DIRECTION_TERMINAL"}
        if self._ensure_interval_path(train, next_index) is None:
            return {"ok": False, "error": "INITIAL_ROUTE_UNAVAILABLE"}
        conflicting_train_ids = self._initial_placement_conflicts(train)
        if conflicting_train_ids:
            return {
                "ok": False,
                "error": "INITIAL_PLACEMENT_OCCUPIED",
                "conflictingTrainIds": conflicting_train_ids,
            }
        conflicting_route_ids = self._initial_placement_route_locks(train)
        if conflicting_route_ids:
            return {
                "ok": False,
                "error": "INITIAL_PLACEMENT_ROUTE_LOCKED",
                "conflictingRouteIds": conflicting_route_ids,
            }
        train.operation_mode = operation_mode
        if operation_mode == "MANUAL":
            self._manual_mode_by_train[train_id] = True

        # 如有用户车辆参数，应用之
        vehicle_data = payload.get("vehicleConfig")
        if vehicle_data and isinstance(vehicle_data, dict):
            vcfg = VehicleConfig.from_user_config(train_id, vehicle_data)
            self._vehicle_config_by_train[train_id] = vcfg
            train.mass_kg = vcfg.mass_kg

        self.trains.append(train)
        self._refresh_interlocking()
        self._snapshot = self._build_snapshot()
        return {"ok": True, "train": train.to_dict()}

    def available_initial_directions(
        self,
        station_code: str,
        initial_segment_id: int,
    ) -> tuple[str, ...]:
        """Return train directions with a route-table chain from this platform Seg."""
        station_index = next((index for index, station in enumerate(self._station_list) if station.get("code") == station_code), None)
        platform_id = self._platform_id_by_segment.get(initial_segment_id)
        if station_index is None or platform_id not in self._station_platform_ids.get(station_index, ()):
            return ()
        directions: list[str] = []
        for direction, offset in (("UP", 1), ("DOWN", -1)):
            destination_index = station_index + offset
            if not 0 <= destination_index < len(self._station_list):
                continue
            if self._route_plan_for_station_pair(station_index, destination_index, preferred_origin_platform_id=platform_id) is not None:
                directions.append(direction)
        return tuple(directions)

    def _initial_placement_conflicts(self, candidate: SimTrainState) -> list[str]:
        """Return existing train IDs whose body overlaps a candidate start.

        This is intentionally a placement rule, not MA behavior. MA supervises
        an admitted train in motion; a train that begins inside another train's
        footprint is already an invalid state and must be rejected outright.
        """
        candidate_footprint = self._train_physical_footprint(candidate)
        if not candidate_footprint:
            return []
        return [
            existing.train_id
            for existing in self.trains
            if candidate_footprint & self._train_physical_footprint(existing)
        ]

    def _train_physical_footprint(self, train: SimTrainState) -> set[int]:
        if train.current_segment_id is None:
            return set()
        state = InterlockingTrainState(
            train_id=train.train_id,
            sim_time_ms=self._absolute_sim_time_ms(),
            seg_id=train.current_segment_id,
            offset_m=train.current_segment_offset_m,
            position_m=train.path_position_m,
            speed_mps=train.speed_mps,
            direction="FORWARD" if train.direction == "UP" else "BACKWARD",
            length_m=self._make_vehicle_config(train.train_id).train_length_m,
            path_track=self._interlocking_path_track(train),
            operation_mode=train.operation_mode,
            sim_time_s=self.clock.sim_time_seconds,
        )
        return self.section_occupation.physical_footprint(
            state, state.path_track or self.track_query,
        )

    def _initial_placement_route_locks(self, candidate: SimTrainState) -> list[str]:
        """Reject a new body placed inside another train's locked route.

        A locked route may be clear of trains while reserved for its owner.  A
        placement test must honour that reservation rather than waiting for the
        next tick's route request to discover the conflict.
        """
        footprint = self._train_physical_footprint(candidate)
        if not footprint:
            return []
        footprint_sections = set(self.route_catalog.axle_section_ids_covering_segments(footprint))
        if not footprint_sections:
            return []
        return [
            str(route["routeId"])
            for route in self.route_service.snapshot()
            if route.get("state") in ("LOCKED", "APPROACH_LOCKED")
            and footprint_sections & {str(section_id) for section_id in route.get("lockedSections", [])}
        ]
    def remove_train(self, train_id: str) -> JsonDict:
        """Dynamically remove a train and all interlocking state it owns."""
        if not any(train.train_id == train_id for train in self.trains):
            return {"ok": False, "error": "TRAIN_NOT_FOUND"}

        # A removed train has no physical tail that can trigger normal route
        # release.  Explicitly clear only its locks before dropping its state,
        # otherwise an orphaned route blocks later trains indefinitely.
        released_route_ids = self.route_service.release_routes_owned_by(train_id)
        self.trains = [train for train in self.trains if train.train_id != train_id]
        self._vehicle_config_by_train.pop(train_id, None)
        self._manual_mode_by_train.pop(train_id, None)
        self._dcdp_curve_data.pop(train_id, None)
        self._dcdp_curve_meta.pop(train_id, None)
        self._profile_run_times.pop(train_id, None)
        self._ato_by_train.pop(train_id, None)
        self._refresh_interlocking()
        self._snapshot = self._build_snapshot()
        return {"ok": True, "removed": train_id, "releasedRouteIds": released_route_ids}
    def load(self) -> None:
        """加载场景，初始化仿真（初始无车，由前端动态加车）."""
        self.clock.load()
        self._init_interlocking()
        self._last_arrivals_by_platform = {}
        self._last_power_states = self._empty_power_states()
        self._last_dispatch_decisions = []
        self._pending_dispatch_decisions = []
        self._ato_by_train = {}
        self._dcdp_curve_data = {}
        self._dcdp_curve_meta = {}
        self._profile_run_times = {}
        self._power_commands.clear()
        self._power_command_results = []
        self._power_command_sequence = 0
        self._recorded_power_command_ids = set()
        self._manual_mode_by_train = {}
        self._vehicle_config_by_train = {}
        self.trains = []  # 初始无车，由前端动态加车
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
            if self.power_service.network is not None:
                self.recorder.upsert_power_topology(self.power_service.network.topology_dict())

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

    def set_tick_interval_seconds(self, interval_seconds: float) -> float:
        """Set real-time playback pacing without changing the simulation timestep."""
        self._tick_interval_seconds = min(2.0, max(0.06, float(interval_seconds)))
        return self._tick_interval_seconds

    def step_once(self) -> None:
        """Advance exactly one simulation tick without starting the run loop."""
        if self.clock.state.value in ("IDLE", "STOPPED"):
            self.load()
        if self.clock.state.value == "LOADED":
            self.clock.start()
        elif self.clock.state.value == "PAUSED":
            self.clock.resume()
        elif self.clock.state.value == "RUNNING":
            raise RuntimeError("cannot single-step while simulation is running")
        try:
            self._tick()
        finally:
            if self.clock.state.value == "RUNNING":
                self.clock.pause()
            self._snapshot = self._build_snapshot()

    def reset_power_network(self) -> None:
        """Restore traction-power topology and clear transient power states."""
        with self._power_lock:
            self.power_service = self._build_power_service()
            self._last_power_states = self._empty_power_states()
            self._snapshot = self._build_snapshot()

    def queue_power_command(self, command_type: str, payload: JsonDict) -> JsonDict:
        """Queue an external topology operation for deterministic application at a tick boundary."""
        with self._power_lock:
            self._power_command_sequence += 1
            command = PowerCommand(
                command_id=f"PWR-CMD-{self._power_command_sequence:06d}",
                command_type=command_type,
                payload=dict(payload),
            )
            self._power_commands.append(command)
            return {"commandId": command.command_id, "status": "QUEUED"}

    def _apply_power_commands(self, sim_time_ms: int) -> None:
        with self._power_lock:
            while self._power_commands:
                command = self._power_commands.popleft()
                result: JsonDict = {
                    "commandId": command.command_id,
                    "commandType": command.command_type,
                    "simTimeMs": sim_time_ms,
                }
                try:
                    if command.command_type == "SUBSTATION_OUTAGE":
                        result["data"] = self.apply_power_substation_outage(
                            str(command.payload["targetId"]),
                            big_bilateral=bool(command.payload.get("bigBilateral", True)),
                        )
                    elif command.command_type == "OPERATE_SWITCH":
                        switch = self.operate_power_switch(
                            str(command.payload["switchId"]),
                            str(command.payload["state"]),
                        )
                        result["data"] = {
                            "switchId": switch.switch_id,
                            "currentState": switch.current_state,
                        }
                    elif command.command_type == "RESET_NETWORK":
                        self.power_service = self._build_power_service()
                        self._last_power_states = self._empty_power_states()
                        result["data"] = {"action": "power_reset"}
                    else:
                        raise ValueError("UNSUPPORTED_POWER_COMMAND")
                    result["status"] = "APPLIED"
                except Exception as exc:
                    result["status"] = "REJECTED"
                    result["error"] = str(exc)
                self._power_command_results.append(result)
            self._power_command_results = self._power_command_results[-20:]

    def apply_power_substation_outage(
        self,
        substation_id: str,
        *,
        big_bilateral: bool = True,
    ) -> dict[str, list[str] | str]:
        """Apply a topology fault atomically with respect to the power-flow tick."""
        with self._power_lock:
            network = self.power_service.network
            if network is None:
                raise RuntimeError("POWER_NETWORK_NOT_INITIALIZED")
            result = network.apply_substation_outage(
                substation_id,
                big_bilateral=big_bilateral,
            )
            self._last_power_states = self._empty_power_states()
            self._snapshot = self._build_snapshot()
            return result

    def operate_power_switch(self, switch_id: str, state: str):
        """Operate a power switch atomically with respect to the power-flow tick."""
        with self._power_lock:
            network = self.power_service.network
            if network is None:
                raise RuntimeError("POWER_NETWORK_NOT_INITIALIZED")
            switch = network.operate_switch(switch_id, state)
            self._last_power_states = self._empty_power_states()
            self._snapshot = self._build_snapshot()
            return switch

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
            sleep_sec = max(0.06, self._tick_interval_seconds - elapsed)
            time.sleep(sleep_sec)

    def _tick(self) -> None:
        """单步仿真."""
        self.clock.step()
        tick = self.clock.current_tick
        sim_time_ms = self._absolute_sim_time_ms()

        # External topology operations are serialized at the tick boundary.
        self._apply_power_commands(sim_time_ms)

        # 1) 客流先到达，停站处理生成的载荷供本tick车辆请求使用。
        self._last_arrivals_by_platform = self.station_service.update_arrivals(
            sim_time_ms,
            dt_sec=self.clock.tick_seconds,
        )
        self._refresh_interlocking()

        # 2) 生成全部列车控制与候选牵引/再生请求，不推进位置。
        movement_positions = self._movement_position_snapshot()
        prepared_steps: dict[str, PreparedTrainStep] = {}
        handled_train_ids: set[str] = set()
        for train in self.trains:
            handled, prepared = self._prepare_train_step(train, sim_time_ms, movement_positions)
            if handled:
                handled_train_ids.add(train.train_id)
            if prepared is not None:
                prepared_steps[train.train_id] = prepared

        # 3) 同时求解全部列车负荷，得到本tick电压、限牵和再生能力。
        power_states = self._update_power(sim_time_ms, prepared_steps)
        self._last_power_states = power_states

        train_power_flows = {
            item.train_id: item
            for item in (self.power_service.last_network_snapshot.trains if self.power_service.last_network_snapshot else [])
        }

        # 4) 使用本tick供电反馈分配实际牵引/电制动/空气制动并推进动力学。
        for prepared in prepared_steps.values():
            self._apply_prepared_train_step(prepared, train_power_flows.get(prepared.train.train_id), sim_time_ms)
        for train in self.trains:
            if train.train_id not in handled_train_ids:
                self._advance_train(train, sim_time_ms)

        self._refresh_interlocking()

        # 5) 调度决策
        decisions = [*self._pending_dispatch_decisions, *self._make_dispatch_decisions(sim_time_ms, power_states)]
        self._pending_dispatch_decisions = []
        self._last_dispatch_decisions = decisions

        # 6) 发布事件
        for train in self.trains:
            self.bus.publish(
                "train.state",
                train.to_dict(),
                source="engine",
                tick=tick,
            )
        self.bus.publish("clock.tick", {"tick": tick, "simTimeMs": sim_time_ms}, source="engine", tick=tick)

        # 7) 记录到 SQLite
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
            network_snapshot = self.power_service.last_network_snapshot
            if network_snapshot is not None:
                for train_flow in network_snapshot.trains:
                    self.recorder.record_train_voltage(
                        self._run_id,
                        sim_time_ms=sim_time_ms,
                        train_id=train_flow.train_id,
                        power_section_id=train_flow.power_section_id,
                        mileage_m=train_flow.mileage_m,
                        voltage_v=train_flow.voltage_v,
                        current_a=train_flow.current_a,
                        requested_power_kw=train_flow.requested_power_kw,
                        traction_limit_ratio=train_flow.traction_limit_ratio,
                        regen_limit_ratio=train_flow.regen_limit_ratio,
                        voltage_level=train_flow.voltage_level,
                        detail={"tick": tick},
                    )
                for substation_flow in network_snapshot.substations:
                    self.recorder.record_substation_power(
                        self._run_id,
                        sim_time_ms=sim_time_ms,
                        substation_id=substation_flow.substation_id,
                        voltage_v=substation_flow.voltage_v,
                        current_a=substation_flow.current_a,
                        power_kw=substation_flow.power_kw,
                        energy_kwh=substation_flow.energy_kwh,
                        load_ratio=substation_flow.load_ratio,
                        status=substation_flow.status,
                        detail={"tick": tick},
                    )
                self.recorder.record_regen_energy(
                    self._run_id,
                    sim_time_ms=sim_time_ms,
                    generated_regen_kw=network_snapshot.generated_regen_kw,
                    absorbed_regen_kw=network_snapshot.absorbed_regen_kw,
                    feedback_regen_kw=network_snapshot.feedback_regen_kw,
                    wasted_regen_kw=network_snapshot.wasted_regen_kw,
                    detail={"tick": tick, "alerts": network_snapshot.alerts},
                )
                self.recorder.record_power_solver(
                    self._run_id,
                    sim_time_ms=sim_time_ms,
                    converged=network_snapshot.converged,
                    iterations=network_snapshot.iterations,
                    solve_time_ms=network_snapshot.solve_time_ms,
                    power_balance_error_kw=network_snapshot.power_balance_error_kw,
                    power_balance_error_ratio=network_snapshot.power_balance_error_ratio,
                    detail={"tick": tick},
                )
            for command_result in self._power_command_results:
                command_id = str(command_result["commandId"])
                if command_id in self._recorded_power_command_ids:
                    continue
                self.recorder.record_power_command(
                    self._run_id,
                    command_id=command_id,
                    sim_time_ms=int(command_result["simTimeMs"]),
                    command_type=str(command_result["commandType"]),
                    status=str(command_result["status"]),
                    payload=command_result.get("data", {}),
                    error=command_result.get("error"),
                )
                self._recorded_power_command_ids.add(command_id)

        # 8) 更新快照
        self._snapshot = self._build_snapshot()

    # ═══════════════════════════════════════════════════════════
    #  列车推进逻辑
    # ═══════════════════════════════════════════════════════════

    def _prepare_train_step(
        self,
        train: SimTrainState,
        sim_time_ms: int,
        movement_positions: tuple[TrainPosition, ...],
    ) -> tuple[bool, PreparedTrainStep | None]:
        """Prepare a PathPlan train without advancing physics; return whether the train was handled."""
        stations = self._station_list
        n = len(stations)
        if (train.direction == "UP" and train.station_index >= n - 1) or (
            train.direction == "DOWN" and train.station_index <= 0
        ):
            return self._handle_terminal_turnback(train, sim_time_ms)

        next_idx = train.station_index + 1 if train.direction == "UP" else train.station_index - 1
        if next_idx < 0 or next_idx >= n:
            return True, None
        path_plan = self._ensure_interval_path(train, next_idx)
        if path_plan is None:
            train.phase = WAITING_ROUTE
            train.speed_mps = 0.0
            train.traction_percent = 0.0
            train.brake_percent = 20.0
            train._route_failure_reason = "NO_ROUTE_CHAIN"
            return True, None

        dt = self.clock.tick_seconds
        if train.phase in (DWELLING, IDLE, WAITING_ROUTE):
            train.speed_mps = 0.0
            train.path_position_m = 0.0
            train.path_total_length_m = path_plan.total_length_m
            train.distance_to_next_m = path_plan.total_length_m
            train.target_distance_m = path_plan.total_length_m
            train.segment_progress = 0.0
            train.traction_percent = 0.0
            train.brake_percent = 0.0 if train.phase == IDLE else 20.0
            train.target_speed_mps = 0.0
            train.traction_force_n = 0.0
            train.electric_brake_force_n = 0.0
            train.pneumatic_brake_force_n = 0.0
            self._update_train_path_context(train)

            limit_source_idx = train.station_index if train.direction == "UP" else next_idx
            limit_kmh = int(self._station_list[limit_source_idx].get("speedLimitToNextKmh", 80))
            if limit_kmh <= 0:
                limit_kmh = 80
            train.permitted_speed_mps = limit_kmh / 3.6
            train.local_speed_limit_mps = path_plan.speed_limit_at(0.0, train.permitted_speed_mps)
            if not train._profile_triggered:
                train._profile_triggered = True
                self._prime_path_profile(train, path_plan)
            # Pre-lock the departure route during dwell.  This is deliberately
            # before the dwell timer expires: otherwise the route and MA exist for
            # only one movement tick before the train enters, so the topology can
            # miss the yellow/green departure aspect and the train may fall into
            # WAITING_ROUTE exactly at departure time.  Do not move this back to
            # the post-dwell branch without replacing this pre-authorisation flow.
            if train.phase == DWELLING:
                self._ensure_route_chain_locked(train, sim_time_ms)
            if train.dwell_remaining_sec > 0:
                train.dwell_remaining_sec = max(0.0, train.dwell_remaining_sec - dt)
                return True, None
            train.dwell_remaining_sec = 0.0
            if not self._ensure_route_chain_locked(train, sim_time_ms):
                train.phase = WAITING_ROUTE
                train.speed_mps = 0.0
                train.traction_percent = 0.0
                train.brake_percent = 20.0
                train.target_speed_mps = 0.0
                return True, None
            train.estimated_run_time_s = self._profile_run_times.get(train.train_id, 0.0)
            train.phase = DEPARTING

        path_position_m = min(max(0.0, train.path_position_m), path_plan.total_length_m)
        self._update_train_path_context(train)
        state = TrainState(
            train_id=train.train_id,
            position_m=path_position_m,
            speed_mps=train.speed_mps,
            sim_time_s=self.clock.sim_time_seconds,
            segment_id=train.current_segment_id,
            net_energy_kwh=train.energy_kwh,
        )
        vehicle_config = self._make_vehicle_config(train.train_id)
        self._attempt_route_lookahead(train, path_plan, path_position_m, sim_time_ms)
        authority = self.movement_authority_service.calculate(
            train_id=train.train_id,
            path_plan=path_plan,
            route_chain_ids=train._route_chain_ids,
            position_m=path_position_m,
            speed_mps=train.speed_mps,
            vehicle=vehicle_config,
            other_trains=movement_positions,
        )
        train.movement_authority_end_m = authority.end_position_m
        train.movement_authority_reason = authority.end_reason
        train.movement_authority_speed_mps = authority.permitted_speed_mps
        train.movement_authority_service_brake_start_m = authority.service_brake_start_m
        train.movement_authority_emergency_brake_start_m = authority.emergency_brake_start_m
        target = AtoTarget(
            target_position_m=authority.end_position_m,
            permitted_speed_mps=min(train.permitted_speed_mps, authority.permitted_speed_mps),
            path_plan=path_plan,
        )
        ato = self._ato_for_train(train.train_id)
        command = ato.decide(state, target)
        command = self._manual_override(command, train.train_id)
        command = self.movement_authority_service.supervise(
            command, authority, position_m=path_position_m, speed_mps=train.speed_mps,
        )
        demand = TractionDriveModel(vehicle_config).demand(command, train.speed_mps)
        gradient_force_n = vehicle_config.mass_kg * 9.80665 * path_plan.grade_ratio_at(path_position_m)
        return True, PreparedTrainStep(
            train=train,
            next_idx=next_idx,
            next_station=stations[next_idx],
            path_plan=path_plan,
            state=state,
            command=command,
            vehicle_config=vehicle_config,
            demand=demand,
            gradient_force_n=gradient_force_n,
            movement_authority=authority,
        )

    def _apply_prepared_train_step(self, prepared: PreparedTrainStep, flow: Any, sim_time_ms: int) -> None:
        train = prepared.train
        try:
            traction_limit = float(flow.traction_limit_ratio) if flow is not None else 1.0
            regen_limit = float(flow.regen_limit_ratio) if flow is not None else 1.0
            blend = BrakeBlendService.blend(prepared.demand, regen_limit)
            traction_force_n = prepared.demand.traction_force_n * max(0.0, min(1.0, traction_limit))
            model = SimpleVehicleModel(prepared.vehicle_config)
            result = model.step_with_forces(
                prepared.state,
                traction_force_n=traction_force_n,
                brake_force_n=blend.total_brake_force_n,
                dt_s=self.clock.tick_seconds,
                gradient_force_n=prepared.gradient_force_n,
            )

            path_plan = prepared.path_plan
            new_position_m = min(max(0.0, result.position_m), path_plan.total_length_m)
            next_limit_mps = path_plan.speed_limit_at(new_position_m, train.permitted_speed_mps)
            train.speed_mps = min(
                max(0.0, result.speed_mps),
                next_limit_mps,
                prepared.movement_authority.permitted_speed_mps,
            )
            train.path_position_m = new_position_m
            train.path_total_length_m = path_plan.total_length_m
            train.segment_progress = min(1.0, new_position_m / path_plan.total_length_m) if path_plan.total_length_m else 1.0
            train.distance_to_next_m = max(0.0, path_plan.total_length_m - new_position_m)
            train.target_distance_m = path_plan.total_length_m
            train.traction_percent = prepared.command.traction_percent
            train.brake_percent = prepared.command.brake_percent
            train.target_speed_mps = self._ato_for_train(train.train_id).last_target_speed_mps
            train.energy_kwh = result.net_energy_kwh
            train.mass_kg = prepared.vehicle_config.mass_kg
            train.traction_force_n = traction_force_n
            train.electric_brake_force_n = blend.electric_brake_force_n
            train.pneumatic_brake_force_n = blend.pneumatic_brake_force_n
            train.traction_limit_ratio = max(0.0, min(1.0, traction_limit))
            train.regen_limit_ratio = max(0.0, min(1.0, regen_limit))
            if prepared.command.traction_percent > 0 and train.traction_limit_ratio < 0.999:
                train.power_limited_duration_sec += self.clock.tick_seconds
                train.power_constraint_delay_sec += self.clock.tick_seconds * (
                    1.0 / max(train.traction_limit_ratio, 0.1) - 1.0
                )
            if flow is not None:
                train.requested_power_kw = float(flow.requested_power_kw)
                train.pantograph_voltage_v = float(flow.voltage_v)
            self._update_train_path_context(train)

            arrived = train.distance_to_next_m <= max(self._ato_config.stop_tolerance_m, self.STATION_CAPTURE_DISTANCE_M) and train.speed_mps <= 0.2
            if arrived:
                self._complete_path_arrival(train, prepared.next_idx, prepared.next_station, sim_time_ms)
                return

            ato = self._ato_for_train(train.train_id)
            cruise_threshold = min(self.CRUISE_SPEED_MPS, train.local_speed_limit_mps) * 0.95
            braking_profile = ato.last_profile_mode == "MAX_BRAKE" or ato.last_profile_mode.startswith("BRAKE_")
            if train.speed_mps >= cruise_threshold:
                train.phase = CRUISING
            elif (prepared.command.brake_percent > 5 or braking_profile) and train.speed_mps > 0.5:
                train.phase = APPROACHING
            else:
                train.phase = DEPARTING
        except Exception as exc:
            print(f"[Engine] Prepared advancement failed for {train.train_id}: {exc}")
            train.traction_percent = 0.0
            train.brake_percent = 0.0
            train.traction_force_n = 0.0
            train.electric_brake_force_n = 0.0
            train.pneumatic_brake_force_n = 0.0
            train.speed_mps = max(0.0, train.speed_mps - 0.8 * self.clock.tick_seconds)

    def _advance_train(self, train: SimTrainState, sim_time_ms: int) -> None:
        """每 tick 推进一辆列车 — ATO 决策 + 牛顿物理模型."""
        dt = self.clock.tick_seconds
        stations = self._station_list
        n = len(stations)

        if (train.station_index >= n - 1 and train.direction == "UP") or (
            train.station_index <= 0 and train.direction == "DOWN"
        ):
            self._handle_terminal_turnback(train, sim_time_ms)
            return

        next_idx = train.station_index + 1 if train.direction == "UP" else train.station_index - 1
        if next_idx < 0 or next_idx >= n:
            train.phase = IDLE
            train.speed_mps = 0
            return

        next_stn = stations[next_idx]
        path_plan = self._ensure_interval_path(train, next_idx)
        dist = (
            path_plan.total_length_m
            if path_plan is not None
            else abs(self._station_distances[next_idx] - self._station_distances[train.station_index])
        )

        if path_plan is not None:
            self._advance_train_on_path(train, next_idx, next_stn, path_plan, sim_time_ms, dist, dt)
            return

        # ── DWELLING ──
        if train.phase in (DWELLING, IDLE):
            train.speed_mps = 0
            train.distance_to_next_m = dist
            train.segment_progress = 0
            train.traction_percent = 0.0
            train.brake_percent = 0.0 if train.phase == IDLE else 20.0
            train.target_speed_mps = 0.0

            # 从 CSV 读取当前区间线路限速
            limit_source_idx = train.station_index if train.direction == "UP" else next_idx
            limit_kmh = int(self._station_list[limit_source_idx].get("speedLimitToNextKmh", 80))
            if limit_kmh <= 0:
                limit_kmh = 80
            train.permitted_speed_mps = limit_kmh / 3.6

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

            if train.operation_mode != "MANUAL":
                if profile_mode == "MAX_TRACTION" and train.speed_mps < effective_limit - 0.2:
                    cmd = ControlCommand(
                        train_id=train.train_id,
                        traction_percent=100.0,
                        source=CommandSource.ATO,
                    )
            else:
                cmd = self._manual_override(cmd, train.train_id)

            vcfg = self._make_vehicle_config(train.train_id)
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
                self._dcdp_curve_meta.pop(train.train_id, None)
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

    def _advance_train_on_path(
        self,
        train: SimTrainState,
        next_idx: int,
        next_stn: JsonDict,
        path_plan: PathPlan,
        sim_time_ms: int,
        dist: float,
        dt: float,
    ) -> None:
        """按 PathPlan 局部坐标推进一辆车."""
        if train.phase in (DWELLING, IDLE):
            train.speed_mps = 0.0
            train.path_position_m = 0.0
            train.path_total_length_m = dist
            train.distance_to_next_m = dist
            train.target_distance_m = dist
            train.segment_progress = 0.0
            train.traction_percent = 0.0
            train.brake_percent = 0.0 if train.phase == IDLE else 20.0
            train.target_speed_mps = 0.0
            self._update_train_path_context(train)

            limit_source_idx = train.station_index if train.direction == "UP" else next_idx
            limit_kmh = int(self._station_list[limit_source_idx].get("speedLimitToNextKmh", 80))
            if limit_kmh <= 0:
                limit_kmh = 80
            train.permitted_speed_mps = limit_kmh / 3.6
            train.local_speed_limit_mps = path_plan.speed_limit_at(train.path_position_m, train.permitted_speed_mps)

            if not train._profile_triggered:
                train._profile_triggered = True
                self._prime_path_profile(train, path_plan)

            if train.dwell_remaining_sec > 0:
                train.dwell_remaining_sec = max(0.0, train.dwell_remaining_sec - dt)
                return

            train.dwell_remaining_sec = 0.0
            train.estimated_run_time_s = self._profile_run_times.get(train.train_id, 0.0)
            train.phase = DEPARTING

        try:
            path_position_m = min(max(0.0, train.path_position_m), path_plan.total_length_m)
            self._update_train_path_context(train)

            state = TrainState(
                train_id=train.train_id,
                position_m=path_position_m,
                speed_mps=train.speed_mps,
                sim_time_s=self.clock.sim_time_seconds,
                segment_id=train.current_segment_id,
                net_energy_kwh=train.energy_kwh,
            )
            target = AtoTarget(
                target_position_m=path_plan.total_length_m,
                permitted_speed_mps=train.permitted_speed_mps,
                path_plan=path_plan,
            )
            ato = self._ato_for_train(train.train_id)
            cmd = ato.decide(state, target)
            cmd = self._manual_override(cmd, train.train_id)
            vcfg = self._make_vehicle_config(train.train_id)
            vm = SimpleVehicleModel(vcfg)
            gradient_force_n = vcfg.mass_kg * 9.80665 * path_plan.grade_ratio_at(path_position_m)
            result = vm.step(
                state,
                cmd,
                dt,
                traction_limit_ratio=self._traction_limit_for_train(train),
                gradient_force_n=gradient_force_n,
            )

            new_position_m = min(max(0.0, result.position_m), path_plan.total_length_m)
            next_limit_mps = path_plan.speed_limit_at(new_position_m, train.permitted_speed_mps)
            train.speed_mps = min(max(0.0, result.speed_mps), next_limit_mps)
            train.path_position_m = new_position_m
            train.path_total_length_m = path_plan.total_length_m
            train.segment_progress = (
                min(1.0, train.path_position_m / path_plan.total_length_m)
                if path_plan.total_length_m > 0
                else 1.0
            )
            train.distance_to_next_m = max(0.0, path_plan.total_length_m - train.path_position_m)
            train.target_distance_m = path_plan.total_length_m
            train.traction_percent = cmd.traction_percent
            train.brake_percent = cmd.brake_percent
            train.target_speed_mps = ato.last_target_speed_mps
            train.energy_kwh = result.net_energy_kwh
            self._update_train_path_context(train)

            arrived = (
                train.distance_to_next_m <= self._ato_config.stop_tolerance_m
                and train.speed_mps <= 0.2
            )

            if arrived:
                self._complete_path_arrival(train, next_idx, next_stn, sim_time_ms)
                return

            cruise_threshold = min(self.CRUISE_SPEED_MPS, train.local_speed_limit_mps) * 0.95
            braking_profile = ato.last_profile_mode == "MAX_BRAKE" or ato.last_profile_mode.startswith("BRAKE_")
            if train.speed_mps >= cruise_threshold:
                train.phase = CRUISING
            elif (cmd.brake_percent > 5 or braking_profile) and train.speed_mps > 0.5:
                train.phase = APPROACHING
            else:
                train.phase = DEPARTING

        except Exception as exc:
            print(f"[Engine] PathPlan advancement failed for {train.train_id}: {exc}")
            train.traction_percent = 0.0
            train.brake_percent = 0.0
            train.target_speed_mps = 0.0
            train.speed_mps = max(0.0, train.speed_mps - 0.8 * dt)
            train.path_position_m = min(path_plan.total_length_m, train.path_position_m + train.speed_mps * dt)
            train.distance_to_next_m = max(0.0, path_plan.total_length_m - train.path_position_m)
            train.segment_progress = train.path_position_m / dist if dist > 0 else 1.0
            self._update_train_path_context(train)

    def _complete_path_arrival(
        self,
        train: SimTrainState,
        next_idx: int,
        next_stn: JsonDict,
        sim_time_ms: int,
    ) -> None:
        # Preserve the final path constraint for terminal parking.  At a
        # non-terminal station the next PathPlan immediately replaces it; at a
        # terminal there is no next plan, so clearing it makes an IDLE train
        # disappear from the topology even though it is still in the engine.
        arrival_segment_id = train.current_segment_id
        arrival_segment_offset_m = train.current_segment_offset_m
        train.speed_mps = 0.0
        train.segment_progress = 0.0
        train.path_position_m = 0.0
        train.path_total_length_m = 0.0
        train.current_segment_id = arrival_segment_id
        train.current_segment_offset_m = arrival_segment_offset_m
        train.local_speed_limit_mps = train.permitted_speed_mps
        train.grade_ratio = 0.0
        train.path_segment_count = 0
        train.path_constraint_count = 0
        train.station_index = next_idx
        train.current_station_code = str(next_stn.get("code", ""))
        train.current_station_name = next_stn.get("name", "")
        train.phase = DWELLING
        train.dispatch_hold_applied_station_index = None
        train.traction_percent = 0.0
        train.brake_percent = 20.0
        train.target_speed_mps = 0.0

        self._dcdp_curve_data.pop(train.train_id, None)
        self._dcdp_curve_meta.pop(train.train_id, None)
        self._profile_run_times.pop(train.train_id, None)
        train._profile_triggered = False
        train._path_plan = None
        train._path_origin_station_index = None
        train._path_destination_station_index = None
        train._route_chain_ids = ()
        train._route_retry_at_ms = 0
        train._route_failure_reason = None
        self._ato_for_train(train.train_id).reset()

        stations = self._station_list
        new_next_idx = next_idx + 1 if train.direction == "UP" else next_idx - 1
        if 0 <= new_next_idx < len(stations):
            new_next_stn = stations[new_next_idx]
            train.next_station_code = str(new_next_stn.get("code", ""))
            train.next_station_name = new_next_stn.get("name", "")
            next_plan = self._path_plan_for_station_pair(next_idx, new_next_idx)
            if next_plan is not None:
                train.target_distance_m = next_plan.total_length_m
                train.distance_to_next_m = next_plan.total_length_m
            else:
                train.target_distance_m = abs(self._station_distances[new_next_idx] - self._station_distances[next_idx])
                train.distance_to_next_m = train.target_distance_m
        else:
            train.next_station_code = ""
            train.next_station_name = ""
            train.target_distance_m = 0.0
            train.distance_to_next_m = 0.0

        self._process_station_stop(train, sim_time_ms)

    def _ato_for_train(self, train_id: str) -> ATOController:
        controller = self._ato_by_train.get(train_id)
        if controller is None:
            controller = ATOController(self._ato_config)
            self._ato_by_train[train_id] = controller
            if len(self._ato_by_train) == 1:
                self.ato = controller
        return controller

    def _handle_terminal_turnback(
        self, train: SimTrainState, sim_time_ms: int,
    ) -> tuple[bool, PreparedTrainStep | None]:
        """Hold at a terminal, then reverse only through a legal same-platform route."""
        reverse_direction = "DOWN" if train.direction == "UP" else "UP"
        train.speed_mps = 0.0
        train.traction_percent = 0.0
        train.brake_percent = 20.0
        train.target_speed_mps = 0.0

        if train.dwell_remaining_sec > 0:
            train.dwell_remaining_sec = max(0.0, train.dwell_remaining_sec - self.clock.tick_seconds)
            return True, None

        station_code = train.current_station_code
        platform_id = self._platform_id_by_segment.get(train.current_segment_id or -1)
        legal_reverse = (
            platform_id in self._station_platform_ids.get(train.station_index, ())
            and train.current_segment_id is not None
            and reverse_direction in self.available_initial_directions(station_code, train.current_segment_id)
        )
        if not legal_reverse:
            # Do not teleport across a terminal throat. A future depot/crossover
            # planner can supply a physical transfer path for this case.
            train.phase = IDLE
            train._route_failure_reason = "TURNBACK_PLATFORM_UNAVAILABLE"
            train.last_dispatch_action = "TERMINAL"
            train.last_dispatch_reason = "TURNBACK_PLATFORM_UNAVAILABLE"
            return True, None

        decision = self.dispatch_service.decide(DispatchContext(
            sim_time_ms=sim_time_ms,
            train_id=train.train_id,
            station_id=station_code,
            terminal_turnback=True,
            turnback_direction=reverse_direction,
        ))
        self._pending_dispatch_decisions.append(decision)
        train.last_dispatch_action = decision.action
        train.last_dispatch_reason = decision.reason

        # The inbound route has no further movement to release it normally at a
        # terminal. Occupation remains present, but its locks must clear before
        # the same train requests the reverse departure route.
        self.route_service.release_routes_owned_by(train.train_id)
        train.direction = reverse_direction
        train._initial_platform_id = platform_id
        next_index = train.station_index + (1 if reverse_direction == "UP" else -1)
        next_station = self._station_list[next_index]
        train.next_station_code = str(next_station.get("code", ""))
        train.next_station_name = str(next_station.get("name", ""))
        train.phase = DWELLING
        train.dwell_remaining_sec = 0.0
        train.dispatch_hold_applied_station_index = None
        train._route_retry_at_ms = 0
        train._route_failure_reason = None
        train.turnback_count += 1
        return True, None
    def _ensure_interval_path(self, train: SimTrainState, next_idx: int) -> PathPlan | None:
        if (
            train._path_plan is not None
            and train._path_origin_station_index == train.station_index
            and train._path_destination_station_index == next_idx
        ):
            self._update_train_path_context(train)
            return train._path_plan

        route_plan = self._route_plan_for_station_pair(
            train.station_index, next_idx, preferred_origin_platform_id=train._initial_platform_id,
        )
        if route_plan is None:
            return None
        path_plan, route_ids = route_plan
        train._initial_platform_id = None

        train._path_plan = path_plan
        train._path_origin_station_index = train.station_index
        train._path_destination_station_index = next_idx
        train.path_position_m = 0.0
        train.path_total_length_m = path_plan.total_length_m
        train.target_distance_m = path_plan.total_length_m
        train.distance_to_next_m = path_plan.total_length_m
        train.path_segment_count = len(path_plan.segment_ids)
        train.path_constraint_count = len(path_plan.constraints)
        train._profile_triggered = False
        train._route_chain_ids = route_ids
        train._route_retry_at_ms = 0
        train._route_failure_reason = None
        self._dcdp_curve_data.pop(train.train_id, None)
        self._dcdp_curve_meta.pop(train.train_id, None)
        self._profile_run_times.pop(train.train_id, None)
        self._ato_for_train(train.train_id).reset()
        self._update_train_path_context(train)
        return path_plan

    def _route_plan_for_station_pair(
        self,
        origin_idx: int,
        destination_idx: int,
        preferred_origin_platform_id: int | None = None,
    ) -> tuple[PathPlan, tuple[str, ...]] | None:
        origin_platforms = self._station_platform_ids.get(origin_idx, ())
        destination_platforms = self._station_platform_ids.get(destination_idx, ())
        if not origin_platforms or not destination_platforms:
            return None
        if preferred_origin_platform_id is not None:
            if preferred_origin_platform_id not in origin_platforms:
                return None
            origin_platforms = (preferred_origin_platform_id,)

        direction = 'forward' if destination_idx > origin_idx else 'backward'
        try:
            selected = self.route_chain_planner.plan_between_platform_sets(
                origin_platforms, destination_platforms, direction,
            )
        except ValueError:
            return None
        return selected.path_plan, selected.route_ids

    def _path_plan_for_station_pair(self, origin_idx: int, destination_idx: int) -> PathPlan | None:
        """Compatibility wrapper for code which only needs the physical plan."""
        route_plan = self._route_plan_for_station_pair(origin_idx, destination_idx)
        return route_plan[0] if route_plan is not None else None

    def _prime_path_profile(self, train: SimTrainState, path_plan: PathPlan) -> None:
        ato = self._ato_for_train(train.train_id)
        state = TrainState(
            train_id=train.train_id,
            position_m=0.0,
            speed_mps=0.0,
            sim_time_s=self.clock.sim_time_seconds,
            segment_id=path_plan.start_segment_id,
            net_energy_kwh=train.energy_kwh,
        )
        target = AtoTarget(
            target_position_m=path_plan.total_length_m,
            permitted_speed_mps=train.permitted_speed_mps,
            path_plan=path_plan,
        )
        train.target_speed_mps = ato.target_speed_mps(state, target)
        self._store_path_profile(train, path_plan, ato)

    def _store_path_profile(
        self,
        train: SimTrainState,
        path_plan: PathPlan,
        ato: ATOController,
    ) -> None:
        profile = ato.current_profile
        if profile is None:
            return

        points: list[dict[str, Any]] = []
        for point in profile.points:
            constraint = path_plan.constraint_at(point.position_m)
            points.append(
                {
                    "positionM": round(point.position_m, 1),
                    "speedMps": round(point.speed_mps, 2),
                    "mode": point.mode,
                    "localSpeedLimitMps": round(
                        path_plan.speed_limit_at(point.position_m, train.permitted_speed_mps),
                        2,
                    ),
                    "gradeRatio": round(path_plan.grade_ratio_at(point.position_m), 7),
                    "segmentId": constraint.segment_id if constraint is not None else None,
                }
            )
        self._dcdp_curve_data[train.train_id] = points
        self._dcdp_curve_meta[train.train_id] = {
            "source": "DCDP_STRICT",
            "terminalScore": profile.terminal_score,
            "scheduledRunTimeS": profile.scheduled_run_time_s,
            "targetPositionM": profile.target_position_m,
            "permittedSpeedMps": profile.permitted_speed_mps,
            "pointCount": len(points),
        }
        self._profile_run_times[train.train_id] = profile.scheduled_run_time_s

    def _update_train_path_context(self, train: SimTrainState) -> None:
        path_plan = train._path_plan
        if path_plan is None:
            train.path_total_length_m = 0.0
            train.current_segment_id = None
            train.current_segment_offset_m = 0.0
            train.local_speed_limit_mps = train.permitted_speed_mps
            train.grade_ratio = 0.0
            train.path_segment_count = 0
            train.path_constraint_count = 0
            return

        train.path_total_length_m = path_plan.total_length_m
        train.path_segment_count = len(path_plan.segment_ids)
        train.path_constraint_count = len(path_plan.constraints)
        bounded_position_m = min(max(0.0, train.path_position_m), path_plan.total_length_m)
        constraint = path_plan.constraint_at(bounded_position_m)
        train.current_segment_id = constraint.segment_id if constraint is not None else None
        if constraint is None or constraint.path_end_m <= constraint.path_start_m:
            train.current_segment_offset_m = 0.0
        else:
            ratio = (bounded_position_m - constraint.path_start_m) / (
                constraint.path_end_m - constraint.path_start_m
            )
            train.current_segment_offset_m = (
                constraint.start_offset_m + (constraint.end_offset_m - constraint.start_offset_m) * ratio
            )
        train.local_speed_limit_mps = path_plan.speed_limit_at(bounded_position_m, train.permitted_speed_mps)
        train.grade_ratio = path_plan.grade_ratio_at(bounded_position_m)


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

    def export_speed_profile(self, train_id: str) -> list[dict[str, Any]]:
        """返回指定列车的 DCDP 规划速度曲线数据."""
        return self._dcdp_curve_data.get(train_id, [])

    def export_speed_profile_meta(self, train_id: str) -> dict[str, Any]:
        """返回指定列车速度曲线来源与终端质量."""
        return self._dcdp_curve_meta.get(train_id, {})

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

    def _update_power(
        self,
        sim_time_ms: int,
        prepared_steps: dict[str, PreparedTrainStep] | None = None,
    ) -> dict[str, Any]:
        """更新供电状态."""
        if not self.power_service.sections:
            return {}
        requests: list[TrainPowerRequest] = []
        prepared_steps = prepared_steps or {}
        for train in self.trains:
            prepared = prepared_steps.get(train.train_id)
            if prepared is not None:
                traction_force_n = prepared.demand.traction_force_n
                brake_force_n = prepared.demand.candidate_electric_brake_force_n
                train.mass_kg = prepared.vehicle_config.mass_kg
            else:
                vehicle = VehicleConfig.for_load(train.train_id, train.onboard_pax)
                command = ControlCommand(
                    train_id=train.train_id,
                    traction_percent=train.traction_percent if train.speed_mps > 0 else 0.0,
                    brake_percent=train.brake_percent if train.speed_mps > 0 else 0.0,
                    source=CommandSource.ATO,
                )
                demand = TractionDriveModel(vehicle).demand(command, train.speed_mps)
                traction_force_n = demand.traction_force_n
                brake_force_n = demand.candidate_electric_brake_force_n
                train.mass_kg = vehicle.mass_kg
            requests.append(
                TrainPowerRequest(
                    train_id=train.train_id,
                    power_section_id=self._power_section_for_train(train),
                    speed_mps=train.speed_mps,
                    traction_force_n=traction_force_n,
                    brake_force_n=brake_force_n,
                    position_m=self._train_mileage_m(train),
                    direction=train.direction,
                    aux_power_kw=150.0 if train.phase not in {IDLE, DWELLING} else 80.0,
                )
            )
        with self._power_lock:
            return self.power_service.update(
                requests,
                dt_sec=self.clock.tick_seconds,
                sim_time_ms=sim_time_ms,
            )

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
            power_network=self._power_network_snapshot(),
            dispatch_decisions=[self._dispatch_snapshot(item) for item in self._last_dispatch_decisions],
            interlocking={
                "routes": self.route_service.snapshot(),
                "sections": self.section_occupation.snapshot(),
                "switches": self.switch_lock.snapshot(),
                "signals": self.signal_resolver.snapshot(),
            },
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
                "minTrainVoltageV": round(
                    min((state.min_train_voltage_v for state in self._last_power_states.values()), default=750.0),
                    2,
                ),
                "totalAbsorbedRegenKw": round(
                    sum(state.absorbed_regen_kw for state in self._last_power_states.values()),
                    3,
                ),
                "totalWastedRegenKw": round(
                    sum(state.wasted_regen_kw for state in self._last_power_states.values()),
                    3,
                ),
                "powerLossesKw": round(
                    max((state.losses_kw for state in self._last_power_states.values()), default=0.0),
                    3,
                ),
                "totalPowerConstraintDelaySec": round(sum(t.power_constraint_delay_sec for t in self.trains), 3),
                "maxPowerLimitedDurationSec": round(max((t.power_limited_duration_sec for t in self.trains), default=0.0), 3),
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

    def _build_station_platform_ids(self) -> dict[int, tuple[int, ...]]:
        """按车站里程把线路站台表映射到 station_index."""
        platforms_by_mileage: dict[float, list[int]] = {}
        for platform in self.line_map.get("platforms", []):
            platform_id = platform.get("id")
            mileage = platform.get("mileageM")
            if platform_id is None or mileage is None:
                continue
            platforms_by_mileage.setdefault(round(float(mileage), 2), []).append(int(platform_id))

        mapping: dict[int, tuple[int, ...]] = {}
        for index, station in enumerate(self._station_list):
            station_mileage = float(station.get("mileageM", 0.0))
            exact = platforms_by_mileage.get(round(station_mileage, 2), [])
            if exact:
                mapping[index] = tuple(sorted(exact))
                continue

            nearest_key = min(
                platforms_by_mileage,
                key=lambda key: abs(key - station_mileage),
                default=None,
            )
            if nearest_key is not None and abs(nearest_key - station_mileage) <= 1.0:
                mapping[index] = tuple(sorted(platforms_by_mileage[nearest_key]))
        return mapping

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
        initial_path_plan = self._path_plan_for_station_pair(idx, next_idx) if idx != next_idx else None
        if initial_path_plan is not None:
            dist = initial_path_plan.total_length_m

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
            load_factor=(cfg.initial_load_pax / cfg.capacity_pax) if cfg.capacity_pax > 0 else 0.0,
            mass_kg=225_000.0 + cfg.initial_load_pax * 65.0,
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
        topology_path = Path(__file__).resolve().parents[2] / "data" / "scenarios" / "line9_power_topology.json"
        network = load_line9_power_network(topology_path) if topology_path.exists() else None
        return PowerService(
            [
                PowerSection(
                    power_section_id="PWR-09-UP",
                    name="Line 9 Up traction section",
                    max_traction_power_kw=12000.0,
                    warning_power_kw=9000.0,
                    regen_absorb_limit_kw=1800.0,
                ),
                PowerSection(
                    power_section_id="PWR-09-DOWN",
                    name="Line 9 Down traction section",
                    max_traction_power_kw=12000.0,
                    warning_power_kw=9000.0,
                    regen_absorb_limit_kw=1800.0,
                ),
            ],
            traction_efficiency=0.88,
            regen_efficiency=0.65,
            network=network,
        )

    def _empty_power_states(self) -> dict[str, Any]:
        return self.power_service.update(
            [],
            dt_sec=0.0,
            sim_time_ms=self._absolute_sim_time_ms(),
        )

    def _init_interlocking(self) -> None:
        """Create the engine-owned interlocking services and route planner."""
        self.route_catalog = RouteCatalog(self.line_map)
        self.section_occupation = SectionOccupationService(self.line_map)
        self.switch_lock = SwitchLockService([
            self.route_catalog.get_switch(switch_id)
            for switch_id in self.route_catalog.switch_ids
            if self.route_catalog.get_switch(switch_id) is not None
        ])
        self.interlocking_rules = InterlockingRuleEngine(
            self.route_catalog, self.section_occupation, self.switch_lock,
        )
        self.route_service = RouteService(
            self.route_catalog, self.interlocking_rules, self.section_occupation, self.switch_lock,
        )
        self.signal_resolver = SignalAspectResolver(
            self.route_catalog, self.route_service, self.section_occupation, self.switch_lock,
        )
        self.route_chain_planner = RouteChainPlanner(self.line_map, self.route_catalog)
        self.movement_authority_service = MovementAuthorityService(
            self.line_map, self.route_catalog, self.route_service, self.section_occupation,
        )

    def _movement_position_snapshot(self) -> tuple[TrainPosition, ...]:
        """Freeze train footprints before control preparation for order-independent MA."""
        positions: list[TrainPosition] = []
        for train in self.trains:
            if train._path_plan is None:
                continue
            positions.append(TrainPosition(
                train_id=train.train_id,
                direction=train._path_plan.direction,
                path_plan=train._path_plan,
                head_position_m=train.path_position_m,
                length_m=self._make_vehicle_config(train.train_id).train_length_m,
            ))
        return tuple(positions)
    def _refresh_interlocking(self) -> None:
        train_states = [
            InterlockingTrainState(
                train_id=train.train_id,
                sim_time_ms=self._absolute_sim_time_ms(),
                seg_id=train.current_segment_id,
                offset_m=train.current_segment_offset_m,
                position_m=train.path_position_m,
                speed_mps=train.speed_mps,
                direction="FORWARD" if train.direction == "UP" else "BACKWARD",
                length_m=self._make_vehicle_config(train.train_id).train_length_m,
                path_track=self._interlocking_path_track(train),
                operation_mode=train.operation_mode,
                sim_time_s=self.clock.sim_time_seconds,
            )
            for train in self.trains
            if train.current_segment_id is not None
        ]
        self.section_occupation.update(train_states, self.track_query)
        self.route_service.update()
        self.signal_resolver.refresh()

    def _interlocking_path_track(self, train: SimTrainState) -> PathTrackQuery | None:
        """Build the route-limited topology used to trace a train's rear.

        Normal route chains do not repeat Segs. If an external malformed loop
        appears, fall back to the global graph rather than breaking a tick.
        """
        if train._path_plan is None:
            return None
        try:
            return PathTrackQuery(self.track_query, train._path_plan.segment_ids)
        except ValueError:
            return None

    def _ensure_route_chain_locked(self, train: SimTrainState, sim_time_ms: int) -> bool:
        if not train._route_chain_ids:
            train._route_failure_reason = "NO_ROUTE_CHAIN"
            return False
        return self._request_route_lock(train, train._route_chain_ids[0], sim_time_ms, required=True)

    def _attempt_route_lookahead(
        self,
        train: SimTrainState,
        path_plan: PathPlan,
        position_m: float,
        sim_time_ms: int,
    ) -> None:
        remaining = self.movement_authority_service.remaining_route_ids(
            path_plan, train._route_chain_ids, position_m,
        )
        if len(remaining) < 2 or self.route_service.locked_by(remaining[0]) != train.train_id:
            return
        endpoint_m = self.movement_authority_service.route_endpoint_position(path_plan, remaining[0])
        if endpoint_m is None or position_m < endpoint_m - self.ROUTE_LOOKAHEAD_DISTANCE_M:
            return
        self._request_route_lock(train, remaining[1], sim_time_ms, required=False)

    def _request_route_lock(
        self,
        train: SimTrainState,
        route_id: str,
        sim_time_ms: int,
        *,
        required: bool,
    ) -> bool:
        if self.route_service.locked_by(route_id) == train.train_id:
            return True
        if train._route_retry_at_ms and sim_time_ms < train._route_retry_at_ms:
            return False

        transient_reasons = {"SECTION_OCCUPIED", "CONFLICT_ROUTE_LOCKED", "SWITCH_UNAVAILABLE"}
        owner = self.route_service.locked_by(route_id)
        if owner is not None:
            train._route_failure_reason = "CONFLICT_ROUTE_LOCKED"
            train._route_retry_at_ms = sim_time_ms + 2_000
            return False
        result = self.route_service.request(RouteRequest(
            request_id=f"ENG-{train.train_id}-{sim_time_ms}-{route_id}",
            route_id=route_id,
            train_id=train.train_id,
            source="DISPATCH",
        ))
        if result.accepted:
            train._route_failure_reason = None
            train._route_retry_at_ms = 0
            return True
        train._route_failure_reason = result.failure_reason or "ROUTE_REQUEST_FAILED"
        train._route_retry_at_ms = sim_time_ms + 2_000 if result.failure_reason in transient_reasons else 0
        return not required

    def _power_section_for_train(self, train: SimTrainState) -> str:
        return "PWR-09-UP" if train.direction == "UP" else "PWR-09-DOWN"

    def _train_mileage_m(self, train: SimTrainState) -> float:
        if not self._station_distances:
            return 0.0
        current_m = self._station_distances[train.station_index]
        next_idx = train.station_index + 1 if train.direction == "UP" else train.station_index - 1
        if next_idx < 0 or next_idx >= len(self._station_distances):
            return current_m
        next_m = self._station_distances[next_idx]
        return current_m + (next_m - current_m) * max(0.0, min(1.0, train.segment_progress))

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
            "minTrainVoltageV": round(state.min_train_voltage_v, 2),
            "maxTrainCurrentA": round(state.max_train_current_a, 2),
            "substationCount": state.substation_count,
            "overloadedSubstations": state.overloaded_substations,
            "overloadedFeeders": state.overloaded_feeders,
            "lossesKw": round(state.losses_kw, 3),
            "feedbackRegenKw": round(state.feedback_regen_kw, 3),
            "alerts": list(state.alerts),
            "source": state.source,
            "quality": state.quality,
        }

    def _power_network_snapshot(self) -> JsonDict:
        with self._power_lock:
            snapshot = self.power_service.last_network_snapshot
            result = snapshot.to_dict() if snapshot is not None else {}
            if self.power_service.network is not None:
                topology = self.power_service.network.topology_dict()
                result["switches"] = topology["switches"]
                result["contactRailSections"] = topology["contactRailSections"]
                result["returnRailSections"] = topology["returnRailSections"]
            result["commandResults"] = list(self._power_command_results)
            return result

    def export_current_run(self) -> JsonDict:
        if self.recorder is None or self._run_id is None:
            raise RuntimeError("RUN_RECORDER_NOT_AVAILABLE")
        return self.recorder.export_run(self._run_id)

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
