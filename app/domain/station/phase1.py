"""Phase 1: station stop judgment for Member D.

Judges whether a train has stopped successfully at a target station platform,
classifying the result as SUCCESS, OVERRUN, or UNDERSHOOT.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StopResult(Enum):
    SUCCESS = "SUCCESS"
    OVERRUN = "OVERRUN"
    UNDERSHOOT = "UNDERSHOOT"


@dataclass(frozen=True)
class StationStopJudgment:
    train_id: str
    station_id: str
    target_stop_m: float
    actual_stop_m: float
    stop_error_m: float
    tolerance_m: float
    is_stopped: bool
    stop_result: StopResult


def judge_stop(
    train_id: str,
    station_id: str,
    target_stop_m: float,
    actual_stop_m: float,
    tolerance_m: float = 0.5,
    speed_mps: float = 0.0,
) -> StationStopJudgment:
    if tolerance_m <= 0.0:
        raise ValueError(f"tolerance_m must be positive, got {tolerance_m}")
    is_stopped = speed_mps < 0.01
    stop_error_m = actual_stop_m - target_stop_m
    if not is_stopped:
        return StationStopJudgment(
            train_id=train_id,
            station_id=station_id,
            target_stop_m=target_stop_m,
            actual_stop_m=actual_stop_m,
            stop_error_m=stop_error_m,
            tolerance_m=tolerance_m,
            is_stopped=False,
            stop_result=StopResult.UNDERSHOOT,
        )
    if abs(stop_error_m) <= tolerance_m:
        result = StopResult.SUCCESS
    elif stop_error_m > tolerance_m:
        result = StopResult.OVERRUN
    else:
        result = StopResult.UNDERSHOOT
    return StationStopJudgment(
        train_id=train_id,
        station_id=station_id,
        target_stop_m=target_stop_m,
        actual_stop_m=actual_stop_m,
        stop_error_m=stop_error_m,
        tolerance_m=tolerance_m,
        is_stopped=True,
        stop_result=result,
    )
