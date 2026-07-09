from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.core.clock import SimulationClock
from app.core.message_bus import MessageBus
from app.domain.line.services import LineMapRepository, TrackQueryService
from app.domain.operations.member_d_demo import Phase2MemberDDemoRunner
from app.domain.operations.phase0_member_d_demo import Phase0MemberDDemoRunner
from app.domain.operations.phase1_member_d_demo import Phase1MemberDDemoRunner
from app.domain.operations.phase2_member_d_full_demo import Phase2MemberDFullDemoRunner
from app.domain.signal.models import ControlCommand, TrainState
from app.domain.signal.services import SafetyGuard, TrainControlService, collect_safety_events
from app.infra.excel_importer import LineDataImporter, validate_line_map
from app.infra.recorder import RunRecorder


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def import_line(args: argparse.Namespace) -> None:
    importer = LineDataImporter()
    line_map = importer.import_file(args.source)
    outputs = importer.write_cache(line_map, args.cache_dir)
    validation = line_map.get("validation", {})
    _print_json(
        {
            "ok": validation.get("ok", False),
            "lineMap": str(outputs["line_map"]),
            "report": str(outputs["report"]),
            "counts": {
                "Seg表": line_map["counts"].get("Seg表"),
                "信号机表": line_map["counts"].get("信号机表"),
                "站台表": line_map["counts"].get("站台表"),
                "进路表": line_map["counts"].get("进路表"),
            },
            "summary": validation.get("summary", {}),
        }
    )


def validate_line(args: argparse.Namespace) -> None:
    line_map = LineMapRepository(args.cache).load()
    report = validate_line_map(line_map).to_dict()
    _print_json(report)


def query_line(args: argparse.Namespace) -> None:
    line_map = LineMapRepository(args.cache).load()
    service = TrackQueryService(line_map)
    payload = {
        "segment": service.get_segment(args.seg_id),
        "nextSegments": service.get_next_segments(args.seg_id, args.direction),
        "speedLimit": service.get_speed_limit(args.seg_id, args.offset),
        "gradient": service.get_gradient(args.seg_id, args.offset),
        "nearestPlatform": service.get_nearest_platform(args.seg_id, args.offset, args.direction),
        "nextSignal": service.get_next_signal(args.seg_id, args.offset, args.direction),
    }
    _print_json(payload)


def clock_demo(args: argparse.Namespace) -> None:
    clock = SimulationClock(tick_seconds=args.tick_seconds)
    bus = MessageBus()
    events: list[dict[str, Any]] = []

    def on_tick(tick: int, sim_time: float) -> None:
        envelope = bus.publish(
            "clock.tick",
            {"tick": tick, "simTimeSeconds": sim_time},
            source="clock-demo",
            tick=tick,
        )
        events.append({"sequence": envelope.sequence, **envelope.payload})

    clock.load()
    clock.start()
    clock.run_for_ticks(args.ticks, [on_tick])
    clock.pause()
    paused_tick = clock.current_tick
    clock.resume()
    clock.step([on_tick])
    clock.stop()
    _print_json(
        {
            "state": clock.state.value,
            "pausedAtTick": paused_tick,
            "finalTick": clock.current_tick,
            "events": events,
        }
    )


def bus_demo(args: argparse.Namespace) -> None:
    bus = MessageBus()
    recorder = RunRecorder(Path(args.output_dir) / "phase0_demo.sqlite")
    try:
        run_id = recorder.start_run("phase0-bus-demo", {"purpose": "MessageBus acceptance"})
        envelope = bus.publish(
            "train.state",
            {"trainId": "T001", "segmentId": 13, "offsetM": 0.0, "speedMps": 0.0},
            source="bus-demo",
            tick=1,
        )
        recorder.record_event(run_id, envelope.topic, envelope.payload, tick=envelope.tick)
        latest = bus.latest("train.state")
        _print_json(
            {
                "publishedSequence": envelope.sequence,
                "latest": latest.payload if latest else None,
                "historySize": len(bus.history("train.state")),
                "recordDb": str(recorder.db_path),
            }
        )
    finally:
        recorder.close()


def member_d_demo(args: argparse.Namespace) -> None:
    runner = Phase2MemberDDemoRunner(Path(args.output_dir) / "phase2_member_d_demo.sqlite")
    _print_json(runner.run())


def phase0_member_d_demo(args: argparse.Namespace) -> None:
    runner = Phase0MemberDDemoRunner(Path(args.output_dir) / "phase0_member_d_demo.sqlite")
    _print_json(runner.run())


