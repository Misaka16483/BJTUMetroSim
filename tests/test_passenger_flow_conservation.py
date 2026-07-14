from __future__ import annotations

import unittest
from pathlib import Path

from app.core.engine import SimulationEngine
from app.core.scenario import ScenarioConfig


ROOT = Path(__file__).resolve().parents[1]


def make_engine() -> SimulationEngine:
    engine = SimulationEngine.load_from_files(
        ROOT / "data/scenarios/line9_interactive.json",
        ROOT / "data/cache/line_map.json",
        ROOT / "data/line9/stations.csv",
    )
    engine.load()
    return engine


class PassengerFlowConservationTests(unittest.TestCase):
    def test_passenger_demand_scale_is_optional_and_validated(self) -> None:
        payload = {
            "lineId": "9",
            "name": "passenger-scale-test",
            "startTimeMs": 6 * 3600 * 1000,
        }
        self.assertEqual(ScenarioConfig.from_dict(payload).passenger_demand_scale, 1.0)
        self.assertTrue(ScenarioConfig.from_dict(payload).passenger_use_poisson)
        self.assertEqual(
            ScenarioConfig.from_dict({**payload, "passengerDemandScale": 0.35}).passenger_demand_scale,
            0.35,
        )
        self.assertFalse(
            ScenarioConfig.from_dict({**payload, "passengerUsePoisson": False}).passenger_use_poisson
        )
        with self.assertRaises(ValueError):
            ScenarioConfig.from_dict({**payload, "passengerDemandScale": -0.1})

    def test_poisson_mode_can_change_without_rebuilding_the_engine(self) -> None:
        engine = make_engine()

        self.assertTrue(engine.passenger_flow_configuration()["usePoisson"])
        configuration = engine.set_passenger_poisson_enabled(False)

        self.assertFalse(configuration["usePoisson"])
        self.assertFalse(configuration["enabled"])
        self.assertTrue(configuration["manualInputAllowed"])
        self.assertEqual(configuration["mode"], "DISABLED_MANUAL")
        self.assertEqual(configuration["boardingPolicy"], "FILL_TO_CAPACITY")
        self.assertFalse(engine.snapshot().kpi["passengerUsePoisson"])

    def test_disabled_generation_stays_zero_and_allows_manual_platform_addition(self) -> None:
        engine = make_engine()
        engine.set_passenger_poisson_enabled(False)

        for tick in range(240):
            arrivals = engine.station_service.update_arrivals(
                6 * 3600 * 1000 + tick * 250,
                0.25,
                engine._serviceable_passenger_platforms(),
            )
            self.assertEqual(arrivals, {})

        result = engine.add_platform_passengers("BWR", "DOWN", 125)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "APPLIED")
        self.assertEqual(result["waitingPax"], 125)
        platform = engine.station_service.ensure_platform("BWR", "DOWN")
        self.assertEqual(platform.waiting_pax, 125)
        self.assertEqual(platform._total_arrived_pax, 125)
        self.assertTrue(engine.station_service.passenger_totals()["platformBalanced"])

    def test_manual_platform_addition_is_rejected_while_generation_is_enabled(self) -> None:
        engine = make_engine()

        result = engine.add_platform_passengers("GGZ", "UP", 50)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "AUTO_PASSENGER_GENERATION_ENABLED")
        self.assertEqual(engine.station_service.ensure_platform("GGZ", "UP").waiting_pax, 0)

    def test_running_manual_platform_addition_is_applied_at_next_tick_boundary(self) -> None:
        engine = make_engine()
        engine.set_passenger_poisson_enabled(False)
        engine.clock.start()

        queued = engine.add_platform_passengers("GGZ", "UP", 80)
        self.assertEqual(queued["status"], "QUEUED")
        self.assertEqual(engine.station_service.ensure_platform("GGZ", "UP").waiting_pax, 0)

        engine._tick()

        self.assertEqual(engine.station_service.ensure_platform("GGZ", "UP").waiting_pax, 80)
        self.assertEqual(engine.snapshot().stations[0]["waitingPax"], 80)

    def test_current_exchange_interface_reports_totals_rates_and_full_load(self) -> None:
        engine = make_engine()
        result = engine.add_train({
            "trainId": "T-EXCHANGE",
            "initialStationCode": "GGZ",
            "direction": "UP",
            "capacityPax": 600,
        })
        self.assertTrue(result["ok"])
        train = engine.trains[0]
        platform = engine.station_service.ensure_platform("GGZ", "UP")
        platform.waiting_pax = 1_000
        platform._total_arrived_pax = 1_000
        engine._process_station_stop(train, 6 * 3600 * 1000)

        observed_positive_rate = False
        for tick in range(120):
            engine._advance_open_door_passengers(
                train,
                6 * 3600 * 1000 + tick * 250,
                0.25,
            )
            observed_positive_rate |= train.current_boarding_rate_pax_per_sec > 0
            train.dwell_remaining_sec = max(0.0, train.dwell_remaining_sec - 0.25)
        engine._snapshot = engine._build_snapshot()

        response = engine.current_station_passenger_exchange("GGZ")
        self.assertEqual(len(response["exchanges"]), 1)
        exchange = response["exchanges"][0]
        self.assertEqual(exchange["currentBoardingPax"], 600)
        self.assertEqual(exchange["currentAlightingPax"], 0)
        self.assertEqual(exchange["onboardPax"], 600)
        self.assertEqual(exchange["platformWaitingPax"], 400)
        self.assertTrue(observed_positive_rate)

    def test_arrival_only_terminal_platforms_do_not_generate_demand(self) -> None:
        engine = make_engine()
        allowed = engine._serviceable_passenger_platforms()

        self.assertNotIn(("GGZ", "DOWN"), allowed)
        self.assertNotIn(("GTG", "UP"), allowed)
        self.assertIn(("GGZ", "UP"), allowed)
        self.assertIn(("GTG", "DOWN"), allowed)

        for _ in range(240):
            engine.station_service.update_arrivals(6 * 3600 * 1000, 0.25, allowed)

        self.assertEqual(engine.station_service.platforms[("GGZ", "DOWN")].waiting_pax, 0)
        self.assertEqual(engine.station_service.platforms[("GTG", "UP")].waiting_pax, 0)

    def test_turnback_discharges_remaining_passengers(self) -> None:
        engine = make_engine()
        engine.add_train({
            "trainId": "T-TERM",
            "initialStationCode": "GTG",
            "direction": "DOWN",
            "initialLoadPax": 240,
        })
        train = engine.trains[0]

        engine._turn_train_at_terminal(train)

        self.assertEqual(train.direction, "UP")
        self.assertTrue(train._terminal_turnback_pending)
        engine._process_station_stop(train, 6 * 3600 * 1000)
        for tick in range(120):
            engine._advance_open_door_passengers(
                train,
                6 * 3600 * 1000 + tick * 250,
                0.25,
            )
            train.dwell_remaining_sec = max(0.0, train.dwell_remaining_sec - 0.25)
        self.assertEqual(train.onboard_pax, 0)
        self.assertEqual(train.load_factor, 0.0)

    def test_fast_forward_microticks_keep_second_level_station_history(self) -> None:
        engine = make_engine()
        engine.clock.start()

        # One 60x batch is still sixty 250 ms microticks, not one 15-second
        # coarse passenger update.  The history must contain all 15 seconds.
        for _ in range(60):
            engine._tick()

        self.assertEqual(engine.clock.current_tick, 60)
        self.assertEqual(engine.clock.sim_time_seconds, 15.0)
        self.assertEqual(len(engine.snapshot().stations), 26)
        for history in engine._station_history.values():
            self.assertEqual(history[-1]["simTimeMs"], 6 * 3600 * 1000 + 15_000)

        kpi = engine.snapshot().kpi
        totals = engine.station_service.passenger_totals()
        self.assertEqual(kpi["passengerDemandScale"], 1.0)
        self.assertEqual(kpi["totalPassengerArrivedPax"], totals["arrivedPax"])
        self.assertEqual(kpi["totalPassengerBoardedPax"], totals["boardedPax"])
        self.assertEqual(kpi["totalPassengerAlightedPax"], totals["alightedPax"])
        self.assertTrue(kpi["passengerPlatformBalanced"])


if __name__ == "__main__":
    unittest.main()
