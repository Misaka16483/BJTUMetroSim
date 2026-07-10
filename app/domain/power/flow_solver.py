from __future__ import annotations

from collections import defaultdict
import time

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
        started = time.perf_counter()
        gross_traction_kw = sum(load.traction_power_kw + load.aux_power_kw for load in loads)
        generated_regen_kw = sum(load.regen_power_kw for load in loads)
        absorbed_regen_kw = min(generated_regen_kw, gross_traction_kw)
        remaining_regen_kw = max(generated_regen_kw - absorbed_regen_kw, 0.0)
        efs_capacity_kw = sum(sub.efs_capacity_kw for sub in self.network.substations.values() if sub.in_service)
        feedback_regen_kw = min(remaining_regen_kw, efs_capacity_kw)
        wasted_regen_kw = max(remaining_regen_kw - feedback_regen_kw, 0.0)

        net_demand_kw = max(gross_traction_kw - absorbed_regen_kw, 0.0)
        demand_scale = net_demand_kw / gross_traction_kw if gross_traction_kw > 0 else 0.0
        effective_demand_kw = {
            load.train_id: (load.traction_power_kw + load.aux_power_kw) * demand_scale
            for load in loads
        }

        terminal_voltage = {
            sub.substation_id: sub.no_load_voltage_v if sub.in_service else 0.0
            for sub in self.network.substations.values()
        }
        substation_currents: dict[str, float] = defaultdict(float)
        feeder_currents: dict[str, float] = defaultdict(float)
        train_solution: dict[str, tuple[float, float, float, list[tuple[str, str, float, float]]]] = {}
        converged = False
        iterations = 0
        losses_kw = 0.0

        for iterations in range(1, 81):
            next_substation_currents: dict[str, float] = defaultdict(float)
            next_feeder_currents: dict[str, float] = defaultdict(float)
            next_train_solution: dict[str, tuple[float, float, float, list[tuple[str, str, float, float]]]] = {}
            next_losses_kw = 0.0
            for load in loads:
                requested_kw = effective_demand_kw[load.train_id]
                paths = self._source_paths(load.mileage_m, load.direction)
                if requested_kw <= 0 or not paths:
                    next_train_solution[load.train_id] = (self.network.nominal_voltage_v if paths else 0.0, 0.0, 1.0, [])
                    continue
                sources = [(terminal_voltage[sub_id], resistance) for sub_id, _feeder_id, resistance in paths]
                voltage_v, delivered_ratio = self._solve_voltage_multi(requested_kw, sources)
                source_contributions: list[tuple[str, str, float, float]] = []
                total_current = 0.0
                for sub_id, feeder_id, resistance in paths:
                    current_a = max((terminal_voltage[sub_id] - voltage_v) / resistance, 0.0)
                    if current_a <= 0:
                        continue
                    next_substation_currents[sub_id] += current_a
                    next_feeder_currents[feeder_id] += current_a
                    next_losses_kw += current_a * current_a * resistance / 1000.0
                    total_current += current_a
                    source_contributions.append((sub_id, feeder_id, resistance, current_a))
                next_train_solution[load.train_id] = (voltage_v, total_current, delivered_ratio, source_contributions)

            next_terminal_voltage = {
                sub.substation_id: max(
                    0.0,
                    sub.no_load_voltage_v
                    - next_substation_currents[sub.substation_id] * sub.internal_resistance_ohm,
                ) if sub.in_service else 0.0
                for sub in self.network.substations.values()
            }
            max_delta_v = max(
                (abs(next_terminal_voltage[key] - terminal_voltage[key]) for key in terminal_voltage),
                default=0.0,
            )
            terminal_voltage = {
                key: 0.25 * next_terminal_voltage[key] + 0.75 * terminal_voltage[key]
                for key in terminal_voltage
            }
            substation_currents = next_substation_currents
            feeder_currents = next_feeder_currents
            train_solution = next_train_solution
            losses_kw = next_losses_kw + sum(
                current * current * self.network.substations[sub_id].internal_resistance_ohm / 1000.0
                for sub_id, current in substation_currents.items()
            )
            if max_delta_v <= 0.10:
                converged = True
                break

        trains: list[TrainPowerFlow] = []
        accepted_regen_ratio = min(
            1.0,
            (absorbed_regen_kw + feedback_regen_kw) / generated_regen_kw,
        ) if generated_regen_kw > 0 else 1.0
        for load in loads:
            section = self.network.locate_section(load.mileage_m, load.direction)
            solution_voltage, solution_current, delivered_ratio, source_contributions = train_solution.get(
                load.train_id,
                (self.network.nominal_voltage_v, 0.0, 1.0, []),
            )
            if load.regen_power_kw > 0:
                paths = self._source_paths(load.mileage_m, load.direction)
                equivalent_r = 1.0 / sum((1.0 / item[2] for item in paths), start=0.0) if paths else 1.0
                accepted_kw = load.regen_power_kw * accepted_regen_ratio
                base_voltage = max((terminal_voltage[item[0]] for item in paths), default=self.network.nominal_voltage_v)
                voltage_v = min(1000.0, base_voltage + accepted_kw * 1000.0 / max(base_voltage, 1.0) * equivalent_r)
                current_a = -accepted_kw * 1000.0 / max(voltage_v, 1.0)
            else:
                voltage_v = solution_voltage
                current_a = solution_current
            traction_limit_ratio, regen_limit_ratio, voltage_level = self._limits(voltage_v)
            traction_limit_ratio = min(traction_limit_ratio, delivered_ratio)
            if load.regen_power_kw > 0:
                regen_limit_ratio = min(regen_limit_ratio, accepted_regen_ratio)
                if regen_limit_ratio < 0.999 and voltage_level == "NORMAL":
                    voltage_level = "REGEN_LIMITED"
            source_ids = [item[0] for item in source_contributions]
            trains.append(
                TrainPowerFlow(
                    train_id=load.train_id,
                    power_section_id=section.section_id,
                    mileage_m=load.mileage_m,
                    voltage_v=voltage_v,
                    current_a=current_a,
                    requested_power_kw=load.requested_power_kw,
                    traction_limit_ratio=traction_limit_ratio,
                    regen_limit_ratio=regen_limit_ratio,
                    voltage_level=voltage_level,
                    left_substation_id=source_ids[0] if source_ids else section.left_substation_id,
                    right_substation_id=source_ids[-1] if source_ids else section.right_substation_id,
                )
            )

        alerts = self._build_alerts(trains, substation_currents, feeder_currents, wasted_regen_kw)
        if not converged:
            alerts.append({"type": "POWER_FLOW_NOT_CONVERGED", "targetId": "NETWORK", "iterations": iterations})
        substations = self._substation_flows(substation_currents, terminal_voltage, dt_sec)
        feeders = self._feeder_flows(feeder_currents, terminal_voltage)
        delivered_demand_kw = sum(
            effective_demand_kw[load.train_id] * train_solution.get(load.train_id, (0.0, 0.0, 0.0, []))[2]
            for load in loads
        )
        source_input_kw = sum(
            self.network.substations[sub_id].no_load_voltage_v * current / 1000.0
            for sub_id, current in substation_currents.items()
        )
        balance_error_kw = abs(source_input_kw - (delivered_demand_kw + losses_kw))
        balance_error_ratio = balance_error_kw / max(delivered_demand_kw + losses_kw, 1.0)
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
            converged=converged,
            iterations=iterations,
            solve_time_ms=(time.perf_counter() - started) * 1000.0,
            power_balance_error_kw=balance_error_kw,
            power_balance_error_ratio=balance_error_ratio,
            alerts=alerts,
        )

    def _source_paths(self, mileage_m: float, direction: str) -> list[tuple[str, str, float]]:
        """Return electrically reachable source, feeder and line resistance tuples."""
        direction = direction.upper()
        ordered = self.network.ordered_substations
        section = self.network.locate_section(mileage_m, direction)
        index_by_id = {item.substation_id: idx for idx, item in enumerate(ordered)}
        left_idx = index_by_id[section.left_substation_id]
        right_idx = index_by_id[section.right_substation_id]
        paths: list[tuple[str, str, float]] = []
        for source_idx, sub in enumerate(ordered):
            if not sub.in_service:
                continue
            side = "RIGHT" if sub.mileage_m <= mileage_m else "LEFT"
            feeder = self.network.feeder_for(sub.substation_id, direction, side)
            if feeder is None or not feeder.closed:
                continue
            if source_idx < left_idx and not self._ties_closed(source_idx + 1, left_idx):
                continue
            if source_idx > right_idx and not self._ties_closed(right_idx + 1, source_idx):
                continue
            rail_resistance = self._rail_resistance(sub.mileage_m, mileage_m, direction)
            if rail_resistance is None:
                continue
            paths.append((sub.substation_id, feeder.feeder_id, max(feeder.cable_resistance_ohm + rail_resistance, 1e-6)))
        return paths

    def _ties_closed(self, first_boundary_idx: int, last_boundary_idx: int) -> bool:
        ordered = self.network.ordered_substations
        for boundary_idx in range(first_boundary_idx, last_boundary_idx + 1):
            switch_id = f"SW-TIE-{ordered[boundary_idx].substation_id[-4:]}"
            switch = self.network.switches.get(switch_id)
            if switch is None or switch.current_state != "CLOSED":
                return False
        return True

    def _rail_resistance(self, source_m: float, load_m: float, direction: str) -> float | None:
        start_m, end_m = sorted((source_m, load_m))
        if end_m - start_m <= 0:
            return 1e-6
        contact_r = 0.0
        return_r = 0.0
        covered_contact_m = 0.0
        covered_return_m = 0.0
        for section in self.network.contact_sections.values():
            if section.direction != direction:
                continue
            overlap_m = max(0.0, min(end_m, section.to_mileage_m) - max(start_m, section.from_mileage_m))
            if overlap_m <= 0:
                continue
            if section.status != "ENERGIZED":
                return None
            contact_r += section.resistance_ohm_per_km * overlap_m / 1000.0
            covered_contact_m += overlap_m
        for section in self.network.return_sections.values():
            if section.direction != direction:
                continue
            overlap_m = max(0.0, min(end_m, section.to_mileage_m) - max(start_m, section.from_mileage_m))
            if overlap_m <= 0:
                continue
            return_r += section.resistance_ohm_per_km * overlap_m / 1000.0
            covered_return_m += overlap_m
        distance_m = end_m - start_m
        if covered_contact_m + 0.1 < distance_m or covered_return_m + 0.1 < distance_m:
            return None
        return max(contact_r + return_r, 1e-6)

    @staticmethod
    def _solve_voltage_multi(power_kw: float, sources: list[tuple[float, float]]) -> tuple[float, float]:
        if power_kw <= 0 or not sources:
            return max((item[0] for item in sources), default=0.0), 1.0
        upper = max(item[0] for item in sources)
        lower = min(500.0, upper)
        target_w = power_kw * 1000.0
        available_at_floor_w = lower * sum(
            max((voltage - lower) / resistance, 0.0)
            for voltage, resistance in sources
        )
        if available_at_floor_w < target_w:
            return lower, max(0.0, min(1.0, available_at_floor_w / target_w))
        for _ in range(60):
            mid = (lower + upper) / 2.0
            supply_w = mid * sum(max((voltage - mid) / resistance, 0.0) for voltage, resistance in sources)
            if supply_w >= target_w:
                lower = mid
            else:
                upper = mid
        return max(lower, 0.0), 1.0

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
