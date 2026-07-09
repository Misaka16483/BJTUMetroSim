from __future__ import annotations

from collections import defaultdict

from app.domain.power.network import TractionPowerNetwork
from app.domain.power.network_models import (
    FeederPowerFlow,
    PowerFlowSnapshot,
    SubstationPowerFlow,
    TrainElectricalLoad,
    TrainPowerFlow,
)


class DCTractionPowerFlowSolver:
    """Reduced quasi-static DC traction power-flow solver for Line 9 V0."""

    def __init__(self, network: TractionPowerNetwork) -> None:
        self.network = network
        self._substation_energy_kwh: dict[str, float] = defaultdict(float)

    def solve(
        self,
        loads: list[TrainElectricalLoad],
        *,
        dt_sec: float,
        sim_time_ms: int = 0,
    ) -> PowerFlowSnapshot:
        substation_currents: dict[str, float] = defaultdict(float)
        feeder_currents: dict[str, float] = defaultdict(float)
        losses_kw = 0.0
        gross_traction_kw = sum(max(load.requested_power_kw, 0.0) for load in loads)
        generated_regen_kw = sum(load.regen_power_kw for load in loads)
        initial: list[tuple[TrainElectricalLoad, float, float, float, float, str, str]] = []

        for load in loads:
            section = self.network.locate_section(load.mileage_m, load.direction)
            left = self.network.substations[section.left_substation_id]
            right = self.network.substations[section.right_substation_id]
            left_r, right_r = self._path_resistances(load.mileage_m, load.direction)
            requested_kw = max(load.requested_power_kw, 0.0)
            if requested_kw <= 0:
                initial.append((load, self.network.nominal_voltage_v, 0.0, 0.0, 0.0, left.substation_id, right.substation_id))
                continue
            voltage_v = self._solve_voltage(requested_kw, left.no_load_voltage_v, right.no_load_voltage_v, left_r, right_r)
            current_a = requested_kw * 1000.0 / max(voltage_v, 1.0)
            left_share, right_share = self._current_shares(left_r, right_r)
            left_current = current_a * left_share if left.in_service else 0.0
            right_current = current_a * right_share if right.in_service else 0.0
            substation_currents[left.substation_id] += left_current
            substation_currents[right.substation_id] += right_current
            feeder_currents[f"FD-{left.substation_id[-4:]}-{load.direction.upper()}-RIGHT"] += left_current
            feeder_currents[f"FD-{right.substation_id[-4:]}-{load.direction.upper()}-LEFT"] += right_current
            losses_kw += (left_current ** 2 * left_r + right_current ** 2 * right_r) / 1000.0
            initial.append((load, voltage_v, current_a, left_current, right_current, left.substation_id, right.substation_id))

        terminal_voltage = {
            sub.substation_id: max(0.0, sub.no_load_voltage_v - substation_currents[sub.substation_id] * sub.internal_resistance_ohm)
            if sub.in_service else 0.0
            for sub in self.network.substations.values()
        }

        absorbed_regen_kw = min(generated_regen_kw, gross_traction_kw)
        remaining_regen_kw = max(generated_regen_kw - absorbed_regen_kw, 0.0)
        efs_capacity_kw = sum(sub.efs_capacity_kw for sub in self.network.substations.values() if sub.in_service)
        feedback_regen_kw = min(remaining_regen_kw, efs_capacity_kw)
        wasted_regen_kw = max(remaining_regen_kw - feedback_regen_kw, 0.0)

        trains: list[TrainPowerFlow] = []
        for load, _v0, _i0, _il, _ir, left_id, right_id in initial:
            requested_kw = max(load.requested_power_kw, 0.0)
            if requested_kw > 0:
                left_r, right_r = self._path_resistances(load.mileage_m, load.direction)
                voltage_v = self._solve_voltage(
                    requested_kw,
                    terminal_voltage[left_id],
                    terminal_voltage[right_id],
                    left_r,
                    right_r,
                )
                current_a = requested_kw * 1000.0 / max(voltage_v, 1.0)
            elif load.regen_power_kw > 0:
                voltage_v = 875.0
                if wasted_regen_kw > 0:
                    voltage_v = 930.0 if efs_capacity_kw > 0 else 1000.0
                current_a = -load.regen_power_kw * 1000.0 / max(voltage_v, 1.0)
            else:
                voltage_v = self.network.nominal_voltage_v
                current_a = 0.0
            traction_limit_ratio, regen_limit_ratio, voltage_level = self._limits(voltage_v)
            if load.regen_power_kw > 0 and wasted_regen_kw > 0 and voltage_level == "NORMAL":
                voltage_level = "REGEN_LIMITED"
                regen_limit_ratio = min(regen_limit_ratio, 0.75)
            trains.append(
                TrainPowerFlow(
                    train_id=load.train_id,
                    power_section_id=self.network.locate_section(load.mileage_m, load.direction).section_id,
                    mileage_m=load.mileage_m,
                    voltage_v=voltage_v,
                    current_a=current_a,
                    requested_power_kw=load.requested_power_kw,
                    traction_limit_ratio=traction_limit_ratio,
                    regen_limit_ratio=regen_limit_ratio,
                    voltage_level=voltage_level,
                    left_substation_id=left_id,
                    right_substation_id=right_id,
                )
            )

        alerts = self._build_alerts(trains, substation_currents, feeder_currents, wasted_regen_kw)
        substations = self._substation_flows(substation_currents, terminal_voltage, dt_sec)
        feeders = self._feeder_flows(feeder_currents, terminal_voltage)
        return PowerFlowSnapshot(
            sim_time_ms=sim_time_ms,
            trains=trains,
            substations=substations,
            feeders=feeders,
            generated_regen_kw=generated_regen_kw,
            absorbed_regen_kw=absorbed_regen_kw,
            feedback_regen_kw=feedback_regen_kw,
            wasted_regen_kw=wasted_regen_kw,
            losses_kw=losses_kw,
            alerts=alerts,
        )

    def _path_resistances(self, mileage_m: float, direction: str) -> tuple[float, float]:
        section = self.network.locate_section(mileage_m, direction)
        left = self.network.substations[section.left_substation_id]
        right = self.network.substations[section.right_substation_id]
        left_distance_km = max(0.001, abs(mileage_m - left.mileage_m) / 1000.0)
        right_distance_km = max(0.001, abs(right.mileage_m - mileage_m) / 1000.0)
        rail_r = 0.0083 + 0.0083
        left_feeder = self.network.feeder_for(left.substation_id, direction, "RIGHT")
        right_feeder = self.network.feeder_for(right.substation_id, direction, "LEFT")
        left_feeder_r = left_feeder.cable_resistance_ohm if left_feeder and left_feeder.closed and left.in_service else 1_000_000.0
        right_feeder_r = right_feeder.cable_resistance_ohm if right_feeder and right_feeder.closed and right.in_service else 1_000_000.0
        left_r = (left.internal_resistance_ohm if left.in_service else 1_000_000.0) + left_feeder_r + rail_r * left_distance_km
        right_r = (right.internal_resistance_ohm if right.in_service else 1_000_000.0) + right_feeder_r + rail_r * right_distance_km
        return max(left_r, 1e-6), max(right_r, 1e-6)

    @staticmethod
    def _solve_voltage(power_kw: float, left_voltage_v: float, right_voltage_v: float, left_r: float, right_r: float) -> float:
        if power_kw <= 0:
            return max(left_voltage_v, right_voltage_v)
        upper = max(left_voltage_v, right_voltage_v, 1.0)
        lower = 1.0
        target_w = power_kw * 1000.0
        for _ in range(80):
            mid = (lower + upper) / 2.0
            current_left = max((left_voltage_v - mid) / left_r, 0.0)
            current_right = max((right_voltage_v - mid) / right_r, 0.0)
            supply_w = mid * (current_left + current_right)
            if supply_w >= target_w:
                lower = mid
            else:
                upper = mid
        return max(lower, 0.0)

    @staticmethod
    def _current_shares(left_r: float, right_r: float) -> tuple[float, float]:
        left_g = 1.0 / left_r
        right_g = 1.0 / right_r
        total = left_g + right_g
        if total <= 0:
            return 0.5, 0.5
        return left_g / total, right_g / total

    @staticmethod
    def _limits(voltage_v: float) -> tuple[float, float, str]:
        if voltage_v >= 1000.0:
            return 1.0, 0.0, "OVERVOLTAGE"
        if voltage_v >= 900.0:
            return 1.0, max(0.0, (1000.0 - voltage_v) / 100.0), "OVERVOLTAGE_WARNING"
        if voltage_v >= 650.0:
            return 1.0, 1.0, "NORMAL"
        if voltage_v >= 500.0:
            return max(0.35, (voltage_v - 500.0) / 150.0), 1.0, "LIMITED"
        return 0.0, 1.0, "UNDERVOLTAGE"

    def _substation_flows(
        self,
        substation_currents: dict[str, float],
        terminal_voltage: dict[str, float],
        dt_sec: float,
    ) -> list[SubstationPowerFlow]:
        flows: list[SubstationPowerFlow] = []
        for sub in self.network.ordered_substations:
            current = substation_currents[sub.substation_id]
            voltage = terminal_voltage[sub.substation_id]
            power_kw = voltage * current / 1000.0
            self._substation_energy_kwh[sub.substation_id] += max(power_kw, 0.0) * max(dt_sec, 0.0) / 3600.0
            if not sub.in_service:
                status = "OUTAGE"
            elif current > sub.overload_current_a:
                status = "OVERLOAD"
            elif current > sub.rated_current_a:
                status = "WARNING"
            else:
                status = "NORMAL"
            flows.append(
                SubstationPowerFlow(
                    substation_id=sub.substation_id,
                    name=sub.name,
                    mileage_m=sub.mileage_m,
                    voltage_v=voltage,
                    current_a=current,
                    power_kw=power_kw,
                    energy_kwh=self._substation_energy_kwh[sub.substation_id],
                    load_ratio=current / sub.rated_current_a if sub.rated_current_a else 0.0,
                    status=status,
                )
            )
        return flows

    def _feeder_flows(
        self,
        feeder_currents: dict[str, float],
        terminal_voltage: dict[str, float],
    ) -> list[FeederPowerFlow]:
        flows: list[FeederPowerFlow] = []
        for feeder in self.network.feeders.values():
            current = feeder_currents[feeder.feeder_id]
            voltage = terminal_voltage.get(feeder.substation_id, self.network.nominal_voltage_v)
            if not feeder.closed:
                status = "OPEN"
            elif current > feeder.short_time_current_a:
                status = "OVERLOAD"
            elif current > feeder.continuous_current_a:
                status = "WARNING"
            else:
                status = "NORMAL"
            flows.append(
                FeederPowerFlow(
                    feeder_id=feeder.feeder_id,
                    substation_id=feeder.substation_id,
                    direction=feeder.direction,
                    side=feeder.side,
                    current_a=current,
                    power_kw=voltage * current / 1000.0,
                    load_ratio=current / feeder.continuous_current_a if feeder.continuous_current_a else 0.0,
                    status=status,
                )
            )
        return flows

    def _build_alerts(
        self,
        trains: list[TrainPowerFlow],
        substation_currents: dict[str, float],
        feeder_currents: dict[str, float],
        wasted_regen_kw: float,
    ) -> list[dict]:
        alerts: list[dict] = []
        for train in trains:
            if train.voltage_level in {"UNDERVOLTAGE", "LIMITED", "OVERVOLTAGE", "OVERVOLTAGE_WARNING", "REGEN_LIMITED"}:
                alerts.append({
                    "type": train.voltage_level,
                    "targetType": "TRAIN",
                    "targetId": train.train_id,
                    "voltageV": round(train.voltage_v, 2),
                })
        for sub in self.network.substations.values():
            current = substation_currents[sub.substation_id]
            if current > sub.overload_current_a:
                alerts.append({"type": "SUBSTATION_OVERLOAD", "targetId": sub.substation_id, "currentA": round(current, 2)})
            elif current > sub.rated_current_a:
                alerts.append({"type": "SUBSTATION_WARNING", "targetId": sub.substation_id, "currentA": round(current, 2)})
            if not sub.in_service:
                alerts.append({"type": "SUBSTATION_OUTAGE", "targetId": sub.substation_id})
        for feeder in self.network.feeders.values():
            current = feeder_currents[feeder.feeder_id]
            if current > feeder.short_time_current_a:
                alerts.append({"type": "FEEDER_OVERLOAD", "targetId": feeder.feeder_id, "currentA": round(current, 2)})
            elif current > feeder.continuous_current_a:
                alerts.append({"type": "FEEDER_WARNING", "targetId": feeder.feeder_id, "currentA": round(current, 2)})
        if wasted_regen_kw > 0:
            alerts.append({"type": "REGEN_WASTED", "targetId": "NETWORK", "powerKw": round(wasted_regen_kw, 3)})
        return alerts
