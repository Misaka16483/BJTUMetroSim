from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.api_server import DEFAULT_SCENARIO, ApiHandler, Line9DataService
from app.core.scenario import ScenarioConfig
from app.domain.operations.member_c_demo import MemberCDemoRunner


class _ModeEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def set_manual_mode(self, train_id: str, enabled: bool) -> dict:
        self.calls.append((train_id, enabled))
        return {"ok": True, "trainId": train_id, "manualMode": enabled}


class _CabController:
    def __init__(self, state: str, train_id: str = "T0901") -> None:
        self.state = state
        self.train_id = train_id

    def status(self) -> dict:
        return {"ok": True, "status": {"state": self.state, "trainId": self.train_id}}


class ApiServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.service = Line9DataService()

    def test_default_scenario_enables_timetable_auto_dispatch(self) -> None:
        self.assertEqual(DEFAULT_SCENARIO.name, "line9_timetable_operation.json")
        scenario = ScenarioConfig.load(DEFAULT_SCENARIO)
        self.assertTrue(scenario.operation_plan.enabled)
        self.assertGreater(scenario.operation_plan.max_duties, 0)

    def test_macro_line_shape(self) -> None:
        macro = self.service.macro_line()
        self.assertEqual(macro["id"], "9")
        self.assertEqual(len(macro["stations"]), 13)
        self.assertEqual(macro["stations"][0]["name"], "郭公庄")
        self.assertEqual(macro["stations"][0]["platformSegmentIds"], [13, 39])

    def test_track_map_counts(self) -> None:
        track_map = self.service.track_map()
        self.assertEqual(track_map["counts"]["segments"], 319)
        self.assertEqual(track_map["counts"]["signals"], 157)
        self.assertEqual(track_map["counts"]["platforms"], 56)
        self.assertEqual(track_map["counts"]["routes"], 249)
        self.assertEqual(track_map["scope"]["activeForSimulation"], "line9-mainline-v1")
        self.assertEqual(track_map["scope"]["mainlineSegmentCount"], 77)
        self.assertTrue(track_map["scope"]["fullMapRetained"])

    def test_power_topology_shape(self) -> None:
        topology = self.service.power_topology()
        self.assertEqual(topology["lineId"], "9")
        self.assertEqual(topology["nominalVoltageV"], 750)
        self.assertGreaterEqual(len(topology["substations"]), 10)
        self.assertGreaterEqual(len(topology["contactRailSections"]), 18)
        self.assertEqual(topology["quality"], "ENGINEERING_ESTIMATE")
        self.assertEqual(topology["modelVersion"], "LINE9-DC750-V1.0")
        self.assertTrue(topology["provenance"]["sources"])
        self.assertTrue(topology["substations"][0]["parameterSources"])
        self.assertEqual(len(topology["supercapacitorStorageSystems"]), 1)
        storage = topology["supercapacitorStorageSystems"][0]
        self.assertEqual(storage["storageId"], "SCESS-0905")
        self.assertEqual(storage["dischargeTriggerPowerKw"], 1000.0)
        self.assertEqual(storage["quality"], "ENGINEERING_ESTIMATE")

    def test_member_c_static_topology_contains_full_line(self) -> None:
        topology = self.service.member_c_static_routes()
        segment_ids = {segment["id"] for segment in topology["segments"]}

        self.assertEqual(len(segment_ids), 319)
        self.assertTrue({1, 2}.issubset(segment_ids))
        self.assertEqual(len(topology["routes"]), 249)
        self.assertEqual(len(topology["signals"]), 157)
        self.assertEqual(len(topology["switches"]), 60)
        self.assertTrue(all("row" in segment and "col" in segment for segment in topology["segments"]))
        self.assertTrue(all(route["pathOrderComplete"] for route in topology["routes"]))
        positions = {(segment["row"], segment["col"]) for segment in topology["segments"]}
        self.assertEqual(len(positions), 319)
        self.assertLessEqual(topology["layout"]["rows"], 24)

        route_seven = next(route for route in topology["routes"] if route["id"] == "7")
        self.assertEqual(route_seven["pathSegs"], [11, 12, 36, 34, 32, 31])

    def test_member_c_demo_train_chain_follows_route_topology(self) -> None:
        runner = MemberCDemoRunner(self.service.cache_path)
        chain = runner._seg_chain
        self.assertEqual(chain[:8], [13, 14, 17, 44, 43, 45, 46, 48])
        self.assertNotIn(18, chain)
        for current, following in zip(chain, chain[1:]):
            segment = runner.track.get_segment(current)
            assert segment is not None
            self.assertIn(
                following,
                {
                    segment.get("startForwardSegId"),
                    segment.get("startDivergingSegId"),
                    segment.get("endForwardSegId"),
                    segment.get("endDivergingSegId"),
                },
            )

    def test_member_c_demo_occupancy_follows_active_route(self) -> None:
        runner = MemberCDemoRunner(self.service.cache_path)
        for _ in range(90):
            runner.step()
            if runner.state_snapshot()["trains"][0]["segId"] == 43:
                break
        snapshot = runner.state_snapshot()
        covered = {int(segment_id) for segment_id in snapshot["segTrainColors"]}

        self.assertEqual(snapshot["trains"][0]["segId"], 43)
        self.assertTrue({43, 44}.issubset(covered))
        self.assertFalse({39, 40, 41} & covered)

    def test_member_c_demo_prelocks_routes_for_signal_progression(self) -> None:
        runner = MemberCDemoRunner(self.service.cache_path)
        runner.step()
        snapshot = runner.state_snapshot()

        locked = {
            route["routeId"] for route in snapshot["routes"]
            if route["state"] in {"LOCKED", "APPROACH_LOCKED"}
        }
        aspects = {signal["id"]: signal["aspect"] for signal in snapshot["signals"]}

        self.assertTrue({"9", "28", "29"}.issubset(locked))
        self.assertEqual(aspects[61], "GREEN")
        self.assertEqual(aspects[62], "YELLOW")

    def test_member_c_manual_route_waits_for_lock_before_moving(self) -> None:
        runner = MemberCDemoRunner(self.service.cache_path)
        placed = runner.place_train_for_route("9")
        self.assertTrue(placed["ok"])
        initial_position = runner.state_snapshot()["trains"][0]["positionM"]

        runner.step()
        self.assertEqual(runner.state_snapshot()["trains"][0]["positionM"], initial_position)

        requested = runner.request_manual_route()
        self.assertTrue(requested["ok"])
        runner.step()
        snapshot = runner.state_snapshot()
        self.assertGreater(snapshot["trains"][0]["positionM"], initial_position)
        self.assertTrue(any("办理成功" in event["message"] for event in snapshot["events"]))

    def test_member_c_manual_request_rejects_conflicting_route(self) -> None:
        runner = MemberCDemoRunner(self.service.cache_path)
        self.assertTrue(runner.place_manual_train(1)["ok"])
        self.assertTrue(runner.request_manual_route("25")["ok"])

        result = runner.request_manual_route("24")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "CONFLICT_ROUTE_LOCKED")
        self.assertTrue(runner.route_svc.is_locked("25"))
        self.assertFalse(runner.route_svc.is_locked("24"))

    def test_frontend_mode_switch_is_rejected_while_driver_cab_connected(self) -> None:
        handler = object.__new__(ApiHandler)
        engine = _ModeEngine()
        handler.engine = engine
        handler._driver_cab_controller = lambda: _CabController("CONNECTED")

        result = handler._set_manual_mode_from_frontend("T0901", False)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "DRIVER_CAB_MODE_CONTROL_EXCLUSIVE")
        self.assertEqual(engine.calls, [])

    def test_frontend_mode_switch_is_allowed_after_driver_cab_disconnects(self) -> None:
        handler = object.__new__(ApiHandler)
        engine = _ModeEngine()
        handler.engine = engine
        handler._driver_cab_controller = lambda: _CabController("DISCONNECTED")

        result = handler._set_manual_mode_from_frontend("T0901", True)

        self.assertTrue(result["ok"])
        self.assertEqual(engine.calls, [("T0901", True)])

    def test_connected_driver_cab_does_not_lock_other_trains(self) -> None:
        handler = object.__new__(ApiHandler)
        engine = _ModeEngine()
        handler.engine = engine
        handler._driver_cab_controller = lambda: _CabController("CONNECTED", "T0901")

        result = handler._set_manual_mode_from_frontend("T0902", True)

        self.assertTrue(result["ok"])
        self.assertEqual(engine.calls, [("T0902", True)])

    def test_member_d_demo_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = Line9DataService(run_dir=Path(tmp))
            payload = service.member_d_demo()

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["lineId"], "9")
            self.assertEqual(payload["phase"], 2)
            summary = payload["summary"]
            self.assertEqual(summary["counts"]["dispatch_decisions"], 3)
            self.assertLess(summary["power"]["PWR-0901"]["tractionLimitRatio"], 1.0)
            self.assertEqual(summary["power"]["PWR-0901"]["source"], "SELF_SIM")
            self.assertIn(
                "STAGGER_DEPARTURE",
                {decision["action"] for decision in summary["dispatch"]},
            )


if __name__ == "__main__":
    unittest.main()
