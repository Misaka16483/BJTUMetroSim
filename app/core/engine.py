"""浠跨湡寮曟搸 鈥?鎴愬憳A: 鏃堕挓椹卞姩 + 鍩熸湇鍔＄紪鎺?+ 浜嬩欢鍙戝竷 + 鏁版嵁璁板綍."""

from __future__ import annotations

import csv
from collections import deque
import hashlib
import json
import math
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.core.clock import ClockState, SimulationClock
from app.core.message_bus import Envelope, MessageBus
from app.core.scenario import ScenarioConfig, TrainConfig
from app.domain.control.models import AtoConfig, AtoTarget, OperationMode
from app.domain.control.movement_authority import MovementAuthorityService, TrainPosition
from app.domain.control.services import ATOController
from app.domain.control.profile_runtime import (
    PROFILE_CACHE_VERSION,
    AsyncSpeedProfileService,
    SpeedProfileRequest,
    build_speed_profile_request,
)
from app.domain.dispatch.kpi import DispatchKpiTracker
from app.domain.dispatch.runtime import DispatchRuntimeCoordinator
from app.domain.dispatch.services import DispatchContext, DispatchDecision, DispatchRuleConfig, RuleBasedDispatchService
from app.domain.dispatch.timetable import HeadwayConfig, Timetable, TimetableService, TrainDuty, TrainService
from app.domain.interlocking.runtime import InterlockingRuntimeCoordinator
from app.domain.interlocking.route_chain_planner import (
    RouteChainPlan,
    RouteChainPlanner,
    TurnbackPhase,
    TurnbackPlan,
)
from app.domain.interlocking.train_track_trace import TrainTrackTrace
from app.domain.power.line9_topology import load_line9_power_network
from app.domain.line.services import LineMapRepository, LineScope, PathPlan, PathPlanner, TrackQueryService
from app.domain.power.services import PowerSection, PowerService, TrainPowerRequest
from app.domain.station.passenger_profiles import load_passenger_profile
from app.domain.station.services import PoissonPassengerFlowGenerator, StationService, TrainLoadState
from app.domain.vehicle.models import ControlCommand, TrainState, VehicleConfig, CommandSource
from app.domain.vehicle.doors import DoorSide, TrainDoorSystem
from app.domain.vehicle.services import (
    BrakeBlendService,
    SimpleVehicleModel,
    TractionDriveModel,
    VehicleForceDemand,
    VehiclePowerDemand,
)
from app.infra.recorder import RunRecorder


JsonDict = dict[str, Any]

# 鈹€鈹€ 鍒楄溅杩愯闃舵 鈹€鈹€
APPROACHING = "APPROACHING"     # 进站制动
DWELLING = "DWELLING"           # 停站上下客
DEPARTING = "DEPARTING"         # 出站加速
CRUISING = "CRUISING"           # 区间巡航
IDLE = "IDLE"                   # 尚未启动或已完成


@dataclass
class SimTrainState:
    """鍒楄溅瀹炴椂鐘舵€?"""
    train_id: str
    line_id: str
    station_index: int           # 褰撳墠鎵€鍦ㄧ珯搴忓彿 (0-based)
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
    capacity_pax: int = 1460
    load_factor: float = 0.0
    door_state: str = "CLOSED"
    door_side: str = "NONE"
    door_notice: str = "CLOSED"
    door_permission: str = "SIMULATED_GRANTED"
    door_transition_remaining_sec: float = 0.0
    # Per-door state is authoritative. Legacy door_* fields remain as an
    # aggregate compatibility contract for existing passenger and UI code.
    door_system: TrainDoorSystem = field(default_factory=TrainDoorSystem.line9_default)
    last_boarding: int = 0
    last_alighting: int = 0
    current_boarding_rate_pax_per_sec: float = 0.0
    current_alighting_rate_pax_per_sec: float = 0.0
    last_passenger_event_ms: int | None = None
    current_station_name: str = ""
    next_station_name: str = ""
    segment_progress: float = 0.0  # 0鈫? between current and next station
    last_dispatch_action: str = "FOLLOW_TIMETABLE"
    last_dispatch_reason: str = "NO_ADJUSTMENT_NEEDED"
    dispatch_hold_applied_station_index: int | None = None
    # 鈹€鈹€ 椹鹃┒妯″紡 鈹€鈹€
    operation_mode: str = "ATO"  # "ATO" or "MANUAL"
    # 鈹€鈹€ 椹鹃┒鍙板弽棣堝瓧娈?鈹€鈹€
    traction_percent: float = 0.0
    brake_percent: float = 0.0
    energy_kwh: float = 0.0
    traction_energy_kwh: float = 0.0
    auxiliary_energy_kwh: float = 0.0
    regen_generated_kwh: float = 0.0
    regen_self_consumed_kwh: float = 0.0
    regen_accepted_kwh: float = 0.0
    regen_wasted_kwh: float = 0.0
    target_speed_mps: float = 0.0
    estimated_run_time_s: float = 0.0   # 棰勮鍖洪棿杩愯鏃堕棿锛岀敱閫熷害鏇茬嚎绉垎寰楀嚭
    path_position_m: float = 0.0
    path_total_length_m: float = 0.0
    current_platform_id: int | None = None
    current_segment_id: int | None = None
    current_segment_offset_m: float = 0.0
    local_speed_limit_mps: float = 22.22
    grade_ratio: float = 0.0
    applied_grade_ratio: float = 0.0
    path_segment_count: int = 0
    path_constraint_count: int = 0
    mass_kg: float = 225_000.0
    acceleration_mps2: float = 0.0
    resistance_force_n: float = 0.0
    traction_force_n: float = 0.0
    electric_brake_force_n: float = 0.0
    pneumatic_brake_force_n: float = 0.0
    requested_power_kw: float = 0.0
    traction_power_request_kw: float = 0.0
    traction_power_delivered_kw: float = 0.0
    auxiliary_power_kw: float = 0.0
    regen_power_available_kw: float = 0.0
    regen_power_self_consumed_kw: float = 0.0
    regen_power_accepted_kw: float = 0.0
    regen_power_wasted_kw: float = 0.0
    pantograph_voltage_v: float = 750.0
    traction_limit_ratio: float = 1.0
    regen_limit_ratio: float = 1.0
    power_limited_duration_sec: float = 0.0
    power_constraint_delay_sec: float = 0.0
    train_length_m: float = 118.0
    head_mileage_m: float = 0.0
    tail_mileage_m: float = 0.0
    pantograph_mileages_m: tuple[float, ...] = ()
    spanned_power_section_ids: tuple[str, ...] = ()
    departure_authorized: bool = False
    interlocking_hold_reason: str | None = None
    active_route_ids: tuple[str, ...] = ()
    route_retry_at_ms: int | None = None
    turnback_count: int = 0
    service_id: str | None = None
    next_service_id: str | None = None
    duty_id: str | None = None
    lifecycle_state: str = "UNPLANNED"
    planned_departure_ms: int | None = None
    planned_arrival_ms: int | None = None
    actual_departure_ms: int | None = None
    actual_arrival_ms: int | None = None
    schedule_deviation_sec: float | None = None
    lifecycle_updated_ms: int | None = None
    turnback_state: str | None = None
    turnback_phase_index: int | None = None
    movement_authority_end_m: float = 0.0
    movement_authority_reason: str | None = None
    movement_authority_speed_mps: float = 0.0
    movement_authority_locked_route_ids: tuple[str, ...] = ()
    # ── profile 触发控制 ──
    _profile_triggered: bool = False
    _path_plan: PathPlan | None = field(default=None, repr=False, compare=False)
    _planned_route_ids: tuple[str, ...] = field(default=(), repr=False, compare=False)
    _path_origin_station_index: int | None = field(default=None, repr=False, compare=False)
    _path_destination_station_index: int | None = field(default=None, repr=False, compare=False)
    _track_trace: TrainTrackTrace | None = field(default=None, repr=False, compare=False)
    _trace_path_start_index: int | None = field(default=None, repr=False, compare=False)
    _turnback_plan: TurnbackPlan | None = field(default=None, repr=False, compare=False)
    _turnback_authorized_route_count: int = field(default=0, repr=False, compare=False)
    _terminal_arrival_release_route_ids: tuple[str, ...] = field(default=(), repr=False, compare=False)
    # 鈹€鈹€ 鎵嬪姩椹鹃┒ per-train 鎸囦护 鈹€鈹€
    _manual_command: ControlCommand | None = field(default=None, repr=False, compare=False)
    _passenger_service_pending: bool = field(default=False, repr=False, compare=False)
    # Path planning may clear current_platform_id before a terminal turnback
    # starts. Keep the physical door-stop platform until the train actually
    # departs so door permission is independent from route planning state.
    _door_stop_platform_id: int | None = field(default=None, repr=False, compare=False)
    _planned_alighting_total: int = field(default=0, repr=False, compare=False)
    _boarding_credit_pax: float = field(default=0.0, repr=False, compare=False)
    _passenger_stop_started_ms: int | None = field(default=None, repr=False, compare=False)

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
            "doorState": self.door_state,
            "doorSide": self.door_side,
            "doorNotice": self.door_notice,
            "doorPermission": self.door_permission,
            "doorTransitionRemainingSec": round(self.door_transition_remaining_sec, 1),
            "doorSystem": self.door_system.to_dict(),
            "lastBoarding": self.last_boarding,
            "lastAlighting": self.last_alighting,
            "currentBoardingRatePaxPerSec": round(self.current_boarding_rate_pax_per_sec, 2),
            "currentAlightingRatePaxPerSec": round(self.current_alighting_rate_pax_per_sec, 2),
            "lastPassengerEventMs": self.last_passenger_event_ms,
            "currentStation": self.current_station_name,
            "nextStation": self.next_station_name,
            "segmentProgress": round(self.segment_progress, 3),
            "lastDispatchAction": self.last_dispatch_action,
            "lastDispatchReason": self.last_dispatch_reason,
            "tractionPercent": round(self.traction_percent, 1),
            "brakePercent": round(self.brake_percent, 1),
            "energyKwh": round(self.energy_kwh, 2),
            "tractionEnergyKwh": round(self.traction_energy_kwh, 4),
            "auxiliaryEnergyKwh": round(self.auxiliary_energy_kwh, 4),
            "regenGeneratedKwh": round(self.regen_generated_kwh, 4),
            "regenSelfConsumedKwh": round(self.regen_self_consumed_kwh, 4),
            "regenAcceptedKwh": round(self.regen_accepted_kwh, 4),
            "regenWastedKwh": round(self.regen_wasted_kwh, 4),
            "targetSpeedMps": round(self.target_speed_mps, 2),
            "estimatedRunTimeS": round(self.estimated_run_time_s, 1),
            "pathPositionM": round(self.path_position_m, 1),
            "pathTotalLengthM": round(self.path_total_length_m, 1),
            "currentPlatformId": self.current_platform_id,
            "currentSegmentId": self.current_segment_id,
            "currentSegmentOffsetM": round(self.current_segment_offset_m, 1),
            "localSpeedLimitMps": round(self.local_speed_limit_mps, 2),
            "gradeRatio": round(self.grade_ratio, 7),
            "dynamicsGradeRatio": round(self.applied_grade_ratio, 7),
            "pathSegmentCount": self.path_segment_count,
            "pathConstraintCount": self.path_constraint_count,
            "massKg": round(self.mass_kg, 1),
            "accelerationMps2": round(self.acceleration_mps2, 6),
            "resistanceForceN": round(self.resistance_force_n, 3),
            "tractionForceN": round(self.traction_force_n, 1),
            "electricBrakeForceN": round(self.electric_brake_force_n, 1),
            "pneumaticBrakeForceN": round(self.pneumatic_brake_force_n, 1),
            "requestedPowerKw": round(self.requested_power_kw, 3),
            "tractionPowerRequestKw": round(self.traction_power_request_kw, 3),
            "tractionPowerDeliveredKw": round(self.traction_power_delivered_kw, 3),
            "auxiliaryPowerKw": round(self.auxiliary_power_kw, 3),
            "regenPowerAvailableKw": round(self.regen_power_available_kw, 3),
            "regenPowerSelfConsumedKw": round(self.regen_power_self_consumed_kw, 3),
            "regenPowerAcceptedKw": round(self.regen_power_accepted_kw, 3),
            "regenPowerWastedKw": round(self.regen_power_wasted_kw, 3),
            "pantographVoltageV": round(self.pantograph_voltage_v, 2),
            "tractionLimitRatio": round(self.traction_limit_ratio, 4),
            "regenLimitRatio": round(self.regen_limit_ratio, 4),
            "powerLimitedDurationSec": round(self.power_limited_duration_sec, 3),
            "powerConstraintDelaySec": round(self.power_constraint_delay_sec, 3),
            "operationMode": self.operation_mode,
            "trainLengthM": round(self.train_length_m, 3),
            "headMileageM": round(self.head_mileage_m, 3),
            "tailMileageM": round(self.tail_mileage_m, 3),
            "pantographMileagesM": [round(value, 3) for value in self.pantograph_mileages_m],
            "spannedPowerSectionIds": list(self.spanned_power_section_ids),
            "departureAuthorized": self.departure_authorized,
            "interlockingHoldReason": self.interlocking_hold_reason,
            "activeRouteIds": list(self.active_route_ids),
            "routeChainIds": list(self.active_route_ids),
            "routeRetryAtMs": self.route_retry_at_ms,
            "turnbackCount": self.turnback_count,
            "turnbackState": self.turnback_state,
            "turnbackPhaseIndex": self.turnback_phase_index,
            "serviceId": self.service_id,
            "nextServiceId": self.next_service_id,
            "dutyId": self.duty_id,
            "lifecycleState": self.lifecycle_state,
            "plannedDepartureMs": self.planned_departure_ms,
            "plannedArrivalMs": self.planned_arrival_ms,
            "actualDepartureMs": self.actual_departure_ms,
            "actualArrivalMs": self.actual_arrival_ms,
            "scheduleDeviationSec": (
                round(self.schedule_deviation_sec, 3)
                if self.schedule_deviation_sec is not None else None
            ),
            "lifecycleUpdatedMs": self.lifecycle_updated_ms,
            "movementAuthorityEndM": round(self.movement_authority_end_m, 1),
            "movementAuthorityReason": self.movement_authority_reason,
            "movementAuthoritySpeedMps": round(self.movement_authority_speed_mps, 2),
            "movementAuthorityLockedRouteIds": list(self.movement_authority_locked_route_ids),
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
    power_demand: VehiclePowerDemand
    gradient_force_n: float
    turnback_phase_index: int | None = None


@dataclass(frozen=True)
class PowerCommand:
    command_id: str
    command_type: str
    payload: JsonDict
    apply_at_sim_time_ms: int | None = None


@dataclass
class TickSnapshot:
    """姣忎釜 tick 鐨勫畬鏁村揩鐓э紝渚?API 璇诲彇."""
    tick: int = 0
    sim_time_ms: int = 0
    sim_time_str: str = "06:00:00"
    clock_state: str = "IDLE"
    speed_multiplier: int = 1
    session_id: str = ""
    run_id: int | None = None
    snapshot_sequence: int = 0
    model_quality: str = "UNKNOWN"
    trains: list[dict[str, Any]] = field(default_factory=list)
    stations: list[dict[str, Any]] = field(default_factory=list)
    power: list[dict[str, Any]] = field(default_factory=list)
    power_network: dict[str, Any] = field(default_factory=dict)
    dispatch_decisions: list[dict[str, Any]] = field(default_factory=list)
    dispatch_runtime: dict[str, Any] = field(default_factory=dict)
    interlocking: dict[str, Any] = field(default_factory=dict)
    kpi: dict[str, Any] = field(default_factory=dict)
    operations: dict[str, Any] = field(default_factory=dict)

    def to_api_dict(self, *, tick_interval_ms: int) -> JsonDict:
        return {
            "sessionId": self.session_id,
            "runId": self.run_id,
            "snapshotSequence": self.snapshot_sequence,
            "dataMode": "LIVE_SIM",
            "modelQuality": self.model_quality,
            "clock": {
                "state": self.clock_state,
                "simTime": self.sim_time_str,
                "tick": self.tick,
                "simTimeMs": self.sim_time_ms,
                "speedMultiplier": self.speed_multiplier,
                "tickIntervalMs": tick_interval_ms,
            },
            "trains": self.trains,
            "stations": self.stations,
            "power": self.power,
            "powerNetwork": self.power_network,
            "dispatchDecisions": self.dispatch_decisions,
            "dispatchRuntime": self.dispatch_runtime,
            "interlocking": self.interlocking,
            "kpi": self.kpi,
            "operations": self.operations,
            "source": "simulation-engine",
        }


