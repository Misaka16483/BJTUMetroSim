from __future__ import annotations

from dataclasses import dataclass

from app.domain.power.flow_solver import DCTractionPowerFlowSolver
from app.domain.power.network import TractionPowerNetwork
from app.domain.power.network_models import PowerFlowSnapshot, TrainElectricalLoad


@dataclass(frozen=True)
class PowerSection:
    power_section_id: str
    name: str
    max_traction_power_kw: float
    warning_power_kw: float
    regen_absorb_limit_kw: float = 0.0
    min_limit_ratio: float = 0.35
    outage: bool = False


@dataclass(frozen=True)
class TrainPowerRequest:
    train_id: str
    power_section_id: str
    speed_mps: float
    traction_force_n: float = 0.0
    brake_force_n: float = 0.0
    position_m: float | None = None
    direction: str = "UP"
    aux_power_kw: float = 150.0
    head_mileage_m: float | None = None
    tail_mileage_m: float | None = None
    pantograph_mileages_m: tuple[float, ...] = ()
    traction_power_request_kw: float | None = None
    regen_power_available_kw: float | None = None


@dataclass(frozen=True)
class PowerState:
    power_section_id: str
    requested_power_kw: float
    available_power_kw: float
    traction_limit_ratio: float
    voltage_level: str
    energy_kwh: float
    regen_energy_kwh: float
    absorbed_regen_kw: float
    wasted_regen_kw: float
    generated_regen_kw: float = 0.0
    self_consumed_regen_kw: float = 0.0
    source: str = "SELF_SIM"
    quality: str = "ENGINEERING_ESTIMATE"
    min_train_voltage_v: float = 750.0
    max_train_current_a: float = 0.0
    substation_count: int = 0
    overloaded_substations: int = 0
    overloaded_feeders: int = 0
    losses_kw: float = 0.0
    feedback_regen_kw: float = 0.0
    alerts: tuple[dict, ...] = ()


