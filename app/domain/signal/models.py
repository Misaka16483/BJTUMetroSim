"""Shared data models for signal, train control and safety — Member C Phase 1.

TrainState and ControlCommand are defined here as shared models between
members B and C.  Once member B begins implementation they should be
extracted to a common location (e.g. app/domain/shared.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TrainState:
    """Snapshot of a single train's kinematic and positional state.

    This is the canonical model consumed by TrainControlService and
    SafetyGuard.  The fields mirror section 6.3.1 of the software
    design document.
    """

    train_id: str
    sim_time_ms: int
    seg_id: int
    offset_m: float
    position_m: float
    speed_mps: float
    acceleration_mps2: float = 0.0
    traction_level: float = 0.0
    brake_level: float = 0.0
    direction: str = "FORWARD"
    operation_mode: str = "ATO"
    run_phase: str = "ACCEL"
    target_platform_id: int | None = None
    target_stop_point_m: float | None = None
    distance_to_target_m: float | None = None
    emergency_brake: bool = False
    # Length for section-occupancy derivation (Phase 2).
    # Default 120 m ≈ 6-car B-type metro train.  A/B override per scenario.
    length_m: float = 120.0


@dataclass(frozen=True)
class ControlCommand:
    """Traction / brake command issued by ATO, driver desk or ATP override.

    Mirror of section 6.3.2 of the software design document.
    """

    train_id: str
    sim_time_ms: int
    source: str = "ATO"
    traction_level: float = 0.0
    brake_level: float = 0.0
    emergency_brake: bool = False
    door_command: str = "NONE"
    mode_command: str = "KEEP"
    reason: str | None = None


@dataclass(frozen=True)
class SignalState:
    """Signal aspect, permitted speed and MA boundary for a train.

    This is the primary output of TrainControlService.
    """

    train_id: str
    sim_time_ms: int
    signal_aspect: str  # GREEN / YELLOW / RED / UNKNOWN
    permitted_speed_mps: float
    movement_authority_end_m: float
    target_distance_m: float
    emergency_brake_required: bool
    current_block_id: str | None = None
    next_signal_id: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class MovementAuthority:
    """Detailed movement authority (used for record-keeping and debugging).

    Provides the same information as SignalState but in a flatter,
    MA-centric structure suitable for database persistence.
    """

    train_id: str
    ma_end_m: float
    permitted_speed_mps: float
    target_speed_mps: float
    target_distance_m: float
    emergency_brake_required: bool
    reason: str | None = None
    source: str = "SELF_SIM"


@dataclass(frozen=True)
class SafetyEvent:
    """A safety-related incident detected by ATP or SafetyGuard."""

    sim_time_ms: int
    train_id: str
    event_type: str  # OVERSPEED / MA_OVERRUN / EMERGENCY_BRAKE / SIGNAL_VIOLATION
    severity: str  # WARN / CRITICAL
    action_taken: str
    detail: dict[str, Any] = field(default_factory=dict)
