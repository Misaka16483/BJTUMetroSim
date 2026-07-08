from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.domain.dispatch.services import DispatchContext, DispatchDecision, RuleBasedDispatchService
from app.domain.power.services import PowerSection, PowerService, PowerState, TrainPowerRequest
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


class Phase2MemberDDemoRunner:
    """Runs a deterministic peak-period scenario for member D Phase 2 services."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def run(self) -> dict[str, Any]:
        recorder = RunRecorder(self.db_path)
        try:
            run_id = recorder.start_run(
                "phase2-member-d-demo",
                {
                    "phase": 2,
                    "scope": "passenger-load-dispatch-power",
                    "line": "Beijing Subway Line 9 demo subset",
                    "powerSource": "SELF_SIM",
                },
            )
            station_service = self._build_station_service()
            power_service = self._build_power_service()
            dispatch_service = RuleBasedDispatchService()
            trains = self._build_train_states()
            primary_train = trains[0]

            sim_time_ms = 8 * 60 * 60 * 1000
            arrivals_by_platform = station_service.update_arrivals(sim_time_ms, dt_sec=180.0)
            platform = station_service.ensure_platform(
                primary_train.station_id,
                primary_train.direction,
                platform_area_m2=90.0,
            )

            boarding_result, dwell_plan = station_service.process_train_stop(
                sim_time_ms=sim_time_ms,
                station_id=primary_train.station_id,
                direction=primary_train.direction,
                train_load=TrainLoadState(
                    primary_train.train_id,
                    onboard_pax=primary_train.onboard_pax,
                    capacity_pax=primary_train.capacity_pax,
                ),
                platform_area_m2=90.0,
            )
            self._record_station_stop(
                recorder,
                run_id,
                sim_time_ms,
                boarding_result,
                dwell_plan,
                arrivals_by_platform.get(("S-GGZ", "UP"), 0),
                platform,
            )

            secondary_platform = station_service.ensure_platform("S-FSP", "UP", platform_area_m2=110.0)
            self._record_station_snapshot(
                recorder,
                run_id,
                sim_time_ms,
                "S-FSP",
                "UP",
                arrivals_by_platform.get(("S-FSP", "UP"), 0),
                secondary_platform,
            )

            power_states = power_service.update(
                [
                    TrainPowerRequest("T0901", "PWR-0901", speed_mps=10.0, traction_force_n=70_000.0),
                    TrainPowerRequest("T0902", "PWR-0901", speed_mps=10.0, traction_force_n=70_000.0),
                    TrainPowerRequest("T0903", "PWR-0901", speed_mps=10.0, brake_force_n=20_000.0),
                ],
                dt_sec=30.0,
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
                    detail={"requests": ["T0901", "T0902", "T0903"]},
                )

            decisions = self._make_dispatch_decisions(
                dispatch_service,
                sim_time_ms,
                boarding_result,
                platform,
                power_states["PWR-0901"],
                trains,
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
                    detail={"scenario": "member-d-phase2-demo"},
                )

            recorder.record_metric(run_id, "memberD.totalWaitingPax", float(platform.waiting_pax), unit="pax", tick=1)
            recorder.record_metric(
                run_id,
                "memberD.tractionLimitRatio",
                power_states["PWR-0901"].traction_limit_ratio,
                unit="ratio",
                tick=1,
            )
            recorder.record_metric(run_id, "memberD.dispatchDecisionCount", float(len(decisions)), unit="count", tick=1)

            return self._summary(
                run_id,
                boarding_result,
                dwell_plan,
                power_states,
                decisions,
                arrivals_by_platform,
                recorder,
            )
        finally:
            recorder.close()

    def _build_station_service(self) -> StationService:
        return StationService(
            PassengerFlowGenerator(
                [
                    PassengerDemandProfile("S-GGZ", "UP", 7 * 3600, 9 * 3600, 180.0, alighting_ratio=0.08),
                    PassengerDemandProfile("S-FSP", "UP", 7 * 3600, 9 * 3600, 72.0, alighting_ratio=0.14),
                    PassengerDemandProfile("S-KYL", "UP", 7 * 3600, 9 * 3600, 48.0, alighting_ratio=0.16),
                ]
            ),
            DwellTimeConfig(base_dwell_sec=30.0, door_capacity_pax_per_sec=4.0),
        )

    def _build_power_service(self) -> PowerService:
        return PowerService(
            [
                PowerSection(
                    power_section_id="PWR-0901",
                    name="Line 9 demo traction section",
                    max_traction_power_kw=1000.0,
                    warning_power_kw=800.0,
                    regen_absorb_limit_kw=200.0,
                )
            ]
        )

    def _build_train_states(self) -> list[DemoTrainState]:
        return [
            DemoTrainState("T0901", "S-GGZ", "UP", onboard_pax=520, capacity_pax=600, rear_headway_sec=120.0),
            DemoTrainState("T0902", "S-FSP", "UP", onboard_pax=430, capacity_pax=600, rear_headway_sec=45.0),
            DemoTrainState("T0903", "S-KYL", "UP", onboard_pax=330, capacity_pax=600, front_headway_sec=360.0),
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

    def _record_station_snapshot(
        self,
        recorder: RunRecorder,
        run_id: int,
        sim_time_ms: int,
        station_id: str,
        direction: str,
        arrivals: int,
        platform: PlatformCrowdState,
    ) -> None:
        recorder.record_station_passenger(
            run_id,
            sim_time_ms=sim_time_ms,
            station_id=station_id,
            direction=direction,
            arrivals=arrivals,
            waiting=platform.waiting_pax,
            left_behind=platform.left_behind_pax,
            platform_density_pax_per_m2=platform.platform_density_pax_per_m2,
            crowding_level=platform.crowding_level,
            detail={"snapshotOnly": True},
        )

    def _make_dispatch_decisions(
        self,
        dispatch_service: RuleBasedDispatchService,
        sim_time_ms: int,
        boarding_result: BoardingResult,
        platform: PlatformCrowdState,
        power_state: PowerState,
        trains: list[DemoTrainState],
    ) -> list[DispatchDecision]:
        contexts = [
            DispatchContext(
                sim_time_ms=sim_time_ms,
                train_id=trains[0].train_id,
                station_id=boarding_result.station_id,
                rear_headway_sec=trains[0].rear_headway_sec,
                platform_crowding_level=platform.crowding_level,
                load_factor=boarding_result.updated_load.load_factor,
                left_behind_pax=boarding_result.left_behind,
                power_traction_limit_ratio=power_state.traction_limit_ratio,
            ),
            DispatchContext(
                sim_time_ms=sim_time_ms + 30_000,
                train_id=trains[1].train_id,
                station_id=trains[1].station_id,
                rear_headway_sec=trains[1].rear_headway_sec,
                platform_crowding_level="MEDIUM",
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
        boarding_result: BoardingResult,
        dwell_plan: DwellPlan,
        power_states: dict[str, PowerState],
        decisions: list[DispatchDecision],
        arrivals_by_platform: dict[tuple[str, str], int],
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
            "runId": run_id,
            "recordDb": str(self.db_path),
            "counts": counts,
            "arrivals": {
                f"{station}:{direction}": value for (station, direction), value in arrivals_by_platform.items()
            },
            "trainLoad": {
                "trainId": boarding_result.train_id,
                "onboardPax": boarding_result.updated_load.onboard_pax,
                "capacityPax": boarding_result.updated_load.capacity_pax,
                "loadFactor": round(boarding_result.updated_load.load_factor, 4),
                "leftBehindPax": boarding_result.left_behind,
            },
            "dwell": {
                "trainId": dwell_plan.train_id,
                "stationId": dwell_plan.station_id,
                "plannedSec": dwell_plan.planned_dwell_sec,
                "estimatedSec": round(dwell_plan.estimated_dwell_sec, 2),
                "canDepart": dwell_plan.can_depart,
            },
            "power": {section_id: self._power_state_to_dict(state) for section_id, state in power_states.items()},
            "dispatch": [self._decision_to_dict(decision) for decision in decisions],
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

    def _power_state_to_dict(self, state: PowerState) -> dict[str, Any]:
        return {
            "requestedPowerKw": round(state.requested_power_kw, 3),
            "availablePowerKw": round(state.available_power_kw, 3),
            "tractionLimitRatio": round(state.traction_limit_ratio, 4),
            "voltageLevel": state.voltage_level,
            "energyKwh": round(state.energy_kwh, 4),
            "regenEnergyKwh": round(state.regen_energy_kwh, 4),
            "absorbedRegenKw": round(state.absorbed_regen_kw, 3),
            "wastedRegenKw": round(state.wasted_regen_kw, 3),
            "source": state.source,
            "quality": state.quality,
        }

    def _decision_to_dict(self, decision: DispatchDecision) -> dict[str, Any]:
        return {
            "decisionId": decision.decision_id,
            "simTimeMs": decision.sim_time_ms,
            "trainId": decision.train_id,
            "stationId": decision.station_id,
            "action": decision.action,
            "durationSec": decision.duration_sec,
            "reason": decision.reason,
            "applied": decision.applied,
            "expectedImpact": decision.expected_impact or {},
        }
