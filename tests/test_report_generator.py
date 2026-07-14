from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from app.core.report_generator import ReportGenerator
from app.infra.recorder import RunRecorder


class ReportGeneratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.work_dir = Path("outputs") / "test_report_generator"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.work_dir / "report.sqlite"
        if self.db_path.exists():
            self.db_path.unlink()
        self.recorder = RunRecorder(self.db_path)

    def tearDown(self) -> None:
        self.recorder.close()
        shutil.rmtree(self.work_dir, ignore_errors=True)

    def test_generate_save_and_load_structured_report(self) -> None:
        run_id = self.recorder.start_run(
            "report-smoke",
            {"startTimeMs": 21_600_000, "tickSeconds": 0.25, "trainCount": 1},
        )
        self.recorder.record_event(
            run_id,
            "train.state",
            {
                "trainId": "T0901",
                "speedMps": 12.5,
                "energyKwh": 1.25,
                "tractionEnergyKwh": 1.0,
                "auxiliaryEnergyKwh": 0.25,
                "regenGeneratedKwh": 0.2,
                "regenAcceptedKwh": 0.12,
                "regenWastedKwh": 0.08,
                "pathPositionM": 800.0,
            },
            tick=1,
        )
        self.recorder.record_station_passenger(
            run_id,
            sim_time_ms=21_600_250,
            station_id="BWR",
            direction="UP",
            arrivals=8,
            boarding=5,
            alighting=2,
            waiting=3,
            left_behind=1,
            platform_density_pax_per_m2=0.35,
            crowding_level="LOW",
        )
        self.recorder.record_power(
            run_id,
            sim_time_ms=21_600_250,
            power_section_id="PWR-09-UP",
            requested_power_kw=900.0,
            available_power_kw=12_000.0,
            traction_limit_ratio=1.0,
            voltage_level="NORMAL",
            energy_kwh=1.25,
            regen_energy_kwh=0.2,
            absorbed_regen_kw=120.0,
            wasted_regen_kw=80.0,
        )
        self.recorder.record_train_voltage(
            run_id,
            sim_time_ms=21_600_250,
            train_id="T0901",
            power_section_id="PWR-09-UP",
            voltage_v=748.0,
            current_a=100.0,
            requested_power_kw=900.0,
            traction_limit_ratio=1.0,
            regen_limit_ratio=1.0,
            voltage_level="NORMAL",
        )
        self.recorder.record_substation_power(
            run_id,
            sim_time_ms=21_600_250,
            substation_id="SS-01",
            voltage_v=750.0,
            current_a=100.0,
            power_kw=900.0,
            energy_kwh=1.25,
            load_ratio=0.1,
            status="IN_SERVICE",
        )

        report = ReportGenerator(self.recorder).generate(run_id)
        self.recorder.save_report(run_id, report)
        loaded = self.recorder.get_report(run_id)

        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded["runId"], run_id)
        self.assertEqual(loaded["summary"]["trainCount"], 1)
        self.assertEqual(loaded["dynamics"]["totalEnergyKwh"], 1.25)
        self.assertEqual(loaded["passenger"]["totalArrivals"], 8)
        self.assertEqual(loaded["passenger"]["maxWaitingPax"], 3)
        self.assertEqual(loaded["power"]["avgVoltageV"], 748.0)
        self.assertEqual(set(loaded["charts"].keys()), {"dynamics", "passenger", "power"})

    def test_power_consumption_uses_each_sections_terminal_cumulative_value(self) -> None:
        run_id = self.recorder.start_run(
            "power-terminal-cumulative",
            {"startTimeMs": 0, "tickSeconds": 0.25, "trainCount": 0},
        )
        for sim_time_ms, section_id, energy_kwh in (
            (250, "PWR-A", 1.0),
            (500, "PWR-A", 2.0),
            (250, "PWR-B", 1.5),
            (500, "PWR-B", 3.0),
        ):
            self.recorder.record_power(
                run_id,
                sim_time_ms=sim_time_ms,
                power_section_id=section_id,
                requested_power_kw=100.0,
                available_power_kw=1_000.0,
                traction_limit_ratio=1.0,
                voltage_level="NORMAL",
                energy_kwh=energy_kwh,
                regen_energy_kwh=0.0,
                absorbed_regen_kw=0.0,
                wasted_regen_kw=0.0,
            )

        report = ReportGenerator(self.recorder).generate(run_id)

        self.assertEqual(report["power"]["totalPowerConsumedKwh"], 5.0)


if __name__ == "__main__":
    unittest.main()
