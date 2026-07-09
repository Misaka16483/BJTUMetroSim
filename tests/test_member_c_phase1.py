"""Phase 1 acceptance tests for member C — signal, ATP and safety guard."""

from __future__ import annotations

import unittest
from typing import Any

from app.domain.signal.models import ControlCommand, SignalState, TrainState
from app.domain.signal.services import SafetyGuard, TrainControlService, collect_safety_events


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_JsonDict = dict[str, Any]


def _fake_track_query(speed_limits: _JsonDict | None = None, signals: _JsonDict | None = None) -> Any:
    """Build a lightweight stub that quacks like TrackQueryService.

    Returns an object whose methods return canned data, so tests don't
    need the real 200 MB line_map.json.
    """

    class _Stub:
        @staticmethod
        def get_speed_limit(seg_id: int, offset_m: float) -> _JsonDict | None:
            return speed_limits

        @staticmethod
        def get_next_signal(seg_id: int, offset_m: float, direction: str) -> _JsonDict | None:
            return signals

    return _Stub()


def _make_train(**overrides: Any) -> TrainState:
    defaults: dict[str, Any] = {
        "train_id": "T001",
        "sim_time_ms": 120_000,
        "seg_id": 13,
        "offset_m": 30.0,
        "position_m": 343.0,
        "speed_mps": 12.0,
        "target_stop_point_m": 1500.0,
    }
    defaults.update(overrides)
    return TrainState(**defaults)


def _make_command(**overrides: Any) -> ControlCommand:
    defaults: dict[str, Any] = {
        "train_id": "T001",
        "sim_time_ms": 120_000,
        "source": "ATO",
        "traction_level": 3.0,
        "brake_level": 0.0,
        "emergency_brake": False,
    }
    defaults.update(overrides)
    return ControlCommand(**defaults)


# ---------------------------------------------------------------------------
# TrainControlService
# ---------------------------------------------------------------------------


class TrainControlServicePermittedSpeedTests(unittest.TestCase):
    def test_respects_static_limit(self) -> None:
        """Static limit 13.33 m/s → permitted = 13.33 (capped)."""
        svc = TrainControlService(
            _fake_track_query(
                speed_limits={"speedLimitMps": 13.33},
            ),
            scenario_max_speed_mps=22.22,
        )
        train = _make_train(speed_mps=8.0)
        state = svc.compute_signal_state(train)
        self.assertEqual(state.permitted_speed_mps, 13.33)

    def test_capped_by_scenario_max(self) -> None:
        """Scenario max 16.67 beats static 22.22."""
        svc = TrainControlService(
            _fake_track_query(
                speed_limits={"speedLimitMps": 22.22},
            ),
            scenario_max_speed_mps=16.67,
        )
        train = _make_train(speed_mps=8.0)
        state = svc.compute_signal_state(train)
        self.assertEqual(state.permitted_speed_mps, 16.67)

    def test_yellow_signal_caps_speed(self) -> None:
        """YELLOW signal → permitted = min(static, yellow=8.0)."""
        svc = TrainControlService(
            _fake_track_query(
                speed_limits={"speedLimitMps": 22.22},
                signals={"id": 5, "offsetM": 100.0},
            ),
            scenario_max_speed_mps=22.22,
            yellow_speed_mps=8.0,
        )
        train = _make_train(speed_mps=7.0)
        state = svc.compute_signal_state(train, forced_signal_aspect="YELLOW")
        self.assertEqual(state.permitted_speed_mps, 8.0)

    def test_red_signal_speed_zero(self) -> None:
        """RED signal → permitted = 0."""
        svc = TrainControlService(
            _fake_track_query(
                speed_limits={"speedLimitMps": 22.22},
            ),
            scenario_max_speed_mps=22.22,
        )
        train = _make_train()
        state = svc.compute_signal_state(train, forced_signal_aspect="RED")
        self.assertEqual(state.permitted_speed_mps, 0.0)


class TrainControlServiceMAEndpointTests(unittest.TestCase):
    def test_ma_end_is_target_stop_point(self) -> None:
        """No red signal → MA = target stop point."""
        svc = TrainControlService(
            _fake_track_query(signals={"id": 5, "offsetM": 100.0}),
        )
        train = _make_train(target_stop_point_m=1500.0)
        state = svc.compute_signal_state(train)
        self.assertEqual(state.movement_authority_end_m, 1500.0)

    def test_ma_end_is_red_signal_position(self) -> None:
        """RED signal ahead → MA = signal position (before target)."""
        svc = TrainControlService(
            _fake_track_query(signals={"id": 5, "offsetM": 80.0}),
        )
        train = _make_train(offset_m=30.0, position_m=343.0, target_stop_point_m=1500.0)
        state = svc.compute_signal_state(train, forced_signal_aspect="RED")
        # signal at offset 80, train at offset 30 → signal is 50 m ahead
        expected = 343.0 + (80.0 - 30.0)
        self.assertEqual(state.movement_authority_end_m, expected)

    def test_ma_end_sentinel_when_no_target(self) -> None:
        """No target stop point and no red → MA = large sentinel."""
        svc = TrainControlService(_fake_track_query())
        train = _make_train(target_stop_point_m=None)
        state = svc.compute_signal_state(train)
        self.assertGreater(state.movement_authority_end_m, 100_000.0)


