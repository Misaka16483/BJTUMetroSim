from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from app.domain.power.flow_solver import DCTractionPowerFlowSolver
from app.domain.power.line9_topology import load_line9_power_network
from app.domain.power.network_models import TrainElectricalLoad
from app.domain.power.validation import validate_power_snapshot


ROOT = Path(__file__).resolve().parents[1]
TOPOLOGY = ROOT / "data" / "scenarios" / "line9_power_topology.json"


def valid_snapshot():
    solver = DCTractionPowerFlowSolver(load_line9_power_network(TOPOLOGY))
    return solver.solve(
        [TrainElectricalLoad("V1", "UP", 2400.0, 18.0, traction_force_n=100_000.0)],
        dt_sec=0.25,
    )


class PowerSnapshotValidationTests(unittest.TestCase):
    def test_valid_solver_snapshot_passes(self) -> None:
        report = validate_power_snapshot(valid_snapshot())
        self.assertTrue(report.passed, report.issues)

    def test_excessive_balance_error_is_rejected(self) -> None:
        snapshot = replace(valid_snapshot(), power_balance_error_ratio=0.051)
        report = validate_power_snapshot(snapshot, balance_error_limit_ratio=0.05)
        self.assertFalse(report.passed)
        self.assertIn("POWER_BALANCE_EXCEEDED", {item.code for item in report.issues})

    def test_regen_ledger_mismatch_is_rejected(self) -> None:
        snapshot = replace(valid_snapshot(), generated_regen_kw=100.0)
        report = validate_power_snapshot(snapshot)
        self.assertFalse(report.passed)
        self.assertIn("REGEN_BALANCE_EXCEEDED", {item.code for item in report.issues})

    def test_substation_power_identity_mismatch_is_rejected(self) -> None:
        snapshot = valid_snapshot()
        bad_substation = replace(snapshot.substations[0], power_kw=snapshot.substations[0].power_kw + 1.0)
        report = validate_power_snapshot(replace(snapshot, substations=[bad_substation, *snapshot.substations[1:]]))
        self.assertFalse(report.passed)
        self.assertIn("SUBSTATION_POWER_IDENTITY", {item.code for item in report.issues})


if __name__ == "__main__":
    unittest.main()
