"""Phase 1: energy estimation and station stop judgment demo for Member D."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.domain.power.phase1 import estimate_traction_energy
from app.domain.station.phase1 import judge_stop
from app.infra.recorder import RunRecorder


class Phase1MemberDDemoRunner:

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def run(self) -> dict[str, Any]:
        recorder = RunRecorder(self.db_path)
        try:
            run_id = recorder.start_run(
                "phase1-member-d-demo",
                {"phase": 1, "member": "D", "scope": "energy-estimation-stop-judgment"},
            )

            energy_scenarios = [
                {
                    "label": "accelerating",
                    "train_id": "T0901",
                    "segment_id": "SEG-001",
                    "traction_force_n": 70_000.0,
                    "speed_mps": 5.0,
                    "dt_sec": 30.0,
                },
                {
                    "label": "cruising",
                    "train_id": "T0902",
                    "segment_id": "SEG-002",
                    "traction_force_n": 30_000.0,
                    "speed_mps": 15.0,
                    "dt_sec": 60.0,
                },
                {
                    "label": "coasting",
                    "train_id": "T0903",
                    "segment_id": "SEG-003",
                    "traction_force_n": 0.0,
                    "speed_mps": 12.0,
                    "dt_sec": 30.0,
                },
            ]

            energy_estimates: list[dict[str, Any]] = []
            for i, scenario in enumerate(energy_scenarios):
                estimate = estimate_traction_energy(
                    train_id=scenario["train_id"],
                    segment_id=scenario["segment_id"],
                    traction_force_n=scenario["traction_force_n"],
                    speed_mps=scenario["speed_mps"],
                    dt_sec=scenario["dt_sec"],
                )
                energy_estimates.append({
                    "label": scenario["label"],
                    "trainId": estimate.train_id,
                    "segmentId": estimate.segment_id,
                    "tractionForceN": estimate.traction_force_n,
                    "speedMps": estimate.speed_mps,
                    "powerKw": round(estimate.power_kw, 3),
                    "energyKwh": round(estimate.energy_kwh, 4),
                    "durationSec": estimate.duration_sec,
                    "method": estimate.method,
                })
                recorder.record_metric(
                    run_id,
                    f"memberD.energyEstimate.{scenario['label']}.energyKwh",
                    estimate.energy_kwh,
                    unit="kwh",
                    tick=i + 1,
                )

            stop_scenarios = [
                {
                    "label": "success",
                    "train_id": "T0901",
                    "station_id": "S-GGZ",
                    "target_stop_m": 1660.52,
                    "actual_stop_m": 1660.30,
                    "tolerance_m": 0.5,
                    "speed_mps": 0.0,
                },
                {
                    "label": "overrun",
                    "train_id": "T0902",
                    "station_id": "S-FSP",
                    "target_stop_m": 1660.52,
                    "actual_stop_m": 1662.00,
                    "tolerance_m": 0.5,
                    "speed_mps": 0.0,
                },
                {
                    "label": "undershoot",
                    "train_id": "T0903",
                    "station_id": "S-KYL",
                    "target_stop_m": 1660.52,
                    "actual_stop_m": 1658.00,
                    "tolerance_m": 0.5,
                    "speed_mps": 0.0,
                },
                {
                    "label": "not-stopped",
                    "train_id": "T0904",
                    "station_id": "S-FTN",
                    "target_stop_m": 1660.52,
                    "actual_stop_m": 1650.00,
                    "tolerance_m": 0.5,
                    "speed_mps": 3.0,
                },
            ]

            stop_judgments: list[dict[str, Any]] = []
            for i, scenario in enumerate(stop_scenarios):
                judgment = judge_stop(
                    train_id=scenario["train_id"],
                    station_id=scenario["station_id"],
                    target_stop_m=scenario["target_stop_m"],
                    actual_stop_m=scenario["actual_stop_m"],
                    tolerance_m=scenario["tolerance_m"],
                    speed_mps=scenario["speed_mps"],
                )
                judgment_dict = {
                    "label": scenario["label"],
                    "trainId": judgment.train_id,
                    "stationId": judgment.station_id,
                    "targetStopM": judgment.target_stop_m,
                    "actualStopM": judgment.actual_stop_m,
                    "stopErrorM": round(judgment.stop_error_m, 3),
                    "toleranceM": judgment.tolerance_m,
                    "isStopped": judgment.is_stopped,
                    "stopResult": judgment.stop_result.value,
                }
                stop_judgments.append(judgment_dict)
                recorder.record_event(
                    run_id,
                    "station.stop_judgment",
                    judgment_dict,
                    tick=i + 1,
                )

            counts = self._table_counts(
                recorder,
                run_id,
                ["events", "metrics"],
            )

            return {
                "phase": 1,
                "module": "member-d-energy-stop",
                "runId": run_id,
                "recordDb": str(self.db_path),
                "energyEstimates": energy_estimates,
                "stopJudgments": stop_judgments,
                "counts": counts,
            }
        finally:
            recorder.close()

    def _table_counts(self, recorder: RunRecorder, run_id: int, tables: list[str]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for table in tables:
            counts[table] = int(
                recorder.connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0]
            )
        return counts
