from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()

