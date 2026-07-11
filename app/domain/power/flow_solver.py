from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import math
import time

from app.domain.power.network import TractionPowerNetwork
from app.domain.power.network_models import (
    ContactRailPowerFlow,
    FeederPowerFlow,
    PowerFlowSnapshot,
    RegenPathFlow,
    SubstationPowerFlow,
    TrainElectricalLoad,
    TrainPowerFlow,
)


@dataclass
class _RegenAllocation:
    generated_kw: float = 0.0
    absorbed_kw: float = 0.0
    feedback_kw: float = 0.0
    wasted_kw: float = 0.0
    transfer_losses_kw: float = 0.0
    absorbed_by_train_kw: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    feeder_currents_a: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    feedback_currents_a: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    accepted_by_source_kw: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    source_currents_a: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    source_voltages_v: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    sink_currents_a: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    sink_voltages_v: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    paths: list[RegenPathFlow] = field(default_factory=list)


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
        gross_traction_kw = sum(load.traction_demand_kw for load in loads)
        source_paths_by_train = {
            load.train_id: self._load_source_paths(load)
            for load in loads
        }
        regen = self._allocate_regen(loads, source_paths_by_train)
        generated_regen_kw = regen.generated_kw
        absorbed_regen_kw = regen.absorbed_kw
        feedback_regen_kw = regen.feedback_kw
        wasted_regen_kw = regen.wasted_kw
        effective_demand_kw = {
            load.train_id: max(load.traction_demand_kw - regen.absorbed_by_train_kw[load.train_id], 0.0)
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
                paths = source_paths_by_train[load.train_id]
                if requested_kw <= 0 or not paths:
                    delivered_ratio = 1.0 if requested_kw <= 0 else 0.0
                    next_train_solution[load.train_id] = (
                        self.network.nominal_voltage_v if paths else 0.0,
                        0.0,
                        delivered_ratio,
                        [],
                    )
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
        accepted_regen_ratio = min(1.0, max(0.0, 1.0 - wasted_regen_kw / generated_regen_kw)) if generated_regen_kw > 0 else 1.0
        for load in loads:
            section = self.network.locate_section(load.mileage_m, load.direction)
            head_mileage_m = load.head_mileage_m if load.head_mileage_m is not None else load.mileage_m
            tail_mileage_m = load.tail_mileage_m if load.tail_mileage_m is not None else load.mileage_m
            spanned_sections = self.network.sections_spanned(head_mileage_m, tail_mileage_m, load.direction)
            solution_voltage, solution_current, delivered_ratio, source_contributions = train_solution.get(
                load.train_id,
                (self.network.nominal_voltage_v, 0.0, 1.0, []),
            )
            if load.regen_power_kw > 0:
                accepted_kw = regen.accepted_by_source_kw[load.train_id]
                source_voltages = regen.source_voltages_v[load.train_id]
                voltage_v = max(source_voltages, default=self.network.nominal_voltage_v if accepted_kw > 0 else 1000.0)
                current_a = -regen.source_currents_a[load.train_id]
            else:
                transfer_current_a = regen.sink_currents_a[load.train_id]
                transfer_voltages = regen.sink_voltages_v[load.train_id]
                if solution_current <= 1e-9 and transfer_voltages:
                    voltage_v = min(transfer_voltages)
                elif transfer_voltages:
                    voltage_v = min(solution_voltage, min(transfer_voltages))
                else:
                    voltage_v = solution_voltage
                current_a = solution_current + transfer_current_a
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
                    head_mileage_m=head_mileage_m,
                    tail_mileage_m=tail_mileage_m,
                    pantograph_mileages_m=load.electrical_contact_mileages_m,
                    spanned_power_section_ids=tuple(item.section_id for item in spanned_sections),
                )
            )

        rectifier_substation_currents = defaultdict(float, substation_currents)
        for feeder_id, current_a in regen.feeder_currents_a.items():
            feeder_currents[feeder_id] += current_a
        for substation_id, current_a in regen.feedback_currents_a.items():
            substation_currents[substation_id] -= current_a

        contact_rail_flows = self._contact_rail_flows(feeder_currents)
        alerts = self._build_alerts(
            trains,
            substation_currents,
            feeder_currents,
            contact_rail_flows,
            wasted_regen_kw,
        )
        if not converged:
            alerts.append({"type": "POWER_FLOW_NOT_CONVERGED", "targetId": "NETWORK", "iterations": iterations})
        substations = self._substation_flows(
            substation_currents,
            rectifier_substation_currents,
            regen.feedback_currents_a,
            terminal_voltage,
            dt_sec,
        )
        feeders = self._feeder_flows(feeder_currents, terminal_voltage)
        delivered_demand_kw = sum(
            effective_demand_kw[load.train_id] * train_solution.get(load.train_id, (0.0, 0.0, 0.0, []))[2]
            for load in loads
        )
        rectifier_input_kw = sum(
            self.network.substations[sub_id].no_load_voltage_v * current / 1000.0
            for sub_id, current in rectifier_substation_currents.items()
        )
        accepted_regen_kw = generated_regen_kw - wasted_regen_kw
        actual_traction_kw = absorbed_regen_kw + delivered_demand_kw
        total_losses_kw = losses_kw + regen.transfer_losses_kw
        balance_error_kw = abs(
            rectifier_input_kw + accepted_regen_kw
            - (actual_traction_kw + total_losses_kw + feedback_regen_kw)
        )
        balance_base_kw = max(rectifier_input_kw + accepted_regen_kw, 1.0)
        balance_error_ratio = balance_error_kw / balance_base_kw
        return PowerFlowSnapshot(
            sim_time_ms=sim_time_ms,
            trains=trains,
            substations=substations,
            feeders=feeders,
            contact_rail_flows=contact_rail_flows,
            generated_regen_kw=generated_regen_kw,
            absorbed_regen_kw=absorbed_regen_kw,
            feedback_regen_kw=feedback_regen_kw,
            wasted_regen_kw=wasted_regen_kw,
            regen_transfer_losses_kw=regen.transfer_losses_kw,
            regen_paths=regen.paths,
            losses_kw=total_losses_kw,
            converged=converged,
            iterations=iterations,
            solve_time_ms=(time.perf_counter() - started) * 1000.0,
            power_balance_error_kw=balance_error_kw,
            power_balance_error_ratio=balance_error_ratio,
            alerts=alerts,
        )

    def _allocate_regen(
        self,
        loads: list[TrainElectricalLoad],
        source_paths_by_train: dict[str, list[tuple[str, str, float]]] | None = None,
    ) -> _RegenAllocation:
        """Allocate regenerative energy only across electrically reachable paths."""
        allocation = _RegenAllocation(
            generated_kw=sum(load.regen_power_kw for load in loads),
        )
        regen_remaining = {
            load.train_id: load.regen_power_kw
            for load in loads
            if load.regen_power_kw > 1e-9
        }
        demand_remaining = {
            load.train_id: load.traction_demand_kw
            for load in loads
            if load.traction_demand_kw > 1e-9
        }
        if not regen_remaining:
            return allocation

        path_by_train: dict[str, dict[str, tuple[str, float]]] = {}
        for load in loads:
            best_by_substation: dict[str, tuple[str, float]] = {}
            source_paths = (
                source_paths_by_train[load.train_id]
                if source_paths_by_train is not None
                else self._load_source_paths(load)
            )
            for substation_id, feeder_id, resistance in source_paths:
                current = best_by_substation.get(substation_id)
                if current is None or resistance < current[1]:
                    best_by_substation[substation_id] = (feeder_id, resistance)
            path_by_train[load.train_id] = best_by_substation

        candidates: list[tuple[float, str, str, str, str, float, str, float]] = []
        for source_id in sorted(regen_remaining):
            source_paths = path_by_train[source_id]
            for sink_id in sorted(demand_remaining):
                sink_paths = path_by_train[sink_id]
                shared = sorted(set(source_paths).intersection(sink_paths))
                if not shared:
                    continue
                choices = [
                    (
                        source_paths[substation_id][1] + sink_paths[substation_id][1],
                        substation_id,
                        source_paths[substation_id][0],
                        source_paths[substation_id][1],
                        sink_paths[substation_id][0],
                        sink_paths[substation_id][1],
                    )
                    for substation_id in shared
                ]
                total_r, via_id, source_feeder, source_r, sink_feeder, sink_r = min(choices)
                candidates.append((total_r, source_id, sink_id, via_id, source_feeder, source_r, sink_feeder, sink_r))

        for _total_r, source_id, sink_id, via_id, source_feeder, source_r, sink_feeder, sink_r in sorted(candidates):
            available_kw = regen_remaining[source_id]
            demand_kw = demand_remaining[sink_id]
            if available_kw <= 1e-9 or demand_kw <= 1e-9:
                continue
            bus_voltage_v = self.network.substations[via_id].no_load_voltage_v
            current_a, generated_kw, delivered_kw, loss_kw = self._path_transfer(
                available_kw,
                demand_kw,
                bus_voltage_v,
                source_r,
                sink_r,
            )
            if current_a <= 1e-9:
                continue
            regen_remaining[source_id] = max(0.0, available_kw - generated_kw)
            demand_remaining[sink_id] = max(0.0, demand_kw - delivered_kw)
            allocation.absorbed_kw += delivered_kw
            allocation.transfer_losses_kw += loss_kw
            allocation.absorbed_by_train_kw[sink_id] += delivered_kw
            allocation.accepted_by_source_kw[source_id] += generated_kw
            allocation.source_currents_a[source_id] += current_a
            allocation.source_voltages_v[source_id].append(bus_voltage_v + current_a * source_r)
            allocation.sink_currents_a[sink_id] += current_a
            allocation.sink_voltages_v[sink_id].append(bus_voltage_v - current_a * sink_r)
            allocation.feeder_currents_a[source_feeder] -= current_a
            allocation.feeder_currents_a[sink_feeder] += current_a
            allocation.paths.append(RegenPathFlow(
                source_train_id=source_id,
                sink_type="TRAIN",
                sink_id=sink_id,
                via_substation_id=via_id,
                source_feeder_id=source_feeder,
                sink_feeder_id=sink_feeder,
                generated_kw=generated_kw,
                delivered_kw=delivered_kw,
                losses_kw=loss_kw,
                current_a=current_a,
                path_resistance_ohm=source_r + sink_r,
            ))

        feedback_capacity = {
            sub.substation_id: sub.efs_capacity_kw
            for sub in self.network.ordered_substations
            if sub.in_service and sub.efs_capacity_kw > 0
        }
        for source_id in sorted(regen_remaining):
            source_paths = path_by_train[source_id]
            feedback_paths = sorted(
                (
                    resistance,
                    substation_id,
                    feeder_id,
                )
                for substation_id, (feeder_id, resistance) in source_paths.items()
                if feedback_capacity.get(substation_id, 0.0) > 1e-9
            )
            for source_r, substation_id, source_feeder in feedback_paths:
                available_kw = regen_remaining[source_id]
                capacity_kw = feedback_capacity[substation_id]
                if available_kw <= 1e-9 or capacity_kw <= 1e-9:
                    continue
                bus_voltage_v = self.network.substations[substation_id].no_load_voltage_v
                current_a, generated_kw, delivered_kw, loss_kw = self._path_transfer(
                    available_kw,
                    capacity_kw,
                    bus_voltage_v,
                    source_r,
                    0.0,
                )
                if current_a <= 1e-9:
                    continue
                regen_remaining[source_id] = max(0.0, available_kw - generated_kw)
                feedback_capacity[substation_id] = max(0.0, capacity_kw - delivered_kw)
                allocation.feedback_kw += delivered_kw
                allocation.transfer_losses_kw += loss_kw
                allocation.accepted_by_source_kw[source_id] += generated_kw
                allocation.source_currents_a[source_id] += current_a
                allocation.source_voltages_v[source_id].append(bus_voltage_v + current_a * source_r)
                allocation.feeder_currents_a[source_feeder] -= current_a
                allocation.feedback_currents_a[substation_id] += current_a
                allocation.paths.append(RegenPathFlow(
                    source_train_id=source_id,
                    sink_type="SUBSTATION_FEEDBACK",
                    sink_id=substation_id,
                    via_substation_id=substation_id,
                    source_feeder_id=source_feeder,
                    sink_feeder_id=None,
                    generated_kw=generated_kw,
                    delivered_kw=delivered_kw,
                    losses_kw=loss_kw,
                    current_a=current_a,
                    path_resistance_ohm=source_r,
                ))

        for source_id in sorted(regen_remaining):
            wasted_kw = regen_remaining[source_id]
            if wasted_kw <= 1e-9:
                continue
            allocation.wasted_kw += wasted_kw
            allocation.paths.append(RegenPathFlow(
                source_train_id=source_id,
                sink_type="WASTE",
                sink_id="BRAKE_RESISTOR",
                via_substation_id=None,
                source_feeder_id=None,
                sink_feeder_id=None,
                generated_kw=wasted_kw,
                delivered_kw=0.0,
                losses_kw=0.0,
                current_a=0.0,
                path_resistance_ohm=0.0,
            ))
        return allocation

    @staticmethod
    def _path_transfer(
        available_generated_kw: float,
        delivery_limit_kw: float,
        bus_voltage_v: float,
        source_resistance_ohm: float,
        sink_resistance_ohm: float,
    ) -> tuple[float, float, float, float]:
        """Return current, generated, delivered and resistive-loss power for one path."""
        if available_generated_kw <= 0 or delivery_limit_kw <= 0 or bus_voltage_v <= 0:
            return 0.0, 0.0, 0.0, 0.0
        source_r = max(source_resistance_ohm, 0.0)
        sink_r = max(sink_resistance_ohm, 0.0)
        if source_r > 0:
            source_limit_a = (
                -bus_voltage_v
                + math.sqrt(bus_voltage_v * bus_voltage_v + 4.0 * source_r * available_generated_kw * 1000.0)
            ) / (2.0 * source_r)
        else:
            source_limit_a = available_generated_kw * 1000.0 / bus_voltage_v

        if sink_r <= 0:
            sink_limit_a = delivery_limit_kw * 1000.0 / bus_voltage_v
        else:
            max_deliverable_w = bus_voltage_v * bus_voltage_v / (4.0 * sink_r)
            if delivery_limit_kw * 1000.0 >= max_deliverable_w:
                sink_limit_a = bus_voltage_v / (2.0 * sink_r)
            else:
                discriminant = max(
                    bus_voltage_v * bus_voltage_v - 4.0 * sink_r * delivery_limit_kw * 1000.0,
                    0.0,
                )
                sink_limit_a = (bus_voltage_v - math.sqrt(discriminant)) / (2.0 * sink_r)

        current_a = max(0.0, min(source_limit_a, sink_limit_a))
        generated_kw = (bus_voltage_v + current_a * source_r) * current_a / 1000.0
        delivered_kw = max(0.0, (bus_voltage_v - current_a * sink_r) * current_a / 1000.0)
        losses_kw = current_a * current_a * (source_r + sink_r) / 1000.0
        return current_a, generated_kw, delivered_kw, losses_kw

    def _load_source_paths(self, load: TrainElectricalLoad) -> list[tuple[str, str, float]]:
        """Build source paths through all collection points without duplicating one feeder."""
        best_resistance_by_path: dict[tuple[str, str], float] = {}
        for mileage_m in load.electrical_contact_mileages_m:
            for substation_id, feeder_id, resistance in self._source_paths(mileage_m, load.direction):
                key = (substation_id, feeder_id)
                current = best_resistance_by_path.get(key)
                if current is None or resistance < current:
                    best_resistance_by_path[key] = resistance
        return [
            (substation_id, feeder_id, resistance)
            for (substation_id, feeder_id), resistance in sorted(best_resistance_by_path.items())
        ]

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
        rectifier_currents: dict[str, float],
        feedback_currents: dict[str, float],
        terminal_voltage: dict[str, float],
        dt_sec: float,
    ) -> list[SubstationPowerFlow]:
        flows: list[SubstationPowerFlow] = []
        for sub in self.network.ordered_substations:
            current = substation_currents[sub.substation_id]
            rectifier_current = rectifier_currents[sub.substation_id]
            feedback_current = feedback_currents[sub.substation_id]
            voltage = terminal_voltage[sub.substation_id]
            power_kw = voltage * current / 1000.0
            rectifier_power_kw = voltage * rectifier_current / 1000.0
            feedback_power_kw = voltage * feedback_current / 1000.0
            self._substation_energy_kwh[sub.substation_id] += max(rectifier_power_kw, 0.0) * max(dt_sec, 0.0) / 3600.0
            absolute_current = abs(current)
            if not sub.in_service:
                status = "OUTAGE"
            elif absolute_current > sub.overload_current_a:
                status = "OVERLOAD"
            elif absolute_current > sub.rated_current_a:
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
                    load_ratio=absolute_current / sub.rated_current_a if sub.rated_current_a else 0.0,
                    status=status,
                    rectifier_power_kw=rectifier_power_kw,
                    feedback_power_kw=feedback_power_kw,
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
            absolute_current = abs(current)
            voltage = terminal_voltage.get(feeder.substation_id, self.network.nominal_voltage_v)
            if not feeder.closed:
                status = "OPEN"
            elif absolute_current > feeder.short_time_current_a:
                status = "OVERLOAD"
            elif absolute_current > feeder.continuous_current_a:
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
                    load_ratio=absolute_current / feeder.continuous_current_a if feeder.continuous_current_a else 0.0,
                    status=status,
                )
            )
        return flows

    def _contact_rail_flows(self, feeder_currents: dict[str, float]) -> list[ContactRailPowerFlow]:
        """Report signed current in each inter-substation contact-rail section.

        Positive current is defined in increasing-mileage direction. The two end
        feeder currents are retained through the protection load ratio by using
        the largest absolute end/through current.
        """
        substation_by_mileage = {
            round(item.mileage_m, 6): item
            for item in self.network.ordered_substations
        }
        flows: list[ContactRailPowerFlow] = []
        for section in sorted(
            self.network.contact_sections.values(),
            key=lambda item: (item.from_mileage_m, item.direction),
        ):
            left = substation_by_mileage.get(round(section.from_mileage_m, 6))
            right = substation_by_mileage.get(round(section.to_mileage_m, 6))
            left_feeder = self.network.feeder_for(left.substation_id, section.direction, "RIGHT") if left else None
            right_feeder = self.network.feeder_for(right.substation_id, section.direction, "LEFT") if right else None
            left_current_a = feeder_currents[left_feeder.feeder_id] if left_feeder else 0.0
            right_current_a = feeder_currents[right_feeder.feeder_id] if right_feeder else 0.0
            signed_current_a = left_current_a - right_current_a
            protection_current_a = max(abs(left_current_a), abs(right_current_a), abs(signed_current_a))
            if section.status != "ENERGIZED":
                status = "DEENERGIZED"
            elif protection_current_a > section.current_limit_a:
                status = "OVERLOAD"
            elif protection_current_a >= 0.8 * section.current_limit_a:
                status = "WARNING"
            else:
                status = "NORMAL"
            flows.append(ContactRailPowerFlow(
                section_id=section.section_id,
                direction=section.direction,
                current_a=signed_current_a,
                power_kw=self.network.nominal_voltage_v * signed_current_a / 1000.0,
                load_ratio=protection_current_a / section.current_limit_a if section.current_limit_a else 0.0,
                status=status,
            ))
        return flows

    def _build_alerts(
        self,
        trains: list[TrainPowerFlow],
        substation_currents: dict[str, float],
        feeder_currents: dict[str, float],
        contact_rail_flows: list[ContactRailPowerFlow],
        wasted_regen_kw: float,
    ) -> list[dict]:
        alerts: list[dict] = []
        for train in trains:
            if train.requested_power_kw > 0 and train.voltage_v <= 1e-6:
                alerts.append({
                    "type": "POWER_SECTION_ISOLATED",
                    "targetType": "TRAIN",
                    "targetId": train.train_id,
                    "powerSectionId": train.power_section_id,
                })
            if train.voltage_level in {"UNDERVOLTAGE", "LIMITED", "OVERVOLTAGE", "OVERVOLTAGE_WARNING", "REGEN_LIMITED"}:
                alerts.append({
                    "type": train.voltage_level,
                    "targetType": "TRAIN",
                    "targetId": train.train_id,
                    "voltageV": round(train.voltage_v, 2),
                })
        for sub in self.network.substations.values():
            current = substation_currents[sub.substation_id]
            absolute_current = abs(current)
            if absolute_current > sub.overload_current_a:
                alerts.append({"type": "SUBSTATION_OVERLOAD", "targetId": sub.substation_id, "currentA": round(current, 2)})
            elif absolute_current > sub.rated_current_a:
                alerts.append({"type": "SUBSTATION_WARNING", "targetId": sub.substation_id, "currentA": round(current, 2)})
            if not sub.in_service:
                alerts.append({"type": "SUBSTATION_OUTAGE", "targetId": sub.substation_id})
        for feeder in self.network.feeders.values():
            current = feeder_currents[feeder.feeder_id]
            absolute_current = abs(current)
            if absolute_current > feeder.short_time_current_a:
                alerts.append({"type": "FEEDER_OVERLOAD", "targetId": feeder.feeder_id, "currentA": round(current, 2)})
            elif absolute_current > feeder.continuous_current_a:
                alerts.append({"type": "FEEDER_WARNING", "targetId": feeder.feeder_id, "currentA": round(current, 2)})
        for section in contact_rail_flows:
            if section.status == "OVERLOAD":
                alerts.append({
                    "type": "CONTACT_RAIL_OVERLOAD",
                    "targetId": section.section_id,
                    "currentA": round(section.current_a, 2),
                    "loadRatio": round(section.load_ratio, 4),
                })
            elif section.status == "WARNING":
                alerts.append({
                    "type": "CONTACT_RAIL_WARNING",
                    "targetId": section.section_id,
                    "currentA": round(section.current_a, 2),
                    "loadRatio": round(section.load_ratio, 4),
                })
            elif section.status == "DEENERGIZED":
                alerts.append({"type": "CONTACT_RAIL_DEENERGIZED", "targetId": section.section_id})
        if wasted_regen_kw > 0:
            alerts.append({"type": "REGEN_WASTED", "targetId": "NETWORK", "powerKw": round(wasted_regen_kw, 3)})
        return alerts
