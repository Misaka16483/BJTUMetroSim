from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.domain.power.trajectory import JsonlTrajectoryProvider, validate_trajectory_frames


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a POWER-TRAJECTORY-V1 JSONL trace")
    parser.add_argument("trajectory", type=Path)
    parser.add_argument("--fixed-roster", action="store_true")
    parser.add_argument("--max-dynamics-residual-n", type=float, default=5_000.0)
    args = parser.parse_args()

    provider = JsonlTrajectoryProvider(args.trajectory, validate=False)
    report = validate_trajectory_frames(
        provider.frames,
        allow_roster_changes=not args.fixed_roster,
        max_dynamics_residual_n=args.max_dynamics_residual_n,
    )
    payload = {
        "passed": report.passed,
        "frameCount": report.frame_count,
        "sampleCount": report.sample_count,
        "trajectorySource": provider.source,
        "operationalMetricsAvailable": bool(
            provider.tracking_metrics({}).get("operationalMetricsAvailable", 0.0)
        ),
        "issues": [asdict(issue) for issue in report.issues],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
