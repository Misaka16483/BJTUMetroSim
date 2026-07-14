from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.engine import SimulationEngine


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a timetable through departure, turnback, return and storage acceptance."
    )
    parser.add_argument(
        "--scenario",
        type=Path,
        default=ROOT / "data" / "scenarios" / "line9_timetable_operation.json",
    )
    parser.add_argument("--max-duties", type=int, default=None)
    parser.add_argument("--tick-seconds", type=float, default=None)
    parser.add_argument("--wall-timeout-sec", type=float, default=600.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    engine = SimulationEngine.load_from_files(
        scenario_path=args.scenario,
        line_map_path=ROOT / "data" / "cache" / "line_map.json",
        stations_csv_path=ROOT / "data" / "line9" / "stations.csv",
    )
    operation_plan = engine.scenario.operation_plan
    if args.max_duties is not None:
        operation_plan = replace(operation_plan, max_duties=max(1, args.max_duties))
    tick_seconds = (
        max(0.05, float(args.tick_seconds))
        if args.tick_seconds is not None
        else engine.scenario.tick_seconds
    )
    engine.scenario = replace(
        engine.scenario,
        tick_seconds=tick_seconds,
        operation_plan=operation_plan,
    )
    engine.clock.tick_seconds = tick_seconds
    engine._snapshot_interval_ticks = max(1, round(5.0 / tick_seconds))

    timed_out = False
    try:
        engine.load()
        engine.clock.start()
        deadline = time.monotonic() + max(1.0, float(args.wall_timeout_sec))
        while True:
            engine._tick()
            state = engine.operation_plan_state()
            status = state["acceptance"]["status"]
            if status in {"PASSED", "FAILED"}:
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
        result = {
            "timedOut": timed_out,
            "simTimeMs": engine._absolute_sim_time_ms(),
            "planHash": state["planHash"],
            "experimentWindow": state["experimentWindow"],
            "profileWarmup": state["profileWarmup"],
            "acceptance": state["acceptance"],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if timed_out:
            return 3
        return 0 if status == "PASSED" else 2
    finally:
        engine.speed_profile_service.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
