from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from app.infra.rtdb_power_dictionary import power_point_contract_sha256
from tools.run_power_baseline import (
    build_report,
    outage_recovery_case,
    single_train_case,
    two_train_regen_case,
)


ROOT = Path(__file__).resolve().parents[1]


class PowerCredibilityBaselineTests(unittest.TestCase):
    def test_frozen_manifest_matches_contract_and_topology(self) -> None:
        manifest = json.loads(
            (ROOT / "data" / "contracts" / "power_credibility_baseline_v1.json").read_text(encoding="utf-8")
        )
        topology_path = ROOT / manifest["topology"]["path"]
        self.assertEqual(manifest["pointContract"]["sha256"], power_point_contract_sha256())
        self.assertEqual(
            manifest["topology"]["sha256"],
            hashlib.sha256(topology_path.read_bytes()).hexdigest(),
        )
        self.assertTrue(manifest["releaseGatePassed"])

    def test_single_train_traction_gate(self) -> None:
        result = single_train_case()
        self.assertTrue(result["passed"], result)

    def test_two_train_regen_coordination_gate(self) -> None:
        result = two_train_regen_case()
        self.assertTrue(result["passed"], result)
        self.assertGreater(result["crossTrainPathCount"], 0)

    def test_substation_outage_recovery_gate(self) -> None:
        result = outage_recovery_case()
        self.assertTrue(result["passed"], result)
        self.assertGreater(result["voltageDropV"], 0.0)
        self.assertAlmostEqual(result["recoveryVoltageErrorV"], 0.0, places=6)

    def test_complete_report_is_releasable_with_teacher_definition(self) -> None:
        source = ROOT / "188_2.tableData-1(1).csv"
        if not source.exists():
            self.skipTest("teacher definition is an external read-only artifact")
        report = build_report(source)
        self.assertTrue(report["passed"], report)
        self.assertTrue(all(report["gates"].values()))


if __name__ == "__main__":
    unittest.main()