class PowerService:
    """Phase 2 self-developed reduced traction-power model."""

    def __init__(
        self,
        sections: list[PowerSection],
        *,
        traction_efficiency: float = 0.88,
        regen_efficiency: float = 0.65,
        limited_slope: float = 0.6,
        network: TractionPowerNetwork | None = None,
    ) -> None:
        self.sections = {section.power_section_id: section for section in sections}
        self.traction_efficiency = traction_efficiency
        self.regen_efficiency = regen_efficiency
        self.limited_slope = limited_slope
        self.network = network
        self.solver = DCTractionPowerFlowSolver(network) if network is not None else None
        self.last_network_snapshot: PowerFlowSnapshot | None = None
        self.last_valid_network_snapshot: PowerFlowSnapshot | None = None
        self.last_failed_network_snapshot: PowerFlowSnapshot | None = None
        self.last_solver_failure: dict | None = None
        self.max_balance_error_ratio = 0.01
        self._energy_kwh_by_section: dict[str, float] = {section.power_section_id: 0.0 for section in sections}
        self._regen_kwh_by_section: dict[str, float] = {section.power_section_id: 0.0 for section in sections}
        self._overload_duration_sec: dict[str, float] = {}
        self.protection_trip_delay_sec = 2.0

    def update(
        self,
        requests: list[TrainPowerRequest],
        dt_sec: float,
        *,
        sim_time_ms: int = 0,
    ) -> dict[str, PowerState]:
        if self.solver is not None and all(request.position_m is not None for request in requests):
            return self._update_network(requests, dt_sec, sim_time_ms)
        self.last_network_snapshot = None
        return self._update_legacy(requests, dt_sec)

    def _update_legacy(self, requests: list[TrainPowerRequest], dt_sec: float) -> dict[str, PowerState]:
        states: dict[str, PowerState] = {}
        requests_by_section: dict[str, list[TrainPowerRequest]] = {}
        for request in requests:
            requests_by_section.setdefault(request.power_section_id, []).append(request)

        for section_id, section in self.sections.items():
            section_requests = requests_by_section.get(section_id, [])
            gross_traction_kw = sum(self._traction_power_kw(item) for item in section_requests)
            generated_regen_kw = sum(self._raw_regen_power_kw(item) for item in section_requests)
            self_consumed_regen_kw = sum(
                min(self._raw_regen_power_kw(item), item.aux_power_kw)
                for item in section_requests
            )
            exported_regen_kw = max(generated_regen_kw - self_consumed_regen_kw, 0.0)
            absorbed_regen_kw = min(exported_regen_kw, gross_traction_kw, section.regen_absorb_limit_kw)
            wasted_regen_kw = max(exported_regen_kw - absorbed_regen_kw, 0.0)
            net_requested_kw = max(gross_traction_kw - absorbed_regen_kw, 0.0)

            self._energy_kwh_by_section[section_id] += net_requested_kw * dt_sec / 3600.0
            self._regen_kwh_by_section[section_id] += generated_regen_kw * dt_sec / 3600.0

            voltage_level, traction_limit_ratio = self._voltage_and_limit(section, net_requested_kw)
            states[section_id] = PowerState(
                power_section_id=section_id,
                requested_power_kw=net_requested_kw,
                available_power_kw=0.0 if section.outage else section.max_traction_power_kw,
                traction_limit_ratio=traction_limit_ratio,
                voltage_level=voltage_level,
                energy_kwh=self._energy_kwh_by_section[section_id],
                regen_energy_kwh=self._regen_kwh_by_section[section_id],
                absorbed_regen_kw=absorbed_regen_kw,
                wasted_regen_kw=wasted_regen_kw,
                generated_regen_kw=generated_regen_kw,
                self_consumed_regen_kw=self_consumed_regen_kw,
            )
        return states

    def _update_network(
        self,
        requests: list[TrainPowerRequest],
        dt_sec: float,
        sim_time_ms: int,
    ) -> dict[str, PowerState]:
        loads = [
            TrainElectricalLoad(
                train_id=request.train_id,
                direction=request.direction,
                mileage_m=float(request.position_m or 0.0),
                speed_mps=request.speed_mps,
                traction_force_n=request.traction_force_n,
                brake_force_n=request.brake_force_n,
                aux_power_kw=request.aux_power_kw,
                traction_efficiency=self.traction_efficiency,
                regen_efficiency=self.regen_efficiency,
                traction_power_request_kw=request.traction_power_request_kw,
                regen_power_available_kw=request.regen_power_available_kw,
                head_mileage_m=request.head_mileage_m,
                tail_mileage_m=request.tail_mileage_m,
                pantograph_mileages_m=request.pantograph_mileages_m,
            )
            for request in requests
        ]
        snapshot = self.solver.solve(loads, dt_sec=dt_sec, sim_time_ms=sim_time_ms) if self.solver else None
        if snapshot is None:
            return self._update_legacy(requests, dt_sec)
        protection_events = self._apply_protection(snapshot, dt_sec)
        if protection_events and self.solver is not None:
            snapshot = self.solver.solve(loads, dt_sec=0.0, sim_time_ms=sim_time_ms)
            snapshot.alerts.extend(protection_events)

        failure_reasons: list[str] = []
        if not snapshot.converged:
            failure_reasons.append("NOT_CONVERGED")
        if snapshot.power_balance_error_ratio > self.max_balance_error_ratio:
            failure_reasons.append("POWER_BALANCE_EXCEEDED")
        accounting_dt_sec = dt_sec
        if failure_reasons:
            self.last_failed_network_snapshot = snapshot
            self.last_solver_failure = {
                "type": "POWER_SOLVER_FAILURE",
                "reasons": failure_reasons,
                "simTimeMs": sim_time_ms,
                "iterations": snapshot.iterations,
                "powerBalanceErrorRatio": snapshot.power_balance_error_ratio,
            }
            if self.last_valid_network_snapshot is not None:
                snapshot = self.last_valid_network_snapshot
                accounting_dt_sec = 0.0
            else:
                self.last_network_snapshot = None
                return self._update_legacy(requests, 0.0)
        else:
            self.last_solver_failure = None
            self.last_valid_network_snapshot = snapshot
        self.last_network_snapshot = snapshot

        request_section_by_train = {request.train_id: request.power_section_id for request in requests}
        requests_by_train = {request.train_id: request for request in requests}
        flows_by_section: dict[str, list] = {section_id: [] for section_id in self.sections}
        for flow in snapshot.trains:
            legacy_section_id = request_section_by_train.get(flow.train_id, flow.power_section_id)
            flows_by_section.setdefault(legacy_section_id, []).append(flow)

        states: dict[str, PowerState] = {}
        substation_count = len(snapshot.substations)
        overloaded_substations = len([item for item in snapshot.substations if item.status == "OVERLOAD"])
        overloaded_feeders = len([item for item in snapshot.feeders if item.status == "OVERLOAD"])

        for section_id, section in self.sections.items():
            flows = flows_by_section.get(section_id, [])
            requested_power_kw = sum(max(flow.requested_power_kw, 0.0) for flow in flows)
            generated_regen_kw = sum(
                self._raw_regen_power_kw(requests_by_train[flow.train_id])
                for flow in flows
            )
            self_consumed_regen_kw = sum(flow.regen_power_self_consumed_kw for flow in flows)
            absorbed_regen_kw = sum(
                path.delivered_kw
                for path in snapshot.regen_paths
                if path.sink_type == "TRAIN" and request_section_by_train.get(path.sink_id) == section_id
            )
            feedback_regen_kw = sum(
                path.delivered_kw
                for path in snapshot.regen_paths
                if path.sink_type == "SUBSTATION_FEEDBACK"
                and request_section_by_train.get(path.source_train_id) == section_id
            )
            wasted_regen_kw = sum(
                path.generated_kw
                for path in snapshot.regen_paths
                if path.sink_type == "WASTE"
                and request_section_by_train.get(path.source_train_id) == section_id
            )
            net_requested_kw = max(requested_power_kw - absorbed_regen_kw, 0.0)
            self._energy_kwh_by_section[section_id] += net_requested_kw * max(accounting_dt_sec, 0.0) / 3600.0
            self._regen_kwh_by_section[section_id] += generated_regen_kw * max(accounting_dt_sec, 0.0) / 3600.0

            min_voltage = min((flow.voltage_v for flow in flows), default=self.network.nominal_voltage_v if self.network else 750.0)
            max_current = max((abs(flow.current_a) for flow in flows), default=0.0)
            traction_limit_ratio = min((flow.traction_limit_ratio for flow in flows), default=1.0)
            voltage_level = self._worst_voltage_level([flow.voltage_level for flow in flows])
            states[section_id] = PowerState(
                power_section_id=section_id,
                requested_power_kw=net_requested_kw,
                available_power_kw=0.0 if section.outage else section.max_traction_power_kw,
                traction_limit_ratio=traction_limit_ratio,
                voltage_level=voltage_level,
                energy_kwh=self._energy_kwh_by_section[section_id],
                regen_energy_kwh=self._regen_kwh_by_section[section_id],
                absorbed_regen_kw=absorbed_regen_kw,
                wasted_regen_kw=wasted_regen_kw,
                generated_regen_kw=generated_regen_kw,
                self_consumed_regen_kw=self_consumed_regen_kw,
                min_train_voltage_v=min_voltage,
                max_train_current_a=max_current,
                substation_count=substation_count,
                overloaded_substations=overloaded_substations,
                overloaded_feeders=overloaded_feeders,
                losses_kw=snapshot.losses_kw,
                feedback_regen_kw=feedback_regen_kw,
                alerts=tuple(snapshot.alerts),
            )
        return states

    def _apply_protection(self, snapshot: PowerFlowSnapshot, dt_sec: float) -> list[dict]:
        if self.network is None:
            return []
        events: list[dict] = []
        active_overloads: set[str] = set()
        for feeder in snapshot.feeders:
            if feeder.status != "OVERLOAD":
                continue
            active_overloads.add(feeder.feeder_id)
            duration = self._overload_duration_sec.get(feeder.feeder_id, 0.0) + max(dt_sec, 0.0)
            self._overload_duration_sec[feeder.feeder_id] = duration
            if duration >= self.protection_trip_delay_sec:
                self.network.set_feeder_status(feeder.feeder_id, "OPEN")
                events.append({
                    "type": "FEEDER_PROTECTION_TRIP",
                    "targetId": feeder.feeder_id,
                    "durationSec": round(duration, 3),
                })
        for substation in snapshot.substations:
            if substation.status != "OVERLOAD":
                continue
            active_overloads.add(substation.substation_id)
            duration = self._overload_duration_sec.get(substation.substation_id, 0.0) + max(dt_sec, 0.0)
            self._overload_duration_sec[substation.substation_id] = duration
            if duration >= self.protection_trip_delay_sec and self.network.substations[substation.substation_id].in_service:
                self.network.apply_substation_outage(substation.substation_id, big_bilateral=True)
                events.append({
                    "type": "SUBSTATION_PROTECTION_TRIP",
                    "targetId": substation.substation_id,
                    "durationSec": round(duration, 3),
                })
        for section in snapshot.contact_rail_flows:
            if section.status != "OVERLOAD":
                continue
            active_overloads.add(section.section_id)
            duration = self._overload_duration_sec.get(section.section_id, 0.0) + max(dt_sec, 0.0)
            self._overload_duration_sec[section.section_id] = duration
            network_section = self.network.contact_sections[section.section_id]
            if duration >= self.protection_trip_delay_sec and network_section.status == "ENERGIZED":
                self.network.set_contact_section_status(section.section_id, "DEENERGIZED")
                events.append({
                    "type": "CONTACT_RAIL_PROTECTION_TRIP",
                    "targetId": section.section_id,
                    "durationSec": round(duration, 3),
                })
        for target_id in list(self._overload_duration_sec):
            if target_id not in active_overloads:
                self._overload_duration_sec.pop(target_id, None)
        return events

    def _traction_power_kw(self, request: TrainPowerRequest) -> float:
        if request.traction_power_request_kw is not None:
            return max(request.traction_power_request_kw, 0.0) + max(
                request.aux_power_kw - self._raw_regen_power_kw(request),
                0.0,
            )
        if request.traction_force_n <= 0 or request.speed_mps <= 0:
            return request.aux_power_kw
        return request.traction_force_n * request.speed_mps / 1000.0 / self.traction_efficiency + request.aux_power_kw

    def _regen_power_kw(self, request: TrainPowerRequest) -> float:
        return max(self._raw_regen_power_kw(request) - request.aux_power_kw, 0.0)

    def _raw_regen_power_kw(self, request: TrainPowerRequest) -> float:
        if request.regen_power_available_kw is not None:
            return max(request.regen_power_available_kw, 0.0)
        if request.brake_force_n <= 0 or request.speed_mps <= 0:
            return 0.0
        return request.brake_force_n * request.speed_mps / 1000.0 * self.regen_efficiency

    def _voltage_and_limit(self, section: PowerSection, requested_power_kw: float) -> tuple[str, float]:
        if section.outage:
            return "OUTAGE", 0.0
        if requested_power_kw <= 0:
            return "NORMAL", 1.0
        if section.max_traction_power_kw <= 0:
            return "OUTAGE", 0.0

        warning_ratio = min(max(section.warning_power_kw / section.max_traction_power_kw, 0.0), 1.0)
        load_ratio = requested_power_kw / section.max_traction_power_kw
        if load_ratio <= warning_ratio:
            return "NORMAL", 1.0
        if load_ratio <= 1.0:
            limit = 1.0 - self.limited_slope * (load_ratio - warning_ratio)
            return "LIMITED", max(section.min_limit_ratio, min(1.0, limit))
        return "UNDERVOLTAGE", max(section.min_limit_ratio, min(1.0, 1.0 / load_ratio))

    @staticmethod
    def _section_share(value: float, total: float) -> float:
        if value <= 0 or total <= 0:
            return 0.0
        return min(max(value / total, 0.0), 1.0)

    @staticmethod
    def _worst_voltage_level(levels: list[str]) -> str:
        rank = {
            "OUTAGE": 6,
            "UNDERVOLTAGE": 5,
            "OVERVOLTAGE": 4,
            "OVERVOLTAGE_WARNING": 3,
            "REGEN_LIMITED": 2,
            "LIMITED": 1,
            "NORMAL": 0,
        }
        if not levels:
            return "NORMAL"
        return max(levels, key=lambda level: rank.get(level, 0))

