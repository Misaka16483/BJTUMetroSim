from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.core.clock import SimulationClock
from app.core.message_bus import MessageBus
from app.domain.control import run_ato_stop_demo
from app.domain.line.services import LineMapRepository, TrackQueryService
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


def vehicle_demo(args: argparse.Namespace) -> None:
    result = run_ato_stop_demo(
        target_position_m=args.target_position,
        permitted_speed_mps=args.permitted_speed,
        dt_s=args.dt,
        max_ticks=args.max_ticks,
        expected_deceleration_mps2=args.expected_deceleration,
        stop_tolerance_m=args.stop_tolerance,
        train_id=args.train_id,
    )
    _print_json(result.to_dict(include_history=args.include_history))


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

    vehicle_parser = subparsers.add_parser("vehicle-demo", help="Run a single-train ATO stopping demo")
    vehicle_parser.add_argument("--train-id", default="T001", help="Train id")
    vehicle_parser.add_argument("--target-position", type=float, default=200.0, help="Target stop position in meters")
    vehicle_parser.add_argument("--permitted-speed", type=float, default=12.0, help="Permitted speed in m/s")
    vehicle_parser.add_argument("--dt", type=float, default=1.0, help="Simulation tick length in seconds")
    vehicle_parser.add_argument("--max-ticks", type=int, default=120, help="Maximum simulation ticks")
    vehicle_parser.add_argument(
        "--expected-deceleration",
        type=float,
        default=0.6,
        help="ATO expected deceleration in m/s^2",
    )
    vehicle_parser.add_argument("--stop-tolerance", type=float, default=1.0, help="Acceptable stop error in meters")
    vehicle_parser.add_argument("--include-history", action="store_true", help="Include per-tick state history")
    vehicle_parser.set_defaults(func=vehicle_demo)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
