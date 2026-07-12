from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.api_server import ApiHandler, Line9DataService


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
