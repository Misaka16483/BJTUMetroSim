from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.domain.power.experiments import PowerExperimentRegistry


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Line 9 traction-power batch optimization")
    parser.add_argument(
        "--problem",
        action="append",
        choices=["REGEN_MATCHING", "TRACTION_STAGGER", "EFS_CAPACITY", "N1_ROBUST_TIMETABLE"],
        help="Repeat to run multiple problems; defaults to all four.",
    )
    parser.add_argument("--algorithm", choices=["EVOLUTIONARY", "RANDOM_SEARCH"], default="EVOLUTIONARY")
    parser.add_argument("--population", type=int, default=8)
    parser.add_argument("--generations", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260711)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs" / "power-optimization-report.json")
    parser.add_argument("--database", type=Path, default=ROOT / "outputs" / "power_experiments.sqlite")
    args = parser.parse_args()

    problems = args.problem or [
        "REGEN_MATCHING",
        "TRACTION_STAGGER",
        "EFS_CAPACITY",
        "N1_ROBUST_TIMETABLE",
    ]
    registry = PowerExperimentRegistry(
        ROOT / "data" / "scenarios" / "line9_power_topology.json",
        args.database,
    )
    try:
        results = [
            registry.create({
                "problem": problem,
                "algorithm": args.algorithm,
                "populationSize": args.population,
                "generations": args.generations,
                "seed": args.seed,
            })
            for problem in problems
        ]
    finally:
        registry.close()

    report = {
        "modelQuality": "ENGINEERING_ESTIMATE",
        "algorithm": args.algorithm,
        "seed": args.seed,
        "experimentCount": len(results),
        "experiments": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = [
        {
            "experimentId": item["experimentId"],
            "problem": item["request"]["problem"],
            "baselineScore": item["baseline"]["score"],
            "bestScore": item["bestTrial"]["score"],
            "improvementPercent": item["improvementPercent"],
            "bestCandidate": item["bestTrial"]["candidate"],
        }
        for item in results
    ]
    print(json.dumps({"output": str(args.output), "summary": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
