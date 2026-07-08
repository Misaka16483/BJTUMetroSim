"""Train control service, ATP supervision and safety guard — Member C Phase 1.

TrainControlService
    Computes permitted speed, movement-authority endpoint and ATP
    emergency-brake conditions from static line data and the current
    train state.

SafetyGuard
    Filters a ControlCommand against safety constraints and returns
    the (possibly overridden) safe command.
"""

from __future__ import annotations

from typing import Any

from app.domain.line.services import TrackQueryService
from app.domain.signal.models import (
    ControlCommand,
    MovementAuthority,
    SafetyEvent,
    SignalState,
    TrainState,
)


# ---------------------------------------------------------------------------
# TrainControlService
# ---------------------------------------------------------------------------


class TrainControlService:
    """Phase 1 simplified ATP / signal supervision.

    Does **not** implement full CBTC — only:
    * static speed-limit supervision
    * temporary speed-limit placeholder
    * basic signal-aspect → speed mapping
    * simplified MA endpoint
    * overspeed emergency brake
    * MA-overrun emergency brake
    """

    def __init__(
        self,
        track_query: TrackQueryService,
        *,
        overspeed_tolerance_mps: float = 0.3,
        ma_tolerance_m: float = 0.5,
        yellow_speed_mps: float = 8.0,
        scenario_max_speed_mps: float = 22.22,
    ) -> None:
        self._track = track_query
        self.overspeed_tolerance_mps = overspeed_tolerance_mps
        self.ma_tolerance_m = ma_tolerance_m
        self.yellow_speed_mps = yellow_speed_mps
        self.scenario_max_speed_mps = scenario_max_speed_mps

    # -- public API ---------------------------------------------------------

    def compute_signal_state(
        self,
        train_state: TrainState,
        *,
        target_stop_point_m: float | None = None,
        forced_signal_aspect: str | None = None,
    ) -> SignalState:
        """Compute the full signal / safety picture for *train_state*.

        *target_stop_point_m* overrides TrainState.target_stop_point_m
        when the scenario wants a different stopping position.

        *forced_signal_aspect* allows the scenario to inject a specific
        signal aspect (e.g. ``"RED"``) for testing ATP behaviour.
        """
        permitted = self._compute_permitted_speed(train_state, forced_signal_aspect)
        ma_end = self._compute_ma_end(train_state, target_stop_point_m, forced_signal_aspect)
        emergency, reason = self._check_atp_conditions(train_state, permitted, ma_end)

        aspect = forced_signal_aspect or self._resolve_signal_aspect(train_state)
        target_distance = ma_end - train_state.position_m

        return SignalState(
            train_id=train_state.train_id,
            sim_time_ms=train_state.sim_time_ms,
            signal_aspect=aspect,
            permitted_speed_mps=permitted,
            movement_authority_end_m=ma_end,
            target_distance_m=max(target_distance, 0.0),
            emergency_brake_required=emergency,
            reason=reason,
        )

    def compute_movement_authority(
        self,
        train_state: TrainState,
        *,
        target_stop_point_m: float | None = None,
    ) -> MovementAuthority:
        """Return a detailed MA record (suitable for database persistence)."""
        signal = self.compute_signal_state(
            train_state,
            target_stop_point_m=target_stop_point_m,
        )
        return MovementAuthority(
            train_id=train_state.train_id,
            ma_end_m=signal.movement_authority_end_m,
            permitted_speed_mps=signal.permitted_speed_mps,
            target_speed_mps=0.0,
            target_distance_m=signal.target_distance_m,
            emergency_brake_required=signal.emergency_brake_required,
            reason=signal.reason,
        )

    # -- internal -----------------------------------------------------------

    def _compute_permitted_speed(
        self,
        train_state: TrainState,
        forced_aspect: str | None,
    ) -> float:
        """``min(static limit, scenario max, signal-aspect speed)``."""
        candidates: list[float] = [self.scenario_max_speed_mps]

        # static speed restriction from line data
        static = self._track.get_speed_limit(train_state.seg_id, train_state.offset_m)
        if static is not None and static.get("speedLimitMps") is not None:
            candidates.append(float(static["speedLimitMps"]))

        # signal-aspect speed cap
        aspect = forced_aspect or self._resolve_signal_aspect(train_state)
        aspect_speed = _ASPECT_SPEED.get(aspect)
        if aspect_speed is not None:
            candidates.append(aspect_speed)

        return min(candidates)

    def _compute_ma_end(
        self,
        train_state: TrainState,
        target_stop_point_m: float | None,
        forced_aspect: str | None,
    ) -> float:
        """Simplified MA endpoint.

        Phase 1 rule:
        * If the next signal is RED → MA = signal position
        * Else if a target stop point exists → MA = target stop point
        * Else → MA = a large sentinel (no effective limit)
        """
        aspect = forced_aspect or self._resolve_signal_aspect(train_state)

        # RED signal: train must stop before it
        if aspect == "RED":
            signal_pos = self._next_signal_position_m(train_state)
            if signal_pos is not None:
                return signal_pos

        # Target stop point (scenario override takes precedence)
        stop_point = target_stop_point_m or train_state.target_stop_point_m
        if stop_point is not None:
            return stop_point

        # No effective limit — use a sentinel far beyond any reasonable position
        return _NO_MA_LIMIT_M

    def _check_atp_conditions(
        self,
        train_state: TrainState,
        permitted_speed: float,
        ma_end: float,
    ) -> tuple[bool, str | None]:
        """Return ``(emergency_brake_required, reason)``."""
        # MA overrun (checked first — more severe)
        if train_state.position_m > ma_end + self.ma_tolerance_m:
            return True, "MA_OVERRUN"

        # Overspeed
        if train_state.speed_mps > permitted_speed + self.overspeed_tolerance_mps:
            return True, "OVERSPEED"

        return False, None

    def _resolve_signal_aspect(self, train_state: TrainState) -> str:
        """Return the aspect of the next signal ahead of the train.

        Phase 1: we cannot determine dynamic aspect without interlocking,
        so we return ``"GREEN"`` when a signal exists and ``"UNKNOWN"``
        otherwise.  Scenarios can override via *forced_signal_aspect*.
        """
        direction = _to_query_direction(train_state.direction)
        signal = self._track.get_next_signal(
            train_state.seg_id,
            train_state.offset_m,
            direction,
        )
        if signal is None or signal.get("id") is None:
            return "UNKNOWN"
        return "GREEN"

    def _next_signal_position_m(self, train_state: TrainState) -> float | None:
        """Return the absolute position (m) of the next signal, if any."""
        direction = _to_query_direction(train_state.direction)
        signal = self._track.get_next_signal(
            train_state.seg_id,
            train_state.offset_m,
            direction,
        )
        if signal is None:
            return None
        offset = signal.get("offsetM")
        if offset is None:
            return None
        # For signals on the *same* segment we can approximate the absolute
        # position as position_m + (signal_offset - train_offset).  This
        # is not millimetre-precise but sufficient for Phase 1 MA checks.
        return train_state.position_m + (float(offset) - train_state.offset_m)


