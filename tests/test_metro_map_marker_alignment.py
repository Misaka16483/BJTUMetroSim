from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "bj-metro-sim" / "src"


class MetroMapMarkerAlignmentContractTests(unittest.TestCase):
    def test_train_marker_transform_is_not_delayed_by_global_transitions(self) -> None:
        css = (FRONTEND / "index.css").read_text(encoding="utf-8")
        metro_map = (FRONTEND / "components" / "MetroMap.tsx").read_text(
            encoding="utf-8"
        )

        marker_rule = re.search(
            r"\.maplibregl-marker\.train-marker\s*\{(?P<body>[^}]*)\}",
            css,
        )

        self.assertIsNotNone(marker_rule)
        self.assertRegex(marker_rule.group("body"), r"transition\s*:\s*none\s*;")
        self.assertIn("el.className = 'train-marker';", metro_map)
        self.assertIn("existing.setLngLat(lngLat);", metro_map)
        self.assertIn("new maplibregl.Marker({ element: el, anchor: 'bottom' })", metro_map)


if __name__ == "__main__":
    unittest.main()
