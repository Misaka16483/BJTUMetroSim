"""Phase 0: default station/power states and metric structure demo for Member D."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.domain.power.phase0 import DEFAULT_POWER_SECTIONS, DefaultPowerState
from app.domain.station.phase0 import LINE9_STATIONS, DefaultStationState, StationMetricNames, generate_default_station_state
from app.infra.recorder import RunRecorder


class Phase0MemberDDemoRunner:

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def run(self) -> dict[str, Any]:
        recorder = RunRecorder(self.db_path)
        try:
            run_id = recorder.start_run(
                "phase0-member-d-demo",
                {"phase": 0, "member": "D", "scope": "default-states-metrics"},
            )

            station_states: list[dict[str, Any]] = []
            for station in LINE9_STATIONS:
                for direction in ("UP", "DOWN"):
                    state = generate_default_station_state(
                        station["id"],
                        station["name"],
                        direction=direction,
                    )
                    station_states.append(self._station_state_to_dict(state))
                    recorder.record_station_passenger(
                        run_id,
                        sim_time_ms=state.timestamp_ms,
                        station_id=state.station_id,
                        direction=state.direction,
                        platform_density_pax_per_m2=state.platform_density_pax_per_m2,
                        crowding_level=state.crowding_level,
                        detail={"phase": 0},
                    )

            power_states: list[dict[str, Any]] = []
            for section in DEFAULT_POWER_SECTIONS:
                power_states.append(self._power_state_to_dict(section))
                recorder.record_power(
                    run_id,
                    sim_time_ms=0,
                    power_section_id=section.power_section_id,
                    requested_power_kw=section.requested_power_kw,
                    available_power_kw=section.available_power_kw,
                    traction_limit_ratio=section.traction_limit_ratio,
                    voltage_level=section.voltage_level,
                    energy_kwh=0.0,
                    regen_energy_kwh=0.0,
                    source=section.source,
                    quality=section.quality,
                    detail={"phase": 0},
                )

            metric_names = StationMetricNames()
            for name in [
                metric_names.WAITING_PAX,
                metric_names.CROWDING_LEVEL,
                metric_names.PLATFORM_DENSITY,
                metric_names.BOARDING_COUNT,
                metric_names.ALIGHTING_COUNT,
                metric_names.LEFT_BEHIND_PAX,
                metric_names.DWELL_SECONDS,
                metric_names.LOAD_FACTOR,
                metric_names.VEHICLE_LOAD_KG,
                metric_names.POWER_REQUESTED_KW,
                metric_names.POWER_TRACTION_LIMIT,
                metric_names.POWER_ENERGY_KWH,
                metric_names.POWER_REGEN_KWH,
                metric_names.DISPATCH_DECISION_COUNT,
                metric_names.TOTAL_WAITING_PAX,
            ]:
                recorder.record_metric(run_id, name, 0.0, unit="definition", tick=0)

            counts = self._table_counts(
                recorder,
                run_id,
                [
                    "station_passenger_records",
                    "power_records",
                    "metrics",
                ],
            )

            return {
                "phase": 0,
                "module": "member-d-station-power",
                "runId": run_id,
                "recordDb": str(self.db_path),
                "stationStates": station_states,
                "powerStates": power_states,
                "metricNames": [
                    name for name in vars(metric_names).values() if not name.startswith("_")
                ],
                "counts": counts,
            }
        finally:
            recorder.close()

    @staticmethod
    def _station_state_to_dict(state: DefaultStationState) -> dict[str, Any]:
        return {
            "stationId": state.station_id,
            "stationName": state.station_name,
            "direction": state.direction,
            "platformAreaM2": state.platform_area_m2,
            "waitingPax": state.waiting_pax,
            "crowdingLevel": state.crowding_level,
        }

    @staticmethod
    def _power_state_to_dict(state: DefaultPowerState) -> dict[str, Any]:
        return {
            "powerSectionId": state.power_section_id,
            "name": state.name,
            "maxTractionPowerKw": state.max_traction_power_kw,
            "availablePowerKw": state.available_power_kw,
            "warningPowerKw": state.warning_power_kw,
            "voltageLevel": state.voltage_level,
            "tractionLimitRatio": state.traction_limit_ratio,
            "source": state.source,
            "quality": state.quality,
        }

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