def phase1_member_d_demo(args: argparse.Namespace) -> None:
    runner = Phase1MemberDDemoRunner(Path(args.output_dir) / "phase1_member_d_demo.sqlite")
    _print_json(runner.run())


def phase2_member_d_full_demo(args: argparse.Namespace) -> None:
    runner = Phase2MemberDFullDemoRunner(Path(args.output_dir) / "phase2_member_d_full_demo.sqlite")
    _print_json(runner.run())


def signal_demo(args: argparse.Namespace) -> None:
    """Phase 1 signal / ATP / safety-guard demo using real line data."""
    line_map = LineMapRepository(args.cache).load()
    track = TrackQueryService(line_map)
    tcs = TrainControlService(
        track,
        scenario_max_speed_mps=args.scenario_max_speed,
        overspeed_tolerance_mps=args.overspeed_tolerance,
        yellow_speed_mps=args.yellow_speed,
    )
    guard = SafetyGuard()

    # Build a handful of representative train states
    scenarios: list[dict[str, Any]] = [
        {
            "label": "normal-cruise",
            "train": TrainState(
                train_id="T001", sim_time_ms=60_000, seg_id=13, offset_m=30.0,
                position_m=400.0, speed_mps=12.0, target_stop_point_m=1660.0,
                distance_to_target_m=1260.0,
            ),
            "command": ControlCommand(
                train_id="T001", sim_time_ms=60_000, source="ATO",
                traction_level=2.0,
            ),
        },
        {
            "label": "approaching-station-braking",
            "train": TrainState(
                train_id="T001", sim_time_ms=120_000, seg_id=13, offset_m=100.0,
                position_m=1550.0, speed_mps=6.0, target_stop_point_m=1660.0,
                distance_to_target_m=110.0,
            ),
            "command": ControlCommand(
                train_id="T001", sim_time_ms=120_000, source="ATO",
                brake_level=3.0, reason="Approaching target stop point",
            ),
        },
        {
            "label": "overspeed-violation",
            "train": TrainState(
                train_id="T001", sim_time_ms=90_000, seg_id=13, offset_m=60.0,
                position_m=800.0, speed_mps=18.0, target_stop_point_m=1660.0,
                distance_to_target_m=860.0,
            ),
            "command": ControlCommand(
                train_id="T001", sim_time_ms=90_000, source="ATO",
                traction_level=2.0,
            ),
        },
        {
            "label": "ma-overrun",
            "train": TrainState(
                train_id="T001", sim_time_ms=150_000, seg_id=13, offset_m=120.0,
                position_m=1670.0, speed_mps=1.0, target_stop_point_m=1660.0,
                distance_to_target_m=-10.0,
            ),
            "command": ControlCommand(
                train_id="T001", sim_time_ms=150_000, source="ATO",
                brake_level=1.0,
            ),
        },
        {
            "label": "red-signal-ahead",
            "train": TrainState(
                train_id="T001", sim_time_ms=70_000, seg_id=13, offset_m=50.0,
                position_m=600.0, speed_mps=14.0, target_stop_point_m=1660.0,
                distance_to_target_m=1060.0,
            ),
            "command": ControlCommand(
                train_id="T001", sim_time_ms=70_000, source="ATO",
                traction_level=3.0,
            ),
        },
    ]

    results: list[dict[str, Any]] = []
    for scenario in scenarios:
        train: TrainState = scenario["train"]
        command: ControlCommand = scenario["command"]

        forced_aspect = "RED" if scenario["label"] == "red-signal-ahead" else None
        signal_state = tcs.compute_signal_state(train, forced_signal_aspect=forced_aspect)
        safe_command = guard.filter_command(command, train, signal_state)
        events = collect_safety_events(signal_state, safe_command)

        results.append({
            "label": scenario["label"],
            "input": {
                "positionM": train.position_m,
                "speedMps": train.speed_mps,
                "targetStopPointM": train.target_stop_point_m,
                "commandSource": command.source,
                "tractionLevel": command.traction_level,
                "brakeLevel": command.brake_level,
            },
            "signalState": {
                "aspect": signal_state.signal_aspect,
                "permittedSpeedMps": signal_state.permitted_speed_mps,
                "maEndM": signal_state.movement_authority_end_m,
                "targetDistanceM": signal_state.target_distance_m,
                "emergencyBrakeRequired": signal_state.emergency_brake_required,
                "reason": signal_state.reason,
            },
            "safeCommand": {
                "source": safe_command.source,
                "tractionLevel": safe_command.traction_level,
                "brakeLevel": safe_command.brake_level,
                "emergencyBrake": safe_command.emergency_brake,
                "reason": safe_command.reason,
            },
            "safetyEvents": [
                {"type": e.event_type, "severity": e.severity, "action": e.action_taken}
                for e in events
            ],
        })

    _print_json({
        "phase": 1,
        "module": "signal-control",
        "member": "C",
        "cache": str(Path(args.cache).resolve()),
        "scenarios": results,
    })


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rail transit simulation Phase 0 CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser("import-line", help="Import Excel line data to JSON cache")
    import_parser.add_argument("--source", required=True, help="Path to .xls/.xlsx line data workbook")
    import_parser.add_argument("--cache-dir", default="data/cache", help="Cache output directory")
    import_parser.set_defaults(func=import_line)

    validate_parser = subparsers.add_parser("validate-line", help="Validate cached line_map.json")
    validate_parser.add_argument("--cache", default="data/cache/line_map.json", help="Path to line_map.json")
    validate_parser.set_defaults(func=validate_line)

    query_parser = subparsers.add_parser("query-line", help="Query topology and static line attributes")
    query_parser.add_argument("--cache", default="data/cache/line_map.json", help="Path to line_map.json")
    query_parser.add_argument("--seg-id", type=int, required=True, help="Segment id")
    query_parser.add_argument("--offset", type=float, default=0.0, help="Offset in meters")
    query_parser.add_argument(
        "--direction",
        choices=["forward", "backward"],
        default="forward",
        help="Logical query direction",
    )
    query_parser.set_defaults(func=query_line)

    clock_parser = subparsers.add_parser("clock-demo", help="Run a minimal simulation clock demo")
    clock_parser.add_argument("--ticks", type=int, default=3, help="Ticks before pause/resume")
    clock_parser.add_argument("--tick-seconds", type=float, default=1.0, help="Seconds per tick")
    clock_parser.set_defaults(func=clock_demo)

    bus_parser = subparsers.add_parser("bus-demo", help="Run a minimal message bus and recorder demo")
    bus_parser.add_argument("--output-dir", default="outputs/runs", help="Recorder output directory")
    bus_parser.set_defaults(func=bus_demo)

    member_d_parser = subparsers.add_parser(
        "member-d-demo",
        help="Run Phase 2 member D passenger, dispatch and self-simulated power demo",
    )
    member_d_parser.add_argument("--output-dir", default="outputs/runs", help="Recorder output directory")
    member_d_parser.set_defaults(func=member_d_demo)

    phase0_member_d_parser = subparsers.add_parser(
        "phase0-member-d-demo",
        help="Phase 0: output default station/power states and metric structure for member D",
    )
    phase0_member_d_parser.add_argument("--output-dir", default="outputs/runs", help="Recorder output directory")
    phase0_member_d_parser.set_defaults(func=phase0_member_d_demo)

    phase1_member_d_parser = subparsers.add_parser(
        "phase1-member-d-demo",
        help="Phase 1: energy estimation and station stop judgment for member D",
    )
    phase1_member_d_parser.add_argument("--output-dir", default="outputs/runs", help="Recorder output directory")
    phase1_member_d_parser.set_defaults(func=phase1_member_d_demo)

    phase2_full_parser = subparsers.add_parser(
        "phase2-member-d-full-demo",
        help="Phase 2: passenger-dispatch-power demo across all 13 stations on Line 9",
    )
    phase2_full_parser.add_argument("--output-dir", default="outputs/runs", help="Recorder output directory")
    phase2_full_parser.set_defaults(func=phase2_member_d_full_demo)

    signal_parser = subparsers.add_parser(
        "signal-demo",
        help="Run Phase 1 member C signal, ATP and safety guard demo",
    )
    signal_parser.add_argument("--cache", default="data/cache/line_map.json", help="Path to line_map.json")
    signal_parser.add_argument(
        "--scenario-max-speed", type=float, default=22.22,
        help="Scenario maximum speed in m/s (default: 22.22 = 80 km/h)",
    )
    signal_parser.add_argument(
        "--overspeed-tolerance", type=float, default=0.3,
        help="Overspeed tolerance in m/s (default: 0.3)",
    )
    signal_parser.add_argument(
        "--yellow-speed", type=float, default=8.0,
        help="Yellow aspect speed limit in m/s (default: 8.0)",
    )
    signal_parser.set_defaults(func=signal_demo)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
