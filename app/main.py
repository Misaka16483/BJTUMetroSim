from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.adapters.cab import MitsubishiPlcCabOutputFrameBuilder, MitsubishiPlcCabOutputState, MitsubishiPlcTcpClient
from app.adapters.hmi import NetworkScreenClient, NetworkScreenFrameBuilder, NetworkScreenState
from app.adapters.mmi import SignalScreenClient, SignalScreenFrameBuilder, SignalScreenState
from app.core.clock import SimulationClock
from app.core.message_bus import MessageBus
from app.domain.control import CabControlService, DriverInput, VehicleInteractiveSession, run_ato_stop_demo
from app.domain.vehicle import ControlCommand
from app.domain.line.services import LineMapRepository, TrackQueryService
from app.domain.operations.member_d_demo import Phase2MemberDDemoRunner
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


def plc_cab_monitor(args: argparse.Namespace) -> None:
    client = MitsubishiPlcTcpClient(host=args.host, port=args.port, timeout_s=args.timeout)
    control_service = CabControlService()
    max_frames = args.max_frames if args.max_frames > 0 else None
    try:
        with client:
            if not args.json_lines:
                print(f"plc cab monitor: {args.host}:{args.port}, max_frames={max_frames or 'forever'}")
            for sequence, driver_input in enumerate(
                client.iter_driver_inputs(train_id=args.train_id, max_frames=max_frames),
                start=1,
            ):
                command = control_service.command_from_driver_input(driver_input)
                payload = _plc_cab_payload(sequence, driver_input, command)
                if args.json_lines:
                    print(json.dumps(payload, ensure_ascii=False))
                else:
                    print(_format_plc_cab_line(payload))
    except (ConnectionError, OSError, RuntimeError) as exc:
        print(f"ERROR: PLC connection failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def plc_cab_send_status(args: argparse.Namespace) -> None:
    speed_cmps = None if args.speed_mps is None else int(round(args.speed_mps * 100))
    state = MitsubishiPlcCabOutputState(
        high_breaker_closed_light=args.high_breaker_closed,
        brake_release_fault_light=args.brake_release_fault,
        door_open_light=args.door_open,
        doors_closed_light=args.doors_closed,
        network_fault_light=args.network_fault,
        auto_turnback_available=args.auto_turnback_available,
        ato_available=args.ato_available,
        wash_mode_entered=args.wash_mode,
        ato_active=args.ato_active,
        auto_turnback_active=args.auto_turnback_active,
        vehicle_speed_cmps=speed_cmps,
    )
    frame = MitsubishiPlcCabOutputFrameBuilder().build(state)
    if args.dry_run:
        _print_frame_summary("plc-cab-status", frame)
        return
    with MitsubishiPlcTcpClient(host=args.host, port=args.port, timeout_s=args.timeout) as client:
        client.send_frame(frame)
    print(f"sent plc-cab-status bytes={len(frame)} to {args.host}:{args.port}")


def hmi_send_demo(args: argparse.Namespace) -> None:
    state = NetworkScreenState(
        curr_station_id=args.curr_station,
        next_station_id=args.next_station,
        end_station_id=args.end_station,
        speed_mps=args.speed_mps,
        acceleration_mps2=args.acceleration,
        speed_limit=args.speed_limit,
        level_pos=args.level_pos,
        run_mode=args.run_mode,
        train_no=args.train_no,
    )
    frame = NetworkScreenFrameBuilder().build(state)
    if args.dry_run:
        _print_frame_summary("hmi-network-screen", frame)
        return
    NetworkScreenClient(host=args.host, port=args.port, timeout_s=args.timeout).send_state(state)
    print(f"sent hmi-network-screen bytes={len(frame)} to {args.host}:{args.port}")


def mmi_send_demo(args: argparse.Namespace) -> None:
    state = SignalScreenState(
        curr_station_id=args.curr_station,
        next_station_id=args.next_station,
        end_station_id=args.end_station,
        speed_mps=args.speed_mps,
        acceleration_mps2=args.acceleration,
        speed_limit=args.speed_limit,
        mode=args.mode,
        pull_state=args.pull_state,
        brake_state=args.brake_state,
        urgency_stop_state=args.urgency_stop_state,
        train_no=args.train_no,
        next_station_distance_m=args.next_station_distance,
    )
    frame = SignalScreenFrameBuilder().build(state)
    if args.dry_run:
        _print_frame_summary("mmi-signal-screen", frame)
        return
    SignalScreenClient(host=args.host, port=args.port, timeout_s=args.timeout).send_state(state)
    print(f"sent mmi-signal-screen bytes={len(frame)} to {args.host}:{args.port}")


def vehicle_console(args: argparse.Namespace) -> None:
    session = VehicleInteractiveSession(
        target_position_m=args.target_position,
        permitted_speed_mps=args.permitted_speed,
        dt_s=args.dt,
        expected_deceleration_mps2=args.expected_deceleration,
        stop_tolerance_m=args.stop_tolerance,
        train_id=args.train_id,
    )
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


def _plc_cab_payload(sequence: int, driver_input: DriverInput, command: ControlCommand) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "trainId": driver_input.train_id,
        "source": driver_input.source,
        "handleMode": driver_input.handle_mode.value,
        "tractionPercent": driver_input.traction_percent,
        "brakePercent": driver_input.brake_percent,
        "emergencyBrake": driver_input.emergency_brake,
        "reportedSpeedMps": driver_input.reported_speed_mps,
        "command": {
            "tractionLevel": command.traction_level,
            "brakeLevel": command.brake_level,
            "emergencyBrake": command.emergency_brake,
            "source": command.source.value,
        },
    }


