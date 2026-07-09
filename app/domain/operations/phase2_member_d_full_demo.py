"""Phase 2: full Line 9 passenger-dispatch-power demo across all 13 stations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.domain.dispatch.services import DispatchContext, DispatchDecision, RuleBasedDispatchService
from app.domain.power.services import PowerSection, PowerService, PowerState, TrainPowerRequest
from app.domain.station.phase0 import LINE9_STATIONS
from app.domain.station.services import (
    BoardingResult,
    DwellPlan,
    DwellTimeConfig,
    PassengerDemandProfile,
    PassengerFlowGenerator,
    PlatformCrowdState,
    StationService,
    TrainLoadState,
)
from app.infra.recorder import RunRecorder


@dataclass(frozen=True)
class DemoTrainState:
    train_id: str
    station_id: str
    direction: str
    onboard_pax: int
    capacity_pax: int
    rear_headway_sec: float | None = None
    front_headway_sec: float | None = None


class Phase2MemberDFullDemoRunner:

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def run(self) -> dict[str, Any]:
        recorder = RunRecorder(self.db_path)
        try:
            run_id = recorder.start_run(
                "phase2-member-d-full-demo",
                {
                    "phase": 2,
                    "scope": "passenger-load-dispatch-power-full-line9",
                    "line": "Beijing Subway Line 9 (all 13 stations)",
                    "powerSource": "SELF_SIM",
                    "stationCount": 13,
                },
            )
            station_service = self._build_station_service()
            power_service = self._build_power_service()
            dispatch_service = RuleBasedDispatchService()
            trains = self._build_train_states()

            sim_time_ms = 8 * 60 * 60 * 1000
            dt_sec = 180.0

            station_results: list[dict[str, Any]] = []
            primary_train = trains[0]
            current_load = TrainLoadState(
                primary_train.train_id,
                onboard_pax=primary_train.onboard_pax,
                capacity_pax=primary_train.capacity_pax,
            )
            total_left_behind = 0

            for station_idx, station in enumerate(LINE9_STATIONS):
                station_id = station["id"]
                arrivals_by_platform = station_service.update_arrivals(sim_time_ms, dt_sec=dt_sec)
                platform = station_service.ensure_platform(station_id, "UP", platform_area_m2=90.0)

                boarding_result, dwell_plan = station_service.process_train_stop(
                    sim_time_ms=sim_time_ms,
                    station_id=station_id,
                    direction="UP",
                    train_load=current_load,
                    platform_area_m2=90.0,
                )
                self._record_station_stop(
                    recorder,
                    run_id,
                    sim_time_ms,
                    boarding_result,
                    dwell_plan,
                    arrivals_by_platform.get((station_id, "UP"), 0),
                    platform,
                )
                current_load = boarding_result.updated_load
                total_left_behind += boarding_result.left_behind

                station_results.append({
                    "stationId": station_id,
                    "stationName": station["name"],
                    "simTimeMs": sim_time_ms,
                    "onboardPaxAfter": current_load.onboard_pax,
                    "capacityPax": current_load.capacity_pax,
                    "loadFactor": round(current_load.load_factor, 4),
                    "boarding": boarding_result.boarding,
                    "alighting": boarding_result.alighting,
                    "waiting": boarding_result.waiting,
                    "leftBehind": boarding_result.left_behind,
                    "dwellSec": round(dwell_plan.estimated_dwell_sec, 2),
                    "canDepart": dwell_plan.can_depart,
                })

                sim_time_ms += int(dt_sec * 1000)

            power_states = power_service.update(
                [
                    TrainPowerRequest("T0901", "PWR-09-UP", speed_mps=10.0, traction_force_n=70_000.0),
                    TrainPowerRequest("T0901", "PWR-09-UP", speed_mps=8.0, traction_force_n=40_000.0),
                    TrainPowerRequest("T0901", "PWR-09-UP", speed_mps=12.0, brake_force_n=15_000.0),
                ],
                dt_sec=dt_sec,
            )
            for state in power_states.values():
                recorder.record_power(
                    run_id,
                    sim_time_ms=sim_time_ms,
                    power_section_id=state.power_section_id,
                    requested_power_kw=state.requested_power_kw,
                    available_power_kw=state.available_power_kw,
                    traction_limit_ratio=state.traction_limit_ratio,
                    voltage_level=state.voltage_level,
                    energy_kwh=state.energy_kwh,
                    regen_energy_kwh=state.regen_energy_kwh,
                    absorbed_regen_kw=state.absorbed_regen_kw,
                    wasted_regen_kw=state.wasted_regen_kw,
                    source=state.source,
                    quality=state.quality,
                    detail={"stationCount": 13},
                )

            decisions = self._make_dispatch_decisions(
                dispatch_service,
                sim_time_ms,
                trains,
                station_results[-1] if station_results else {},
                power_states,
                total_left_behind,
            )
            for decision in decisions:
                recorder.record_dispatch_decision(
                    run_id,
                    decision_id=decision.decision_id,
                    sim_time_ms=decision.sim_time_ms,
                    train_id=decision.train_id,
                    station_id=decision.station_id,
                    action=decision.action,
                    duration_sec=decision.duration_sec,
                    reason=decision.reason,
                    expected_impact=decision.expected_impact,
                    applied=decision.applied,
                    detail={"scenario": "member-d-phase2-full-line9"},
                )

            recorder.record_metric(run_id, "memberD.totalWaitingPax", float(total_left_behind), unit="pax", tick=1)
            prime_state = list(power_states.values())[0]
            recorder.record_metric(
                run_id,
                "memberD.tractionLimitRatio",
                prime_state.traction_limit_ratio,
                unit="ratio",
                tick=1,
            )
            recorder.record_metric(
                run_id,
                "memberD.dispatchDecisionCount",
                float(len(decisions)),
                unit="count",
                tick=1,
            )
            recorder.record_metric(
                run_id,
                "memberD.totalEnergyKwh",
                sum(s.energy_kwh for s in power_states.values()),
                unit="kwh",
                tick=1,
            )

            return self._summary(
                run_id=run_id,
                station_results=station_results,
                power_states=power_states,
                decisions=decisions,
                recorder=recorder,
            )
        finally:
            recorder.close()

    def _build_station_service(self) -> StationService:
        arrival_rates = [180.0, 72.0, 48.0, 40.0, 55.0, 60.0, 85.0, 50.0, 120.0, 90.0, 45.0, 65.0, 30.0]
        alighting_ratios = [0.08, 0.14, 0.16, 0.15, 0.12, 0.13, 0.11, 0.14, 0.09, 0.10, 0.15, 0.12, 0.20]
        profiles = [
            PassengerDemandProfile(
                station["id"], "UP",
                7 * 3600, 9 * 3600,
                rate,
                alighting_ratio=alighting_ratios[i],
            )
            for i, (station, rate) in enumerate(zip(LINE9_STATIONS, arrival_rates))
        ]
        return StationService(
            PassengerFlowGenerator(profiles),
            DwellTimeConfig(base_dwell_sec=30.0, door_capacity_pax_per_sec=4.0),
        )

    def _build_power_service(self) -> PowerService:
        return PowerService([
            PowerSection(
                power_section_id="PWR-09-UP",
                name="Line 9 Up-track",
                max_traction_power_kw=1000.0,
                warning_power_kw=800.0,
                regen_absorb_limit_kw=200.0,
            ),
            PowerSection(
                power_section_id="PWR-09-DOWN",
                name="Line 9 Down-track",
                max_traction_power_kw=1000.0,
                warning_power_kw=800.0,
                regen_absorb_limit_kw=200.0,
            ),
        ])

    @staticmethod
    def _build_train_states() -> list[DemoTrainState]:
        return [
            DemoTrainState("T0901", "S-GGZ", "UP", onboard_pax=520, capacity_pax=600,
                           rear_headway_sec=120.0, front_headway_sec=240.0),
            DemoTrainState("T0902", "S-FSP", "UP", onboard_pax=430, capacity_pax=600,
                           rear_headway_sec=90.0),
            DemoTrainState("T0903", "S-KYL", "UP", onboard_pax=330, capacity_pax=600,
                           front_headway_sec=360.0),
        ]

    def _record_station_stop(
        self,
        recorder: RunRecorder,
        run_id: int,
        sim_time_ms: int,
        result: BoardingResult,
        dwell_plan: DwellPlan,
        arrivals: int,
        platform: PlatformCrowdState,
    ) -> None:
        recorder.record_station_passenger(
            run_id,
            sim_time_ms=sim_time_ms,
            station_id=result.station_id,
            direction=result.direction,
            arrivals=arrivals,
            boarding=result.boarding,
            alighting=result.alighting,
            waiting=result.waiting,
            left_behind=result.left_behind,
            platform_density_pax_per_m2=platform.platform_density_pax_per_m2,
            crowding_level=platform.crowding_level,
            detail={"trainId": result.train_id},
        )
        recorder.record_train_load(
            run_id,
            sim_time_ms=sim_time_ms,
            train_id=result.train_id,
            onboard_pax=result.updated_load.onboard_pax,
            capacity_pax=result.updated_load.capacity_pax,
            load_factor=result.updated_load.load_factor,
            vehicle_load_kg=result.updated_load.vehicle_load_kg,
            detail={"stationId": result.station_id},
        )
        recorder.record_dwell(
            run_id,
            train_id=result.train_id,
            station_id=result.station_id,
            arrival_ms=sim_time_ms,
            depart_ms=sim_time_ms + int(dwell_plan.estimated_dwell_sec * 1000),
            planned_dwell_sec=dwell_plan.planned_dwell_sec,
            estimated_dwell_sec=dwell_plan.estimated_dwell_sec,
            actual_dwell_sec=dwell_plan.estimated_dwell_sec,
            dispatch_hold_sec=dwell_plan.dispatch_hold_sec,
            reason=dwell_plan.blocking_reason or "PASSENGER_BOARDING",
            detail={"canDepart": dwell_plan.can_depart},
        )

    def _make_dispatch_decisions(
        self,
        dispatch_service: RuleBasedDispatchService,
        sim_time_ms: int,
        trains: list[DemoTrainState],
        last_station_result: dict[str, Any],
        power_states: dict[str, PowerState],
        total_left_behind: int,
    ) -> list[DispatchDecision]:
        prime_power = list(power_states.values())[0]
        contexts = [
            DispatchContext(
                sim_time_ms=sim_time_ms,
                train_id=trains[0].train_id,
                station_id=trains[0].station_id,
                rear_headway_sec=trains[0].rear_headway_sec,
                platform_crowding_level="MEDIUM",
                load_factor=last_station_result.get("loadFactor", 0.5),
                left_behind_pax=total_left_behind,
                power_traction_limit_ratio=prime_power.traction_limit_ratio,
            ),
            DispatchContext(
                sim_time_ms=sim_time_ms + 30_000,
                train_id=trains[1].train_id,
                station_id=trains[1].station_id,
                rear_headway_sec=trains[1].rear_headway_sec,
                platform_crowding_level="LOW",
                load_factor=trains[1].onboard_pax / trains[1].capacity_pax,
                power_traction_limit_ratio=1.0,
            ),
            DispatchContext(
                sim_time_ms=sim_time_ms + 60_000,
                train_id=trains[2].train_id,
                station_id=trains[2].station_id,
                front_headway_sec=trains[2].front_headway_sec,
                platform_crowding_level="HIGH",
                load_factor=trains[2].onboard_pax / trains[2].capacity_pax,
                power_traction_limit_ratio=1.0,
            ),
        ]
        return [dispatch_service.decide(context) for context in contexts]

    def _summary(
        self,
        run_id: int,
        station_results: list[dict[str, Any]],
        power_states: dict[str, PowerState],
        decisions: list[DispatchDecision],
        recorder: RunRecorder,
    ) -> dict[str, Any]:
        counts = self._table_counts(
            recorder,
            run_id,
            [
                "station_passenger_records",
                "train_load_records",
                "dwell_records",
                "dispatch_decisions",
                "power_records",
                "metrics",
            ],
        )
        return {
            "phase": 2,
            "module": "member-d-full-line9",
            "line": "Beijing Subway Line 9 (all 13 stations)",
            "stationCount": len(station_results),
            "runId": run_id,
            "recordDb": str(self.db_path),
            "stations": station_results,
            "powerStates": {
                section_id: {
                    "requestedPowerKw": round(s.requested_power_kw, 3),
                    "availablePowerKw": round(s.available_power_kw, 3),
                    "tractionLimitRatio": round(s.traction_limit_ratio, 4),
                    "voltageLevel": s.voltage_level,
                    "energyKwh": round(s.energy_kwh, 4),
                    "regenEnergyKwh": round(s.regen_energy_kwh, 4),
                    "source": s.source,
                }
                for section_id, s in power_states.items()
            },
            "dispatch": [
                {
                    "decisionId": d.decision_id,
                    "trainId": d.train_id,
                    "action": d.action,
                    "reason": d.reason,
                    "durationSec": d.duration_sec,
                    "applied": d.applied,
                }
                for d in decisions
            ],
            "counts": counts,
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
