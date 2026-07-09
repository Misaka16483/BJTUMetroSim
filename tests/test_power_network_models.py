from __future__ import annotations

import unittest
from pathlib import Path

from app.domain.power.line9_topology import load_line9_power_network


ROOT = Path(__file__).resolve().parents[1]


class Line9PowerNetworkTests(unittest.TestCase):
    def test_loads_v0_topology_with_ten_substations(self) -> None:
        network = load_line9_power_network(ROOT / "data" / "scenarios" / "line9_power_topology.json")

        self.assertEqual(network.line_id, "9")
        self.assertGreaterEqual(len(network.substations), 10)
        self.assertGreaterEqual(len(network.feeders), 36)
        self.assertGreaterEqual(len(network.contact_sections), 18)
        self.assertEqual(network.quality, "ENGINEERING_ESTIMATE")

    def test_locates_adjacent_substations_by_mileage_and_direction(self) -> None:
        network = load_line9_power_network(ROOT / "data" / "scenarios" / "line9_power_topology.json")

        left, right = network.adjacent_substations(2_500.0, "UP")

        self.assertEqual(left.substation_id, "TS-0902")
        self.assertEqual(right.substation_id, "TS-0903")
        self.assertEqual(network.locate_section(2_500.0, "DOWN").direction, "DOWN")

    def test_substation_outage_opens_feeders_and_closes_tie_switch(self) -> None:
        network = load_line9_power_network(ROOT / "data" / "scenarios" / "line9_power_topology.json")

        result = network.apply_substation_outage("TS-0905")

        self.assertEqual(network.substations["TS-0905"].status, "OUTAGE")
        self.assertGreater(len(result["openedSwitches"]), 0)
        self.assertGreater(len(result["closedSwitches"]), 0)


if __name__ == "__main__":
    unittest.main()
