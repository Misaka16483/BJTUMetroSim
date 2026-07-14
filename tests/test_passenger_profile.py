from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.core.engine import SimulationEngine
from app.domain.station.passenger_profiles import (
    PassengerProfileError,
    load_passenger_profile,
)
from app.domain.station.services import PoissonPassengerFlowGenerator


ROOT = Path(__file__).resolve().parents[1]
WEST_STATION_AREA_CODES = {"LLE", "BWR", "JBG", "BDZ", "BQS", "GTG"}


class PassengerProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load_passenger_profile()
        self.generator = PoissonPassengerFlowGenerator(
            list(self.profile.station_configs),
            self.profile.flow_scenario,
            use_poisson=False,
        )

    def test_calibrated_daily_volume_matches_declared_aggregate_benchmark(self) -> None:
        total = self.profile.estimated_daily_arrivals()
        west_area = self.profile.estimated_daily_arrivals(
            station_codes=WEST_STATION_AREA_CODES,
        )

        self.assertEqual(self.profile.quality, "CALIBRATED_SYNTHETIC")
        self.assertAlmostEqual(total, 549_986, delta=1.0)
        self.assertAlmostEqual(west_area, 400_059, delta=1.0)
        self.assertGreater(west_area / total, 0.70)
        self.assertLess(west_area / total, 0.75)

    def test_terminal_platforms_do_not_generate_impossible_departures(self) -> None:
        peak_ms = 8 * 3600 * 1000

        self.assertGreater(self.generator.arrival_rate_pax_per_min("GGZ", "UP", peak_ms), 0)
        self.assertEqual(self.generator.arrival_rate_pax_per_min("GGZ", "DOWN", peak_ms), 0)
        self.assertEqual(self.generator.arrival_rate_pax_per_min("GTG", "UP", peak_ms), 0)
        self.assertGreater(self.generator.arrival_rate_pax_per_min("GTG", "DOWN", peak_ms), 0)
        self.assertEqual(self.generator.alighting_ratio("GGZ", "DOWN"), 1.0)
        self.assertEqual(self.generator.alighting_ratio("GTG", "UP"), 1.0)

    def test_directional_tidal_factors_reverse_between_morning_and_evening_peaks(self) -> None:
        def line_rate(direction: str, hour: int) -> float:
            return sum(
                self.generator.arrival_rate_pax_per_min(
                    config.station_id,
                    direction,
                    hour * 3600 * 1000,
                )
                for config in self.profile.station_configs
                if config.direction == direction
            )

        self.assertGreater(line_rate("UP", 8), line_rate("DOWN", 8))
        self.assertGreater(line_rate("DOWN", 18), line_rate("UP", 18))

    def test_engine_exposes_profile_quality_capacity_and_station_area(self) -> None:
        engine = SimulationEngine.load_from_files(
            ROOT / "data" / "scenarios" / "line9_single.json",
            ROOT / "data" / "cache" / "line_map.json",
            ROOT / "data" / "line9" / "stations.csv",
        )
        engine.load()
        result = engine.add_train({
            "trainId": "PAX-PROFILE-001",
            "initialStationCode": "GGZ",
            "direction": "UP",
        })
        snapshot = engine.snapshot()

        self.assertTrue(result["ok"])
        self.assertEqual(result["train"]["capacityPax"], 1_460)
        engine.set_vehicle_config({"trainId": "PAX-PROFILE-001"})
        self.assertEqual(engine.trains[-1].capacity_pax, 1_460)
        self.assertEqual(engine.station_service.platforms[("BWR", "UP")].platform_area_m2, 240.0)
        assert snapshot is not None
        self.assertEqual(snapshot.kpi["passengerProfileId"], self.profile.profile_id)
        self.assertEqual(snapshot.kpi["passengerDataQuality"], "CALIBRATED_SYNTHETIC")
        self.assertEqual(snapshot.kpi["estimatedWeekdayPassengerArrivals"], 549_986)

    def test_loader_rejects_incomplete_direction_data(self) -> None:
        payload = {
            "schemaVersion": "PASSENGER-PROFILE-V1",
            "profileId": "BROKEN",
            "lineId": "9",
            "stations": [{
                "stationCode": "GGZ",
                "directions": {
                    "UP": {"baseArrivalRatePaxPerMin": 1.0, "alightingRatio": 0.0}
                },
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(PassengerProfileError):
                load_passenger_profile(path)


if __name__ == "__main__":
    unittest.main()
