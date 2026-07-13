from __future__ import annotations

import unittest
import json
from pathlib import Path

from app.domain.power.line9_topology import build_line9_power_network, load_line9_power_network


ROOT = Path(__file__).resolve().parents[1]


class Line9PowerNetworkTests(unittest.TestCase):
    def test_loads_v0_topology_with_ten_substations(self) -> None:
        network = load_line9_power_network(ROOT / "data" / "scenarios" / "line9_power_topology.json")

        self.assertEqual(network.line_id, "9")
        self.assertGreaterEqual(len(network.substations), 10)
        self.assertGreaterEqual(len(network.feeders), 36)
        self.assertGreaterEqual(len(network.contact_sections), 18)
        self.assertEqual(network.quality, "ENGINEERING_ESTIMATE")
        self.assertEqual(network.model_version, "LINE9-DC750-V1.0")
        self.assertTrue(network.provenance["sources"])
        self.assertTrue(all(item.source_id != "UNSPECIFIED" for item in network.substations.values()))
        self.assertTrue(all(item.parameter_sources for item in network.feeders.values()))

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

    def test_substation_restore_returns_exact_prefault_topology(self) -> None:
        network = load_line9_power_network(ROOT / "data" / "scenarios" / "line9_power_topology.json")
        network.set_feeder_status("FD-0905-UP-LEFT", "OPEN")
        network.operate_switch("SW-TIE-0905", "CLOSED")
        feeder_states = {key: item.status for key, item in network.feeders.items()}
        switch_states = {key: item.current_state for key, item in network.switches.items()}

        network.apply_substation_outage("TS-0905", big_bilateral=True)
        result = network.restore_substation("TS-0905")

        self.assertEqual(network.substations["TS-0905"].status, "IN_SERVICE")
        self.assertEqual({key: item.status for key, item in network.feeders.items()}, feeder_states)
        self.assertEqual({key: item.current_state for key, item in network.switches.items()}, switch_states)
        self.assertNotIn("FD-0905-UP-LEFT", result["closedFeeders"])
        self.assertGreater(len(result["restoredFeeders"]), 0)
        self.assertGreater(len(result["restoredSwitches"]), 0)

    def test_repeated_outage_restore_cycles_do_not_drift_topology(self) -> None:
        network = load_line9_power_network(ROOT / "data" / "scenarios" / "line9_power_topology.json")
        initial = network.topology_dict()

        for _ in range(5):
            network.apply_substation_outage("TS-0905", big_bilateral=True)
            network.restore_substation("TS-0905")

        self.assertEqual(network.topology_dict(), initial)

    def test_strict_topology_rejects_implicit_generated_devices(self) -> None:
        data = json.loads(
            (ROOT / "data" / "scenarios" / "line9_power_topology.json").read_text(encoding="utf-8")
        )
        data["feeders"] = []

        with self.assertRaisesRegex(ValueError, "explicit non-empty arrays: feeders"):
            build_line9_power_network(data)

    def test_strict_topology_rejects_missing_parameter_provenance(self) -> None:
        data = json.loads(
            (ROOT / "data" / "scenarios" / "line9_power_topology.json").read_text(encoding="utf-8")
        )
        data["feeders"][0].pop("parameterSources")

        with self.assertRaisesRegex(ValueError, "requires sourceId, quality and parameterSources"):
            build_line9_power_network(data)

    def test_strict_topology_rejects_duplicate_device_ids(self) -> None:
        data = json.loads(
            (ROOT / "data" / "scenarios" / "line9_power_topology.json").read_text(encoding="utf-8")
        )
        data["feeders"][1]["feederId"] = data["feeders"][0]["feederId"]

        with self.assertRaisesRegex(ValueError, "duplicate feederId"):
            build_line9_power_network(data)


if __name__ == "__main__":
    unittest.main()