class SimulationEngine:
    """Phase 1 浠跨湡寮曟搸锛氬崟杞︿负涓伙紝棰勭暀澶氳溅鎺ュ彛."""

    # 鈹€鈹€ 閫熷害鏇茬嚎鍙傛暟 鈹€鈹€
    CRUISE_SPEED_MPS = 22.22  # 80 km/h 宸¤埅
    ROUTE_REQUEST_RETRY_MS = 1_000

    def __init__(
        self,
        scenario: ScenarioConfig,
        line_map: JsonDict,
        station_catalog: list[JsonDict],  # [{code, name, mileageM, ...}, ...]
        recorder: RunRecorder | None = None,
        line_scope: LineScope | None = None,
    ) -> None:
        self.scenario = scenario
        self.line_map = line_map
        self.station_catalog = station_catalog
        self.recorder = recorder
        self.line_scope = line_scope
        if line_scope is not None and line_scope.line_id != scenario.line_id:
            raise ValueError(
                f"line scope {line_scope.scope_id} belongs to line {line_scope.line_id}, "
                f"but scenario uses line {scenario.line_id}"
            )

        # 鏍稿績缁勪欢
        self.clock = SimulationClock(tick_seconds=scenario.tick_seconds)
        # Keep the physics timestep fixed; acceleration runs multiple complete
        # ticks instead of increasing dt and skipping station/power transitions.
        self._speed_multiplier = 1
        self._tick_interval_seconds = scenario.tick_seconds
        self.bus = MessageBus()
        self.track_query = TrackQueryService(line_map)
        self.path_planner = PathPlanner(
            line_map,
            allowed_segment_ids=line_scope.segment_ids if line_scope is not None else None,
        )

        # 鈹€鈹€ 鏋勫缓杞︾珯杩愯绱㈠紩 鈹€鈹€
        self._station_list: list[JsonDict] = self._build_station_list()
        self._station_distances: list[float] = self._build_station_distances()
        self._station_platform_ids: dict[int, tuple[int, ...]] = self._build_station_platform_ids()
        self._platform_by_id = {
            int(p["id"]): p
            for p in line_map.get("platforms", [])
            if p.get("id") is not None
        }
        self._platform_id_by_segment = {int(p["segmentId"]): int(p["id"]) for p in line_map.get("platforms", []) if p.get("segmentId") is not None and p.get("id") is not None}

        # 鈹€鈹€ 鍒楄溅鐘舵€?鈹€鈹€
        self.trains: list[SimTrainState] = []
        # User-owned train configuration survives stop/start; runtime states do not.
        self._train_specs: dict[str, JsonDict] = ({
            cfg.train_id: {
                "trainId": cfg.train_id,
                "initialStationCode": cfg.initial_station_code,
                "direction": cfg.direction,
                "operationMode": "ATO",
                "capacityPax": cfg.capacity_pax,
                "initialLoadPax": cfg.initial_load_pax,
            }
            for cfg in scenario.trains
        } if scenario.auto_spawn_trains else {})
        self._run_id: int | None = None
        self._session_id = str(uuid.uuid4())
        self._snapshot_sequence = 0
        self._snapshot_interval_ticks = max(1, round(5.0 / scenario.tick_seconds))

        # 鈹€鈹€ 鍩熸湇鍔?鈹€鈹€
        self.station_service = self._build_station_service()
        self.power_service: PowerService = self._build_power_service()
        self.dispatch_service = RuleBasedDispatchService(
            DispatchRuleConfig(
                min_headway_sec=90.0,
                max_headway_sec=300.0,
                overload_threshold=1.20,
                left_behind_threshold_pax=80,
            )
        )
        self.dispatch_runtime = DispatchRuntimeCoordinator(self.dispatch_service)
        self.interlocking_runtime = InterlockingRuntimeCoordinator(
            line_map,
            self.track_query,
            line_scope.segment_ids if line_scope is not None else None,
        )
        self.route_chain_planner = RouteChainPlanner(line_map, self.interlocking_runtime.catalog)
        # The main engine reads the same Member C services used by the runtime;
        # it must not maintain a second route or occupation lifecycle.
        self.route_service = self.interlocking_runtime.route_service
        self.section_occupation = self.interlocking_runtime.section_occupation
        self.signal_resolver = self.interlocking_runtime.signal_resolver
        self.interlocking_rules = self.interlocking_runtime.rule_engine
        self.movement_authority = MovementAuthorityService(
            line_map,
            self.interlocking_runtime.catalog,
            self.route_service,
            self.section_occupation,
        )
        self.kpi_tracker = DispatchKpiTracker()
        self.timetable_service = TimetableService(
            headway_config=HeadwayConfig(min_headway_sec=90.0),
        )
        self._operation_timetables: list[Timetable] = []
        self._operation_services: dict[str, TrainService] = {}
        self._operation_duties: dict[str, TrainDuty] = {}
        self._operation_events: list[JsonDict] = []
        self._pending_operation_events: list[JsonDict] = []
        self._last_arrivals_by_platform: dict[tuple[str, str], int] = {}
        self._station_history: dict[tuple[str, str], deque[JsonDict]] = {}
        self._station_history_arrivals: dict[tuple[str, str], int] = {}
        self._station_history_second: int | None = None
        self._reset_station_history()
        self._last_power_states: dict[str, Any] = {}
        self._last_power_solve_sim_time_ms: int | None = None
        self._last_dispatch_decisions: list[DispatchDecision] = []
        self._pending_dispatch_decisions: list[DispatchDecision] = []
        self._power_commands: deque[PowerCommand] = deque()
        self._power_command_sequence = 0
        self._power_command_results: list[JsonDict] = []
        self._recorded_power_command_ids: set[str] = set()

        # 鈹€鈹€ 鐗╃悊妯″瀷锛歅athPlan-aware ATO + DCDP 瑙勫垝鏇茬嚎 鈹€鈹€
        self._ato_config = AtoConfig(
            target_cruise_speed_mps=self.CRUISE_SPEED_MPS,
            expected_deceleration_mps2=0.6,
            # DCDP is mandatory for every runtime scenario.  Scenario files
            # may retain the field for compatibility, but can no longer turn
            # exact speed-profile calculation off.
            use_dynamic_programming_profile=True,
            profile_position_step_m=10.0,
            profile_speed_step_mps=1.0,
            profile_max_states_per_stage=700,
        )
        self.speed_profile_service = AsyncSpeedProfileService(
            Path(__file__).resolve().parents[2] / "data" / "cache" / "speed_profiles"
        )
        self.ato = ATOController(
            self._ato_config,
            enable_synchronous_profile_optimization=False,
        )
        self._ato_by_train: dict[str, ATOController] = {}
        self._dcdp_curve_data: dict[str, list[dict[str, Any]]] = {}     # 瑙勫垝鏇茬嚎
        self._dcdp_curve_meta: dict[str, dict[str, Any]] = {}
        self._operation_profile_requests: dict[str, SpeedProfileRequest] = {}
        self._operation_startup_profile_keys: tuple[str, ...] = ()
        self._operation_profile_warmup: JsonDict = {
            "enabled": False,
            "ready": True,
            "requestedProfileCount": 0,
            "readyProfileCount": 0,
            "pendingProfileCount": 0,
            "failedProfileCount": 0,
        }
        self._operation_plan_hash: str | None = None
        self._operation_window: JsonDict = {}
        self._profile_run_times: dict[str, float] = {}                  # 棰勮鍖洪棿杩愯鏃堕棿

        # 鈹€鈹€ 鐢ㄦ埛閰嶇疆鐨勮溅杈嗗弬鏁帮紙per-train锛?鈹€鈹€
        self._vehicle_config_by_train: dict[str, VehicleConfig] = {}

        # 鈹€鈹€ 鎵嬪姩椹鹃┒妯″紡锛坧er-train锛?鈹€鈹€
        self._manual_mode_by_train: dict[str, bool] = {}

        # 鈹€鈹€ 绾跨▼瀹夊叏 鈹€鈹€
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._power_lock = threading.RLock()
        self._snapshot: TickSnapshot | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?    #  鍏叡鎺ュ彛
    # 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
    def set_vehicle_config(self, data: JsonDict) -> VehicleConfig:
        """鎺ユ敹鍓嶇浼犳潵鐨勮溅杈嗗弬鏁板苟淇濆瓨锛堥粯璁ょ敤浜庢墍鏈夋柊杞︼級."""
        train_id = data.get("trainId", self.trains[0].train_id if self.trains else "T0901")
        vcfg = VehicleConfig.from_user_config(train_id, data)
        self._vehicle_config_by_train[train_id] = vcfg
        for train in self.trains:
            if train.train_id == train_id:
                train.mass_kg = self._make_vehicle_config(train_id, train.onboard_pax).mass_kg
                self._train_power_geometry(train, self._make_vehicle_config(train_id, train.onboard_pax))
        self._snapshot = self._build_snapshot()
        return vcfg

    def set_train_vehicle_config(self, train_id: str, data: JsonDict) -> VehicleConfig:
        """涓烘寚瀹氬垪杞﹁缃溅杈嗗弬鏁?"""
        vcfg = VehicleConfig.from_user_config(train_id, data)
        self._vehicle_config_by_train[train_id] = vcfg
        for train in self.trains:
            if train.train_id == train_id:
                train.mass_kg = self._make_vehicle_config(train_id, train.onboard_pax).mass_kg
                train.train_length_m = vcfg.train_length_m
                if train.phase in {DWELLING, IDLE}:
                    train._path_plan = None
                    train._path_origin_station_index = None
                    train._path_destination_station_index = None
                    self._anchor_train_at_current_platform(train)
                self._train_power_geometry(train, self._make_vehicle_config(train_id, train.onboard_pax))
        self._snapshot = self._build_snapshot()
        return vcfg

    def set_manual_mode(self, train_id: str, enabled: bool) -> dict:
        """鍒囨崲鎸囧畾鍒楄溅鐨?MANUAL/ATO 妯″紡."""
        for train in self.trains:
            if train.train_id == train_id:
                train.operation_mode = "MANUAL" if enabled else "ATO"
                train.door_system.control_mode = train.operation_mode
                if not enabled:
                    train._manual_command = None
                self._manual_mode_by_train[train_id] = enabled
                self._snapshot = self._build_snapshot()
                return {"ok": True, "trainId": train_id, "manualMode": enabled}
        return {"ok": False, "error": "TRAIN_NOT_FOUND"}

    def set_manual_command(
        self,
        train_id: str,
        traction_percent: float,
        brake_percent: float,
        emergency_brake: bool = False,
    ) -> dict:
        """鎺ユ敹鎸囧畾鍒楄溅鐨勬墜鍔ㄩ┚椹舵寚浠?"""
        for train in self.trains:
            if train.train_id == train_id:
                if train.operation_mode != "MANUAL":
                    return {"ok": False, "error": "NOT_IN_MANUAL_MODE"}
                train._manual_command = ControlCommand(
                    train_id=train_id,
                    traction_percent=0.0 if emergency_brake else max(0.0, min(100.0, traction_percent)),
                    brake_percent=100.0 if emergency_brake else max(0.0, min(100.0, brake_percent)),
                    emergency_brake=emergency_brake,
                    source=CommandSource.MANUAL,
                )
                return {
                    "ok": True,
                    "trainId": train_id,
                    "tractionPercent": train._manual_command.traction_percent,
                    "brakePercent": train._manual_command.brake_percent,
                    "emergencyBrake": train._manual_command.emergency_brake,
                }
        return {"ok": False, "error": "TRAIN_NOT_FOUND"}

    def set_door_command(
        self,
        train_id: str,
        action: str,
        side: str = "NONE",
        source: str = "FRONTEND",
    ) -> JsonDict:
        """Apply an external CM/PLC door command through the vehicle interlock."""
        train = next((item for item in self.trains if item.train_id == train_id), None)
        if train is None:
            return {"ok": False, "error": "TRAIN_NOT_FOUND"}
        action = str(action).upper()
        source = str(source).upper()
        if action == "CLOSE":
            accepted = train.door_system.request_close(source)
        else:
            if action != "OPEN":
                return {"ok": False, "error": "INVALID_DOOR_ACTION"}
            if train.operation_mode != "MANUAL":
                return {"ok": False, "error": "DOOR_MANUAL_COMMAND_REQUIRES_CM"}
            door_platform_id = train._door_stop_platform_id or train.current_platform_id
            if train.phase != DWELLING or train.speed_mps > 0.05 or door_platform_id is None:
                return {"ok": False, "error": "DOOR_OPEN_REQUIRES_PLATFORM_STOP"}
            try:
                requested_side = DoorSide(str(side).upper())
            except ValueError:
                return {"ok": False, "error": "INVALID_DOOR_SIDE"}
            accepted = train.door_system.request_open(requested_side, source)
        self._sync_legacy_door_state(train)
        self._snapshot = self._build_snapshot()
        if not accepted:
            return {
                "ok": False,
                "error": train.door_system.last_rejection_reason or "DOOR_COMMAND_REJECTED",
                "train": train.to_dict(),
            }
        return {
            "ok": True,
            "trainId": train_id,
            "doorSystem": train.door_system.to_dict(),
            "train": train.to_dict(),
        }

    @staticmethod
    def _sync_legacy_door_state(train: SimTrainState) -> None:
        """Keep the pre-door-model aggregate fields stable for old consumers."""
        doors = train.door_system
        train.door_state = doors.aggregate_state
        train.door_transition_remaining_sec = doors.transition_remaining_sec
        shown_side = doors.active_side if doors.active_side != DoorSide.NONE else doors.permitted_side
        train.door_side = shown_side.value
        if (
            doors.aggregate_state in {"OPENING", "OPEN", "CLOSING"}
            and train.door_notice not in {"PREPARE_CLOSE", "WAITING_MANUAL_CLOSE"}
        ):
            train.door_notice = doors.aggregate_state
        elif doors.all_closed_and_locked and train.phase != DWELLING:
            train.door_notice = "CLOSED"

    @staticmethod
    def _enforce_door_interlock(
        train: SimTrainState,
        command: ControlCommand,
    ) -> ControlCommand:
        if train.door_system.all_closed_and_locked:
            return command
        return ControlCommand(
            train_id=train.train_id,
            traction_percent=0.0,
            brake_percent=max(20.0, command.brake_percent),
            emergency_brake=command.emergency_brake,
            source=command.source,
        )

    def _make_vehicle_config(self, train_id: str, onboard_pax: int = 0) -> VehicleConfig:
        """Build a per-train configuration including the current passenger mass."""
        base = self._vehicle_config_by_train.get(train_id, VehicleConfig(train_id=train_id))
        return VehicleConfig(
            train_id=train_id,
            mass_kg=base.empty_mass_kg + max(0, onboard_pax) * base.average_passenger_mass_kg,
            formation=base.formation,
            car_masses_kg=None,
            head_car_length_m=base.head_car_length_m,
            middle_car_length_m=base.middle_car_length_m,
            wheel_radius_m=base.wheel_radius_m,
            max_speed_mps=base.max_speed_mps,
            max_traction_force_n=base.max_traction_force_n,
            max_service_brake_force_n=base.max_service_brake_force_n,
            emergency_brake_force_n=base.emergency_brake_force_n,
            basic_resistance_n=base.basic_resistance_n,
            stop_speed_threshold_mps=base.stop_speed_threshold_mps,
            average_passenger_mass_kg=base.average_passenger_mass_kg,
            motor_count=base.motor_count,
            gear_ratio=base.gear_ratio,
            drivetrain_efficiency=base.drivetrain_efficiency,
            regen_efficiency=base.regen_efficiency,
            auxiliary_power_kw=base.auxiliary_power_kw,
            nominal_line_voltage_v=base.nominal_line_voltage_v,
            parameter_quality=base.parameter_quality,
            pantograph_offsets_from_head_m=base.pantograph_offsets_from_head_m,
        )

    def _manual_override(self, ato_cmd: ControlCommand, train_id: str) -> ControlCommand:
        """濡傛灉璇ュ垪杞﹀浜庢墜鍔ㄦā寮忥紝鐢ㄥ叾鎵嬪姩鎸囦护鏇夸唬 ATO 鎸囦护."""
        for train in self.trains:
            if train.train_id == train_id:
                if train.operation_mode != "MANUAL" or train._manual_command is None:
                    return ato_cmd
                mc = train._manual_command
                return ControlCommand(
                    train_id=train_id,
                    traction_percent=mc.traction_percent,
                    brake_percent=mc.brake_percent,
                    emergency_brake=mc.emergency_brake,
                    source=CommandSource.MANUAL,
                )
        return ato_cmd

    def add_train(self, payload: JsonDict) -> JsonDict:
        """鍔ㄦ€佹坊鍔犱竴鍒楄溅."""
        train_id = str(payload.get("trainId", ""))
        if not train_id:
            return {"ok": False, "error": "MISSING_TRAIN_ID"}
        for t in self.trains:
            if t.train_id == train_id:
                return {"ok": False, "error": "TRAIN_ID_EXISTS"}

        requested_station = str(
            payload.get("initialStationCode", self._station_list[0].get("code", "GGZ"))
        ).strip()
        normalized_station_name = requested_station.removesuffix("站")
        station = next(
            (
                item
                for item in self._station_list
                if requested_station == str(item.get("code", ""))
                or normalized_station_name == str(item.get("name", "")).removesuffix("站")
            ),
            None,
        )
        if station is None:
            return {"ok": False, "error": "INVALID_INITIAL_STATION"}
        initial_station_code = str(station.get("code", ""))
        direction = str(payload.get("direction", "UP")).upper()
        if direction not in {"UP", "DOWN"}:
            return {"ok": False, "error": "INVALID_DIRECTION"}
        station_codes = [str(item.get("code", "")) for item in self._station_list]
        station_index = station_codes.index(initial_station_code)
        destination_index = len(station_codes) - 1 if direction == "UP" else 0
        if station_index == destination_index:
            return {
                "ok": False,
                "error": "INITIAL_STATION_HAS_NO_FORWARD_ROUTE",
                "stationCode": initial_station_code,
                "destinationStationCode": station_codes[destination_index],
                "direction": direction,
            }
        initial_segment_id = payload.get("initialSegmentId")
        initial_platform_id = None
        if initial_segment_id is not None:
            try:
                initial_platform_id = self._platform_id_by_segment.get(int(initial_segment_id))
            except (TypeError, ValueError):
                initial_platform_id = None
            if initial_platform_id not in self._station_platform_ids.get(station_index, ()):
                return {"ok": False, "error": "INITIAL_SEGMENT_NOT_PLATFORM"}
        operation_mode = str(payload.get("operationMode", "ATO")).upper()
        if operation_mode not in ("ATO", "MANUAL"):
            return {"ok": False, "error": "INVALID_OPERATION_MODE"}

        capacity_pax = int(payload.get(
            "capacityPax",
            getattr(self, "_default_train_capacity_pax", 1_460),
        ))
        initial_load_pax = int(payload.get("initialLoadPax", 0))
        if capacity_pax <= 0:
            return {"ok": False, "error": "INVALID_CAPACITY"}
        if initial_load_pax < 0 or initial_load_pax > capacity_pax:
            return {"ok": False, "error": "INVALID_INITIAL_LOAD"}

        vehicle_config: VehicleConfig | None = None
        vehicle_data = payload.get("vehicleConfig")
        if vehicle_data and isinstance(vehicle_data, dict):
            try:
                vehicle_config = VehicleConfig.from_user_config(train_id, vehicle_data)
            except (TypeError, ValueError) as exc:
                return {"ok": False, "error": "INVALID_VEHICLE_CONFIG", "message": str(exc)}

        cfg = TrainConfig(
            train_id=train_id,
            line_id=self.scenario.line_id,
            initial_station_code=initial_station_code,
            direction=direction,
            capacity_pax=capacity_pax,
            initial_load_pax=initial_load_pax,
        )
        train = self._create_train(cfg, vehicle_config)
        if initial_platform_id is not None:
            train.current_platform_id = initial_platform_id
        next_index = station_index + (1 if direction == "UP" else -1)
        if not 0 <= next_index < len(self._station_list):
            return {"ok": False, "error": "INITIAL_STATION_MUST_MATCH_DIRECTION_ORIGIN"}
        if self._ensure_interval_path(train, next_index) is None:
            return {"ok": False, "error": "INITIAL_ROUTE_UNAVAILABLE"}
        if initial_segment_id is not None:
            placement_conflict = self._explicit_initial_placement_conflict(train)
            if placement_conflict is not None:
                return placement_conflict
        train.operation_mode = operation_mode
        if operation_mode == "MANUAL":
            self._manual_mode_by_train[train_id] = True
        # Make the physical platform stop authoritative immediately. Otherwise
        # a CM door command sent between add_train() and the first tick would
        # see no permitted side even though the train is already at a platform.
        self._begin_station_stop(train)

        # 如有用户车辆参数，应用之
        if vehicle_config is not None:
            self._vehicle_config_by_train[train_id] = vehicle_config
            train.mass_kg = vehicle_config.mass_kg

        self._train_power_geometry(train, self._make_vehicle_config(train_id, train.onboard_pax))
        self.trains.append(train)
        self._train_specs[train_id] = {
            "trainId": train_id,
            "initialStationCode": initial_station_code,
            "direction": direction,
            "operationMode": operation_mode,
            "capacityPax": capacity_pax,
            "initialLoadPax": initial_load_pax,
        }
        if initial_segment_id is not None:
            self._train_specs[train_id]["initialSegmentId"] = int(initial_segment_id)
        self.dispatch_runtime.register_train(train)
        self._snapshot = self._build_snapshot()
        return {"ok": True, "train": train.to_dict()}

    def _explicit_initial_placement_conflict(self, train: SimTrainState) -> JsonDict | None:
        if train.current_segment_id is None:
            return None
        candidate = SimpleNamespace(
            train_id=train.train_id,
            seg_id=int(train.current_segment_id),
            offset_m=float(train.current_segment_offset_m),
            length_m=float(train.train_length_m),
            direction="FORWARD" if train.direction == "UP" else "BACKWARD",
        )
        candidate_segments = self.section_occupation.physical_footprint(candidate, self.track_query)
        candidate_sections = self.interlocking_runtime._sections_for_path(train._path_plan) if train._path_plan is not None else frozenset()
        conflicting_route_ids = sorted(
            str(item["routeId"])
            for item in self.route_service.snapshot()
            if item.get("state") in {"LOCKED", "APPROACH_LOCKED"}
            and candidate_sections.intersection(str(section_id) for section_id in item.get("lockedSections", []))
        )
        if conflicting_route_ids:
            return {
                "ok": False,
                "error": "INITIAL_PLACEMENT_ROUTE_LOCKED",
                "conflictingRouteIds": conflicting_route_ids,
            }
        conflicting_train_ids: list[str] = []
        for existing in self.trains:
            existing_segments = self.section_occupation.covered_segments_for(existing.train_id)
            if not existing_segments and existing.current_segment_id is not None:
                existing_segments = {int(existing.current_segment_id)}
            if candidate_segments.intersection(existing_segments):
                conflicting_train_ids.append(existing.train_id)
        if conflicting_train_ids:
            return {
                "ok": False,
                "error": "INITIAL_PLACEMENT_OCCUPIED",
                "conflictingTrainIds": sorted(conflicting_train_ids),
            }
        return None
    def available_initial_directions(self, station_code: str, initial_segment_id: int) -> tuple[str, ...]:
        station_index = next((i for i, station in enumerate(self._station_list) if station.get("code") == station_code), None)
        platform_id = self._platform_id_by_segment.get(initial_segment_id)
        if station_index is None or platform_id not in self._station_platform_ids.get(station_index, ()):
            return ()
        return tuple(direction for direction, offset in (("UP", 1), ("DOWN", -1)) if 0 <= station_index + offset < len(self._station_list) and self._path_plan_for_station_pair(station_index, station_index + offset, platform_id) is not None)
    def remove_train(self, train_id: str) -> JsonDict:
        """鍔ㄦ€佸垹闄や竴鍒楄溅."""
        before = len(self.trains)
        existed_in_specs = train_id in self._train_specs
        self.interlocking_runtime.release_train(train_id)
        self.dispatch_runtime.unregister_train(train_id)
        self.trains = [t for t in self.trains if t.train_id != train_id]
        self._train_specs.pop(train_id, None)
        self._vehicle_config_by_train.pop(train_id, None)
        self._manual_mode_by_train.pop(train_id, None)
        self._dcdp_curve_data.pop(train_id, None)
        self._dcdp_curve_meta.pop(train_id, None)
        self._profile_run_times.pop(train_id, None)
        self._ato_by_train.pop(train_id, None)
        if len(self.trains) == before and not existed_in_specs:
            return {"ok": False, "error": "TRAIN_NOT_FOUND"}
        self._snapshot = self._build_snapshot()
        return {"ok": True, "removed": train_id}

    def load(self) -> None:
        """Load a clean runtime from the persistent configured fleet."""
        self._session_id = str(uuid.uuid4())
        self._snapshot_sequence = 0
        self._run_id = None
        self.clock.load()
        self.station_service = self._build_station_service()
        self._reset_station_history()
        self.power_service = self._build_power_service()
        self.interlocking_runtime.reset()
        # reset() rebuilds the coordinator-owned services, so refresh the
        # compatibility aliases instead of retaining stale pre-reset objects.
        self.route_service = self.interlocking_runtime.route_service
        self.section_occupation = self.interlocking_runtime.section_occupation
        self.signal_resolver = self.interlocking_runtime.signal_resolver
        self.interlocking_rules = self.interlocking_runtime.rule_engine
        self.movement_authority = MovementAuthorityService(
            self.line_map,
            self.interlocking_runtime.catalog,
            self.route_service,
            self.section_occupation,
        )
        self.dispatch_service = RuleBasedDispatchService(
            DispatchRuleConfig(
                min_headway_sec=90.0,
                max_headway_sec=300.0,
                overload_threshold=1.20,
                left_behind_threshold_pax=80,
            )
        )
        self.dispatch_runtime = DispatchRuntimeCoordinator(self.dispatch_service)
        self._last_arrivals_by_platform = {}
        self._last_power_states = self._empty_power_states()
        self._last_power_solve_sim_time_ms = None
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
        self.kpi_tracker.reset()
        self.trains = [
            self._create_train_from_spec(spec)
            for spec in self._train_specs.values()
        ]
        for train in self.trains:
            self._train_power_geometry(train, self._make_vehicle_config(train.train_id, train.onboard_pax))
            self.dispatch_runtime.register_train(train)
        self._initialize_operation_plan()
        if self.recorder is not None:
            self._run_id = self.recorder.start_run(
                self.scenario.name,
                {
                    "phase": 1,
                    "lineId": self.scenario.line_id,
                    "startTimeMs": self.scenario.start_time_ms,
                    "trainCount": len(self.trains),
                    "operationPlanEnabled": self.scenario.operation_plan.enabled,
                    "dutyCount": len(self._operation_duties),
                    "serviceCount": len(self._operation_services),
                    "operationPlanHash": self._operation_plan_hash,
                    "experimentWindow": dict(self._operation_window),
                    "profileWarmup": dict(self._operation_profile_warmup),
                    "powerModelVersion": (
                        self.power_service.network.model_version
                        if self.power_service.network is not None else None
                    ),
                    "powerModelQuality": (
                        self.power_service.network.quality
                        if self.power_service.network is not None else None
                    ),
                },
            )
            if self.power_service.network is not None:
                self.recorder.upsert_power_topology(self.power_service.network.topology_dict())
        self._snapshot = self._build_snapshot()
        self._persist_snapshot(self._snapshot)

    def start(self) -> str:
        """鍚姩浠跨湡锛堝悗鍙扮嚎绋嬶級."""
        with self._lifecycle_lock:
            if self.clock.state.value == "RUNNING":
                self._snapshot = self._build_snapshot()
                return "ALREADY_RUNNING"
            if self.clock.state.value == "PAUSED":
                self.clock.resume()
                self._snapshot = self._build_snapshot()
                return "RESUMED"
            if self.clock.state.value == "IDLE":
                self.load()
            elif self.clock.state.value == "STOPPED":
                self.clock.load()
                self.kpi_tracker.reset()
                if self.scenario.operation_plan.enabled:
                    self._initialize_operation_plan()
                self._snapshot = self._build_snapshot()
            self.clock.start()
            # Publish RUNNING synchronously before the first tick. Otherwise a GET
            # between start() and _tick() can still return the LOADED snapshot.
            self._snapshot = self._build_snapshot()
            self._stop_event.clear()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run_loop, daemon=True)
                self._thread.start()
            return "STARTED"

    def pause(self) -> None:
        self.clock.pause()
        self._snapshot = self._build_snapshot()

    def resume(self) -> None:
        self.clock.resume()
        self._snapshot = self._build_snapshot()

    def set_speed_multiplier(self, multiplier: int) -> int:
        """Set software-only acceleration as a whole number of micro ticks."""
        value = int(multiplier)
        if value < 1 or value > 240:
            raise ValueError("SPEED_MULTIPLIER_OUT_OF_RANGE")
        with self._lifecycle_lock:
            self._speed_multiplier = value
            self._snapshot = self._build_snapshot()
        return value

    def set_tick_interval_seconds(self, interval_seconds: float) -> float:
        """Set wall-clock playback pacing without changing physical tick size."""
        with self._lifecycle_lock:
            self._tick_interval_seconds = min(2.0, max(0.06, float(interval_seconds)))
            self._snapshot = self._build_snapshot()
            return self._tick_interval_seconds

    def step_once(self) -> None:
        """Advance one full engine tick and leave continuous playback paused."""
        with self._lifecycle_lock:
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

    def _should_solve_power(self, sim_time_ms: int) -> bool:
        """Keep electrical fidelity at 1×/10× and sample it once per sim second at 60×."""
        if self._speed_multiplier < 60:
            return True
        return (
            self._last_power_solve_sim_time_ms is None
            or sim_time_ms - self._last_power_solve_sim_time_ms >= 1_000
        )

    def reset_power_network(self) -> None:
        """Restore traction-power topology and clear transient power states."""
        with self._power_lock:
            self.power_service = self._build_power_service()
            self._last_power_states = self._empty_power_states()
            self._last_power_solve_sim_time_ms = None
            self._snapshot = self._build_snapshot()

    def queue_power_command(self, command_type: str, payload: JsonDict) -> JsonDict:
        """Queue an external topology operation for deterministic application at a tick boundary."""
        with self._power_lock:
            command_type = command_type.upper()
            supported = {
                "SUBSTATION_OUTAGE",
                "SUBSTATION_RESTORE",
                "OPERATE_SWITCH",
                "SET_FEEDER_STATUS",
                "SET_CONTACT_SECTION_STATUS",
                "RESET_NETWORK",
            }
            if command_type not in supported:
                raise ValueError("UNSUPPORTED_POWER_COMMAND")
            self._power_command_sequence += 1
            apply_at = payload.get("applyAtSimTimeMs")
            command = PowerCommand(
                command_id=f"PWR-CMD-{self._power_command_sequence:06d}",
                command_type=command_type,
                payload=dict(payload),
                apply_at_sim_time_ms=int(apply_at) if apply_at is not None else None,
            )
            self._power_commands.append(command)
            return {
                "commandId": command.command_id,
                "status": "QUEUED",
                "applyAtSimTimeMs": command.apply_at_sim_time_ms,
            }

    def _apply_power_commands(self, sim_time_ms: int) -> None:
        with self._power_lock:
            pending_count = len(self._power_commands)
            for _ in range(pending_count):
                command = self._power_commands.popleft()
                if command.apply_at_sim_time_ms is not None and command.apply_at_sim_time_ms > sim_time_ms:
                    self._power_commands.append(command)
                    continue
                result: JsonDict = {
                    "commandId": command.command_id,
                    "commandType": command.command_type,
                    "simTimeMs": sim_time_ms,
                    "requestPayload": dict(command.payload),
                }
                try:
                    if command.command_type == "SUBSTATION_OUTAGE":
                        result["data"] = self.apply_power_substation_outage(
                            str(command.payload["targetId"]),
                            big_bilateral=bool(command.payload.get("bigBilateral", True)),
                        )
                    elif command.command_type == "SUBSTATION_RESTORE":
                        network = self.power_service.network
                        if network is None:
                            raise RuntimeError("POWER_NETWORK_NOT_INITIALIZED")
                        result["data"] = network.restore_substation(str(command.payload["targetId"]))
                    elif command.command_type == "OPERATE_SWITCH":
                        switch = self.operate_power_switch(
                            str(command.payload["switchId"]),
                            str(command.payload["state"]),
                        )
                        result["data"] = {
                            "switchId": switch.switch_id,
                            "currentState": switch.current_state,
                        }
                    elif command.command_type == "SET_FEEDER_STATUS":
                        network = self.power_service.network
                        if network is None:
                            raise RuntimeError("POWER_NETWORK_NOT_INITIALIZED")
                        feeder = network.set_feeder_status(
                            str(command.payload["feederId"]),
                            str(command.payload["status"]),
                        )
                        result["data"] = {"feederId": feeder.feeder_id, "status": feeder.status}
                    elif command.command_type == "SET_CONTACT_SECTION_STATUS":
                        network = self.power_service.network
                        if network is None:
                            raise RuntimeError("POWER_NETWORK_NOT_INITIALIZED")
                        section = network.set_contact_section_status(
                            str(command.payload["sectionId"]),
                            str(command.payload["status"]),
                        )
                        result["data"] = {"sectionId": section.section_id, "status": section.status}
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
                if self.recorder is not None and self._run_id is not None:
                    self.recorder.record_power_command(
                        self._run_id,
                        command_id=command.command_id,
                        sim_time_ms=sim_time_ms,
                        command_type=command.command_type,
                        status=str(result["status"]),
                        payload={"request": command.payload, "result": result.get("data", {})},
                        error=result.get("error"),
                    )
                    self._recorded_power_command_ids.add(command.command_id)
            self._power_command_results = self._power_command_results[-20:]

    def power_command_status(self, command_id: str | None = None) -> list[JsonDict]:
        with self._power_lock:
            queued = [
                {
                    "commandId": item.command_id,
                    "commandType": item.command_type,
                    "status": "QUEUED",
                    "applyAtSimTimeMs": item.apply_at_sim_time_ms,
                    "requestPayload": dict(item.payload),
                }
                for item in self._power_commands
            ]
            items = [*self._power_command_results, *queued]
            if command_id is not None:
                items = [item for item in items if item["commandId"] == command_id]
            return items

    def replay_power_commands(self, records: list[JsonDict], *, base_sim_time_ms: int | None = None) -> list[JsonDict]:
        """Queue an exported command sequence while preserving relative timing."""
        if not records:
            return []
        ordered = sorted(records, key=lambda item: (int(item.get("simTimeMs", 0)), str(item.get("commandId", ""))))
        first_time = int(ordered[0].get("simTimeMs", 0))
        base_time = self._absolute_sim_time_ms() if base_sim_time_ms is None else int(base_sim_time_ms)
        queued: list[JsonDict] = []
        for item in ordered:
            payload = dict(item.get("requestPayload") or item.get("payload") or {})
            payload["applyAtSimTimeMs"] = base_time + int(item.get("simTimeMs", 0)) - first_time
            queued.append(self.queue_power_command(str(item["commandType"]), payload))
        return queued

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
            self._last_power_solve_sim_time_ms = None
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
            self._last_power_solve_sim_time_ms = None
            self._snapshot = self._build_snapshot()
            return switch

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._stop_event.set()
            if self.clock.state.value not in ("STOPPED", "IDLE"):
                self.clock.stop()
            if self._thread is not None:
                self._thread.join(timeout=5)
                self._thread = None
            # Stop clears all runtime/transient state but preserves the configured roster.
            self.clock.current_tick = 0
            self.clock.sim_time_seconds = 0.0
            self.station_service = self._build_station_service()
            self._reset_station_history()
            self.power_service = self._build_power_service()
            self.interlocking_runtime.reset()
            # reset() rebuilds the coordinator-owned services, so refresh the
            # compatibility aliases instead of retaining stale pre-reset objects.
            self.route_service = self.interlocking_runtime.route_service
            self.section_occupation = self.interlocking_runtime.section_occupation
            self.signal_resolver = self.interlocking_runtime.signal_resolver
            self.interlocking_rules = self.interlocking_runtime.rule_engine
            self.movement_authority = MovementAuthorityService(
                self.line_map,
                self.interlocking_runtime.catalog,
                self.route_service,
                self.section_occupation,
            )
            self.dispatch_runtime.reset()
            self._last_arrivals_by_platform = {}
            self._last_power_states = self._empty_power_states()
            self._last_power_solve_sim_time_ms = None
            self._last_dispatch_decisions = []
            self._pending_dispatch_decisions = []
            self._ato_by_train = {}
            self._dcdp_curve_data = {}
            self._dcdp_curve_meta = {}
            self._profile_run_times = {}
            self._power_commands.clear()
            self._power_command_results = []
            self._recorded_power_command_ids = set()
            self.speed_profile_service.shutdown()
            # 在 KPI 跟踪器被重置前生成仿真报告（含图表数据）
            self._generate_report()
            self.kpi_tracker.reset()
            self.trains = []
            self._snapshot = self._build_snapshot()

    def _generate_report(self) -> None:
        """在 stop() 中调用：于 KPI 跟踪器重置前生成并保存报告。"""
        if self.recorder is None or self._run_id is None:
            return
        try:
            from app.core.report_generator import ReportGenerator

            kpi_snapshot = self.kpi_tracker.snapshot(self.clock.sim_time_seconds)
            report = ReportGenerator(self.recorder).generate(
                self._run_id, kpi_snapshot=kpi_snapshot
            )
            self.recorder.save_report(self._run_id, report)
            self._last_report = report
        except Exception as exc:  # 报告失败不应影响主流程
            import logging

            logging.getLogger(__name__).warning("仿真报告生成失败: %s", exc)

    def get_report(self, run_id: int | None = None) -> dict | None:
        """获取仿真报告；run_id 缺省时返回当前运行。"""
        if self.recorder is None:
            return None
        rid = run_id if run_id is not None else self._run_id
        if rid is None:
            return None
        return self.recorder.get_report(rid)

    def snapshot(self) -> TickSnapshot | None:
        """绾跨▼瀹夊叏璇诲彇褰撳墠蹇収."""
        with self._lock:
            return self._snapshot

    # 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?    #  浠跨湡涓诲惊鐜?    # 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
    def _refresh_operation_profile_warmup(self) -> None:
        if not self._operation_profile_requests:
            return
        overall = self.speed_profile_service.wait_for(
            tuple(self._operation_profile_requests),
            0.0,
        )
        self._operation_profile_warmup.update({
            "requestedProfileCount": overall["requestedProfileCount"],
            "readyProfileCount": overall["readyProfileCount"],
            "pendingProfileCount": overall["pendingProfileCount"],
            "failedProfileCount": overall["failedProfileCount"],
            "pendingCacheKeys": overall["pendingCacheKeys"],
            "errors": overall["errors"],
            "allProfilesReady": overall["ready"],
        })

    def _hold_operation_clock_for_profile(self) -> bool:
        """Yield wall time for a due DCDP curve without creating fake lateness."""
        if not self.scenario.operation_plan.enabled:
            return False
        waiting = False
        for train in self.trains:
            if (
                train.duty_id is None
                or train.phase != DWELLING
                or train._path_plan is None
                or train._profile_triggered
                or train.dwell_remaining_sec > 0.0
                or train._passenger_service_pending
                or not train.door_system.all_closed_and_locked
            ):
                continue
            if self._prime_path_profile(train, train._path_plan):
                train._profile_triggered = True
                continue
            cache_key = str(self._dcdp_curve_meta.get(train.train_id, {}).get("cacheKey", ""))
            if cache_key:
                status = self.speed_profile_service.wait_for((cache_key,), 0.0)
                if status["failedProfileCount"]:
                    raise RuntimeError(f"DCDP_PROFILE_RUNTIME_FAILED: {status['errors']}")
            waiting = True
        self._refresh_operation_profile_warmup()
        if waiting:
            # Direct batch loops can otherwise consume simulated ticks much
            # faster than the worker receives wall-clock CPU time.
            time.sleep(0.001)
            self._snapshot = self._build_snapshot()
        return waiting

    def _run_loop(self) -> None:
        """鍚庡彴浠跨湡绾跨▼."""
        while not self._stop_event.is_set():
            if self.clock.state.value == "PAUSED":
                time.sleep(0.1)
                continue
            if self.clock.state.value != "RUNNING":
                break
            loop_start = time.perf_counter()
            # Every micro tick still updates passenger arrivals, train stops,
            # power flow and dispatch, so fast-forward preserves the lifecycle.
            for _ in range(self._speed_multiplier):
                if self._stop_event.is_set() or self.clock.state.value != "RUNNING":
                    break
                self._tick()
            elapsed = time.perf_counter() - loop_start
            sleep_sec = max(0.01, self._tick_interval_seconds - elapsed)
            time.sleep(sleep_sec)

    def _tick(self) -> None:
        """鍗曟浠跨湡."""
        self.speed_profile_service.poll()
        if self._hold_operation_clock_for_profile():
            return
        if self.clock.state != ClockState.RUNNING:
            return
        self.clock.step()
        tick = self.clock.current_tick
        sim_time_ms = self._absolute_sim_time_ms()

        # ATS promotes due duties from the abstract depot queue to a physical
        # departure request before CI scans and authorizes the route.
        self._advance_operation_lifecycle(sim_time_ms)

        # External topology operations are serialized at the tick boundary.
        self._apply_power_commands(sim_time_ms)

        # 1) 客流先到达，停站处理生成的载荷供本 tick 车辆请求使用。
        self._last_arrivals_by_platform = self.station_service.update_arrivals(
            sim_time_ms,
            dt_sec=self.clock.tick_seconds,
        )

        # CI scan before control: refresh real head/tail occupation and keep a
        # train at the platform until its interval authority is available.
        self.interlocking_runtime.update(self._interlocking_train_states(sim_time_ms))
        self._authorize_ready_departures(sim_time_ms)

        # 2a) KPI: 记录各列车满载率
        for train in self.trains:
            if train.onboard_pax > 0 or train.phase != IDLE:
                self.kpi_tracker.record_load(train.load_factor)

        # 2) 生成全部列车控制与候选牵引/再生请求，不推进位置。
        prepared_steps: dict[str, PreparedTrainStep] = {}
        handled_train_ids: set[str] = set()
        for train in self.trains:
            handled, prepared = self._prepare_train_step(train, sim_time_ms)
            if handled:
                handled_train_ids.add(train.train_id)
            if prepared is not None:
                prepared_steps[train.train_id] = prepared

        # 3) 同时求解全部列车负载，得到本 tick 电压、限牵和再生能力。
        power_solved = self._should_solve_power(sim_time_ms)
        if power_solved:
            power_states = self._update_power(sim_time_ms, prepared_steps)
            self._last_power_states = power_states
            self._last_power_solve_sim_time_ms = sim_time_ms
        else:
            # 60× fast-forward retains 250 ms train/passenger ticks while
            # holding the most recent 1-second electrical solution.
            power_states = self._last_power_states
        solver_failure = self.power_service.last_solver_failure if power_solved else None
        if solver_failure is not None:
            if self.clock.state == ClockState.RUNNING:
                self.clock.pause()
            self.bus.publish("power.solver_failure", solver_failure, source="engine", tick=tick)
            if self.recorder is not None and self._run_id is not None:
                self.recorder.record_event(
                    self._run_id,
                    "power.solver_failure",
                    solver_failure,
                    tick=tick,
                )

        train_power_flows = {
            item.train_id: item
            for item in (self.power_service.last_network_snapshot.trains if self.power_service.last_network_snapshot else [])
        }

        # 4) 使用本 tick 供电反馈分配实际牵引/电制动力 + 空气制动并推进行车动力学。
        for prepared in prepared_steps.values():
            self._apply_prepared_train_step(prepared, train_power_flows.get(prepared.train.train_id), sim_time_ms)
        for train in self.trains:
            if train.train_id not in handled_train_ids:
                self._advance_train(train, sim_time_ms)

        for train in self.trains:
            if (
                train.phase == DWELLING
                and train.dwell_remaining_sec > self.clock.tick_seconds
                and train.departure_authorized
            ):
                train.departure_authorized = False
                train.interlocking_hold_reason = None
                train.active_route_ids = ()

        # Scan again after movement so signal aspects and tail-clear releases
        # in the published snapshot correspond to the current tick.
        self.interlocking_runtime.update(self._interlocking_train_states(sim_time_ms))
        departures = self.dispatch_runtime.observe(
            self.trains,
            self.clock.sim_time_seconds,
        )
        self._record_operation_departures(departures, sim_time_ms)

        # 5) 璋冨害鍐崇瓥
        decisions = self._make_dispatch_decisions(sim_time_ms, power_states)
        if self._pending_dispatch_decisions:
            decisions = [*self._pending_dispatch_decisions, *decisions]
            self._pending_dispatch_decisions = []
        self._last_dispatch_decisions = decisions

        # 6) 鍙戝竷浜嬩欢
        for train in self.trains:
            self.bus.publish(
                "train.state",
                train.to_dict(),
                source="engine",
                tick=tick,
            )
        for departure in departures:
            self.bus.publish(
                "dispatch.departed",
                departure.to_dict(),
                source="dispatch",
                tick=tick,
            )
        self.bus.publish("clock.tick", {"tick": tick, "simTimeMs": sim_time_ms}, source="engine", tick=tick)

        # 7) 璁板綍鍒?SQLite
        if self.recorder is not None and self._run_id is not None:
            self.recorder.begin_batch()
            for train in self.trains:
                self.recorder.record_event(
                    self._run_id,
                    "train.state",
                    train.to_dict(),
                    tick=tick,
                )
            for departure in departures:
                self.recorder.record_event(
                    self._run_id,
                    "dispatch.departed",
                    departure.to_dict(),
                    tick=tick,
                )
            for operation_event in self._pending_operation_events:
                self.recorder.record_event(
                    self._run_id,
                    "operations.lifecycle",
                    operation_event,
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
                        detail={
                            "tick": tick,
                            "tractionPowerRequestKw": train_flow.traction_power_request_kw,
                            "tractionPowerDeliveredKw": train_flow.traction_power_delivered_kw,
                            "auxiliaryPowerKw": train_flow.auxiliary_power_kw,
                            "regenPowerAvailableKw": train_flow.regen_power_available_kw,
                            "regenPowerSelfConsumedKw": train_flow.regen_power_self_consumed_kw,
                            "regenPowerExportedKw": train_flow.regen_power_exported_kw,
                            "regenPowerAcceptedKw": train_flow.regen_power_accepted_kw,
                            "regenPowerWastedKw": train_flow.regen_power_wasted_kw,
                            "headMileageM": train_flow.head_mileage_m,
                            "tailMileageM": train_flow.tail_mileage_m,
                            "pantographMileagesM": list(train_flow.pantograph_mileages_m),
                            "spannedPowerSectionIds": list(train_flow.spanned_power_section_ids),
                        },
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
                for storage_flow in network_snapshot.supercapacitor_flows:
                    self.recorder.record_supercapacitor_power(
                        self._run_id,
                        sim_time_ms=sim_time_ms,
                        storage_id=storage_flow.storage_id,
                        soc=storage_flow.soc,
                        stored_energy_kwh=storage_flow.stored_energy_kwh,
                        charge_power_kw=storage_flow.charge_power_kw,
                        discharge_power_kw=storage_flow.discharge_power_kw,
                        conversion_losses_kw=storage_flow.conversion_losses_kw,
                        cumulative_charged_kwh=storage_flow.cumulative_charged_kwh,
                        cumulative_discharged_kwh=storage_flow.cumulative_discharged_kwh,
                        state=storage_flow.state,
                        status=storage_flow.status,
                        detail={"tick": tick},
                    )
                self.recorder.record_regen_energy(
                    self._run_id,
                    sim_time_ms=sim_time_ms,
                    generated_regen_kw=network_snapshot.generated_regen_kw,
                    absorbed_regen_kw=network_snapshot.absorbed_regen_kw,
                    feedback_regen_kw=network_snapshot.feedback_regen_kw,
                    wasted_regen_kw=network_snapshot.wasted_regen_kw,
                    detail={
                        "tick": tick,
                        "alerts": network_snapshot.alerts,
                        "transferLossesKw": network_snapshot.regen_transfer_losses_kw,
                        "selfConsumedRegenKw": network_snapshot.self_consumed_regen_kw,
                    },
                )
                for path in network_snapshot.regen_paths:
                    self.recorder.record_regen_path(
                        self._run_id,
                        sim_time_ms=sim_time_ms,
                        source_train_id=path.source_train_id,
                        sink_type=path.sink_type,
                        sink_id=path.sink_id,
                        via_substation_id=path.via_substation_id,
                        source_feeder_id=path.source_feeder_id,
                        sink_feeder_id=path.sink_feeder_id,
                        generated_kw=path.generated_kw,
                        delivered_kw=path.delivered_kw,
                        losses_kw=path.losses_kw,
                        current_a=path.current_a,
                        path_resistance_ohm=path.path_resistance_ohm,
                        detail={"tick": tick},
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
                    payload={
                        "request": command_result.get("requestPayload", {}),
                        "result": command_result.get("data", {}),
                    },
                    error=command_result.get("error"),
                )
                self._recorded_power_command_ids.add(command_id)
            if network_snapshot is not None:
                for alert in network_snapshot.alerts:
                    if str(alert.get("type", "")).endswith("_PROTECTION_TRIP"):
                        self.recorder.record_event(
                            self._run_id,
                            "power.protection_trip",
                            alert,
                            tick=tick,
                        )
            self.recorder.commit_batch()

        self._pending_operation_events = []

        self._record_station_history(sim_time_ms)
        # 8) 鏇存柊蹇収
        self._snapshot = self._build_snapshot()
        if tick % self._snapshot_interval_ticks == 0:
            self._persist_snapshot(self._snapshot)

    # 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?    #  鍒楄溅鎺ㄨ繘閫昏緫
    # 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
    def _other_train_positions(self, train: SimTrainState) -> tuple[TrainPosition, ...]:
        positions: list[TrainPosition] = []
        for other in self.trains:
            if other.train_id == train.train_id or other._path_plan is None:
                continue
            positions.append(
                TrainPosition(
                    train_id=other.train_id,
                    direction=other._path_plan.direction,
                    path_plan=other._path_plan,
                    head_position_m=other.path_position_m,
                    length_m=other.train_length_m,
                )
            )
        return tuple(positions)

    def _movement_authority_for_train(
        self,
        train: SimTrainState,
        path_plan: PathPlan,
        path_position_m: float,
        vehicle_config: VehicleConfig,
    ):
        # MA needs the complete planned chain so an unlocked next route forms
        # a real boundary. Passing only active_route_ids would make a partial
        # prefix look complete and incorrectly authorize the station stop.
        route_ids = tuple(train._planned_route_ids)
        if not route_ids:
            train.movement_authority_end_m = path_position_m
            train.movement_authority_reason = "NO_ROUTE_AUTHORITY"
            train.movement_authority_speed_mps = 0.0
            train.movement_authority_locked_route_ids = ()
            return None
        authority = self.movement_authority.calculate(
            train_id=train.train_id,
            path_plan=path_plan,
            route_chain_ids=route_ids,
            position_m=path_position_m,
            speed_mps=train.speed_mps,
            vehicle=vehicle_config,
            other_trains=self._other_train_positions(train),
        )
        train.movement_authority_end_m = authority.end_position_m
        train.movement_authority_reason = authority.end_reason
        train.movement_authority_speed_mps = authority.permitted_speed_mps
        train.movement_authority_locked_route_ids = authority.locked_route_ids
        return authority
    def _prepare_train_step(
        self,
        train: SimTrainState,
        sim_time_ms: int,
    ) -> tuple[bool, PreparedTrainStep | None]:
        """Prepare a PathPlan train without advancing physics; return whether the train was handled."""
        if train.lifecycle_state in {"IN_DEPOT", "RETURN_REQUESTED", "STORED"}:
            train.speed_mps = 0.0
            train.traction_percent = 0.0
            train.brake_percent = 0.0
            return True, None
        stations = self._station_list
        n = len(stations)
        if train._terminal_arrival_release_route_ids:
            if not self.interlocking_runtime.complete_terminal_arrival(
                train.train_id,
                train._terminal_arrival_release_route_ids,
            ):
                train.speed_mps = 0.0
                train.traction_percent = 0.0
                train.brake_percent = 20.0
                train.departure_authorized = False
                train.interlocking_hold_reason = "TURNBACK_TERMINAL_RELEASE_PENDING"
                return True, None
            train._terminal_arrival_release_route_ids = ()
            train.active_route_ids = ()
            train.departure_authorized = False
            train.movement_authority_locked_route_ids = ()
            train.interlocking_hold_reason = None
        turnback_phase = self._current_turnback_phase(train)
        if turnback_phase is not None:
            next_idx = train.station_index
            path_plan = self._ensure_turnback_phase_path(train, turnback_phase)
            self._extend_turnback_authority(train, turnback_phase)
        else:
            next_idx = train.station_index + 1 if train.direction == "UP" else train.station_index - 1
            if next_idx < 0 or next_idx >= n:
                train.speed_mps = 0.0
                train.traction_percent = 0.0
                train.brake_percent = 20.0
                train.interlocking_hold_reason = "TURNBACK_PLAN_REQUIRED"
                return True, None
            path_plan = self._ensure_interval_path(train, next_idx)
        if path_plan is None:
            # Ordinary passenger operation has no topology-distance fallback.
            # Keep the train at its real platform and retry on later ticks;
            # otherwise a missing route chain would silently bypass CI/MA.
            train.speed_mps = 0.0
            train.traction_percent = 0.0
            train.brake_percent = 20.0
            train.target_speed_mps = 0.0
            train.departure_authorized = False
            train.interlocking_hold_reason = "NO_ROUTE_TABLE_PATH"
            train.active_route_ids = ()
            if train.phase == DWELLING:
                train.dwell_remaining_sec = max(
                    train.dwell_remaining_sec,
                    self.clock.tick_seconds,
                )
            return True, None

        if turnback_phase is None:
            self._extend_interval_authority(train, path_plan, sim_time_ms)

        dt = self.clock.tick_seconds
        if train.phase in (DWELLING, IDLE):
            train.speed_mps = 0.0
            if turnback_phase is None:
                train.path_position_m = 0.0
            train.path_total_length_m = path_plan.total_length_m
            train.distance_to_next_m = max(0.0, path_plan.total_length_m - train.path_position_m)
            train.target_distance_m = path_plan.total_length_m
            train.segment_progress = (
                min(1.0, train.path_position_m / path_plan.total_length_m)
                if path_plan.total_length_m > 0
                else 1.0
            )
            train.traction_percent = 0.0
            train.brake_percent = 0.0 if train.phase == IDLE else 20.0
            train.target_speed_mps = 0.0
            train.acceleration_mps2 = 0.0
            train.resistance_force_n = 0.0
            train.applied_grade_ratio = 0.0
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
                train._profile_triggered = self._prime_path_profile(train, path_plan)
            # Door state advances on simulation time, independently from UI or
            # hardware polling. Passenger dwell starts only after doors prove open.
            train.door_system.control_mode = train.operation_mode
            if (
                train._passenger_service_pending
                and train._door_stop_platform_id is None
                and train.current_platform_id is not None
            ):
                train._door_stop_platform_id = train.current_platform_id
            permitted_side = (
                self._door_side_for(train.direction)
                if train._door_stop_platform_id is not None
                else DoorSide.NONE
            )
            train.door_system.set_permission(permitted_side)
            train.door_system.advance(dt)
            self._sync_legacy_door_state(train)
            if train._passenger_service_pending:
                if train.operation_mode == "ATO" and train.door_system.all_closed_and_locked:
                    train.door_system.request_open(train.door_system.permitted_side, "ATO")
                    self._sync_legacy_door_state(train)
                elif train.operation_mode == "MANUAL" and train.door_system.all_closed_and_locked:
                    train.door_notice = "WAITING_MANUAL_OPEN"
                if train.door_system.aggregate_state != "OPEN":
                    return True, None
                self._process_station_stop(train, sim_time_ms)
                train._passenger_service_pending = False
                self._sync_legacy_door_state(train)
                return True, None
            if train.dwell_remaining_sec > 0:
                if train.door_system.aggregate_state == "OPEN":
                    self._advance_open_door_passengers(train, sim_time_ms, dt)
                train.dwell_remaining_sec = max(0.0, train.dwell_remaining_sec - dt)
                if 0 < train.dwell_remaining_sec <= 5.0:
                    train.door_notice = "PREPARE_CLOSE"
                if train.dwell_remaining_sec == 0.0:
                    self._record_completed_station_stop(train, sim_time_ms)
                    train.current_boarding_rate_pax_per_sec = 0.0
                    train.current_alighting_rate_pax_per_sec = 0.0
                    if train.operation_mode == "ATO":
                        train.door_system.request_close("ATO")
                    else:
                        train.door_notice = "WAITING_MANUAL_CLOSE"
                    self._sync_legacy_door_state(train)
                return True, None
            train.dwell_remaining_sec = 0.0
            if not train.door_system.all_closed_and_locked:
                if train.operation_mode == "ATO" and train.door_system.aggregate_state != "CLOSING":
                    train.door_system.request_close("ATO")
                    self._sync_legacy_door_state(train)
                elif train.operation_mode == "MANUAL":
                    train.door_notice = "WAITING_MANUAL_CLOSE"
                return True, None
            train.door_system.set_permission(DoorSide.NONE)
            train._door_stop_platform_id = None
            self._sync_legacy_door_state(train)
            # DCDP is mandatory for a PathPlan interval.  The asynchronous
            # worker may need more wall-clock time than a 10x simulation dwell
            # provides, so keep the train at the platform until the exact
            # profile is installed instead of departing on the fallback curve.
            if self._ato_config.use_dynamic_programming_profile and not train._profile_triggered:
                return True, None
            train.estimated_run_time_s = self._profile_run_times.get(train.train_id, 0.0)
            if (
                train.duty_id is not None
                and train.planned_departure_ms is not None
                and sim_time_ms < train.planned_departure_ms
                and train.turnback_state in {None, "COMPLETED"}
            ):
                train.last_dispatch_action = "HOLD"
                train.last_dispatch_reason = "TIMETABLE_EARLY_HOLD"
                return True, None
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

        vehicle_config = self._make_vehicle_config(train.train_id, train.onboard_pax)
        authority = self._movement_authority_for_train(train, path_plan, path_position_m, vehicle_config)
        target_position_m = authority.end_position_m if authority is not None else path_position_m
        permitted_speed_mps = authority.permitted_speed_mps if authority is not None else 0.05

        target = AtoTarget(
            target_position_m=target_position_m,
            permitted_speed_mps=max(0.05, min(train.permitted_speed_mps, permitted_speed_mps)),
            path_plan=path_plan,
        )
        ato = self._ato_for_train(train.train_id)
        command = ato.decide(state, target)
        command = self._manual_override(command, train.train_id)
        if authority is not None:
            command = self.movement_authority.supervise(
                command,
                authority,
                position_m=path_position_m,
                speed_mps=train.speed_mps,
            )
        # Vehicle-side door interlock is the final authority for ATO and CM.
        # An open, moving, obstructed or emergency-unlocked door removes
        # traction regardless of the command source.
        command = self._enforce_door_interlock(train, command)
        demand = TractionDriveModel(vehicle_config).demand(command, train.speed_mps)
        power_demand = TractionDriveModel(vehicle_config).electrical_power_demand(
            demand,
            train.speed_mps,
            auxiliary_power_kw=150.0,
        )
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
            power_demand=power_demand,
            gradient_force_n=gradient_force_n,
            turnback_phase_index=(train.turnback_phase_index if turnback_phase is not None else None),
        )

    def _apply_prepared_train_step(self, prepared: PreparedTrainStep, flow: Any, sim_time_ms: int) -> None:
        train = prepared.train
        try:
            # Once physics advances, the train is no longer occupying a
            # platform as a stopping location. Its route-specific PathPlan
            # remains the positional authority until the next arrival.
            train.current_platform_id = None
            traction_limit = float(flow.traction_limit_ratio) if flow is not None else 1.0
            regen_limit = float(flow.regen_limit_ratio) if flow is not None else 1.0
            blend = BrakeBlendService.blend(prepared.demand, regen_limit)
            traction_force_n = prepared.demand.traction_force_n * max(0.0, min(1.0, traction_limit))
            model = SimpleVehicleModel(prepared.vehicle_config)
            result = model.step_with_forces(
                prepared.state,
                traction_force_n=traction_force_n,
                brake_force_n=blend.total_brake_force_n,
                electric_brake_force_n=blend.electric_brake_force_n,
                dt_s=self.clock.tick_seconds,
                gradient_force_n=prepared.gradient_force_n,
            )
            resistance_force_n = model.running_resistance_n(
                prepared.state.speed_mps,
                traction_force_n,
                blend.total_brake_force_n,
            )

            path_plan = prepared.path_plan
            new_position_m = min(max(0.0, result.position_m), path_plan.total_length_m)
            next_limit_mps = path_plan.speed_limit_at(new_position_m, train.permitted_speed_mps)
            train.speed_mps = min(max(0.0, result.speed_mps), next_limit_mps)
            train.path_position_m = new_position_m
            train.path_total_length_m = path_plan.total_length_m
            train.segment_progress = min(1.0, new_position_m / path_plan.total_length_m) if path_plan.total_length_m else 1.0
            train.distance_to_next_m = max(0.0, path_plan.total_length_m - new_position_m)
            train.target_distance_m = path_plan.total_length_m
            train.traction_percent = prepared.command.traction_percent
            train.brake_percent = prepared.command.brake_percent
            train.target_speed_mps = self._ato_for_train(train.train_id).last_target_speed_mps
            train.mass_kg = prepared.vehicle_config.mass_kg
            train.acceleration_mps2 = result.acceleration_mps2
            train.resistance_force_n = resistance_force_n
            train.applied_grade_ratio = (
                prepared.gradient_force_n
                / max(prepared.vehicle_config.mass_kg * 9.80665, 1.0)
            )
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
                train.traction_power_request_kw = float(flow.traction_power_request_kw)
                train.traction_power_delivered_kw = float(flow.traction_power_delivered_kw)
                train.auxiliary_power_kw = float(flow.auxiliary_power_kw)
                train.regen_power_available_kw = float(flow.regen_power_available_kw)
                train.regen_power_self_consumed_kw = float(flow.regen_power_self_consumed_kw)
                train.regen_power_accepted_kw = float(flow.regen_power_accepted_kw)
                train.regen_power_wasted_kw = float(flow.regen_power_wasted_kw)
                dt_h = self.clock.tick_seconds / 3600.0
                train.traction_energy_kwh += train.traction_power_delivered_kw * dt_h
                train.auxiliary_energy_kwh += train.auxiliary_power_kw * dt_h
                train.regen_generated_kwh += train.regen_power_available_kw * dt_h
                train.regen_self_consumed_kwh += train.regen_power_self_consumed_kw * dt_h
                train.regen_accepted_kwh += train.regen_power_accepted_kw * dt_h
                train.regen_wasted_kwh += train.regen_power_wasted_kw * dt_h
                train.energy_kwh += (
                    train.traction_power_delivered_kw
                    + train.auxiliary_power_kw
                    - train.regen_power_accepted_kw
                ) * dt_h
            self._update_train_path_context(train)

            arrived = train.distance_to_next_m <= self._ato_config.stop_tolerance_m and train.speed_mps <= 0.2
            if arrived:
                if prepared.turnback_phase_index is None:
                    self._complete_path_arrival(train, prepared.next_idx, prepared.next_station, sim_time_ms)
                else:
                    self._complete_turnback_phase(train, sim_time_ms)
                return

            ato = self._ato_for_train(train.train_id)
            braking_profile = ato.last_profile_mode == "MAX_BRAKE" or ato.last_profile_mode.startswith("BRAKE_")
            self._update_running_phase(train, prepared.command.brake_percent, braking_profile)
        except Exception as exc:
            print(f"[Engine] Prepared advancement failed for {train.train_id}: {exc}")
            train.traction_percent = 0.0
            train.brake_percent = 0.0
            train.acceleration_mps2 = 0.0
            train.resistance_force_n = 0.0
            train.applied_grade_ratio = 0.0
            train.traction_force_n = 0.0
            train.electric_brake_force_n = 0.0
            train.pneumatic_brake_force_n = 0.0
            train.speed_mps = max(0.0, train.speed_mps - 0.8 * self.clock.tick_seconds)

    def _advance_train(self, train: SimTrainState, sim_time_ms: int) -> None:
        """姣?tick 鎺ㄨ繘涓€杈嗗垪杞?鈥?ATO 鍐崇瓥 + 鐗涢】鐗╃悊妯″瀷."""
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
        path_plan = self._ensure_interval_path(train, next_idx)
        dist = (
            path_plan.total_length_m
            if path_plan is not None
            else abs(self._station_distances[next_idx] - self._station_distances[train.station_index])
        )

        if path_plan is not None:
            self._advance_train_on_path(train, next_idx, next_stn, path_plan, sim_time_ms, dist, dt)
            return

        # 鈹€鈹€ DWELLING 鈹€鈹€
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
            # 搴旂敤棰勮杩愯鏃堕棿
            train.estimated_run_time_s = self._profile_run_times.get(train.train_id, 0.0)
            train.phase = DEPARTING
            # fall through to physics

        # 鈹€鈹€ 鐗╃悊妯″瀷鎺ㄨ繘 鈹€鈹€
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

            # 鈹€鈹€ ATO 鐩爣锛歱rofile 浣滀负璺熻釜鐩爣 + 绾胯矾闄愰€熶綔涓轰笂闄?鈹€鈹€
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

            vcfg = self._make_vehicle_config(train.train_id, train.onboard_pax)
            vm = SimpleVehicleModel(vcfg)
            result = vm.step(state, cmd, dt)

            # 鈹€鈹€ 鍐欏洖 鈹€鈹€
            train.speed_mps = max(0.0, result.speed_mps)
            train.traction_percent = cmd.traction_percent
            train.brake_percent = cmd.brake_percent
            train.target_speed_mps = self.ato.last_target_speed_mps
            train.energy_kwh = result.net_energy_kwh

            # 鈹€鈹€ 浣嶇疆鏇存柊 鈹€鈹€
            new_progress = (result.position_m - cur_mileage) / interval_m if interval_m > 0 else 1.0

            if new_progress >= 1.0:
                # 鍒扮珯
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

                # 娓呴櫎褰撳墠鏇茬嚎 + 瑙﹀彂鏍囧織
                self._dcdp_curve_data.pop(train.train_id, None)
                self._dcdp_curve_meta.pop(train.train_id, None)
                self._profile_run_times.pop(train.train_id, None)
                train._profile_triggered = False  # 涓嬩竴绔欏厑璁歌Е鍙?                self.ato.reset()  # 閲嶇疆 PID 绉垎 + profile cache

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

                self._begin_station_stop(train)
            else:
                train.segment_progress = new_progress
                train.distance_to_next_m = target_position_m - result.position_m if train.direction == "UP" else result.position_m - target_position_m
                # Phase 鏇存柊
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
        """鎸?PathPlan 灞€閮ㄥ潗鏍囨帹杩涗竴杈嗚溅."""
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
                train._profile_triggered = self._prime_path_profile(train, path_plan)

            if train.dwell_remaining_sec > 0:
                train.dwell_remaining_sec = max(0.0, train.dwell_remaining_sec - dt)
                return

            train.dwell_remaining_sec = 0.0
            if self._ato_config.use_dynamic_programming_profile and not train._profile_triggered:
                return
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

            # 检查前方信号灯色
            emergency_brake = False
            if train.phase != DWELLING and train.phase != IDLE:
                next_sig = self._next_signal_ahead(train, path_plan, path_position_m)
                if next_sig is not None:
                    aspect = self.interlocking_runtime.signal_resolver.resolve(
                        int(next_sig.get("id", 0))
                    )
                    if aspect == "RED":
                        emergency_brake = True

            target = AtoTarget(
                target_position_m=path_plan.total_length_m,
                permitted_speed_mps=train.permitted_speed_mps,
                path_plan=path_plan,
                emergency_brake_required=emergency_brake,
            )
            ato = self._ato_for_train(train.train_id)
            cmd = ato.decide(state, target)
            cmd = self._manual_override(cmd, train.train_id)
            vcfg = self._make_vehicle_config(train.train_id, train.onboard_pax)
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

            braking_profile = ato.last_profile_mode == "MAX_BRAKE" or ato.last_profile_mode.startswith("BRAKE_")
            self._update_running_phase(train, cmd.brake_percent, braking_profile)

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

    def _update_running_phase(
        self,
        train: SimTrainState,
        brake_percent: float,
        braking_profile: bool,
    ) -> None:
        """Classify a moving train without flickering on ATO command micro-adjustments."""
        # APPROACHING is an operational phase, not a per-tick brake-command
        # indicator.  Once a train has entered its terminal braking zone it
        # remains approaching until `_complete_path_arrival` makes it DWELLING.
        if train.phase == APPROACHING:
            return

        cruise_threshold = min(self.CRUISE_SPEED_MPS, train.local_speed_limit_mps) * 0.95
        if train.speed_mps >= cruise_threshold:
            train.phase = CRUISING
            return

        braking_distance_m = train.speed_mps * train.speed_mps / (2.0 * self._ato_config.expected_deceleration_mps2)
        approach_zone_m = max(120.0, braking_distance_m + self._ato_config.brake_margin_m * 2.0)
        is_braking = brake_percent > 5.0 or braking_profile
        if is_braking and train.speed_mps > 0.5 and train.distance_to_next_m <= approach_zone_m:
            train.phase = APPROACHING
        else:
            train.phase = DEPARTING

    def _complete_path_arrival(
        self,
        train: SimTrainState,
        next_idx: int,
        next_stn: JsonDict,
        sim_time_ms: int,
    ) -> None:
        completed_plan = train._path_plan
        destination_platform_id = (
            int(completed_plan.destination_platform_id)
            if completed_plan is not None
            else None
        )
        train.speed_mps = 0.0
        # Keep the completed path through the post-movement CI scan. Clearing
        # it here made the train disappear from its destination detector before
        # RouteService could observe entry into the route's final section.
        if completed_plan is not None:
            train.segment_progress = 1.0
            train.path_position_m = completed_plan.total_length_m
            train.path_total_length_m = completed_plan.total_length_m
            train.current_platform_id = destination_platform_id
            self._update_train_path_context(train)
            # A platform at offset 0 can be the exact boundary after the last
            # travelled constraint. Use the PathPlan's explicit destination
            # platform as the stopped head position, not the preceding Seg.
            self._anchor_train_at_platform(train, destination_platform_id)
        else:
            train.segment_progress = 0.0
            train.path_position_m = 0.0
            train.path_total_length_m = 0.0
            train.current_platform_id = None
            train.current_segment_id = None
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
        train.acceleration_mps2 = 0.0
        train.resistance_force_n = 0.0
        train.applied_grade_ratio = 0.0
        train.traction_force_n = 0.0
        train.electric_brake_force_n = 0.0
        train.pneumatic_brake_force_n = 0.0
        self._record_operation_arrival(train, sim_time_ms)

        # The station platform is the explicit end of the previous interval.
        # Release any terminal-overlap route that cannot be tail-cleared by
        # physical occupation before preparing the next station interval.
        self.interlocking_runtime.complete_interval(train.train_id)

        self._dcdp_curve_data.pop(train.train_id, None)
        self._dcdp_curve_meta.pop(train.train_id, None)
        self._profile_run_times.pop(train.train_id, None)
        train._profile_triggered = False
        self._ato_for_train(train.train_id).reset()

        stations = self._station_list
        if((train.direction == "UP" and next_idx == len(stations) - 1) or (
            train.direction == "DOWN" and next_idx == 0)
        ):
            duty = self._operation_duties.get(train.duty_id or "")
            if duty is not None and train.service_id == duty.service_ids[-1]:
                self._handle_planned_terminal(train, sim_time_ms)
                return
            # The inbound route ends in the occupied terminal platform. It
            # must pass the same guarded terminal-arrival release before an
            # opposing turnback route can be requested.
            train._terminal_arrival_release_route_ids = train._planned_route_ids
            self._plan_terminal_turnback(train)
            self._activate_return_service(train, sim_time_ms)
        elif completed_plan is None:
            # Legacy scenarios without a route-table path still need a best
            # effort platform anchor. Normal PathPlan arrivals never reselect
            # another platform at the same station.
            self._anchor_train_at_current_platform(train)
        new_next_idx = next_idx + 1 if train.direction == "UP" else next_idx - 1
        if 0 <= new_next_idx < len(stations):
            new_next_stn = stations[new_next_idx]
            train.next_station_code = str(new_next_stn.get("code", ""))
            train.next_station_name = new_next_stn.get("name", "")
            next_plan = self._path_plan_for_station_pair(
                next_idx,
                new_next_idx,
                train.current_platform_id,
                train_length_m=train.train_length_m,
            )
            if next_plan is not None:
                train.target_distance_m = next_plan.total_length_m
                train.distance_to_next_m = next_plan.total_length_m
            else:
                train.target_distance_m = 0.0
                train.distance_to_next_m = 0.0
        else:
            train.next_station_code = ""
            train.next_station_name = ""
            train.target_distance_m = 0.0
            train.distance_to_next_m = 0.0

        self._begin_station_stop(train)

    def _plan_terminal_turnback(self, train: SimTrainState) -> None:
        """Create a physical route-table turnback without changing direction."""
        if train.current_platform_id is None:
            train.turnback_state = "BLOCKED"
            train.interlocking_hold_reason = "TURNBACK_REQUIRES_PLATFORM"
            return
        try:
            plan = self.route_chain_planner.plan_turnback(
                train.current_station_code,
                int(train.current_platform_id),
            )
        except ValueError as exc:
            train.turnback_state = "BLOCKED"
            train.interlocking_hold_reason = str(exc)
            return
        train._turnback_plan = plan
        train.turnback_phase_index = 0
        train._turnback_authorized_route_count = 0
        train.turnback_state = "PLANNED"
        train.departure_authorized = False
        train.interlocking_hold_reason = None

    def _turn_train_at_terminal(self, train: SimTrainState) -> None:
        """Compatibility entry point that now schedules, rather than teleports.

        Older callers used this method as a synchronous terminal hook. Keep
        that hook, but make it perform the first real planning/CI/MA boundary
        while leaving the service direction unchanged.
        """
        station_platforms = self._station_platform_ids.get(train.station_index, ())
        configured_origin = next(
            (
                platform_id
                for platform_id in station_platforms
                if self._has_terminal_turnback(
                    train.current_station_code,
                    platform_id,
                )
            ),
            None,
        )
        externally_relocated = train.current_platform_id != configured_origin
        if configured_origin is not None and externally_relocated:
            self._anchor_train_at_platform(train, configured_origin)
            # A legacy synchronous caller may relocate a train by changing
            # only its station fields. Its old interval path is not physical
            # evidence for a terminal reversal and must not enter the
            # former-tail continuity check.
            train._path_plan = None
            train._planned_route_ids = ()
            train._path_origin_station_index = None
            train._path_destination_station_index = None
            train._track_trace = None
            train._trace_path_start_index = None
        self._plan_terminal_turnback(train)
        phase = self._current_turnback_phase(train)
        if phase is None or self._ensure_turnback_phase_path(train, phase) is None:
            return
        authority = self.interlocking_runtime.request_departure(
            train.train_id,
            phase.path_plan,
            phase.route_ids[:1],
        )
        train.departure_authorized = authority.granted
        train.interlocking_hold_reason = authority.failure_reason
        train.active_route_ids = authority.route_ids if authority.granted else ()
        if not authority.granted:
            return
        train._turnback_authorized_route_count = 1
        train.turnback_state = "RUNNING"
        vehicle = self._make_vehicle_config(train.train_id, train.onboard_pax)
        self._movement_authority_for_train(
            train,
            phase.path_plan,
            train.path_position_m,
            vehicle,
        )

    def _has_terminal_turnback(self, terminal_id: str, platform_id: int) -> bool:
        try:
            self.route_chain_planner.plan_turnback(terminal_id, platform_id)
        except ValueError:
            return False
        return True

    @staticmethod
    def _current_turnback_phase(train: SimTrainState) -> TurnbackPhase | None:
        plan = train._turnback_plan
        index = train.turnback_phase_index
        if plan is None or index is None or not 0 <= index < len(plan.phases):
            return None
        return plan.phases[index]

    def _ensure_turnback_phase_path(
        self,
        train: SimTrainState,
        phase: TurnbackPhase,
    ) -> PathPlan | None:
        if train._path_plan is phase.path_plan:
            self._update_train_path_context(train)
            return phase.path_plan

        start_position_m = 0.0
        previous_path = train._path_plan
        if previous_path is not None and previous_path.direction != phase.path_plan.direction:
            try:
                start_position_m = self._turnback_reversal_position(train, phase.path_plan)
            except ValueError as exc:
                train.turnback_state = "BLOCKED"
                train.interlocking_hold_reason = str(exc)
                return None

        train._path_plan = phase.path_plan
        train._planned_route_ids = phase.route_ids
        train._path_origin_station_index = train.station_index
        train._path_destination_station_index = train.station_index
        self._activate_train_track_trace(train, phase.path_plan)
        train.path_position_m = start_position_m
        train.path_total_length_m = phase.path_plan.total_length_m
        train.target_distance_m = phase.path_plan.total_length_m
        train.distance_to_next_m = max(0.0, phase.path_plan.total_length_m - start_position_m)
        train.path_segment_count = len(phase.path_plan.segment_ids)
        train.path_constraint_count = len(phase.path_plan.constraints)
        train.current_platform_id = (
            phase.path_plan.origin_platform_id
            if phase.path_plan.origin_platform_id >= 0 and start_position_m <= 1e-9
            else None
        )
        train._profile_triggered = False
        train._turnback_authorized_route_count = 0
        train.turnback_state = "WAITING_ROUTE"
        self._dcdp_curve_data.pop(train.train_id, None)
        self._dcdp_curve_meta.pop(train.train_id, None)
        self._profile_run_times.pop(train.train_id, None)
        self._ato_for_train(train.train_id).reset()
        self._update_train_path_context(train)
        return phase.path_plan

    def _turnback_reversal_position(self, train: SimTrainState, next_path: PathPlan) -> float:
        """Move the positional reference from the old head to the physical tail."""
        trace = train._track_trace
        current_segment_id = train.current_segment_id
        previous_path = train._path_plan
        if trace is None or current_segment_id is None or previous_path is None:
            raise ValueError("TURNBACK_TRACE_UNAVAILABLE")
        if next_path.total_length_m + 1e-9 < train.train_length_m:
            raise ValueError("TURNBACK_PATH_SHORTER_THAN_TRAIN")

        previous_direction = "FORWARD" if previous_path.direction == "forward" else "BACKWARD"
        old_rear_segments = list(trace.rear_segment_ids(current_segment_id, previous_direction))
        reversal_position_m = min(train.train_length_m, next_path.total_length_m)
        new_head_constraint = next_path.constraint_at(reversal_position_m)
        if new_head_constraint is None:
            raise ValueError("TURNBACK_PATH_HAS_NO_CONSTRAINT")
        required_segments = list(
            next_path.segment_ids[
                : next_path.segment_ids.index(new_head_constraint.segment_id) + 1
            ]
        )
        if old_rear_segments[: len(required_segments)] != required_segments:
            raise ValueError("TURNBACK_PATH_DOES_NOT_FOLLOW_FORMER_TAIL")
        return reversal_position_m

    def _extend_turnback_authority(
        self,
        train: SimTrainState,
        phase: TurnbackPhase,
    ) -> None:
        if train._path_plan is not phase.path_plan:
            return
        if (
            train._turnback_authorized_route_count == 0
            and train.phase == DWELLING
            and (
                train.dwell_remaining_sec > self.clock.tick_seconds
                or train.door_state != "CLOSED"
            )
        ):
            return
        if train._turnback_authorized_route_count >= len(phase.route_ids):
            return
        requested_count = train._turnback_authorized_route_count + 1
        authority = self.interlocking_runtime.request_departure(
            train.train_id,
            phase.path_plan,
            phase.route_ids[:requested_count],
        )
        if authority.granted:
            train._turnback_authorized_route_count = requested_count
            train.departure_authorized = True
            train.active_route_ids = authority.route_ids
            train.interlocking_hold_reason = None
            train.turnback_state = "RUNNING"
        elif train._turnback_authorized_route_count == 0:
            train.departure_authorized = False
            train.active_route_ids = ()
            train.interlocking_hold_reason = authority.failure_reason
            train.turnback_state = "WAITING_ROUTE"
            train.dwell_remaining_sec = max(train.dwell_remaining_sec, self.clock.tick_seconds)

    def _complete_turnback_phase(self, train: SimTrainState, sim_time_ms: int) -> None:
        plan = train._turnback_plan
        index = train.turnback_phase_index
        if plan is None or index is None:
            train.interlocking_hold_reason = "TURNBACK_STATE_MISSING"
            return
        train.speed_mps = 0.0
        train.traction_percent = 0.0
        train.brake_percent = 20.0
        train.target_speed_mps = 0.0
        train.segment_progress = 1.0
        train.path_position_m = train._path_plan.total_length_m if train._path_plan else 0.0
        self._update_train_path_context(train)
        completed_phase = plan.phases[index]
        train._terminal_arrival_release_route_ids = completed_phase.route_ids

        next_index = index + 1
        if next_index < len(plan.phases):
            train.turnback_phase_index = next_index
            train._turnback_authorized_route_count = 0
            train.turnback_state = "CHANGING_ENDS"
            train.phase = DWELLING
            train.dwell_remaining_sec = max(5.0, self.clock.tick_seconds)
            train.door_state = "CLOSED"
            train.door_notice = "CLOSED"
            train.door_side = "NONE"
            train._door_stop_platform_id = None
            train.current_platform_id = None
            train.departure_authorized = False
            train._profile_triggered = False
            self._ato_for_train(train.train_id).reset()
            return

        self._finish_terminal_turnback(train, plan, sim_time_ms)

    def _finish_terminal_turnback(
        self,
        train: SimTrainState,
        plan: TurnbackPlan,
        sim_time_ms: int,
    ) -> None:
        train.turnback_count += 1
        train.direction = "DOWN" if train.direction == "UP" else "UP"
        train.turnback_state = "COMPLETED"
        train.turnback_phase_index = None
        train._turnback_plan = None
        train._turnback_authorized_route_count = 0
        train._planned_route_ids = ()
        train._path_origin_station_index = None
        train._path_destination_station_index = None
        train.current_platform_id = plan.final_platform_id
        self._anchor_train_at_platform(train, plan.final_platform_id)
        train.phase = DWELLING
        train.departure_authorized = False
        train.interlocking_hold_reason = None
        next_idx = train.station_index + (1 if train.direction == "UP" else -1)
        if 0 <= next_idx < len(self._station_list):
            next_station = self._station_list[next_idx]
            train.next_station_code = str(next_station.get("code", ""))
            train.next_station_name = str(next_station.get("name", ""))
        self._pending_dispatch_decisions.append(
            DispatchDecision(
                decision_id=f"TURNBACK-{train.train_id}-{train.turnback_count}",
                sim_time_ms=sim_time_ms,
                train_id=train.train_id,
                station_id=train.current_station_code,
                action="TURNBACK",
                duration_sec=0.0,
                reason=f"TERMINAL_TURNBACK_TO_{train.direction}",
                applied=True,
            )
        )
        self._begin_station_stop(train)

    def _ato_for_train(self, train_id: str) -> ATOController:
        controller = self._ato_by_train.get(train_id)
        if controller is None:
            controller = ATOController(
                self._ato_config,
                enable_synchronous_profile_optimization=False,
            )
            self._ato_by_train[train_id] = controller
            if len(self._ato_by_train) == 1:
                self.ato = controller
        # Every playback rate uses the exact DCDP profile.  Profile generation
        # remains asynchronous, and departure is held until the profile has
        # been installed, so fast-forward never substitutes the braking curve.
        controller.allow_profile_compute = True
        return controller

    def _ensure_interval_path(self, train: SimTrainState, next_idx: int) -> PathPlan | None:
        if (
            train._path_plan is not None
            and train._path_origin_station_index == train.station_index
            and train._path_destination_station_index == next_idx
        ):
            self._update_train_path_context(train)
            return train._path_plan

        origin_platform_id = train.current_platform_id
        if origin_platform_id is None:
            origin_platform_id = getattr(train, "_initial_platform_id", None)
        route_plan = self._route_chain_plan_for_station_pair(
            train.station_index,
            next_idx,
            origin_platform_id,
            train_length_m=train.train_length_m,
        )
        if route_plan is None:
            return None
        path_plan = route_plan.path_plan
        start_position_m = 0.0
        if (
            train.turnback_state == "COMPLETED"
            and train._path_plan is not None
            and train._path_plan.direction != path_plan.direction
        ):
            try:
                start_position_m = self._turnback_reversal_position(train, path_plan)
            except ValueError as exc:
                train.interlocking_hold_reason = str(exc)
                return None

        train._path_plan = path_plan
        train.current_platform_id = (
            int(path_plan.origin_platform_id) if start_position_m <= 1e-9 else None
        )
        train._planned_route_ids = route_plan.route_ids
        train._path_origin_station_index = train.station_index
        train._path_destination_station_index = next_idx
        self._activate_train_track_trace(train, path_plan)
        train.path_position_m = start_position_m
        train.path_total_length_m = path_plan.total_length_m
        train.target_distance_m = path_plan.total_length_m
        train.distance_to_next_m = max(0.0, path_plan.total_length_m - start_position_m)
        train.path_segment_count = len(path_plan.segment_ids)
        train.path_constraint_count = len(path_plan.constraints)
        train._profile_triggered = False
        self._dcdp_curve_data.pop(train.train_id, None)
        self._dcdp_curve_meta.pop(train.train_id, None)
        self._profile_run_times.pop(train.train_id, None)
        self._ato_for_train(train.train_id).reset()
        train.turnback_state = None
        self._update_train_path_context(train)
        return path_plan

    def _route_chain_plan_for_station_pair(
        self,
        origin_idx: int,
        destination_idx: int,
        origin_platform_id: int | None = None,
        train_length_m: float | None = None,
    ) -> RouteChainPlan | None:
        origin_platforms = self._station_platform_ids.get(origin_idx, ())
        destination_platforms = self._station_platform_ids.get(destination_idx, ())
        if origin_platform_id is not None:
            origin_platforms = (origin_platform_id,) if origin_platform_id in origin_platforms else ()
        if not origin_platforms or not destination_platforms:
            return None

        direction = "forward" if destination_idx > origin_idx else "backward"
        effective_train_length_m = (
            VehicleConfig().train_length_m
            if train_length_m is None
            else max(0.0, float(train_length_m))
        )
        try:
            return self.route_chain_planner.plan_between_platform_sets(
                origin_platforms,
                destination_platforms,
                direction,
                train_length_m=effective_train_length_m,
            )
        except ValueError:
            return None

    def _path_plan_for_station_pair(
        self,
        origin_idx: int,
        destination_idx: int,
        origin_platform_id: int | None = None,
        train_length_m: float | None = None,
    ) -> PathPlan | None:
        route_plan = self._route_chain_plan_for_station_pair(
            origin_idx,
            destination_idx,
            origin_platform_id,
            train_length_m=train_length_m,
        )
        if route_plan is not None:
            return route_plan.path_plan
        return None

        # Legacy shortest-path fallback intentionally remains unreachable:
        # ordinary passenger operation must use one route-table-derived plan.
        preferred_pairs = [(origin_platforms[0], destination_platforms[0])]
        all_pairs = [
            (origin_platform_id, destination_platform_id)
            for origin_platform_id in origin_platforms
            for destination_platform_id in destination_platforms
        ]
        plans: list[PathPlan] = []
        seen_pairs: set[tuple[int, int]] = set()
        for origin_platform_id, destination_platform_id in preferred_pairs + all_pairs:
            pair = (origin_platform_id, destination_platform_id)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            try:
                plans.append(
                    self.path_planner.plan_between_platforms(
                        origin_platform_id,
                        destination_platform_id,
                        direction=direction,
                    )
                )
            except ValueError:
                continue
        if plans:
            return min(plans, key=lambda item: item.total_length_m)
        if self.line_scope is not None:
            raise ValueError(
                f"no path inside line scope {self.line_scope.scope_id} "
                f"for station indexes {origin_idx}->{destination_idx}"
            )
        return None

    def _prime_path_profile(self, train: SimTrainState, path_plan: PathPlan) -> bool:
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
        if self._ato_config.use_dynamic_programming_profile:
            if train.duty_id is not None and self.scenario.operation_plan.enabled:
                # A frozen reference load makes the planned DCDP curve and the
                # executed curve identical across all duties. Passenger mass
                # still affects the force/power model at every physical tick.
                load_bucket_pax = self.scenario.operation_plan.profile_reference_load_pax
            else:
                load_bucket_pax = int((max(0, train.onboard_pax) + 25) // 50 * 50)
            vehicle_config = self._make_vehicle_config(train.train_id, load_bucket_pax)
            request = build_speed_profile_request(
                path_plan,
                train.permitted_speed_mps,
                self._ato_config,
                vehicle_config,
            )
            profile = self.speed_profile_service.request(request)
            if profile is None:
                train.target_speed_mps = 0.0
                self._store_pending_path_profile(train, path_plan, request.cache_key)
                return False
            ato.install_profile(state, target, profile)
        train.target_speed_mps = ato.target_speed_mps(state, target)
        self._store_path_profile(train, path_plan, ato)
        return True

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

    def _store_pending_path_profile(
        self,
        train: SimTrainState,
        path_plan: PathPlan,
        cache_key: str,
    ) -> None:
        """Publish pending state without exposing a non-DCDP substitute curve."""
        self._dcdp_curve_data[train.train_id] = []
        self._dcdp_curve_meta[train.train_id] = {
            "source": "DCDP_PENDING",
            "status": "DCDP_PENDING",
            "cacheKey": cache_key,
            "targetPositionM": path_plan.total_length_m,
            "pointCount": 0,
        }

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
        if constraint is None or abs(constraint.path_end_m - constraint.path_start_m) < 1e-9:
            train.current_segment_offset_m = 0.0
        else:
            ratio = (bounded_position_m - constraint.path_start_m) / (
                constraint.path_end_m - constraint.path_start_m
            )
            train.current_segment_offset_m = constraint.start_offset_m + (
                constraint.end_offset_m - constraint.start_offset_m
            ) * ratio
        train.local_speed_limit_mps = path_plan.speed_limit_at(bounded_position_m, train.permitted_speed_mps)
        train.grade_ratio = path_plan.grade_ratio_at(bounded_position_m)
        if train.current_segment_id is not None:
            self._sync_train_track_trace_head(train, int(train.current_segment_id))

    def _activate_train_track_trace(
        self,
        train: SimTrainState,
        path_plan: PathPlan,
    ) -> None:
        """Attach a new approved path without dropping Segs still under the tail."""
        new_segments = tuple(int(item) for item in path_plan.segment_ids)
        if not new_segments:
            train._track_trace = None
            train._trace_path_start_index = None
            return

        trace_direction = "FORWARD" if path_plan.direction == "forward" else "BACKWARD"
        previous = train._track_trace
        if previous is None or previous.trace_direction != trace_direction:
            merged = new_segments
            path_start_index = 0
        else:
            overlap = 0
            max_overlap = min(len(previous.segment_ids), len(new_segments))
            for size in range(max_overlap, 0, -1):
                if previous.segment_ids[-size:] == new_segments[:size]:
                    overlap = size
                    break
            path_start_index = len(previous.segment_ids) - overlap
            merged = previous.segment_ids + new_segments[overlap:]

        train._trace_path_start_index = path_start_index
        train._track_trace = TrainTrackTrace(
            self.track_query,
            merged,
            head_index=path_start_index,
            trace_direction=trace_direction,
        )

    def _sync_train_track_trace_head(
        self,
        train: SimTrainState,
        segment_id: int,
    ) -> None:
        """Move the trace cursor to this occurrence in the active PathPlan."""
        trace = train._track_trace
        path_plan = train._path_plan
        path_start_index = train._trace_path_start_index
        if trace is None or path_plan is None or path_start_index is None:
            return
        matching_path_indexes = [
            index
            for index, candidate in enumerate(path_plan.segment_ids)
            if int(candidate) == int(segment_id)
        ]
        if not matching_path_indexes:
            return
        # Normal route-table paths do not repeat a Seg. If a future loop does,
        # select the occurrence nearest the current cursor without guessing a
        # different topology branch.
        candidates = [path_start_index + index for index in matching_path_indexes]
        head_index = min(candidates, key=lambda item: abs(item - trace.head_index))
        if 0 <= head_index < len(trace.segment_ids):
            train._track_trace = trace.with_active_head(head_index)


    def _next_signal_ahead(self, train: SimTrainState, path_plan: PathPlan, path_position_m: float) -> JsonDict | None:
        """查询列车前方最近信号机."""
        constraint = path_plan.constraint_at(path_position_m)
        if constraint is None:
            return None
        seg_span = constraint.end_offset_m - constraint.start_offset_m
        path_span = constraint.path_end_m - constraint.path_start_m
        if abs(path_span) < 1e-9:
            return None
        ratio = (path_position_m - constraint.path_start_m) / path_span
        seg_offset = constraint.start_offset_m + ratio * seg_span
        # 根据约束方向确定信号搜索方向：
        #   forward  →  segment偏移递增方向 = path前进方向
        #   reverse  →  segment偏移递减方向 = path前进方向
        sig_dir = constraint.direction  # "forward" | "reverse"
        # TrackQuery uses an inclusive comparison. Query immediately ahead so
        # the signal at the train's exact position is not mistaken for the
        # next signal after departure authority has already been granted.
        query_offset = seg_offset + (0.01 if sig_dir == "forward" else -0.01)
        return self.track_query.get_next_signal(constraint.segment_id, query_offset, sig_dir)

    def _lookup_profile_speed(self, train_id: str, position_m: float) -> tuple[float, str] | None:
        """浠庤鍒掓洸绾夸腑绾挎€ф彃鍊煎綋鍓嶄綅缃殑鐩爣閫熷害鍜岃繍琛屾ā寮?"""
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
        """杩斿洖鎸囧畾鍒楄溅鐨?DCDP 瑙勫垝閫熷害鏇茬嚎鏁版嵁."""
        return self._dcdp_curve_data.get(train_id, [])

    def export_speed_profile_meta(self, train_id: str) -> dict[str, Any]:
        """杩斿洖鎸囧畾鍒楄溅閫熷害鏇茬嚎鏉ユ簮涓庣粓绔川閲?"""
        return self._dcdp_curve_meta.get(train_id, {})

    # 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?    #  鍩熸湇鍔¤皟鐢?    # 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
    def _process_station_stop(self, train: SimTrainState, sim_time_ms: int) -> None:
        """澶勭悊鍒楄溅鍋滅珯涓婁笅瀹?"""
        try:
            train.door_state = "OPEN"
            train.door_side = self._door_side_for(train.direction)
            train.door_notice = "OPEN"
            ratio = self.station_service.flow_generator.alighting_ratio(
                train.current_station_code, train.direction, sim_time_ms,
            )
            train._planned_alighting_total = min(
                train.onboard_pax,
                max(0, int(round(train.onboard_pax * ratio))),
            )
            train._boarding_credit_pax = 0.0
            train._passenger_stop_started_ms = sim_time_ms
            train.last_boarding = 0
            train.last_alighting = 0
            train.current_boarding_rate_pax_per_sec = 0.0
            train.current_alighting_rate_pax_per_sec = 0.0
            train.last_passenger_event_ms = None
            train.dwell_remaining_sec = 30.0
        except Exception:
            # 客流服务失败时使用默认停站时间
            train.dwell_remaining_sec = 30.0

    def _advance_open_door_passengers(
        self,
        train: SimTrainState,
        sim_time_ms: int,
        dt_sec: float,
    ) -> None:
        """Continuously exchange passengers, prioritising demand and remaining capacity."""
        dwell_total_sec = 30.0
        elapsed_sec = max(0.0, dwell_total_sec - train.dwell_remaining_sec)
        next_elapsed_sec = min(dwell_total_sec, elapsed_sec + dt_sec)
        next_progress = next_elapsed_sec / dwell_total_sec
        # Smooth cosine schedule: the fixed station alighting ratio determines
        # the total, while the actual alighting flow ramps up then down.
        target_alighting = round(
            train._planned_alighting_total * (1.0 - math.cos(math.pi * next_progress)) / 2.0
        )
        requested_alighting = max(0, target_alighting - train.last_alighting)

        platform = self.station_service.ensure_platform(train.current_station_code, train.direction)
        available_capacity = max(train.capacity_pax - (train.onboard_pax - requested_alighting), 0)
        boardable = min(platform.waiting_pax, available_capacity)
        remaining_ticks = max(1, round(train.dwell_remaining_sec / dt_sec))
        flow_shape = 0.3 + 3.0 * math.sin(math.pi * next_progress)
        train._boarding_credit_pax += boardable * flow_shape / remaining_ticks
        requested_boarding = min(boardable, max(0, int(train._boarding_credit_pax)))
        if requested_alighting <= 0 and requested_boarding <= 0:
            train.current_boarding_rate_pax_per_sec = 0.0
            train.current_alighting_rate_pax_per_sec = 0.0
            return
        result = self.station_service.exchange_open_door_passengers(
            station_id=train.current_station_code,
            direction=train.direction,
            train_load=TrainLoadState(train.train_id, train.onboard_pax, train.capacity_pax),
            requested_alighting=requested_alighting,
            requested_boarding=requested_boarding,
        )
        train._boarding_credit_pax -= result.boarding
        train.onboard_pax = result.updated_load.onboard_pax
        train.load_factor = result.updated_load.load_factor
        train.mass_kg = self._make_vehicle_config(train.train_id, train.onboard_pax).mass_kg
        train.last_boarding += result.boarding
        train.last_alighting += result.alighting
        train.current_boarding_rate_pax_per_sec = result.boarding / dt_sec
        train.current_alighting_rate_pax_per_sec = result.alighting / dt_sec
        if result.boarding or result.alighting:
            train.last_passenger_event_ms = sim_time_ms

    def _record_completed_station_stop(self, train: SimTrainState, sim_time_ms: int) -> None:
        if self.recorder is None or self._run_id is None or train._passenger_stop_started_ms is None:
            return
        platform = self.station_service.ensure_platform(train.current_station_code, train.direction)
        self.recorder.record_station_passenger(
            self._run_id,
            sim_time_ms=sim_time_ms,
            station_id=train.current_station_code,
            direction=train.direction,
            arrivals=platform._total_arrived_pax,  # 累计进站人数，而非末 tick 的增量
            boarding=train.last_boarding,
            alighting=train.last_alighting,
            waiting=platform.waiting_pax,
            left_behind=platform.left_behind_pax,
            platform_density_pax_per_m2=platform.platform_density_pax_per_m2,
            crowding_level=platform.crowding_level,
        )
        self.recorder.record_train_load(
            self._run_id,
            sim_time_ms=sim_time_ms,
            train_id=train.train_id,
            onboard_pax=train.onboard_pax,
            capacity_pax=train.capacity_pax,
            load_factor=train.load_factor,
            vehicle_load_kg=(
                train.onboard_pax
                * getattr(self, "_average_passenger_mass_kg", 65.0)
            ),
            detail={"stationId": train.current_station_code},
        )
        self.recorder.record_dwell(
            self._run_id,
            train_id=train.train_id,
            station_id=train.current_station_code,
            arrival_ms=train._passenger_stop_started_ms,
            depart_ms=sim_time_ms,
            planned_dwell_sec=30.0,
            estimated_dwell_sec=30.0,
            actual_dwell_sec=30.0,
            reason="PASSENGER_BOARDING",
        )
        train._passenger_stop_started_ms = None

    @staticmethod
    def _door_side_for(direction: str) -> str:
        """Scenario convention pending authoritative Line 9 platform-side data."""
        return "LEFT" if direction == "UP" else "RIGHT"

    def _platform_for_direction(self, station_index: int, direction: str) -> JsonDict | None:
        expected_code = "0x55" if direction == "UP" else "0xaa"
        platform_ids = self._station_platform_ids.get(station_index, ())
        fallback: JsonDict | None = None
        for platform_id in platform_ids:
            platform = self._platform_by_id.get(int(platform_id))
            if platform is None or platform.get("segmentId") is None:
                continue
            if fallback is None:
                fallback = platform
            if str(platform.get("direction", "")).lower() == expected_code:
                return platform
        return fallback

    def _platform_head_stop_offset_m(
        self,
        platform: JsonDict,
        direction: str,
        train_length_m: float,
    ) -> float:
        segment_id = int(platform["segmentId"])
        segment = self.track_query.segments.get(segment_id, {})
        segment_length_m = float(segment.get("lengthM", 0.0))
        if segment_length_m <= 0.0:
            return float(platform.get("offsetM", 0.0))
        bounded_length_m = min(max(float(train_length_m), 0.0), segment_length_m)
        end_clearance_m = (segment_length_m - bounded_length_m) / 2.0
        return end_clearance_m + bounded_length_m if direction == "UP" else end_clearance_m

    def _anchor_train_at_current_platform(self, train: SimTrainState) -> None:
        # Station dwell, turnback, and topology placement all need the same
        # logical head Seg; otherwise MA and occupation are computed from a
        # stale inter-station path segment after arrival.
        platform_id = self._platform_id_by_segment.get(train.current_segment_id)
        platform = (
            self._platform_by_id.get(platform_id)
            if platform_id in self._station_platform_ids.get(train.station_index, ())
            else None
        )
        if platform is None:
            platform = self._platform_for_direction(train.station_index, train.direction)
        if platform is None:
            return
        train.current_platform_id = int(platform["id"])
        train.current_segment_id = int(platform["segmentId"])
        train.current_segment_offset_m = self._platform_head_stop_offset_m(
            platform, train.direction, train.train_length_m
        )
        self._sync_train_track_trace_head(train, train.current_segment_id)

    def _anchor_train_at_platform(
        self,
        train: SimTrainState,
        platform_id: int | None,
    ) -> bool:
        """Place a stopped train at one explicitly selected PathPlan platform."""
        if platform_id is None:
            return False
        platform = self._platform_by_id.get(int(platform_id))
        if platform is None or platform.get("segmentId") is None:
            return False
        train.current_platform_id = int(platform_id)
        train.current_segment_id = int(platform["segmentId"])
        train.current_segment_offset_m = self._platform_head_stop_offset_m(
            platform, train.direction, train.train_length_m
        )
        self._sync_train_track_trace_head(train, train.current_segment_id)
        return True

    def _begin_station_stop(self, train: SimTrainState) -> None:
        train.phase = DWELLING
        train.door_system.control_mode = train.operation_mode
        train._door_stop_platform_id = train.current_platform_id
        train.door_system.set_permission(self._door_side_for(train.direction))
        train.door_notice = "PREPARE_OPEN" if train.operation_mode == "ATO" else "WAITING_MANUAL_OPEN"
        self._sync_legacy_door_state(train)
        train.dwell_remaining_sec = 0.0
        train._passenger_service_pending = True

    def _reset_station_history(self) -> None:
        start_ms = self._absolute_sim_time_ms()
        self._station_history = {
            key: deque(
                [{
                    "simTimeMs": start_ms,
                    "waitingPax": platform.waiting_pax,
                    "arrivals": 0,
                    "leftBehindPax": platform.left_behind_pax,
                    "platformDensity": round(platform.platform_density_pax_per_m2, 3),
                }],
                maxlen=6 * 3600 + 1,
            )
            for key, platform in self.station_service.platforms.items()
        }
        self._station_history_arrivals = {key: 0 for key in self.station_service.platforms}
        self._station_history_second = start_ms // 1000

    def _record_station_history(self, sim_time_ms: int) -> None:
        for key, arrivals in self._last_arrivals_by_platform.items():
            self._station_history_arrivals[key] = self._station_history_arrivals.get(key, 0) + arrivals
        current_second = sim_time_ms // 1000
        if self._station_history_second == current_second:
            return
        for key, platform in self.station_service.platforms.items():
            self._station_history.setdefault(key, deque(maxlen=6 * 3600 + 1)).append({
                "simTimeMs": current_second * 1000,
                "waitingPax": platform.waiting_pax,
                "arrivals": self._station_history_arrivals.get(key, 0),
                "leftBehindPax": platform.left_behind_pax,
                "platformDensity": round(platform.platform_density_pax_per_m2, 3),
            })
            self._station_history_arrivals[key] = 0
        self._station_history_second = current_second

    def station_passenger_history(self, station_code: str, since_sim_time_ms: int | None = None) -> JsonDict:
        code = str(station_code)
        since = int(since_sim_time_ms) if since_sim_time_ms is not None else None
        return {
            "stationCode": code,
            "source": "simulation-engine",
            "history": {
                direction: [
                    point for point in self._station_history.get((code, direction), ())
                    if since is None or int(point["simTimeMs"]) > since
                ]
                for direction in ("UP", "DOWN")
            },
        }

    def _update_power(
        self,
        sim_time_ms: int,
        prepared_steps: dict[str, PreparedTrainStep] | None = None,
    ) -> dict[str, Any]:
        """鏇存柊渚涚數鐘舵€?"""
        if not self.power_service.sections:
            return {}
        requests: list[TrainPowerRequest] = []
        prepared_steps = prepared_steps or {}
        for train in self.trains:
            # Operation-plan trainsets remain outside the simulated main-line
            # traction network until a departure has actually been requested.
            # Charging every depot train at the origin node makes a dense
            # timetable look like twenty co-located 80 kW line loads and can
            # stop the solver before the first service leaves the depot.
            if train.lifecycle_state in {"IN_DEPOT", "READY", "RETURN_REQUESTED", "STORED"}:
                continue
            prepared = prepared_steps.get(train.train_id)
            if prepared is not None:
                traction_force_n = prepared.demand.traction_force_n
                brake_force_n = prepared.demand.candidate_electric_brake_force_n
                train.mass_kg = prepared.vehicle_config.mass_kg
                power_demand = prepared.power_demand
            else:
                vehicle = self._make_vehicle_config(train.train_id, train.onboard_pax)
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
                power_demand = TractionDriveModel(vehicle).electrical_power_demand(
                    demand,
                    train.speed_mps,
                    auxiliary_power_kw=150.0 if train.phase not in {IDLE, DWELLING} else 80.0,
                )
            head_mileage_m, tail_mileage_m, pantograph_mileages_m, spanned_sections = self._train_power_geometry(
                train,
                prepared.vehicle_config if prepared is not None else vehicle,
            )
            requests.append(
                TrainPowerRequest(
                    train_id=train.train_id,
                    power_section_id=self._power_section_for_train(train),
                    speed_mps=train.speed_mps,
                    traction_force_n=traction_force_n,
                    brake_force_n=brake_force_n,
                    position_m=sum(pantograph_mileages_m) / len(pantograph_mileages_m),
                    direction=train.direction,
                    aux_power_kw=150.0 if train.phase not in {IDLE, DWELLING} else 80.0,
                    traction_power_request_kw=power_demand.traction_power_request_kw,
                    regen_power_available_kw=power_demand.regen_power_available_kw,
                    head_mileage_m=head_mileage_m,
                    tail_mileage_m=tail_mileage_m,
                    pantograph_mileages_m=pantograph_mileages_m,
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
        """鐢熸垚璋冨害鍐崇瓥."""
        decisions: list[DispatchDecision] = []
        for train in self.trains:
            if train.phase != DWELLING:
                continue
            platform = self.station_service.ensure_platform(train.current_station_code, train.direction)
            ps = power_states.get(self._power_section_for_train(train))
            limit_ratio = ps.traction_limit_ratio if ps and hasattr(ps, "traction_limit_ratio") else 1.0
            front_headway_sec, rear_headway_sec = self.dispatch_runtime.headways_for(
                train.train_id,
                train.station_index,
                train.direction,
                self.clock.sim_time_seconds,
            )
            context = DispatchContext(
                sim_time_ms=sim_time_ms,
                train_id=train.train_id,
                station_id=train.current_station_code,
                station_index=train.station_index,
                front_headway_sec=front_headway_sec,
                rear_headway_sec=rear_headway_sec,
                platform_crowding_level=platform.crowding_level,
                load_factor=train.load_factor,
                left_behind_pax=platform.left_behind_pax,
                power_traction_limit_ratio=limit_ratio,
                route_available=self.interlocking_runtime.route_available(
                    train.train_id,
                    train._path_plan,
                ) if train._path_plan is not None else False,
                onboard_pax=train.onboard_pax,
                capacity_pax=train.capacity_pax,
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

    def _continuous_locked_route_prefix(
        self,
        train: SimTrainState,
        path_plan: PathPlan,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Return remaining planned routes and their owner-continuous prefix."""
        remaining = self.movement_authority.remaining_route_ids(
            path_plan,
            train._planned_route_ids,
            train.path_position_m,
        )
        locked: list[str] = []
        for route_id in remaining:
            if self.route_service.locked_by(route_id) != train.train_id:
                break
            locked.append(route_id)
        return remaining, tuple(locked)

    def _extend_interval_authority(
        self,
        train: SimTrainState,
        path_plan: PathPlan,
        sim_time_ms: int,
    ) -> None:
        """Pre-request one additional route without invalidating usable MA."""
        if not train._planned_route_ids:
            return
        if not train.departure_authorized and not train.active_route_ids:
            return
        remaining, locked_prefix = self._continuous_locked_route_prefix(train, path_plan)
        train.active_route_ids = locked_prefix
        if not remaining or len(locked_prefix) >= len(remaining):
            train.route_retry_at_ms = None
            return
        if train.route_retry_at_ms is not None and sim_time_ms < train.route_retry_at_ms:
            return

        requested_routes = remaining[: len(locked_prefix) + 1]
        authority = self.interlocking_runtime.request_departure(
            train.train_id,
            path_plan,
            requested_routes,
        )
        train.route_retry_at_ms = sim_time_ms + self.ROUTE_REQUEST_RETRY_MS
        if authority.granted:
            train.active_route_ids = authority.route_ids
            train.departure_authorized = True
            train.interlocking_hold_reason = None
            return

        # Preserve the routes that remain locked. MA and ATP will stop at the
        # end of that continuous prefix while this method retries the next one.
        _, retained_prefix = self._continuous_locked_route_prefix(train, path_plan)
        train.active_route_ids = retained_prefix
        train.departure_authorized = bool(retained_prefix)
        reason = authority.failure_reason or "ROUTE_NOT_LOCKABLE"
        train.interlocking_hold_reason = (
            f"NEXT_ROUTE_PENDING:{reason}" if retained_prefix else reason
        )

    def _authorize_ready_departures(self, sim_time_ms: int) -> None:
        """Apply CI authority at the dwell/departure boundary without owning dwell logic."""
        station_count = len(self._station_list)
        for train in self.trains:
            if train.lifecycle_state in {"IN_DEPOT", "READY", "RETURN_REQUESTED", "STORED"}:
                continue
            if train.phase != DWELLING or train.dwell_remaining_sec > self.clock.tick_seconds:
                continue
            if train.departure_authorized and train.active_route_ids:
                continue
            front_headway_sec, _ = self.dispatch_runtime.headways_for(
                train.train_id,
                train.station_index,
                train.direction,
                self.clock.sim_time_seconds,
            )
            if (
                front_headway_sec is not None
                and front_headway_sec < self.dispatch_service.config.min_headway_sec
            ):
                # ATS owns the operational headway.  CI may already consider
                # the first route safe after the leading train clears it, but
                # that must not silently weaken the configured service gap.
                train.departure_authorized = False
                train.dwell_remaining_sec = max(
                    train.dwell_remaining_sec,
                    self.clock.tick_seconds,
                )
                continue
            if (
                train.planned_departure_ms is not None
                and self._absolute_sim_time_ms() < train.planned_departure_ms
            ):
                train.departure_authorized = False
                train.interlocking_hold_reason = "TIMETABLE_NOT_DUE"
                train.active_route_ids = ()
                continue
            next_idx = train.station_index + 1 if train.direction == "UP" else train.station_index - 1
            if next_idx < 0 or next_idx >= station_count:
                continue
            path_plan = self._ensure_interval_path(train, next_idx)
            if path_plan is None:
                train.departure_authorized = False
                train.interlocking_hold_reason = "NO_MAINLINE_PATH"
                train.active_route_ids = ()
                continue
            route_chain_ids = train._planned_route_ids
            if not route_chain_ids:
                train.departure_authorized = False
                train.interlocking_hold_reason = "NO_ROUTE_CHAIN"
                train.active_route_ids = ()
                continue
            self._update_train_path_context(train)
            if train.route_retry_at_ms is not None and sim_time_ms < train.route_retry_at_ms:
                train.dwell_remaining_sec = max(
                    train.dwell_remaining_sec,
                    self.clock.tick_seconds,
                )
                continue
            authority = self.interlocking_runtime.request_departure(
                train.train_id, path_plan, route_chain_ids[:1],
            )
            train.route_retry_at_ms = sim_time_ms + self.ROUTE_REQUEST_RETRY_MS
            train.departure_authorized = authority.granted
            train.interlocking_hold_reason = authority.failure_reason
            train.active_route_ids = authority.route_ids if authority.granted else ()
            if not authority.granted:
                train.dwell_remaining_sec = max(train.dwell_remaining_sec, self.clock.tick_seconds)

    def _interlocking_train_states(self, sim_time_ms: int) -> list[TrainState]:
        states: list[TrainState] = []
        occupied_platform_queues: set[tuple[int, str]] = set()
        for train in self.trains:
            if train.lifecycle_state in {"IN_DEPOT", "READY", "RETURN_REQUESTED", "STORED"}:
                continue
            if train.phase in {DWELLING, IDLE}:
                queue_key = (train.station_index, train.direction)
                if queue_key in occupied_platform_queues:
                    # Additional trains at the same origin are treated as a
                    # depot/dispatch queue, not overlapping physical consists.
                    continue
                occupied_platform_queues.add(queue_key)
            path_plan = train._path_plan
            if path_plan is None or train.current_segment_id is None:
                continue
            if train.phase == DWELLING and train.current_platform_id is not None:
                segment_id = int(train.current_segment_id)
                offset_m = float(train.current_segment_offset_m)
            else:
                constraint = path_plan.constraint_at(train.path_position_m)
                if constraint is None:
                    continue
                span_m = max(constraint.path_end_m - constraint.path_start_m, 1e-9)
                ratio = min(
                    1.0,
                    max(0.0, (train.path_position_m - constraint.path_start_m) / span_m),
                )
                segment_id = int(constraint.segment_id)
                offset_m = constraint.start_offset_m + ratio * (
                    constraint.end_offset_m - constraint.start_offset_m
                )
            states.append(
                TrainState(
                    train_id=train.train_id,
                    sim_time_ms=sim_time_ms,
                    sim_time_s=self.clock.sim_time_seconds,
                    seg_id=segment_id,
                    segment_id=segment_id,
                    offset_m=max(0.0, offset_m),
                    position_m=max(0.0, train.head_mileage_m),
                    speed_mps=max(0.0, train.speed_mps),
                    direction=self._physical_train_direction(train),
                    operation_mode=train.operation_mode,
                    run_phase=train.phase,
                    length_m=train.train_length_m,
                    path_track=train._track_trace,
                    net_energy_kwh=train.energy_kwh,
                )
            )
        return states

    @staticmethod
    def _physical_train_direction(train: SimTrainState) -> str:
        """Return the direction of the active movement leg, not service order."""
        if train._path_plan is not None:
            return "FORWARD" if train._path_plan.direction == "forward" else "BACKWARD"
        return "FORWARD" if train.direction == "UP" else "BACKWARD"

    # 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?    #  蹇収鏋勫缓
    # 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
    def _operation_interval_profile_inputs(
        self,
        ordered_stations: list[JsonDict],
        initial_platform_id: int | None = None,
    ) -> tuple[list[float], list[SpeedProfileRequest], int]:
        """Derive timetable runtimes from the same PathPlan/DCDP inputs as ATO."""
        config = self.scenario.operation_plan
        reference_vehicle = self._make_vehicle_config(
            "OPERATION-PROFILE",
            config.profile_reference_load_pax,
        )
        run_times: list[float] = []
        requests: list[SpeedProfileRequest] = []
        current_platform_id = initial_platform_id
        for origin, destination in zip(ordered_stations, ordered_stations[1:]):
            origin_idx = int(origin["stationIndex"])
            destination_idx = int(destination["stationIndex"])
            service_direction = "UP" if destination_idx > origin_idx else "DOWN"
            if current_platform_id is None:
                origin_platform = self._platform_for_direction(origin_idx, service_direction)
                current_platform_id = (
                    int(origin_platform["id"]) if origin_platform is not None else None
                )
            path_plan = self._path_plan_for_station_pair(
                origin_idx,
                destination_idx,
                current_platform_id,
                train_length_m=reference_vehicle.train_length_m,
            )
            if path_plan is None:
                raise ValueError(
                    "OPERATION_PLAN_ROUTE_MISSING:"
                    f"{origin.get('code', origin_idx)}->{destination.get('code', destination_idx)}"
                )
            speed_source_idx = min(origin_idx, destination_idx)
            limit_kmh = int(
                self._station_list[speed_source_idx].get("speedLimitToNextKmh", 80)
            )
            if limit_kmh <= 0:
                limit_kmh = 80
            request = build_speed_profile_request(
                path_plan,
                limit_kmh / 3.6,
                self._ato_config,
                reference_vehicle,
            )
            run_times.append(
                request.scheduled_run_time_s + config.runtime_recovery_margin_sec
            )
            requests.append(request)
            current_platform_id = int(path_plan.destination_platform_id)
        if current_platform_id is None:
            raise ValueError("OPERATION_PLAN_HAS_NO_FINAL_PLATFORM")
        return run_times, requests, current_platform_id

    def _operation_turnback_profile_inputs(
        self,
        terminal_station_code: str,
        inbound_platform_id: int,
    ) -> tuple[TurnbackPlan, list[SpeedProfileRequest], float]:
        """Build the physical terminal reversal inputs included by the duty plan."""
        config = self.scenario.operation_plan
        reference_vehicle = self._make_vehicle_config(
            "OPERATION-PROFILE",
            config.profile_reference_load_pax,
        )
        plan = self.route_chain_planner.plan_turnback(
            terminal_station_code,
            inbound_platform_id,
        )
        requests = [
            build_speed_profile_request(
                phase.path_plan,
                self.CRUISE_SPEED_MPS,
                self._ato_config,
                reference_vehicle,
            )
            for phase in plan.phases
        ]
        change_ends_sec = max(0, len(plan.phases) - 1) * 5.0
        terminal_dwell_sec = (
            self.station_service.dwell_config.base_dwell_sec
            + config.door_cycle_allowance_sec
        )
        required_layover_sec = (
            terminal_dwell_sec
            + sum(request.scheduled_run_time_s for request in requests)
            + change_ends_sec
        )
        return plan, requests, required_layover_sec

    def _prewarm_operation_profiles(self) -> None:
        config = self.scenario.operation_plan
        requests = list(self._operation_profile_requests.values())
        cache_keys = self.speed_profile_service.prime(requests)
        startup = self.speed_profile_service.wait_for(
            self._operation_startup_profile_keys,
            config.profile_prewarm_timeout_sec if config.profile_prewarm_enabled else 0.0,
        )
        overall = self.speed_profile_service.wait_for(cache_keys, 0.0)
        self._operation_profile_warmup = {
            "enabled": config.profile_prewarm_enabled,
            "timeoutSec": config.profile_prewarm_timeout_sec,
            "referenceLoadPax": config.profile_reference_load_pax,
            "startupProfileCount": startup["requestedProfileCount"],
            "startupReadyProfileCount": startup["readyProfileCount"],
            "startupPendingProfileCount": startup["pendingProfileCount"],
            "startupFailedProfileCount": startup["failedProfileCount"],
            **overall,
            "ready": startup["ready"],
            "allProfilesReady": overall["ready"],
        }
        if config.profile_prewarm_enabled and not startup["ready"]:
            failure = "FAILED" if startup["failedProfileCount"] else "TIMEOUT"
            raise RuntimeError(f"DCDP_PROFILE_PREWARM_{failure}: {startup}")

    def _finalize_operation_plan_metadata(self) -> None:
        config = self.scenario.operation_plan
        if not self._operation_duties:
            self._operation_plan_hash = None
            self._operation_window = {}
            return
        first_departure_ms = round(
            min(duty.planned_start_s for duty in self._operation_duties.values()) * 1000
        )
        last_arrival_ms = round(
            max(duty.planned_end_s for duty in self._operation_duties.values()) * 1000
        )
        measurement_start_ms = config.measurement_start_time_ms or first_departure_ms
        measurement_end_ms = config.measurement_end_time_ms or last_arrival_ms
        clearance_end_ms = config.clearance_end_time_ms or (
            measurement_end_ms + round(config.clearance_sec * 1000)
        )
        if measurement_end_ms <= measurement_start_ms:
            raise ValueError("INVALID_OPERATION_MEASUREMENT_WINDOW")
        if clearance_end_ms < measurement_end_ms:
            raise ValueError("INVALID_OPERATION_CLEARANCE_WINDOW")
        self._operation_window = {
            "warmupStartTimeMs": self.scenario.start_time_ms,
            "measurementStartTimeMs": measurement_start_ms,
            "measurementEndTimeMs": measurement_end_ms,
            "clearanceEndTimeMs": clearance_end_ms,
        }
        passenger_profile = getattr(self, "_passenger_profile", None)
        plan_payload = {
            "lineId": self.scenario.line_id,
            "profileCacheVersion": PROFILE_CACHE_VERSION,
            "profileReferenceLoadPax": config.profile_reference_load_pax,
            "passengerProfileId": getattr(
                passenger_profile, "profile_id", "BUILTIN_SYNTHETIC"
            ),
            "timetables": [item.to_dict() for item in self._operation_timetables],
            "services": [item.to_dict() for item in self._operation_services.values()],
            "duties": [
                {
                    "dutyId": duty.duty_id,
                    "trainId": duty.train_id,
                    "serviceIds": duty.service_ids,
                    "plannedStartS": duty.planned_start_s,
                    "plannedEndS": duty.planned_end_s,
                }
                for duty in self._operation_duties.values()
            ],
            "window": self._operation_window,
        }
        encoded = json.dumps(
            plan_payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        self._operation_plan_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _initialize_operation_plan(self) -> None:
        self._operation_timetables = []
        self._operation_services = {}
        self._operation_duties = {}
        self._operation_events = []
        self._pending_operation_events = []
        self._operation_profile_requests = {}
        self._operation_startup_profile_keys = ()
        self._operation_profile_warmup = {
            "enabled": False,
            "ready": True,
            "requestedProfileCount": 0,
            "readyProfileCount": 0,
            "pendingProfileCount": 0,
            "failedProfileCount": 0,
        }
        self._operation_plan_hash = None
        self._operation_window = {}
        self.timetable_service.reset()
        config = self.scenario.operation_plan
        if not config.enabled:
            return
        if config.direction not in {"UP", "DOWN"}:
            raise ValueError("INVALID_OPERATION_PLAN_DIRECTION")

        indexed_stations = [
            {
                **station,
                "stationIndex": index,
                "dwellSeconds": (
                    float(station.get("dwellSeconds", self.timetable_service.base_dwell_sec))
                    + config.door_cycle_allowance_sec
                ),
            }
            for index, station in enumerate(self._station_list)
        ]
        outbound_stations = (
            indexed_stations if config.direction == "UP" else list(reversed(indexed_stations))
        )
        return_stations = list(reversed(outbound_stations))
        (
            outbound_run_times,
            outbound_requests,
            inbound_terminal_platform_id,
        ) = self._operation_interval_profile_inputs(
            outbound_stations
        )
        (
            turnback_plan,
            turnback_requests,
            required_turnback_layover_sec,
        ) = self._operation_turnback_profile_inputs(
            str(outbound_stations[-1].get("code", "")),
            inbound_terminal_platform_id,
        )
        (
            return_run_times,
            return_requests,
            _return_terminal_platform_id,
        ) = self._operation_interval_profile_inputs(
            return_stations,
            turnback_plan.final_platform_id,
        )
        effective_turnback_layover_sec = max(
            config.turnback_layover_sec,
            required_turnback_layover_sec,
        )
        prioritized_requests = [
            *outbound_requests[:1],
            *return_requests[:1],
            *turnback_requests,
            *outbound_requests[1:],
            *return_requests[1:],
        ]
        self._operation_profile_requests = {
            request.cache_key: request for request in prioritized_requests
        }
        self._operation_startup_profile_keys = tuple(
            request.cache_key
            for request in [*outbound_requests[:1], *return_requests[:1]]
        )
        start_ms = config.start_time_ms or self.scenario.start_time_ms
        end_ms = config.end_time_ms or (start_ms + 30 * 60 * 1000)
        if end_ms <= start_ms:
            raise ValueError("INVALID_OPERATION_PLAN_WINDOW")
        outbound = self.timetable_service.generate(
            timetable_id=f"TT-{self.scenario.line_id}-{config.direction}",
            line_id=self.scenario.line_id,
            direction=config.direction,
            stations=outbound_stations,
            start_time_s=start_ms / 1000.0,
            end_time_s=end_ms / 1000.0,
            interval_run_times_s=outbound_run_times,
            run_time_source="DCDP_TARGET_WITH_RECOVERY_MARGIN",
        )
        outbound.services = outbound.services[: config.max_duties]
        self._operation_timetables.append(outbound)

        for index, outbound_service in enumerate(outbound.services, start=1):
            train_id = f"EMU-{self.scenario.line_id}-{index:03d}"
            duty_id = f"DUTY-{self.scenario.line_id}-{index:03d}"
            outbound_service.train_id = train_id
            outbound_service.duty_id = duty_id
            return_start_s = (
                outbound_service.stops[-1].planned_arrival_s
                + effective_turnback_layover_sec
            )
            return_direction = "DOWN" if config.direction == "UP" else "UP"
            return_tt = self.timetable_service.generate(
                timetable_id=f"TT-{self.scenario.line_id}-{return_direction}-{index:03d}",
                line_id=self.scenario.line_id,
                direction=return_direction,
                stations=return_stations,
                start_time_s=return_start_s,
                end_time_s=return_start_s + 1.0,
                interval_run_times_s=return_run_times,
                run_time_source="DCDP_TARGET_WITH_RECOVERY_MARGIN",
            )
            return_service = return_tt.services[0]
            return_service.train_id = train_id
            return_service.duty_id = duty_id
            self._operation_services[outbound_service.service_id] = outbound_service
            self._operation_services[return_service.service_id] = return_service
            duty = TrainDuty(
                duty_id=duty_id,
                train_id=train_id,
                service_ids=[outbound_service.service_id, return_service.service_id],
                planned_start_s=outbound_service.stops[0].planned_departure_s,
                planned_end_s=return_service.stops[-1].planned_arrival_s,
            )
            self._operation_duties[duty_id] = duty

            train = self._create_train(TrainConfig(
                train_id=train_id,
                line_id=self.scenario.line_id,
                initial_station_code=outbound_service.origin_station_code,
                direction=config.direction,
                capacity_pax=getattr(self, "_default_train_capacity_pax", 1_460),
                initial_load_pax=0,
            ))
            train.phase = IDLE
            train.door_state = "CLOSED"
            train.door_notice = "CLOSED"
            train.door_side = "NONE"
            train.dwell_remaining_sec = 0.0
            train._passenger_service_pending = False
            train.service_id = outbound_service.service_id
            train.next_service_id = return_service.service_id
            train.duty_id = duty_id
            train.lifecycle_state = "IN_DEPOT"
            train.planned_departure_ms = round(outbound_service.stops[0].planned_departure_s * 1000)
            train.planned_arrival_ms = round(outbound_service.stops[-1].planned_arrival_s * 1000)
            train.lifecycle_updated_ms = self._absolute_sim_time_ms()
            self._train_power_geometry(train, self._make_vehicle_config(train_id, 0))
            self.trains.append(train)
            self.dispatch_runtime.register_train(train, outbound_service)

        self._finalize_operation_plan_metadata()
        self._prewarm_operation_profiles()
        self._advance_operation_lifecycle(self.scenario.start_time_ms)

    def _set_lifecycle(self, train: SimTrainState, state: str, sim_time_ms: int) -> None:
        previous_state = train.lifecycle_state
        if previous_state == state:
            return
        train.lifecycle_state = state
        train.lifecycle_updated_ms = sim_time_ms
        if train.duty_id and train.duty_id in self._operation_duties:
            duty = self._operation_duties[train.duty_id]
            duty.lifecycle_state = state
            duty.active_service_id = train.service_id
        self._append_operation_event({
            "event": "LIFECYCLE_TRANSITION",
            "trainId": train.train_id,
            "dutyId": train.duty_id,
            "serviceId": train.service_id,
            "fromState": previous_state,
            "toState": state,
            "actualTimeMs": sim_time_ms,
        })

    def _append_operation_event(self, event: JsonDict) -> None:
        self._operation_events.append(event)
        self._operation_events = self._operation_events[-5000:]
        self._pending_operation_events.append(event)
        self.bus.publish(
            "operations.lifecycle",
            event,
            source="ats",
            tick=self.clock.current_tick,
        )

    def _advance_operation_lifecycle(self, sim_time_ms: int) -> None:
        config = self.scenario.operation_plan
        for train in self.trains:
            if train.duty_id is None:
                continue
            if train.lifecycle_state == "RETURN_REQUESTED":
                self._set_lifecycle(train, "STORED", sim_time_ms)
                continue
            planned_departure_ms = train.planned_departure_ms
            if planned_departure_ms is None:
                continue
            ready_lead_sec = config.ready_lead_sec
            active_service = self._operation_services.get(train.service_id or "")
            if active_service is not None and active_service.stops:
                origin_stop = active_service.stops[0]
                ready_lead_sec = max(
                    ready_lead_sec,
                    origin_stop.planned_departure_s - origin_stop.planned_arrival_s,
                )
            if (
                train.lifecycle_state == "IN_DEPOT"
                and sim_time_ms >= planned_departure_ms - round(ready_lead_sec * 1000)
            ):
                self._set_lifecycle(train, "READY", sim_time_ms)
                self._anchor_train_at_current_platform(train)
                self._begin_station_stop(train)
            turnback_ready = (
                train.lifecycle_state == "TURNBACK"
                and train.turnback_state == "COMPLETED"
            )
            if (
                (train.lifecycle_state == "READY" or turnback_ready)
                and sim_time_ms >= planned_departure_ms
            ):
                self._set_lifecycle(train, "DEPARTURE_REQUESTED", sim_time_ms)
                station_stop_active = (
                    train.phase == DWELLING
                    and (
                        train._passenger_service_pending
                        or train.dwell_remaining_sec > 0.0
                        or not train.door_system.all_closed_and_locked
                    )
                )
                if not station_stop_active:
                    train.phase = DWELLING
                    train.dwell_remaining_sec = 0.0
                    train.door_state = "CLOSED"
                    train.door_notice = "CLOSED"
                    train.door_side = "NONE"
                    train._passenger_service_pending = False
                self._anchor_train_at_current_platform(train)

    def _record_operation_departures(self, departures: list[Any], sim_time_ms: int) -> None:
        by_id = {train.train_id: train for train in self.trains}
        for departure in departures:
            train = by_id.get(departure.train_id)
            if train is None or train.duty_id is None:
                continue
            service = self._operation_services.get(train.service_id or "")
            planned_ms = None
            if service is not None:
                planned_s = service.planned_departure_at_station(departure.station_index)
                planned_ms = round(planned_s * 1000) if planned_s is not None else None
            train.actual_departure_ms = sim_time_ms
            train.planned_departure_ms = planned_ms
            train.schedule_deviation_sec = (
                (sim_time_ms - planned_ms) / 1000.0 if planned_ms is not None else None
            )
            self._set_lifecycle(train, "IN_SERVICE", sim_time_ms)
            self._append_operation_event({
                "event": "DEPARTURE",
                "trainId": train.train_id,
                "dutyId": train.duty_id,
                "serviceId": train.service_id,
                "stationIndex": departure.station_index,
                "plannedTimeMs": planned_ms,
                "actualTimeMs": sim_time_ms,
                "deviationSec": train.schedule_deviation_sec,
            })

    def _record_operation_arrival(self, train: SimTrainState, sim_time_ms: int) -> None:
        if train.duty_id is None:
            return
        service = self._operation_services.get(train.service_id or "")
        planned_ms = None
        if service is not None:
            planned_s = service.planned_arrival_at_station(train.station_index)
            planned_ms = round(planned_s * 1000) if planned_s is not None else None
            planned_departure_s = service.planned_departure_at_station(train.station_index)
            train.planned_departure_ms = (
                round(planned_departure_s * 1000)
                if planned_departure_s is not None else None
            )
        train.actual_arrival_ms = sim_time_ms
        train.planned_arrival_ms = planned_ms
        train.schedule_deviation_sec = (
            (sim_time_ms - planned_ms) / 1000.0 if planned_ms is not None else None
        )
        if planned_ms is not None:
            self.kpi_tracker.record_arrival(
                train.train_id,
                train.station_index,
                train.current_station_code,
                planned_arrival_s=planned_ms / 1000.0,
                actual_arrival_s=sim_time_ms / 1000.0,
            )
        self._append_operation_event({
            "event": "ARRIVAL",
            "trainId": train.train_id,
            "dutyId": train.duty_id,
            "serviceId": train.service_id,
            "stationIndex": train.station_index,
            "plannedTimeMs": planned_ms,
            "actualTimeMs": sim_time_ms,
            "deviationSec": train.schedule_deviation_sec,
        })

    def _activate_return_service(self, train: SimTrainState, sim_time_ms: int) -> None:
        if train.duty_id is None:
            return
        duty = self._operation_duties[train.duty_id]
        if train.service_id == duty.service_ids[-1]:
            return
        return_service_id = train.next_service_id or duty.service_ids[-1]
        return_service = self._operation_services.get(return_service_id)
        if return_service is None:
            raise RuntimeError(f"RETURN_SERVICE_NOT_FOUND:{return_service_id}")
        train.service_id = return_service_id
        train.next_service_id = None
        train.planned_departure_ms = round(return_service.stops[0].planned_departure_s * 1000)
        train.planned_arrival_ms = round(return_service.stops[-1].planned_arrival_s * 1000)
        self.dispatch_runtime.assign_service(train.train_id, return_service)
        self._set_lifecycle(train, "TURNBACK", sim_time_ms)

    def _handle_planned_terminal(self, train: SimTrainState, sim_time_ms: int) -> bool:
        """Return True when the duty has returned to depot and must not turn again."""
        if train.duty_id is None:
            return False
        duty = self._operation_duties[train.duty_id]
        if train.service_id == duty.service_ids[-1]:
            self.interlocking_runtime.release_train(train.train_id)
            train.phase = IDLE
            train.next_station_code = ""
            train.next_station_name = ""
            train.departure_authorized = False
            train.active_route_ids = ()
            self._set_lifecycle(train, "RETURN_REQUESTED", sim_time_ms)
            return True

        self._turn_train_at_terminal(train)
        self._activate_return_service(train, sim_time_ms)
        return False

    def _operation_experiment_phase(self, sim_time_ms: int) -> str:
        if not self._operation_window:
            return "DISABLED"
        if sim_time_ms < int(self._operation_window["measurementStartTimeMs"]):
            return "WARMUP"
        if sim_time_ms <= int(self._operation_window["measurementEndTimeMs"]):
            return "MEASUREMENT"
        if sim_time_ms <= int(self._operation_window["clearanceEndTimeMs"]):
            return "CLEARANCE"
        return "COMPLETE"

    def _operation_acceptance(self, sim_time_ms: int) -> JsonDict:
        config = self.scenario.operation_plan
        departure_services = {
            str(event.get("serviceId"))
            for event in self._operation_events
            if event.get("event") == "DEPARTURE" and event.get("serviceId")
        }
        completed_services: set[str] = set()
        deviations: list[float] = []
        worst_deviation_event: JsonDict | None = None
        last_progress_by_train: dict[str, int] = {}
        for event in self._operation_events:
            train_id = str(event.get("trainId", ""))
            actual_time_ms = event.get("actualTimeMs")
            if train_id and actual_time_ms is not None:
                last_progress_by_train[train_id] = max(
                    last_progress_by_train.get(train_id, 0), int(actual_time_ms)
                )
            deviation = event.get("deviationSec")
            if deviation is not None:
                signed_deviation = float(deviation)
                deviations.append(signed_deviation)
                if (
                    worst_deviation_event is None
                    or abs(signed_deviation)
                    > abs(float(worst_deviation_event.get("deviationSec", 0.0)))
                ):
                    worst_deviation_event = {
                        key: event.get(key)
                        for key in (
                            "event",
                            "trainId",
                            "serviceId",
                            "stationIndex",
                            "plannedTimeMs",
                            "actualTimeMs",
                            "deviationSec",
                        )
                    }
            if event.get("event") != "ARRIVAL":
                continue
            service_id = str(event.get("serviceId", ""))
            service = self._operation_services.get(service_id)
            if service is None or not service.stops:
                continue
            if int(event.get("stationIndex", -1)) == service.stops[-1].station_index:
                completed_services.add(service_id)

        threshold_ms = round(config.stuck_threshold_sec * 1000)
        stuck_trains: list[JsonDict] = []
        active_states = {"DEPARTURE_REQUESTED", "IN_SERVICE", "TURNBACK", "RETURN_REQUESTED"}
        for train in self.trains:
            if train.duty_id is None or train.lifecycle_state not in active_states:
                continue
            last_progress_ms = max(
                int(train.lifecycle_updated_ms or 0),
                last_progress_by_train.get(train.train_id, 0),
            )
            stalled_for_ms = max(0, sim_time_ms - last_progress_ms)
            if stalled_for_ms < threshold_ms:
                continue
            stuck_trains.append({
                "trainId": train.train_id,
                "dutyId": train.duty_id,
                "serviceId": train.service_id,
                "lifecycleState": train.lifecycle_state,
                "phase": train.phase,
                "stalledForSec": round(stalled_for_ms / 1000.0, 1),
                "holdReason": train.interlocking_hold_reason,
            })

        completed_duties = sum(
            1 for duty in self._operation_duties.values() if duty.lifecycle_state == "STORED"
        )
        total_duties = len(self._operation_duties)
        total_services = len(self._operation_services)
        all_completed = (
            total_duties > 0
            and completed_duties == total_duties
            and len(completed_services) == total_services
        )
        maximum_deviation_sec = max((abs(value) for value in deviations), default=0.0)
        maximum_late_sec = max(deviations, default=0.0)
        maximum_early_sec = abs(min(deviations, default=0.0))
        schedule_within_tolerance = (
            maximum_deviation_sec <= config.max_schedule_deviation_sec
        )
        phase = self._operation_experiment_phase(sim_time_ms)
        if all_completed and not stuck_trains and schedule_within_tolerance:
            status = "PASSED"
        elif all_completed or phase == "COMPLETE":
            status = "FAILED"
        elif departure_services or phase in {"MEASUREMENT", "CLEARANCE"}:
            status = "RUNNING"
        else:
            status = "PENDING"
        return {
            "status": status,
            "readyForAnalysis": status == "PASSED",
            "totalDutyCount": total_duties,
            "completedDutyCount": completed_duties,
            "dutyCompletionRate": round(
                completed_duties / total_duties if total_duties else 0.0, 4
            ),
            "totalServiceCount": total_services,
            "startedServiceCount": len(departure_services),
            "completedServiceCount": len(completed_services),
            "serviceCompletionRate": round(
                len(completed_services) / total_services if total_services else 0.0, 4
            ),
            "maximumAbsoluteDeviationSec": round(maximum_deviation_sec, 3),
            "maximumLateSec": round(maximum_late_sec, 3),
            "maximumEarlySec": round(maximum_early_sec, 3),
            "worstDeviationEvent": worst_deviation_event,
            "maxScheduleDeviationSec": config.max_schedule_deviation_sec,
            "scheduleWithinTolerance": schedule_within_tolerance,
            "stuckThresholdSec": config.stuck_threshold_sec,
            "stuckTrainCount": len(stuck_trains),
            "stuckTrains": stuck_trains,
            "profileWarmupReady": bool(self._operation_profile_warmup.get("ready", False)),
        }

    def operation_plan_state(self) -> JsonDict:
        sim_time_ms = self._absolute_sim_time_ms()
        experiment_window = dict(self._operation_window)
        experiment_window["phase"] = self._operation_experiment_phase(sim_time_ms)
        passenger_profile = getattr(self, "_passenger_profile", None)
        passenger_scenario = getattr(passenger_profile, "flow_scenario", None)
        return {
            "enabled": self.scenario.operation_plan.enabled,
            "planHash": self._operation_plan_hash,
            "generationWindow": {
                "startTimeMs": self.scenario.operation_plan.start_time_ms,
                "endTimeMs": self.scenario.operation_plan.end_time_ms,
            },
            "experimentWindow": experiment_window,
            "profileWarmup": dict(self._operation_profile_warmup),
            "experimentManifest": {
                "passengerProfileId": getattr(
                    passenger_profile, "profile_id", "BUILTIN_SYNTHETIC"
                ),
                "passengerRandomSeed": getattr(passenger_scenario, "random_seed", None),
                "profileCacheVersion": PROFILE_CACHE_VERSION,
                "profileReferenceLoadPax": self.scenario.operation_plan.profile_reference_load_pax,
                "runTimeSource": "DCDP_TARGET_WITH_RECOVERY_MARGIN",
                "runtimeRecoveryMarginSec": self.scenario.operation_plan.runtime_recovery_margin_sec,
                "doorCycleAllowanceSec": self.scenario.operation_plan.door_cycle_allowance_sec,
            },
            "acceptance": self._operation_acceptance(sim_time_ms),
            "timetables": [item.to_dict() for item in self._operation_timetables],
            "services": [item.to_dict() for item in self._operation_services.values()],
            "duties": [item.to_dict() for item in self._operation_duties.values()],
            "recentEvents": list(self._operation_events[-100:]),
        }

    def _build_snapshot(self) -> TickSnapshot:
        self._snapshot_sequence += 1
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
            speed_multiplier=self._speed_multiplier,
            session_id=self._session_id,
            run_id=self._run_id,
            snapshot_sequence=self._snapshot_sequence,
            model_quality=(
                str(self.power_service.network.quality)
                if self.power_service.network is not None else "SIMULATED"
            ),
            trains=[t.to_dict() for t in self.trains],
            stations=[
                s
                for stn in self._station_list
                for s in [self._station_snapshot(stn, "UP"), self._station_snapshot(stn, "DOWN")]
            ],
            power=[self._power_snapshot(state) for state in self._last_power_states.values()],
            power_network=self._power_network_snapshot(),
            dispatch_decisions=[self._dispatch_snapshot(item) for item in self._last_dispatch_decisions],
            dispatch_runtime=self.dispatch_runtime.snapshot(),
            interlocking=self.interlocking_runtime.snapshot(),
            operations=self.operation_plan_state(),
            kpi=dict({
                "activeTrains": len(active),
                "totalTrains": len(self.trains),
                "avgSpeed": (
                    round(sum(t.speed_mps for t in active) / len(active), 2) if active else 0
                ),
                "totalOnboardPax": sum(t.onboard_pax for t in self.trains),
                "totalWaitingPax": sum(p.waiting_pax for p in self.station_service.platforms.values()),
                "passengerProfileId": self._passenger_profile.profile_id,
                "passengerDataQuality": self._passenger_profile.quality,
                "estimatedWeekdayPassengerArrivals": self._estimated_weekday_passenger_arrivals,
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
                "lineScopeEnforced": self.line_scope is not None,
                "lineScopeId": self.line_scope.scope_id if self.line_scope is not None else None,
                "lineScopeSegmentCount": len(self.line_scope.segment_ids) if self.line_scope is not None else 0,
            }, **self.kpi_tracker.snapshot(self._absolute_sim_time_ms() / 1000.0).to_dict()),
        )

    def _persist_snapshot(self, snapshot: TickSnapshot | None) -> None:
        if snapshot is None or self.recorder is None or self._run_id is None:
            return
        self.recorder.record_world_snapshot(
            self._run_id,
            sequence=snapshot.snapshot_sequence,
            tick=snapshot.tick,
            sim_time_ms=snapshot.sim_time_ms,
            snapshot=snapshot.to_api_dict(
                tick_interval_ms=round(self._tick_interval_seconds * 1000),
            ),
        )

    # 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?    #  鍒濆鍖栬緟鍔?    # 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
    def _build_station_list(self) -> list[JsonDict]:
        """鏋勫缓鎸夐噷绋嬫帓搴忕殑杞︾珯鍒楄〃."""
        stations = sorted(self.station_catalog, key=lambda s: float(s.get("mileageM", 0)))
        return stations

    def _build_station_distances(self) -> list[float]:
        """鍚勭珯绱閲岀▼ (m)."""
        return [float(stn.get("mileageM", 0)) for stn in self._station_list]

    def _build_station_platform_ids(self) -> dict[int, tuple[int, ...]]:
        """鎸夎溅绔欓噷绋嬫妸绾胯矾绔欏彴琛ㄦ槧灏勫埌 station_index."""
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

    def _create_train(
        self,
        cfg: TrainConfig,
        vehicle_config: VehicleConfig | None = None,
    ) -> SimTrainState:
        """根据配置初始化一列列车."""
        # 查找起点站序号
        idx = next(
            (i for i, stn in enumerate(self._station_list) if stn.get("code") == cfg.initial_station_code),
            -1,
        )
        if idx < 0:
            raise ValueError(f"INVALID_INITIAL_STATION:{cfg.initial_station_code}")
        if cfg.direction not in ("UP", "DOWN"):
            raise ValueError(f"INVALID_DIRECTION:{cfg.direction}")
        stn = self._station_list[idx]
        next_idx = min(idx + 1, len(self._station_list) - 1) if cfg.direction == "UP" else max(idx - 1, 0)
        next_stn = self._station_list[next_idx]
        dist = abs(
            self._station_distances[next_idx] - self._station_distances[idx]
        )
        vehicle = vehicle_config or self._vehicle_config_by_train.get(
            cfg.train_id, VehicleConfig(train_id=cfg.train_id)
        )
        initial_path_plan = (
            self._path_plan_for_station_pair(
                idx, next_idx, train_length_m=vehicle.train_length_m
            )
            if idx != next_idx
            else None
        )
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
            dwell_remaining_sec=30.0,
            door_state="PREPARE_OPEN",
            door_side=self._door_side_for(cfg.direction),
            door_notice="PREPARE_OPEN",
            door_transition_remaining_sec=1.0,
            distance_to_next_m=dist,
            onboard_pax=cfg.initial_load_pax,
            capacity_pax=cfg.capacity_pax,
            load_factor=(cfg.initial_load_pax / cfg.capacity_pax) if cfg.capacity_pax > 0 else 0.0,
            mass_kg=(
                vehicle.mass_kg
                + cfg.initial_load_pax * vehicle.average_passenger_mass_kg
            ),
            train_length_m=vehicle.train_length_m,
            current_station_name=stn.get("name", ""),
            next_station_name=next_stn.get("name", ""),
            _passenger_service_pending=True,
        )

    def _create_train_from_spec(self, spec: JsonDict) -> SimTrainState:
        """Create a fresh runtime state from persistent user configuration."""
        train_id = str(spec["trainId"])
        cfg = TrainConfig(
            train_id=train_id,
            line_id="9",
            initial_station_code=str(spec["initialStationCode"]),
            direction=str(spec["direction"]),
            capacity_pax=int(spec.get(
                "capacityPax",
                getattr(self, "_default_train_capacity_pax", 1_460),
            )),
            initial_load_pax=int(spec.get("initialLoadPax", 0)),
        )
        vehicle_config = self._vehicle_config_by_train.get(train_id)
        train = self._create_train(cfg, vehicle_config)
        initial_segment_id = spec.get("initialSegmentId")
        if initial_segment_id is not None:
            try:
                train.current_platform_id = self._platform_id_by_segment.get(int(initial_segment_id))
            except (TypeError, ValueError):
                train.current_platform_id = None
        train.operation_mode = str(spec.get("operationMode", "ATO"))
        self._manual_mode_by_train[train_id] = train.operation_mode == "MANUAL"
        if vehicle_config is not None:
            train.mass_kg = (
                vehicle_config.mass_kg
                + cfg.initial_load_pax * vehicle_config.average_passenger_mass_kg
            )
        return train

    def _build_station_service(self) -> StationService:
        """Build the calibrated synthetic passenger service from versioned data."""
        profile = load_passenger_profile()
        self._passenger_profile = profile
        self._default_train_capacity_pax = profile.train_capacity_pax
        self._average_passenger_mass_kg = profile.average_passenger_mass_kg
        self._estimated_weekday_passenger_arrivals = round(
            profile.estimated_daily_arrivals()
        )
        return StationService(
            PoissonPassengerFlowGenerator(
                list(profile.station_configs),
                profile.flow_scenario,
                use_poisson=profile.use_poisson,
            ),
            profile.dwell_config,
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

    def _power_section_for_train(self, train: SimTrainState) -> str:
        return "PWR-09-UP" if train.direction == "UP" else "PWR-09-DOWN"

    def _train_mileage_m(self, train: SimTrainState) -> float:
        if not self._station_distances:
            return 0.0
        path_plan = train._path_plan
        origin_platform_id = path_plan.origin_platform_id if path_plan is not None else None
        destination_platform_id = (
            path_plan.destination_platform_id if path_plan is not None else None
        )
        if origin_platform_id is None:
            origin_platform_id = self._platform_id_by_segment.get(train.current_segment_id)
        origin_station_index = (
            train._path_origin_station_index
            if path_plan is not None and train._path_origin_station_index is not None
            else train.station_index
        )
        destination_station_index = (
            train._path_destination_station_index
            if path_plan is not None and train._path_destination_station_index is not None
            else train.station_index + (1 if train.direction == "UP" else -1)
        )
        current_m = self._station_head_mileage_m(
            origin_station_index,
            train.direction,
            train.train_length_m,
            platform_id=origin_platform_id,
        )
        if destination_station_index < 0 or destination_station_index >= len(self._station_distances):
            return current_m
        next_m = self._station_head_mileage_m(
            destination_station_index,
            train.direction,
            train.train_length_m,
            platform_id=destination_platform_id,
        )
        return current_m + (next_m - current_m) * max(0.0, min(1.0, train.segment_progress))

    def _station_head_mileage_m(
        self,
        station_index: int,
        direction: str,
        train_length_m: float,
        platform_id: int | None = None,
    ) -> float:
        platform = self._platform_by_id.get(platform_id) if platform_id is not None else None
        if platform_id not in self._station_platform_ids.get(station_index, ()):
            platform = None
        if platform is None:
            platform = self._platform_for_direction(station_index, direction)
        if platform is None:
            return self._station_distances[station_index]
        raw_offset_m = float(platform.get("offsetM", 0.0))
        platform_mileage_m = float(
            platform.get("mileageM", self._station_distances[station_index])
        )
        stop_offset_m = self._platform_head_stop_offset_m(
            platform, direction, train_length_m
        )
        return platform_mileage_m + stop_offset_m - raw_offset_m

    def _train_power_geometry(
        self,
        train: SimTrainState,
        vehicle: VehicleConfig,
    ) -> tuple[float, float, tuple[float, ...], tuple[str, ...]]:
        """Derive head, tail, collection points and overlapped supply sections."""
        head_mileage_m = self._train_mileage_m(train)
        direction_sign = 1.0 if train.direction == "UP" else -1.0
        tail_mileage_m = head_mileage_m - direction_sign * vehicle.train_length_m
        network = self.power_service.network
        if network is not None:
            lower_bound = network.ordered_substations[0].mileage_m
            upper_bound = network.ordered_substations[-1].mileage_m
        else:
            lower_bound = min(self._station_distances, default=0.0)
            upper_bound = max(self._station_distances, default=head_mileage_m)
        pantograph_mileages_m = tuple(
            min(
                upper_bound,
                max(lower_bound, head_mileage_m - direction_sign * offset_m),
            )
            for offset_m in vehicle.pantograph_offsets_from_head_m
        )
        if network is not None:
            spanned_sections = tuple(
                item.section_id
                for item in network.sections_spanned(head_mileage_m, tail_mileage_m, train.direction)
            )
        else:
            spanned_sections = ()
        train.train_length_m = vehicle.train_length_m
        train.head_mileage_m = head_mileage_m
        train.tail_mileage_m = tail_mileage_m
        train.pantograph_mileages_m = pantograph_mileages_m
        train.spanned_power_section_ids = spanned_sections
        return head_mileage_m, tail_mileage_m, pantograph_mileages_m, spanned_sections

    def _traction_limit_for_train(self, train: SimTrainState) -> float:
        state = self._last_power_states.get(self._power_section_for_train(train))
        if state is None:
            return 1.0
        return max(0.0, min(1.0, float(state.traction_limit_ratio)))

    def _absolute_sim_time_ms(self) -> int:
        return self.scenario.start_time_ms + int(self.clock.sim_time_seconds * 1000)

    def _station_snapshot(self, station: JsonDict, direction: str = "UP") -> JsonDict:
        code = str(station.get("code", ""))
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
            "generatedRegenKw": round(state.generated_regen_kw, 3),
            "selfConsumedRegenKw": round(state.self_consumed_regen_kw, 3),
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
            result["solverFailure"] = self.power_service.last_solver_failure
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
        scenario_path = Path(scenario_path)
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
        line_scope = None
        if scenario.line_scope_file:
            scope_candidates = [
                scenario_path.parent / scenario.line_scope_file,
                Path(line_map_path).parent.parent / "scenarios" / scenario.line_scope_file,
            ]
            scope_path = next((path for path in scope_candidates if path.exists()), None)
            if scope_path is None:
                searched = ", ".join(str(path) for path in scope_candidates)
                raise FileNotFoundError(
                    f"line scope file {scenario.line_scope_file} not found; searched: {searched}"
                )
            line_scope = LineScope.load(scope_path)
        return cls(
            scenario,
            line_map,
            station_catalog,
            recorder=recorder,
            line_scope=line_scope,
        )
