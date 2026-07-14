from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.domain.power.trajectory import validate_trajectory_frames
from tools.run_timetable_power_experiment import (
    DEFAULT_SCENARIO,
    capture_timetable_trajectory,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture one timetable baseline and print compact failure diagnostics."
    )
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--max-duties", type=int, default=3)
    parser.add_argument("--capture-seconds", type=int, default=900)
    parser.add_argument("--tick-seconds", type=float, default=0.5)
    parser.add_argument("--wall-timeout-sec", type=float, default=600.0)
    parser.add_argument("--issue-limit", type=int, default=20)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    frames, metadata = capture_timetable_trajectory(
        scenario_path=args.scenario,
        max_duties=args.max_duties,
        capture_seconds=args.capture_seconds,
        tick_seconds=args.tick_seconds,
        wall_timeout_sec=args.wall_timeout_sec,
        timing_candidate={
            "departureSpreadSec": 0.0,
            "tractionTimingSec": 0.0,
            "brakeTimingSec": 0.0,
        },
    )
    validation = validate_trajectory_frames(frames, allow_roster_changes=False)
    frames_by_time = {frame.sim_time_ms: frame for frame in frames}
    issue_samples = []
    for issue in validation.issues[: args.issue_limit]:
        frame = frames_by_time.get(issue.sim_time_ms)
        sample = next(
            (
                item for item in frame.samples
                if item.train_id == issue.train_id
            ),
            None,
        ) if frame is not None else None
        issue_samples.append({
            "issue": asdict(issue),
            "sample": asdict(sample) if sample is not None else None,
        })
    problematic_transitions = []
    for transition in metadata["speedLimitTransitions"]:
        trace = transition["trace"]
        if len(trace) < 2:
            continue
        previous_speed = float(trace[-2].get("speedMps") or 0.0)
        current_speed = float(trace[-1].get("speedMps") or 0.0)
        if previous_speed - current_speed > 1.0:
            problematic_transitions.append(transition)
    payload = {
        "validation": {
            "passed": validation.passed,
            "issueCount": len(validation.issues),
            "issueCounts": dict(Counter(item.code for item in validation.issues)),
            "firstIssues": [asdict(item) for item in validation.issues[: args.issue_limit]],
            "issueSamples": issue_samples,
        },
        "acceptance": metadata["operationAcceptanceAtCaptureEnd"],
        "diagnostics": metadata["operationDiagnosticsAtCaptureEnd"],
        "problematicSpeedLimitTransitions": problematic_transitions,
        "profileWarmup": metadata["profileWarmup"],
        "controlQuality": metadata["controlQuality"],
        "coverage": metadata["coverage"],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if validation.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
