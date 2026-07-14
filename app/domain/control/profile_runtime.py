from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
import multiprocessing
from pathlib import Path
import queue
import threading
import time
from typing import Any

from app.domain.control.models import AtoConfig
from app.domain.control.speed_profile import (
    OptimizedSpeedProfile,
    SpeedProfilePoint,
    estimate_scheduled_run_time_s,
    optimize_speed_profile_dcdp,
)
from app.domain.line.services import PathPlan
from app.domain.vehicle.models import VehicleConfig


PROFILE_CACHE_VERSION = 2


@dataclass(frozen=True)
class SpeedProfileRequest:
    cache_key: str
    target_position_m: float
    permitted_speed_mps: float
    scheduled_run_time_s: float
    vehicle_config: VehicleConfig
    dt_s: float
    position_step_m: float
    speed_step_mps: float
    terminal_tolerance_m: float
    max_states_per_stage: int
    path_plan: PathPlan


def build_speed_profile_request(
    path_plan: PathPlan,
    permitted_speed_mps: float,
    ato_config: AtoConfig,
    vehicle_config: VehicleConfig,
) -> SpeedProfileRequest:
    """Build a train-independent request suitable for sharing and persistence."""
    shared_vehicle = replace(vehicle_config, train_id="PROFILE")
    acceleration_mps2 = max(
        0.05,
        (shared_vehicle.max_traction_force_n - shared_vehicle.basic_resistance_n)
        / shared_vehicle.mass_kg,
    )
    bounded_speed_mps = min(permitted_speed_mps, ato_config.target_cruise_speed_mps)
    scheduled_run_time_s = ato_config.profile_run_time_s or estimate_scheduled_run_time_s(
        target_position_m=path_plan.total_length_m,
        permitted_speed_mps=bounded_speed_mps,
        acceleration_mps2=acceleration_mps2,
        deceleration_mps2=ato_config.expected_deceleration_mps2,
        runtime_margin_ratio=ato_config.profile_runtime_margin_ratio,
    )
    signature = {
        "version": PROFILE_CACHE_VERSION,
        "path": path_plan.cache_key(),
        "permittedSpeedMps": round(bounded_speed_mps, 3),
        "scheduledRunTimeS": round(scheduled_run_time_s, 3),
        "vehicle": {
            key: value
            for key, value in asdict(shared_vehicle).items()
            if key != "train_id"
        },
        "solver": {
            "dtS": ato_config.profile_time_step_s,
            "positionStepM": ato_config.profile_position_step_m,
            "speedStepMps": ato_config.profile_speed_step_mps,
            "terminalToleranceM": ato_config.stop_tolerance_m,
            "maxStatesPerStage": ato_config.profile_max_states_per_stage,
        },
    }
    encoded = json.dumps(signature, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    cache_key = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return SpeedProfileRequest(
        cache_key=cache_key,
        target_position_m=path_plan.total_length_m,
        permitted_speed_mps=bounded_speed_mps,
        scheduled_run_time_s=scheduled_run_time_s,
        vehicle_config=shared_vehicle,
        dt_s=ato_config.profile_time_step_s,
        position_step_m=ato_config.profile_position_step_m,
        speed_step_mps=ato_config.profile_speed_step_mps,
        terminal_tolerance_m=ato_config.stop_tolerance_m,
        max_states_per_stage=ato_config.profile_max_states_per_stage,
        path_plan=path_plan,
    )


def _profile_worker(
    request_queue: Any,
    result_queue: Any,
) -> None:
    while True:
        request = request_queue.get()
        if request is None:
            return
        try:
            profile = optimize_speed_profile_dcdp(
                target_position_m=request.target_position_m,
                permitted_speed_mps=request.permitted_speed_mps,
                scheduled_run_time_s=request.scheduled_run_time_s,
                vehicle_config=request.vehicle_config,
                dt_s=request.dt_s,
                position_step_m=request.position_step_m,
                speed_step_mps=request.speed_step_mps,
                terminal_tolerance_m=request.terminal_tolerance_m,
                max_states_per_stage=request.max_states_per_stage,
                path_plan=request.path_plan,
            )
            result_queue.put((request.cache_key, profile, None))
        except Exception as exc:  # pragma: no cover - worker boundary
            result_queue.put((request.cache_key, None, f"{type(exc).__name__}: {exc}"))


class AsyncSpeedProfileService:
    """Non-blocking, shared DCDP execution for the real-time simulation kernel."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir
        self._cache: dict[str, OptimizedSpeedProfile] = {}
        self._pending: set[str] = set()
        self._errors: dict[str, str] = {}
        self._lock = threading.RLock()
        self._context = multiprocessing.get_context("spawn")
        self._request_queue: Any | None = None
        self._result_queue: Any | None = None
        self._worker: multiprocessing.Process | None = None

    def request(self, request: SpeedProfileRequest) -> OptimizedSpeedProfile | None:
        with self._lock:
            self.poll()
            cached = self._cache.get(request.cache_key)
            if cached is not None:
                return cached
            disk_profile = self._load(request.cache_key)
            if disk_profile is not None:
                self._cache[request.cache_key] = disk_profile
                return disk_profile
            if request.cache_key in self._pending:
                if self._worker is not None and self._worker.is_alive():
                    return None
                self._pending.discard(request.cache_key)
                self._errors[request.cache_key] = "DCDP_WORKER_EXITED"
            if request.cache_key in self._errors:
                return None
            self._ensure_worker()
            self._pending.add(request.cache_key)
            self._request_queue.put(request)
            return None

    def prime(self, requests: list[SpeedProfileRequest]) -> tuple[str, ...]:
        """Queue unique profiles and return the cache keys that must become ready."""
        cache_keys: list[str] = []
        seen: set[str] = set()
        for request in requests:
            if request.cache_key in seen:
                continue
            seen.add(request.cache_key)
            cache_keys.append(request.cache_key)
            self.request(request)
        return tuple(cache_keys)

    def wait_for(
        self,
        cache_keys: tuple[str, ...] | list[str],
        timeout_sec: float,
        *,
        poll_interval_sec: float = 0.01,
    ) -> dict[str, Any]:
        """Wait for an explicitly primed profile set without advancing simulation time."""
        expected = set(cache_keys)
        deadline = time.monotonic() + max(0.0, float(timeout_sec))
        while True:
            with self._lock:
                self.poll()
                ready = expected.intersection(self._cache)
                failed = expected.intersection(self._errors)
                pending = expected - ready - failed
                errors = {key: self._errors[key] for key in sorted(failed)}
            if not pending or time.monotonic() >= deadline:
                return {
                    "requestedProfileCount": len(expected),
                    "readyProfileCount": len(ready),
                    "pendingProfileCount": len(pending),
                    "failedProfileCount": len(failed),
                    "ready": not pending and not failed,
                    "pendingCacheKeys": sorted(pending),
                    "errors": errors,
                }
            time.sleep(max(0.001, float(poll_interval_sec)))

    def poll(self) -> None:
        with self._lock:
            if self._result_queue is None:
                return
            while True:
                try:
                    cache_key, profile, error = self._result_queue.get_nowait()
                except queue.Empty:
                    break
                self._pending.discard(cache_key)
                if profile is not None:
                    self._cache[cache_key] = profile
                    self._save(cache_key, profile)
                    self._errors.pop(cache_key, None)
                elif error:
                    self._errors[cache_key] = error

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self.poll()
            return {
                "cachedProfileCount": len(self._cache),
                "pendingProfileCount": len(self._pending),
                "failedProfileCount": len(self._errors),
                "workerAlive": bool(self._worker and self._worker.is_alive()),
            }

    def shutdown(self) -> None:
        with self._lock:
            worker = self._worker
            if worker is None:
                return
            if self._request_queue is not None:
                self._request_queue.put(None)
            worker.join(timeout=0.2)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=1.0)
            self._worker = None
            self._request_queue = None
            self._result_queue = None
            self._pending.clear()

    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._request_queue = self._context.Queue()
        self._result_queue = self._context.Queue()
        self._worker = self._context.Process(
            target=_profile_worker,
            args=(self._request_queue, self._result_queue),
            name="dcdp-profile-worker",
            daemon=True,
        )
        self._worker.start()

    def _cache_path(self, cache_key: str) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / f"{cache_key}.json"

    def _load(self, cache_key: str) -> OptimizedSpeedProfile | None:
        path = self._cache_path(cache_key)
        if path is None or not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("version") != PROFILE_CACHE_VERSION:
                return None
            return OptimizedSpeedProfile(
                points=tuple(SpeedProfilePoint(**point) for point in payload["points"]),
                target_position_m=float(payload["targetPositionM"]),
                permitted_speed_mps=float(payload["permittedSpeedMps"]),
                scheduled_run_time_s=float(payload["scheduledRunTimeS"]),
                terminal_score=float(payload["terminalScore"]),
            )
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None

    def _save(self, cache_key: str, profile: OptimizedSpeedProfile) -> None:
        path = self._cache_path(cache_key)
        if path is None:
            return
        payload = {
            "version": PROFILE_CACHE_VERSION,
            "targetPositionM": profile.target_position_m,
            "permittedSpeedMps": profile.permitted_speed_mps,
            "scheduledRunTimeS": profile.scheduled_run_time_s,
            "terminalScore": profile.terminal_score,
            "points": [asdict(point) for point in profile.points],
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            return
