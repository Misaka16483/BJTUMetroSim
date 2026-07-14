from __future__ import annotations

import bisect
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence


JsonDict = dict[str, Any]
Candidate = Mapping[str, float]
TRAJECTORY_SCHEMA_VERSION = "POWER-TRAJECTORY-V1"
TIMING_VARIABLES = frozenset({"departureSpreadSec", "tractionTimingSec", "brakeTimingSec"})


class TrajectoryContractError(ValueError):
    pass


@dataclass(frozen=True)
class TrainTrajectorySample:
    sim_time_ms: int
    train_id: str
    direction: str
    mileage_m: float
    speed_mps: float
    acceleration_mps2: float
    mass_kg: float
    traction_force_n: float
    electric_brake_force_n: float
    pneumatic_brake_force_n: float = 0.0
    auxiliary_power_kw: float = 0.0
    traction_power_request_kw: float | None = None
    regen_power_available_kw: float | None = None
    permitted_speed_mps: float | None = None
    grade_ratio: float = 0.0
    resistance_force_n: float | None = None
    phase: str = "UNKNOWN"
    current_station_code: str = ""
    next_station_code: str = ""
    departure_authorized: bool | None = None
    interlocking_hold_reason: str | None = None
    active_route_ids: tuple[str, ...] = ()
    turnback_count: int = 0
    source: str = "UNKNOWN"

    @property
    def total_brake_force_n(self) -> float:
        return self.electric_brake_force_n + self.pneumatic_brake_force_n

    @property
    def dynamics_residual_n(self) -> float | None:
        if self.resistance_force_n is None:
            return None
        gradient_force_n = self.mass_kg * 9.80665 * self.grade_ratio
        return abs(
            self.traction_force_n
            - self.total_brake_force_n
            - self.resistance_force_n
            - gradient_force_n
            - self.mass_kg * self.acceleration_mps2
        )

    def to_dict(self) -> JsonDict:
        payload = asdict(self)
        payload["active_route_ids"] = list(self.active_route_ids)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TrainTrajectorySample":
        values = dict(payload)
        values["active_route_ids"] = tuple(str(item) for item in values.get("active_route_ids", ()))
        return cls(**values)


