from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.api_server import Line9DataService


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

    def test_power_topology_shape(self) -> None:
        topology = self.service.power_topology()
        self.assertEqual(topology["lineId"], "9")
        self.assertEqual(topology["nominalVoltageV"], 750)
        self.assertGreaterEqual(len(topology["substations"]), 10)
        self.assertGreaterEqual(len(topology["contactRailSections"]), 18)
        self.assertEqual(topology["quality"], "ENGINEERING_ESTIMATE")

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

        route_seven = next(route for route in topology["routes"] if route["id"] == "7")
        self.assertEqual(route_seven["pathSegs"], [11, 12, 36, 34, 32, 31])

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
