from __future__ import annotations

import argparse
import json
import os
import select
import sys
import termios
import time
import tty
from pathlib import Path
from typing import Any

from app.core.clock import SimulationClock
from app.core.message_bus import MessageBus
from app.domain.control import VehicleInteractiveSession, run_ato_stop_demo
from app.domain.control.scenarios import MAX_HANDLE_LEVEL
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


def vehicle_console(args: argparse.Namespace) -> None:
    session = VehicleInteractiveSession(
        target_position_m=args.target_position,
        permitted_speed_mps=args.permitted_speed,
        dt_s=args.dt,
        expected_deceleration_mps2=args.expected_deceleration,
        stop_tolerance_m=args.stop_tolerance,
        train_id=args.train_id,
    )
    if not args.line_mode and sys.stdin.isatty() and sys.stdout.isatty():
        vehicle_live_console(session, args.refresh_interval)
        return

    show_prompt = sys.stdin.isatty()
    print("vehicle console: help, quit")
    _print_console_payload(session.status_payload(), as_json=args.json_lines)
    while True:
        try:
            line = input("vehicle> " if show_prompt else "")
        except EOFError:
            print()
            break
        command = line.strip().lower()
        if command in {"quit", "q", "exit"}:
            print("bye")
            break
        try:
            _print_console_payload(session.apply_command(line), as_json=args.json_lines)
        except Exception as exc:
            _print_console_payload({"ok": False, "error": str(exc)}, as_json=args.json_lines)


def vehicle_live_console(session: VehicleInteractiveSession, refresh_interval_s: float) -> None:
    if refresh_interval_s <= 0:
        raise ValueError("refresh_interval_s must be positive")

    handle_level = 0
    paused = False
    last_payload = session.status_payload()
    input_buffer = ""
    old_termios = termios.tcgetattr(sys.stdin)
    try:
        tty.setraw(sys.stdin.fileno())
        print("\x1b[?25l", end="")
        while True:
            input_buffer += _read_available_stdin()
            should_quit = False
            did_step = False
            while True:
                key, input_buffer = _pop_key(input_buffer)
                if key is None:
                    break
                if key in {"q", "Q", "\x03"}:
                    should_quit = True
                    break
                if key == "UP":
                    handle_level = min(MAX_HANDLE_LEVEL, handle_level + 1)
                elif key == "DOWN":
                    handle_level = max(-MAX_HANDLE_LEVEL, handle_level - 1)
                elif key in {" ", "0"}:
                    handle_level = 0
                elif key in {"r", "R"}:
                    last_payload = session.apply_command("reset")
                    handle_level = 0
                    did_step = True
                elif key in {"p", "P"}:
                    paused = not paused
                elif key in {"e", "E"}:
                    last_payload = session.apply_command("eb")
                    did_step = True
            if should_quit:
                break
            if not paused and not did_step:
                last_payload = session.apply_handle_level(handle_level)

            _render_live_console(last_payload, handle_level, paused)
            time.sleep(refresh_interval_s)
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_termios)
        print("\x1b[?25h\x1b[0m")


def _read_available_stdin() -> str:
    chunks: list[str] = []
    fd = sys.stdin.fileno()
    while True:
        readable, _, _ = select.select([fd], [], [], 0)
        if not readable:
            break
        chunks.append(os.read(fd, 1024).decode("utf-8", errors="ignore"))
    return "".join(chunks)


def _pop_key(buffer: str) -> tuple[str | None, str]:
    if not buffer:
        return None, buffer
    if buffer.startswith("\x1b"):
        if len(buffer) < 3:
            return None, buffer
        sequence = buffer[:3]
        if sequence == "\x1b[A":
            return "UP", buffer[3:]
        if sequence == "\x1b[B":
            return "DOWN", buffer[3:]
        return "ESC", buffer[1:]
    return buffer[0], buffer[1:]


def _render_live_console(payload: dict[str, Any], handle_level: int, paused: bool) -> None:
    state = "PAUSED" if paused else payload["status"]
    mode = _handle_mode(handle_level)
    command = payload.get("command")
    command_text = "cmd=-"
    if command:
        command_text = (
            f"cmd={command['mode']} T{command['tractionLevel']} B{command['brakeLevel']} "
            f"EB={command['emergencyBrake']} src={command['source']}"
        )
    lines = [
        "\x1b[H\x1b[J",
        f"vehicle-console {state} train={payload['trainId']} tick={payload['ticks']} t={payload['simTimeS']:.1f}s",
        f"handle={handle_level:+d} mode={mode}",
        _format_motion(payload),
        command_text,
        "keys: up/down handle, space coast, p pause, r reset, e eb, q quit",
    ]
    print("\n".join(lines), end="", flush=True)


def _handle_mode(handle_level: int) -> str:
    if handle_level > 0:
        return "TRACTION"
    if handle_level < 0:
        return "BRAKE"
    return "COAST"


def _format_motion(payload: dict[str, Any]) -> str:
    return (
        f"x={payload['positionM']:.3f}/{payload['targetPositionM']:.3f}m "
        f"err={payload['stopErrorM']:+.3f}m "
        f"v={payload['speedMps']:.3f}m/s "
        f"a={payload['accelerationMps2']:+.3f}m/s2 "
        f"e={payload['netEnergyKwh']:.6f}kWh "
        f"sw={payload['commandSwitches']}"
    )


def _print_console_payload(payload: dict[str, Any], as_json: bool = False) -> None:
    if as_json:
        _print_json(payload)
        return
    if not payload.get("ok", False):
        print(f"ERROR: {payload.get('error', 'unknown error')}")
        return
    if "commands" in payload:
        print(_format_commands(payload["commands"]))
        return
    print(_format_console_line(payload))


def _format_console_line(payload: dict[str, Any]) -> str:
    command = payload.get("command")
    command_text = "cmd=STATUS"
    if command:
        command_text = f"cmd={command['mode']} T{command['tractionLevel']} B{command['brakeLevel']}"
        if command["emergencyBrake"]:
            command_text += " EB"
    message = f" msg={payload['message']}" if "message" in payload else ""
    return (
        f"tick={payload['ticks']} "
        f"t={payload['simTimeS']:.1f}s "
        f"{command_text} "
        f"{_format_motion(payload)} "
        f"status={payload['status']}"
        f"{message}"
    )


def _format_commands(commands: list[str]) -> str:
    rows = ["commands:"]
    rows.extend(f"  {command}" for command in commands)
    return "\n".join(rows)


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

    console_parser = subparsers.add_parser("vehicle-console", help="Interactively control a single train")
    console_parser.add_argument("--train-id", default="T001", help="Train id")
    console_parser.add_argument("--target-position", type=float, default=200.0, help="Target stop position in meters")
    console_parser.add_argument("--permitted-speed", type=float, default=12.0, help="Permitted speed in m/s")
    console_parser.add_argument("--dt", type=float, default=0.1, help="Simulation tick length in seconds")
    console_parser.add_argument(
        "--expected-deceleration",
        type=float,
        default=0.6,
        help="ATO expected deceleration in m/s^2",
    )
    console_parser.add_argument("--stop-tolerance", type=float, default=1.0, help="Acceptable stop error in meters")
    console_parser.add_argument("--line-mode", action="store_true", help="Use blocking line-input console instead of live TTY mode")
    console_parser.add_argument("--json-lines", action="store_true", help="Print JSON payloads in line-input mode")
    console_parser.add_argument("--refresh-interval", type=float, default=0.1, help="Live console refresh interval in seconds")
    console_parser.set_defaults(func=vehicle_console)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
