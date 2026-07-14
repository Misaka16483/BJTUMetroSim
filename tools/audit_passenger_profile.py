from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.domain.station.passenger_profiles import (
    DEFAULT_LINE9_PROFILE_PATH,
    load_passenger_profile,
)
from app.domain.station.services import PoissonPassengerFlowGenerator


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit a versioned passenger profile")
    parser.add_argument("--profile", type=Path, default=DEFAULT_LINE9_PROFILE_PATH)
    args = parser.parse_args()

    profile = load_passenger_profile(args.profile)
    west_codes = set(profile.metadata.get("westStationAreaCodes", ()))
    total = profile.estimated_daily_arrivals()
    west_total = profile.estimated_daily_arrivals(station_codes=west_codes)
    generator = PoissonPassengerFlowGenerator(
        list(profile.station_configs),
        profile.flow_scenario,
        use_poisson=False,
    )
    peak_ms = 8 * 3600 * 1000
    endpoint_checks = {
        "GGZ_DOWN_ZERO": generator.arrival_rate_pax_per_min("GGZ", "DOWN", peak_ms) == 0.0,
        "GTG_UP_ZERO": generator.arrival_rate_pax_per_min("GTG", "UP", peak_ms) == 0.0,
        "GGZ_DOWN_CLEAR": generator.alighting_ratio("GGZ", "DOWN", peak_ms) == 1.0,
        "GTG_UP_CLEAR": generator.alighting_ratio("GTG", "UP", peak_ms) == 1.0,
    }
    declared_total = float(profile.metadata.get("estimatedWeekdayArrivalsPax", total))
    declared_west = float(profile.metadata.get("estimatedWestStationAreaArrivalsPax", west_total))
    passed = (
        abs(total - declared_total) <= 1.0
        and abs(west_total - declared_west) <= 1.0
        and all(endpoint_checks.values())
    )
    print(json.dumps({
        "passed": passed,
        "profileId": profile.profile_id,
        "quality": profile.quality,
        "estimatedWeekdayArrivalsPax": round(total),
        "estimatedWestStationAreaArrivalsPax": round(west_total),
        "westStationAreaShare": round(west_total / total, 4) if total else 0.0,
        "trainCapacityPax": profile.train_capacity_pax,
        "endpointChecks": endpoint_checks,
    }, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