class TrainControlServiceATPTests(unittest.TestCase):
    def test_normal_driving_no_emergency(self) -> None:
        """Train within limits → no emergency brake."""
        svc = TrainControlService(
            _fake_track_query(speed_limits={"speedLimitMps": 16.67}),
            scenario_max_speed_mps=16.67,
        )
        train = _make_train(speed_mps=12.0, position_m=500.0, target_stop_point_m=1500.0)
        state = svc.compute_signal_state(train)
        self.assertFalse(state.emergency_brake_required)
        self.assertIsNone(state.reason)

    def test_overspeed_triggers_emergency(self) -> None:
        """Speed 18 m/s > permitted 13.33 + tolerance 0.3 → emergency."""
        svc = TrainControlService(
            _fake_track_query(speed_limits={"speedLimitMps": 13.33}),
            scenario_max_speed_mps=13.33,
            overspeed_tolerance_mps=0.3,
        )
        train = _make_train(speed_mps=18.0)
        state = svc.compute_signal_state(train)
        self.assertTrue(state.emergency_brake_required)
        self.assertEqual(state.reason, "OVERSPEED")

    def test_overspeed_within_tolerance_no_emergency(self) -> None:
        """Speed 13.5, permitted 13.33, tolerance 0.3 → no emergency."""
        svc = TrainControlService(
            _fake_track_query(speed_limits={"speedLimitMps": 13.33}),
            scenario_max_speed_mps=13.33,
            overspeed_tolerance_mps=0.3,
        )
        train = _make_train(speed_mps=13.5)
        state = svc.compute_signal_state(train)
        self.assertFalse(state.emergency_brake_required)

    def test_ma_overrun_triggers_emergency(self) -> None:
        """Position 1510 > MA 1500 + tolerance 0.5 → emergency."""
        svc = TrainControlService(
            _fake_track_query(),
            ma_tolerance_m=0.5,
        )
        train = _make_train(position_m=1510.0, target_stop_point_m=1500.0)
        state = svc.compute_signal_state(train)
        self.assertTrue(state.emergency_brake_required)
        self.assertEqual(state.reason, "MA_OVERRUN")

    def test_ma_overrun_checked_before_overspeed(self) -> None:
        """When both violations exist, MA_OVERRUN takes priority."""
        svc = TrainControlService(
            _fake_track_query(speed_limits={"speedLimitMps": 13.33}),
            scenario_max_speed_mps=13.33,
            overspeed_tolerance_mps=0.3,
            ma_tolerance_m=0.5,
        )
        train = _make_train(speed_mps=20.0, position_m=1510.0, target_stop_point_m=1500.0)
        state = svc.compute_signal_state(train)
        self.assertTrue(state.emergency_brake_required)
        self.assertEqual(state.reason, "MA_OVERRUN")


class TrainControlServiceMovementAuthorityTests(unittest.TestCase):
    def test_ma_record_matches_signal_state(self) -> None:
        svc = TrainControlService(
            _fake_track_query(speed_limits={"speedLimitMps": 16.67}),
            scenario_max_speed_mps=16.67,
        )
        train = _make_train(speed_mps=10.0, position_m=500.0, target_stop_point_m=1500.0)
        ma = svc.compute_movement_authority(train)
        self.assertEqual(ma.ma_end_m, 1500.0)
        self.assertEqual(ma.permitted_speed_mps, 16.67)
        self.assertEqual(ma.target_distance_m, 1000.0)
        self.assertFalse(ma.emergency_brake_required)


# ---------------------------------------------------------------------------
# SafetyGuard
# ---------------------------------------------------------------------------


class SafetyGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.guard = SafetyGuard()

    def test_passes_normal_command(self) -> None:
        """No safety risk → command passes through unchanged."""
        train = _make_train(speed_mps=10.0, position_m=500.0)
        signal = SignalState(
            train_id="T001",
            sim_time_ms=120_000,
            signal_aspect="GREEN",
            permitted_speed_mps=16.67,
            movement_authority_end_m=1500.0,
            target_distance_m=1000.0,
            emergency_brake_required=False,
        )
        cmd = _make_command(traction_level=2.0)
        result = self.guard.filter_command(cmd, train, signal)
        self.assertEqual(result.traction_level, 2.0)
        self.assertFalse(result.emergency_brake)
        self.assertEqual(result.source, "ATO")

    def test_overrides_on_atp_emergency(self) -> None:
        """ATP demands emergency → force emergency brake regardless."""
        train = _make_train()
        signal = SignalState(
            train_id="T001",
            sim_time_ms=120_000,
            signal_aspect="RED",
            permitted_speed_mps=0.0,
            movement_authority_end_m=500.0,
            target_distance_m=100.0,
            emergency_brake_required=True,
            reason="OVERSPEED",
        )
        cmd = _make_command(traction_level=3.0)
        result = self.guard.filter_command(cmd, train, signal)
        self.assertTrue(result.emergency_brake)
        self.assertEqual(result.traction_level, 0.0)
        self.assertIn("ATP_OVERRIDE", result.source)

    def test_preserves_existing_emergency_brake(self) -> None:
        """Command already has emergency brake → keep it, zero traction."""
        train = _make_train()
        signal = SignalState(
            train_id="T001",
            sim_time_ms=120_000,
            signal_aspect="GREEN",
            permitted_speed_mps=16.67,
            movement_authority_end_m=1500.0,
            target_distance_m=1000.0,
            emergency_brake_required=False,
        )
        cmd = _make_command(emergency_brake=True, traction_level=2.0)
        result = self.guard.filter_command(cmd, train, signal)
        self.assertTrue(result.emergency_brake)
        self.assertEqual(result.traction_level, 0.0)

    def test_cuts_traction_when_overspeed(self) -> None:
        """Speed at limit + still commanding traction → cut to coast."""
        train = _make_train(speed_mps=16.67)
        signal = SignalState(
            train_id="T001",
            sim_time_ms=120_000,
            signal_aspect="GREEN",
            permitted_speed_mps=16.67,
            movement_authority_end_m=1500.0,
            target_distance_m=1000.0,
            emergency_brake_required=False,
        )
        cmd = _make_command(traction_level=3.0)
        result = self.guard.filter_command(cmd, train, signal)
        self.assertEqual(result.traction_level, 0.0)
        self.assertFalse(result.emergency_brake)
        self.assertIn("OVERSPEED", result.reason or "")

    def test_allows_brake_when_overspeed(self) -> None:
        """Overspeed but already braking → allow the brake command."""
        train = _make_train(speed_mps=17.0)
        signal = SignalState(
            train_id="T001",
            sim_time_ms=120_000,
            signal_aspect="GREEN",
            permitted_speed_mps=16.67,
            movement_authority_end_m=1500.0,
            target_distance_m=1000.0,
            emergency_brake_required=False,
        )
        cmd = _make_command(traction_level=0.0, brake_level=4.0)
        result = self.guard.filter_command(cmd, train, signal)
        self.assertEqual(result.brake_level, 4.0)
        self.assertEqual(result.traction_level, 0.0)

    def test_ma_overrun_forces_emergency_brake(self) -> None:
        """Position past MA → force emergency brake."""
        train = _make_train(position_m=1600.0)
        signal = SignalState(
            train_id="T001",
            sim_time_ms=120_000,
            signal_aspect="GREEN",
            permitted_speed_mps=16.67,
            movement_authority_end_m=1500.0,
            target_distance_m=0.0,
            emergency_brake_required=False,
        )
        cmd = _make_command(traction_level=1.0)
        result = self.guard.filter_command(cmd, train, signal)
        self.assertTrue(result.emergency_brake)
        self.assertIn("MA_OVERRUN", result.reason or "")


# ---------------------------------------------------------------------------
# collect_safety_events
# ---------------------------------------------------------------------------


class CollectSafetyEventsTests(unittest.TestCase):
    def test_no_events_when_all_clear(self) -> None:
        signal = SignalState(
            train_id="T001",
            sim_time_ms=120_000,
            signal_aspect="GREEN",
            permitted_speed_mps=16.67,
            movement_authority_end_m=1500.0,
            target_distance_m=1000.0,
            emergency_brake_required=False,
        )
        cmd = _make_command(source="ATO")
        events = collect_safety_events(signal, cmd)
        self.assertEqual(len(events), 0)

    def test_critical_event_on_emergency_brake(self) -> None:
        signal = SignalState(
            train_id="T001",
            sim_time_ms=120_000,
            signal_aspect="RED",
            permitted_speed_mps=0.0,
            movement_authority_end_m=500.0,
            target_distance_m=0.0,
            emergency_brake_required=True,
            reason="OVERSPEED",
        )
        cmd = _make_command(source="ATP_OVERRIDE", emergency_brake=True, traction_level=0.0)
        events = collect_safety_events(signal, cmd)
        self.assertGreaterEqual(len(events), 1)
        critical = [e for e in events if e.severity == "CRITICAL"]
        self.assertEqual(len(critical), 1)
        self.assertEqual(critical[0].event_type, "OVERSPEED")


if __name__ == "__main__":
    unittest.main()