@dataclass(frozen=True)
class TrajectoryFrame:
    sim_time_ms: int
    samples: tuple[TrainTrajectorySample, ...]
    tick: int | None = None
    clock_state: str | None = None
    source: str = "UNKNOWN"

    def to_dict(self) -> JsonDict:
        return {
            "recordType": "frame",
            "schemaVersion": TRAJECTORY_SCHEMA_VERSION,
            "simTimeMs": self.sim_time_ms,
            "tick": self.tick,
            "clockState": self.clock_state,
            "source": self.source,
            "samples": [item.to_dict() for item in self.samples],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TrajectoryFrame":
        return cls(
            sim_time_ms=int(payload["simTimeMs"]),
            tick=int(payload["tick"]) if payload.get("tick") is not None else None,
            clock_state=str(payload["clockState"]) if payload.get("clockState") is not None else None,
            source=str(payload.get("source", "JSONL_REPLAY")),
            samples=tuple(TrainTrajectorySample.from_dict(item) for item in payload.get("samples", ())),
        )


@dataclass(frozen=True)
class TrajectoryIssue:
    code: str
    message: str
    sim_time_ms: int | None = None
    train_id: str | None = None


@dataclass(frozen=True)
class TrajectoryValidationReport:
    issues: tuple[TrajectoryIssue, ...] = ()
    frame_count: int = 0
    sample_count: int = 0

    @property
    def passed(self) -> bool:
        return not self.issues

    def require_valid(self) -> None:
        if self.issues:
            detail = "; ".join(f"{item.code}: {item.message}" for item in self.issues[:5])
            raise TrajectoryContractError(detail)


def validate_trajectory_frames(
    frames: Sequence[TrajectoryFrame],
    *,
    allow_roster_changes: bool = True,
    max_acceleration_mps2: float = 2.0,
    max_dynamics_residual_n: float = 5_000.0,
    position_tolerance_m: float = 25.0,
) -> TrajectoryValidationReport:
    issues: list[TrajectoryIssue] = []
    previous_time: int | None = None
    previous_by_train: dict[str, TrainTrajectorySample] = {}
    initial_roster: frozenset[str] | None = None
    sample_count = 0
    for frame in frames:
        if previous_time is not None and frame.sim_time_ms <= previous_time:
            issues.append(TrajectoryIssue(
                "NON_MONOTONIC_TIME",
                f"frame time {frame.sim_time_ms} is not after {previous_time}",
                frame.sim_time_ms,
            ))
        ids = [sample.train_id for sample in frame.samples]
        if len(ids) != len(set(ids)):
            issues.append(TrajectoryIssue("DUPLICATE_TRAIN", "train ids must be unique within a frame", frame.sim_time_ms))
        roster = frozenset(ids)
        if initial_roster is None:
            initial_roster = roster
        elif not allow_roster_changes and roster != initial_roster:
            issues.append(TrajectoryIssue("ROSTER_CHANGED", "train roster changed in a fixed-roster trace", frame.sim_time_ms))
        for sample in frame.samples:
            sample_count += 1
            if sample.sim_time_ms != frame.sim_time_ms:
                issues.append(TrajectoryIssue(
                    "SAMPLE_TIME_MISMATCH",
                    "sample time does not match its frame",
                    frame.sim_time_ms,
                    sample.train_id,
                ))
            if not sample.train_id:
                issues.append(TrajectoryIssue("EMPTY_TRAIN_ID", "train id is required", frame.sim_time_ms))
            if sample.direction not in {"UP", "DOWN"}:
                issues.append(TrajectoryIssue(
                    "INVALID_DIRECTION", f"unsupported direction {sample.direction!r}", frame.sim_time_ms, sample.train_id
                ))
            numeric_values = {
                "mileage": sample.mileage_m,
                "speed": sample.speed_mps,
                "acceleration": sample.acceleration_mps2,
                "mass": sample.mass_kg,
                "traction force": sample.traction_force_n,
                "electric brake force": sample.electric_brake_force_n,
                "pneumatic brake force": sample.pneumatic_brake_force_n,
                "auxiliary power": sample.auxiliary_power_kw,
                "grade": sample.grade_ratio,
            }
            optional_numeric_values = {
                "traction power": sample.traction_power_request_kw,
                "regen power": sample.regen_power_available_kw,
                "permitted speed": sample.permitted_speed_mps,
                "resistance force": sample.resistance_force_n,
            }
            if any(not math.isfinite(value) for value in numeric_values.values()):
                issues.append(TrajectoryIssue("NON_FINITE_VALUE", "sample contains a non-finite value", frame.sim_time_ms, sample.train_id))
            if any(value is not None and not math.isfinite(value) for value in optional_numeric_values.values()):
                issues.append(TrajectoryIssue("NON_FINITE_VALUE", "sample contains a non-finite optional value", frame.sim_time_ms, sample.train_id))
            if sample.speed_mps < 0 or sample.mass_kg <= 0:
                issues.append(TrajectoryIssue("INVALID_KINEMATICS", "speed must be non-negative and mass positive", frame.sim_time_ms, sample.train_id))
            if min(sample.traction_force_n, sample.electric_brake_force_n, sample.pneumatic_brake_force_n) < 0:
                issues.append(TrajectoryIssue("NEGATIVE_FORCE", "traction and brake forces must be non-negative", frame.sim_time_ms, sample.train_id))
            if sample.traction_force_n > 1e-6 and sample.total_brake_force_n > 1e-6:
                issues.append(TrajectoryIssue("TRACTION_BRAKE_OVERLAP", "traction and braking overlap", frame.sim_time_ms, sample.train_id))
            if sample.permitted_speed_mps is not None and sample.speed_mps > sample.permitted_speed_mps + 0.05:
                issues.append(TrajectoryIssue("SPEED_LIMIT_EXCEEDED", "speed exceeds the recorded permitted speed", frame.sim_time_ms, sample.train_id))
            if any(
                value is not None and value < 0
                for value in (
                    sample.traction_power_request_kw,
                    sample.regen_power_available_kw,
                    sample.permitted_speed_mps,
                    sample.resistance_force_n,
                )
            ):
                issues.append(TrajectoryIssue("NEGATIVE_OPTIONAL_VALUE", "power, speed limit, and resistance must be non-negative", frame.sim_time_ms, sample.train_id))
            residual_n = sample.dynamics_residual_n
            if residual_n is not None and residual_n > max_dynamics_residual_n:
                issues.append(TrajectoryIssue(
                    "DYNAMICS_NOT_CLOSED",
                    f"force balance residual {residual_n:.3f} N exceeds {max_dynamics_residual_n:.3f} N",
                    frame.sim_time_ms,
                    sample.train_id,
                ))
            previous = previous_by_train.get(sample.train_id)
            if previous is not None and frame.sim_time_ms > previous.sim_time_ms:
                dt_sec = (frame.sim_time_ms - previous.sim_time_ms) / 1000.0
                observed_acceleration = (sample.speed_mps - previous.speed_mps) / dt_sec
                if abs(observed_acceleration) > max_acceleration_mps2 + 0.25:
                    issues.append(TrajectoryIssue(
                        "ACCELERATION_JUMP",
                        f"observed acceleration {observed_acceleration:.3f} m/s2 is implausible",
                        frame.sim_time_ms,
                        sample.train_id,
                    ))
                if abs(sample.acceleration_mps2 - observed_acceleration) > 0.25:
                    issues.append(TrajectoryIssue(
                        "ACCELERATION_MISMATCH",
                        f"recorded acceleration differs from the observed value by "
                        f"{abs(sample.acceleration_mps2 - observed_acceleration):.3f} m/s2",
                        frame.sim_time_ms,
                        sample.train_id,
                    ))
                if sample.turnback_count == previous.turnback_count:
                    allowed = max(previous.speed_mps, sample.speed_mps) * dt_sec + 0.5 * max_acceleration_mps2 * dt_sec * dt_sec + position_tolerance_m
                    if abs(sample.mileage_m - previous.mileage_m) > allowed:
                        issues.append(TrajectoryIssue(
                            "POSITION_DISCONTINUITY",
                            f"mileage changed by {abs(sample.mileage_m - previous.mileage_m):.3f} m; allowed {allowed:.3f} m",
                            frame.sim_time_ms,
                            sample.train_id,
                        ))
            previous_by_train[sample.train_id] = sample
        previous_time = frame.sim_time_ms
    return TrajectoryValidationReport(tuple(issues), len(frames), sample_count)


class TrajectoryProvider(Protocol):
    supported_candidate_variables: frozenset[str]
    source: str

    def prepare(self, candidate: Candidate, sample_times_ms: Sequence[int]) -> None: ...

    def frame_at(self, sim_time_ms: int, candidate: Candidate) -> TrajectoryFrame: ...

    def tracking_metrics(self, candidate: Candidate) -> Mapping[str, float]: ...


class ProxyTrajectoryProvider:
    supported_candidate_variables = TIMING_VARIABLES
    source = "ANALYTIC_PROXY_V2"

    def __init__(
        self,
        frame_factory: Callable[[Candidate, int], TrajectoryFrame],
        tracking_factory: Callable[[Candidate], Mapping[str, float]],
    ) -> None:
        self._frame_factory = frame_factory
        self._tracking_factory = tracking_factory

    def prepare(self, candidate: Candidate, sample_times_ms: Sequence[int]) -> None:
        return None

    def frame_at(self, sim_time_ms: int, candidate: Candidate) -> TrajectoryFrame:
        return self._frame_factory(candidate, sim_time_ms)

    def tracking_metrics(self, candidate: Candidate) -> Mapping[str, float]:
        return self._tracking_factory(candidate)


class InMemoryTrajectoryProvider:
    supported_candidate_variables = frozenset()
    source = "MEMORY_REPLAY"

    def __init__(
        self,
        frames: Sequence[TrajectoryFrame],
        *,
        tracking_metrics: Mapping[str, float] | None = None,
        validate: bool = True,
    ) -> None:
        self.frames = tuple(sorted(frames, key=lambda item: item.sim_time_ms))
        if not self.frames:
            raise TrajectoryContractError("trajectory replay requires at least one frame")
        if validate:
            validate_trajectory_frames(self.frames).require_valid()
        self._times = tuple(item.sim_time_ms for item in self.frames)
        maximum_speed = max((sample.speed_mps for frame in self.frames for sample in frame.samples), default=0.0)
        required_metrics = {
            "speedTrackingRmseMps",
            "meanDepartureDeviationSec",
            "maxDepartureDeviationSec",
            "runtimeDeviationSec",
            "stopPositionErrorM",
        }
        supplied_metrics = dict(tracking_metrics or {})
        self._tracking_metrics = {
            "speedTrackingRmseMps": 0.0,
            "meanDepartureDeviationSec": 0.0,
            "maxDepartureDeviationSec": 0.0,
            "runtimeDeviationSec": 0.0,
            "stopPositionErrorM": 0.0,
            "maximumSpeedMps": maximum_speed,
            "operationalMetricsAvailable": float(required_metrics.issubset(supplied_metrics)),
            **supplied_metrics,
        }

    def prepare(self, candidate: Candidate, sample_times_ms: Sequence[int]) -> None:
        if sample_times_ms and (sample_times_ms[0] < self._times[0] or sample_times_ms[-1] > self._times[-1]):
            raise TrajectoryContractError(
                f"requested interval [{sample_times_ms[0]}, {sample_times_ms[-1]}] exceeds replay "
                f"[{self._times[0]}, {self._times[-1]}]"
            )

    def frame_at(self, sim_time_ms: int, candidate: Candidate) -> TrajectoryFrame:
        position = bisect.bisect_left(self._times, sim_time_ms)
        if position < len(self._times) and self._times[position] == sim_time_ms:
            return self.frames[position]
        if position == 0 or position == len(self.frames):
            raise TrajectoryContractError(f"time {sim_time_ms} is outside the replay interval")
        return _interpolate_frames(self.frames[position - 1], self.frames[position], sim_time_ms)

    def tracking_metrics(self, candidate: Candidate) -> Mapping[str, float]:
        return dict(self._tracking_metrics)


class JsonlTrajectoryRecorder:
    def __init__(self, path: str | Path, *, metadata: Mapping[str, Any] | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("w", encoding="utf-8", newline="\n")
        self.write_metadata(metadata or {})

    def write_metadata(self, metadata: Mapping[str, Any]) -> None:
        self._stream.write(json.dumps({
            "recordType": "metadata",
            "schemaVersion": TRAJECTORY_SCHEMA_VERSION,
            "metadata": dict(metadata),
        }, ensure_ascii=False) + "\n")
        self._stream.flush()

    def write(self, frame: TrajectoryFrame) -> None:
        self._stream.write(json.dumps(frame.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n")
        self._stream.flush()

    def close(self) -> None:
        if not self._stream.closed:
            self._stream.close()

    def __enter__(self) -> "JsonlTrajectoryRecorder":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


class JsonlTrajectoryProvider(InMemoryTrajectoryProvider):
    source = "JSONL_REPLAY"

    def __init__(self, path: str | Path, *, validate: bool = True) -> None:
        self.path = Path(path)
        metadata: JsonDict = {}
        frames: list[TrajectoryFrame] = []
        with self.path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                payload = json.loads(line)
                if payload.get("schemaVersion") != TRAJECTORY_SCHEMA_VERSION:
                    raise TrajectoryContractError(f"unsupported trajectory schema at line {line_number}")
                if payload.get("recordType") == "metadata":
                    metadata.update(payload.get("metadata", {}))
                elif payload.get("recordType") == "frame":
                    frames.append(TrajectoryFrame.from_dict(payload))
                else:
                    raise TrajectoryContractError(f"unknown record type at line {line_number}")
        self.metadata = metadata
        super().__init__(frames, tracking_metrics=metadata.get("trackingMetrics"), validate=validate)


class EngineRunTrajectoryProvider(InMemoryTrajectoryProvider):
    source = "ENGINE_RUNNER"

    def __init__(
        self,
        runner: Callable[[Candidate, Sequence[int]], Iterable[TrajectoryFrame]],
        *,
        supported_candidate_variables: Iterable[str] = TIMING_VARIABLES,
        tracking_metrics_factory: Callable[[Candidate, Sequence[TrajectoryFrame]], Mapping[str, float]] | None = None,
    ) -> None:
        self.runner = runner
        self.supported_candidate_variables = frozenset(supported_candidate_variables)
        self.tracking_metrics_factory = tracking_metrics_factory
        self._candidate_key: tuple[tuple[str, float], ...] | None = None
        self.frames = ()
        self._times = ()
        self._tracking_metrics: JsonDict = {}

    def prepare(self, candidate: Candidate, sample_times_ms: Sequence[int]) -> None:
        key = tuple(sorted(
            (name, float(candidate.get(name, 0.0)))
            for name in self.supported_candidate_variables
        ))
        if key == self._candidate_key and self.frames:
            return
        frames = tuple(self.runner(candidate, sample_times_ms))
        metrics = self.tracking_metrics_factory(candidate, frames) if self.tracking_metrics_factory else None
        InMemoryTrajectoryProvider.__init__(self, frames, tracking_metrics=metrics, validate=True)
        self._candidate_key = key


class EngineSnapshotTrajectoryAdapter:
    """Stateful adapter from public TickSnapshot payloads to the power trace contract."""

    def __init__(self) -> None:
        self._previous: dict[str, tuple[int, float]] = {}

    def frame_from_snapshot(self, snapshot: Any) -> TrajectoryFrame:
        sim_time_ms = int(_snapshot_value(snapshot, "sim_time_ms", "simTimeMs", default=0))
        trains = _snapshot_value(snapshot, "trains", "trains", default=())
        samples: list[TrainTrajectorySample] = []
        for train in trains:
            train_id = str(_mapping_value(train, "trainId", "train_id", default=""))
            speed_mps = float(_mapping_value(train, "speedMps", "speed_mps", default=0.0))
            previous = self._previous.get(train_id)
            acceleration = 0.0
            if previous is not None and sim_time_ms > previous[0]:
                acceleration = (speed_mps - previous[1]) / ((sim_time_ms - previous[0]) / 1000.0)
            self._previous[train_id] = (sim_time_ms, speed_mps)
            permitted_speed = _mapping_value(
                train,
                "permittedSpeedMps",
                "permitted_speed_mps",
                default=None,
            )
            local_speed_limit = _mapping_value(
                train,
                "localSpeedLimitMps",
                "local_speed_limit_mps",
                default=None,
            )
            available_speed_limits = [
                float(value)
                for value in (permitted_speed, local_speed_limit)
                if value is not None
            ]
            effective_speed_limit = min(available_speed_limits, default=22.22)
            resistance_force = _mapping_value(
                train,
                "resistanceForceN",
                "resistance_force_n",
                default=None,
            )
            samples.append(TrainTrajectorySample(
                sim_time_ms=sim_time_ms,
                train_id=train_id,
                direction=str(_mapping_value(train, "direction", "direction", default="")),
                mileage_m=float(_mapping_value(train, "headMileageM", "head_mileage_m", default=0.0)),
                speed_mps=speed_mps,
                acceleration_mps2=acceleration,
                mass_kg=float(_mapping_value(train, "massKg", "mass_kg", default=225_000.0)),
                traction_force_n=float(_mapping_value(train, "tractionForceN", "traction_force_n", default=0.0)),
                electric_brake_force_n=float(_mapping_value(train, "electricBrakeForceN", "electric_brake_force_n", default=0.0)),
                pneumatic_brake_force_n=float(_mapping_value(train, "pneumaticBrakeForceN", "pneumatic_brake_force_n", default=0.0)),
                auxiliary_power_kw=float(_mapping_value(train, "auxiliaryPowerKw", "auxiliary_power_kw", default=0.0)),
                traction_power_request_kw=float(_mapping_value(train, "tractionPowerRequestKw", "traction_power_request_kw", default=0.0)),
                regen_power_available_kw=float(_mapping_value(train, "regenPowerAvailableKw", "regen_power_available_kw", default=0.0)),
                permitted_speed_mps=effective_speed_limit,
                grade_ratio=float(_mapping_value(train, "gradeRatio", "grade_ratio", default=0.0)),
                resistance_force_n=float(resistance_force) if resistance_force is not None else None,
                phase=str(_mapping_value(train, "phase", "phase", default="UNKNOWN")),
                current_station_code=str(_mapping_value(train, "currentStationCode", "current_station_code", default="")),
                next_station_code=str(_mapping_value(train, "nextStationCode", "next_station_code", default="")),
                departure_authorized=bool(_mapping_value(train, "departureAuthorized", "departure_authorized", default=False)),
                interlocking_hold_reason=_optional_string(_mapping_value(train, "interlockingHoldReason", "interlocking_hold_reason", default=None)),
                active_route_ids=tuple(str(item) for item in _mapping_value(train, "activeRouteIds", "active_route_ids", default=())),
                turnback_count=int(_mapping_value(train, "turnbackCount", "turnback_count", default=0)),
                source="ENGINE_SNAPSHOT",
            ))
        return TrajectoryFrame(
            sim_time_ms=sim_time_ms,
            tick=int(_snapshot_value(snapshot, "tick", "tick", default=0)),
            clock_state=str(_snapshot_value(snapshot, "clock_state", "clockState", default="UNKNOWN")),
            samples=tuple(samples),
            source="ENGINE_SNAPSHOT",
        )


def assert_candidate_supported(provider: TrajectoryProvider, candidate: Candidate, baseline: Candidate) -> None:
    changed = {
        name for name in TIMING_VARIABLES
        if abs(float(candidate.get(name, baseline.get(name, 0.0))) - float(baseline.get(name, 0.0))) > 1e-9
    }
    unsupported = changed.difference(provider.supported_candidate_variables)
    if unsupported:
        raise TrajectoryContractError(
            f"provider {provider.source} cannot apply timing variables: {', '.join(sorted(unsupported))}"
        )


def _interpolate_frames(left: TrajectoryFrame, right: TrajectoryFrame, sim_time_ms: int) -> TrajectoryFrame:
    if right.sim_time_ms <= left.sim_time_ms:
        raise TrajectoryContractError("cannot interpolate non-increasing frames")
    ratio = (sim_time_ms - left.sim_time_ms) / (right.sim_time_ms - left.sim_time_ms)
    left_by_id = {item.train_id: item for item in left.samples}
    right_by_id = {item.train_id: item for item in right.samples}
    if set(left_by_id) != set(right_by_id):
        raise TrajectoryContractError("cannot interpolate across a train roster change")
    samples: list[TrainTrajectorySample] = []
    for train_id in sorted(left_by_id):
        a = left_by_id[train_id]
        b = right_by_id[train_id]
        if a.direction != b.direction or a.turnback_count != b.turnback_count:
            raise TrajectoryContractError(f"cannot interpolate {train_id} across direction or turnback change")
        nearest = a if ratio < 0.5 else b
        values = {
            name: _lerp(getattr(a, name), getattr(b, name), ratio)
            for name in (
                "mileage_m", "speed_mps", "acceleration_mps2", "mass_kg", "traction_force_n",
                "electric_brake_force_n", "pneumatic_brake_force_n", "auxiliary_power_kw", "grade_ratio",
            )
        }
        for name in ("traction_power_request_kw", "regen_power_available_kw", "permitted_speed_mps", "resistance_force_n"):
            first = getattr(a, name)
            second = getattr(b, name)
            values[name] = _lerp(first, second, ratio) if first is not None and second is not None else None
        samples.append(TrainTrajectorySample(
            sim_time_ms=sim_time_ms,
            train_id=train_id,
            direction=a.direction,
            phase=nearest.phase,
            current_station_code=nearest.current_station_code,
            next_station_code=nearest.next_station_code,
            departure_authorized=nearest.departure_authorized,
            interlocking_hold_reason=nearest.interlocking_hold_reason,
            active_route_ids=nearest.active_route_ids,
            turnback_count=a.turnback_count,
            source="INTERPOLATED_REPLAY",
            **values,
        ))
    return TrajectoryFrame(sim_time_ms, tuple(samples), source="INTERPOLATED_REPLAY")


def _lerp(first: float, second: float, ratio: float) -> float:
    return float(first) + (float(second) - float(first)) * ratio


def _snapshot_value(snapshot: Any, attribute: str, key: str, *, default: Any) -> Any:
    if isinstance(snapshot, Mapping):
        return snapshot.get(key, default)
    return getattr(snapshot, attribute, default)


def _mapping_value(payload: Any, camel: str, snake: str, *, default: Any) -> Any:
    if isinstance(payload, Mapping):
        return payload.get(camel, payload.get(snake, default))
    return getattr(payload, snake, default)


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value)