def _format_plc_cab_line(payload: dict[str, Any]) -> str:
    command = payload["command"]
    speed = payload["reportedSpeedMps"]
    speed_text = "-" if speed is None else f"{speed:.2f}m/s"
    return (
        f"seq={payload['sequence']} "
        f"train={payload['trainId']} "
        f"handle={payload['handleMode']} "
        f"tr={payload['tractionPercent']:.0f}% "
        f"br={payload['brakePercent']:.0f}% "
        f"speed={speed_text} "
        f"cmd=T{command['tractionLevel']} B{command['brakeLevel']} EB={command['emergencyBrake']}"
    )


def _print_frame_summary(name: str, frame: bytes) -> None:
    preview = frame[:32].hex(" ")
    print(f"{name} bytes={len(frame)} head={preview}")


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

    plc_parser = subparsers.add_parser("plc-cab-monitor", help="Read driver cab frames from the Mitsubishi PLC")
    plc_parser.add_argument("--host", default="192.168.100.123", help="PLC server IP address")
    plc_parser.add_argument("--port", type=int, default=8001, choices=[8001, 8002, 8003], help="PLC server TCP port")
    plc_parser.add_argument("--train-id", default="T001", help="Train id")
    plc_parser.add_argument("--timeout", type=float, default=3.0, help="TCP connect/read timeout in seconds")
    plc_parser.add_argument("--max-frames", type=int, default=0, help="Frames to read; 0 means forever")
    plc_parser.add_argument("--json-lines", action="store_true", help="Print one JSON object per PLC frame")
    plc_parser.set_defaults(func=plc_cab_monitor)

    plc_send_parser = subparsers.add_parser("plc-cab-send-status", help="Send one host-to-PLC cab status frame")
    plc_send_parser.add_argument("--host", default="192.168.100.123", help="PLC server IP address")
    plc_send_parser.add_argument("--port", type=int, default=8001, choices=[8001, 8002, 8003], help="PLC server TCP port")
    plc_send_parser.add_argument("--timeout", type=float, default=3.0, help="TCP connect/write timeout in seconds")
    plc_send_parser.add_argument("--speed-mps", type=float, default=None, help="Optional speed feedback; extends frame to 28 bytes")
    plc_send_parser.add_argument("--high-breaker-closed", action="store_true", help="Set high breaker closed light")
    plc_send_parser.add_argument("--brake-release-fault", action="store_true", help="Set brake release fault light")
    plc_send_parser.add_argument("--door-open", action="store_true", help="Set door open light")
    plc_send_parser.add_argument("--doors-closed", action="store_true", help="Set doors closed light")
    plc_send_parser.add_argument("--network-fault", action="store_true", help="Set network fault light")
    plc_send_parser.add_argument("--auto-turnback-available", action="store_true", help="Set auto turnback available flag")
    plc_send_parser.add_argument("--ato-available", action="store_true", help="Set ATO available flag")
    plc_send_parser.add_argument("--wash-mode", action="store_true", help="Set wash mode entered flag")
    plc_send_parser.add_argument("--ato-active", action="store_true", help="Set ATO active flag")
    plc_send_parser.add_argument("--auto-turnback-active", action="store_true", help="Set auto turnback active flag")
    plc_send_parser.add_argument("--dry-run", action="store_true", help="Print frame summary instead of connecting")
    plc_send_parser.set_defaults(func=plc_cab_send_status)

    hmi_parser = subparsers.add_parser("hmi-send-demo", help="Send one network screen HMI frame")
    hmi_parser.add_argument("--host", default="192.168.100.122", help="HMI server IP address")
    hmi_parser.add_argument("--port", type=int, default=8888, help="HMI server TCP port")
    hmi_parser.add_argument("--timeout", type=float, default=3.0, help="TCP connect/write timeout in seconds")
    hmi_parser.add_argument("--curr-station", type=int, default=0, help="Current station id")
    hmi_parser.add_argument("--next-station", type=int, default=0, help="Next station id")
    hmi_parser.add_argument("--end-station", type=int, default=0, help="End station id")
    hmi_parser.add_argument("--speed-mps", type=float, default=0.0, help="Speed in m/s")
    hmi_parser.add_argument("--acceleration", type=float, default=0.0, help="Acceleration in m/s^2")
    hmi_parser.add_argument("--speed-limit", type=int, default=0, help="Speed limit")
    hmi_parser.add_argument("--level-pos", type=int, default=0, help="Level position")
    hmi_parser.add_argument("--run-mode", type=int, default=0, help="Run mode byte")
    hmi_parser.add_argument("--train-no", type=int, default=0, help="Train number")
    hmi_parser.add_argument("--dry-run", action="store_true", help="Print frame summary instead of connecting")
    hmi_parser.set_defaults(func=hmi_send_demo)

    mmi_parser = subparsers.add_parser("mmi-send-demo", help="Send one signal screen MMI frame")
    mmi_parser.add_argument("--host", default="192.168.100.121", help="MMI server IP address")
    mmi_parser.add_argument("--port", type=int, default=9999, help="MMI server TCP port")
    mmi_parser.add_argument("--timeout", type=float, default=3.0, help="TCP connect/write timeout in seconds")
    mmi_parser.add_argument("--curr-station", type=int, default=0, help="Current station id")
    mmi_parser.add_argument("--next-station", type=int, default=0, help="Next station id")
    mmi_parser.add_argument("--end-station", type=int, default=0, help="End station id")
    mmi_parser.add_argument("--speed-mps", type=float, default=0.0, help="Speed in m/s")
    mmi_parser.add_argument("--acceleration", type=float, default=0.0, help="Acceleration in m/s^2")
    mmi_parser.add_argument("--speed-limit", type=int, default=0, help="Speed limit")
    mmi_parser.add_argument("--mode", type=int, default=0, help="Signal mode byte")
    mmi_parser.add_argument("--pull-state", type=int, default=0, help="Traction state")
    mmi_parser.add_argument("--brake-state", type=int, default=0, help="Brake state")
    mmi_parser.add_argument("--urgency-stop-state", type=int, default=0, help="Emergency brake state")
    mmi_parser.add_argument("--train-no", type=int, default=0, help="Train number")
    mmi_parser.add_argument("--next-station-distance", type=float, default=0.0, help="Distance to next station in meters")
    mmi_parser.add_argument("--dry-run", action="store_true", help="Print frame summary instead of connecting")
    mmi_parser.set_defaults(func=mmi_send_demo)

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
    console_parser.add_argument("--json-lines", action="store_true", help="Print JSON payloads")
    console_parser.set_defaults(func=vehicle_console)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