# -- SafetyGuard ------------------------------------------------------------


class SafetyGuard:
    """Final safety filter applied to every ControlCommand before execution.

    Rules (priority order, first match wins):

    1.  SignalState.emergency_brake_required  → force emergency brake
    2.  Command already has emergency_brake    → keep it, zero traction
    3.  Speed >= permitted and command is traction → force coast (traction = 0)
    4.  Position > MA end                      → force emergency brake
    5.  Otherwise                               → pass through unchanged
    """

    # ------------------------------------------------------------------
    def filter_command(
        self,
        command: ControlCommand,
        train_state: TrainState,
        signal_state: SignalState,
    ) -> ControlCommand:
        # Rule 1 — ATP already demands emergency brake
        if signal_state.emergency_brake_required:
            return self._override(
                command,
                emergency_brake=True,
                reason=f"ATP_OVERRIDE: {signal_state.reason or 'safety constraint'}",
            )

        # Rule 2 — command itself carries emergency brake
        if command.emergency_brake:
            return self._override(
                command,
                traction_level=0.0,
                reason="EMERGENCY_BRAKE_ACTIVE",
            )

        # Rule 3 — overspeed while still commanding traction
        if (
            command.traction_level > 0
            and train_state.speed_mps >= signal_state.permitted_speed_mps
        ):
            return self._override(
                command,
                traction_level=0.0,
                reason="OVERSPEED: traction cut",
            )

        # Rule 4 — position has exceeded MA endpoint
        if train_state.position_m > signal_state.movement_authority_end_m:
            return self._override(
                command,
                emergency_brake=True,
                traction_level=0.0,
                reason="MA_OVERRUN: emergency brake",
            )

        # Rule 5 — pass through
        return command

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _override(
        command: ControlCommand,
        *,
        traction_level: float | None = None,
        brake_level: float | None = None,
        emergency_brake: bool | None = None,
        reason: str | None = None,
    ) -> ControlCommand:
        # When emergency brake is active, traction MUST be zero (design §13).
        effective_traction = (
            0.0
            if (emergency_brake or command.emergency_brake)
            else (command.traction_level if traction_level is None else traction_level)
        )
        return ControlCommand(
            train_id=command.train_id,
            sim_time_ms=command.sim_time_ms,
            source="ATP_OVERRIDE" if emergency_brake else "SAFETY_GUARD",
            traction_level=effective_traction,
            brake_level=command.brake_level if brake_level is None else brake_level,
            emergency_brake=(
                command.emergency_brake if emergency_brake is None else emergency_brake
            ),
            reason=reason or command.reason,
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

# Signal aspect → speed cap.  ``None`` means "no additional cap".
# Phase 1: UNKNOWN defaults to no cap (Phase 2 should tighten this once
# interlocking provides dynamic signal state).
_ASPECT_SPEED: dict[str, float | None] = {
    "GREEN": None,
    "YELLOW": 8.0,
    "RED": 0.0,
    "UNKNOWN": None,
}

# Sentinel value for "no effective MA limit".
_NO_MA_LIMIT_M = 1_000_000.0


def _to_query_direction(direction: str) -> str:
    """Convert train direction to TrackQueryService direction string."""
    return "forward" if direction.upper() in ("FORWARD", "UP") else "backward"


def collect_safety_events(
    signal_state: SignalState,
    guard_result: ControlCommand,
) -> list[SafetyEvent]:
    """Build SafetyEvent records from a signal-state / guard-result pair."""
    events: list[SafetyEvent] = []
    sim_time_ms = signal_state.sim_time_ms
    train_id = signal_state.train_id

    if signal_state.emergency_brake_required:
        events.append(
            SafetyEvent(
                sim_time_ms=sim_time_ms,
                train_id=train_id,
                event_type=signal_state.reason or "EMERGENCY_BRAKE",
                severity="CRITICAL",
                action_taken="EMERGENCY_BRAKE",
                detail={"reason": signal_state.reason},
            )
        )

    if guard_result.source in ("ATP_OVERRIDE", "SAFETY_GUARD"):
        events.append(
            SafetyEvent(
                sim_time_ms=sim_time_ms,
                train_id=train_id,
                event_type="COMMAND_OVERRIDE",
                severity="WARN",
                action_taken=f"source={guard_result.source}",
                detail={"reason": guard_result.reason},
            )
        )

    return events
