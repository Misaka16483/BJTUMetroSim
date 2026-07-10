from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from app.domain.power.network_models import (
    ContactRailSection,
    FeederArm,
    PowerSupplySection,
    PowerSwitch,
    ReturnRailSection,
    TractionSubstation,
)


class TractionPowerNetwork:
    """Line-level DC traction power topology and switch/fault state."""

    def __init__(
        self,
        *,
        line_id: str,
        nominal_voltage_v: float,
        quality: str,
        substations: Iterable[TractionSubstation],
        feeders: Iterable[FeederArm],
        contact_sections: Iterable[ContactRailSection],
        return_sections: Iterable[ReturnRailSection],
        switches: Iterable[PowerSwitch],
    ) -> None:
        self.line_id = line_id
        self.nominal_voltage_v = nominal_voltage_v
        self.quality = quality
        self.substations = {
            item.substation_id: item
            for item in sorted(substations, key=lambda sub: sub.mileage_m)
        }
        self.feeders = {item.feeder_id: item for item in feeders}
        self.contact_sections = {item.section_id: item for item in contact_sections}
        self.return_sections = {item.section_id: item for item in return_sections}
        self.switches = {item.switch_id: item for item in switches}
        self.sections = self._build_supply_sections()

    @property
    def ordered_substations(self) -> list[TractionSubstation]:
        return sorted(self.substations.values(), key=lambda item: item.mileage_m)

    def locate_section(self, mileage_m: float, direction: str) -> PowerSupplySection:
        direction = direction.upper()
        candidates = [item for item in self.sections if item.direction == direction]
        if not candidates:
            raise ValueError(f"No power supply sections for direction={direction}")
        for section in candidates:
            if section.contains(mileage_m):
                return section
        if mileage_m < candidates[0].from_mileage_m:
            return candidates[0]
        return candidates[-1]

    def adjacent_substations(self, mileage_m: float, direction: str) -> tuple[TractionSubstation, TractionSubstation]:
        section = self.locate_section(mileage_m, direction)
        return self.substations[section.left_substation_id], self.substations[section.right_substation_id]

    def feeder_for(self, substation_id: str, direction: str, side: str) -> FeederArm | None:
        feeder_id = f"FD-{substation_id[-4:]}-{direction.upper()}-{side.upper()}"
        return self.feeders.get(feeder_id)

    def apply_substation_outage(self, substation_id: str, *, big_bilateral: bool = True) -> dict[str, list[str] | str]:
        if substation_id not in self.substations:
            raise KeyError(substation_id)
        self.substations[substation_id] = replace(self.substations[substation_id], status="OUTAGE")

        opened: list[str] = []
        closed: list[str] = []
        for feeder in list(self.feeders.values()):
            if feeder.substation_id == substation_id:
                self.feeders[feeder.feeder_id] = replace(feeder, status="OPEN")
                opened.append(feeder.feeder_id)

        if big_bilateral:
            for switch in list(self.switches.values()):
                if switch.switch_type == "TIE" and substation_id in {switch.from_node_id, switch.to_node_id}:
                    self.switches[switch.switch_id] = replace(switch, current_state="CLOSED")
                    closed.append(switch.switch_id)

        return {
            "affectedSubstationId": substation_id,
            "supplyMode": "BIG_BILATERAL" if big_bilateral else "SUBSTATION_OUTAGE",
            "openedSwitches": opened,
            "closedSwitches": closed,
        }

    def operate_switch(self, switch_id: str, state: str) -> PowerSwitch:
        if switch_id not in self.switches:
            raise KeyError(switch_id)
        next_switch = replace(self.switches[switch_id], current_state=state.upper())
        self.switches[switch_id] = next_switch
        return next_switch

    def set_feeder_status(self, feeder_id: str, status: str) -> FeederArm:
        if feeder_id not in self.feeders:
            raise KeyError(feeder_id)
        next_feeder = replace(self.feeders[feeder_id], status=status.upper())
        self.feeders[feeder_id] = next_feeder
        return next_feeder

    def topology_dict(self) -> dict:
        return {
            "lineId": self.line_id,
            "nominalVoltageV": self.nominal_voltage_v,
            "quality": self.quality,
            "substations": [
                {
                    "substationId": item.substation_id,
                    "name": item.name,
                    "mileageM": item.mileage_m,
                    "noLoadVoltageV": item.no_load_voltage_v,
                    "internalResistanceOhm": item.internal_resistance_ohm,
                    "ratedCurrentA": item.rated_current_a,
                    "overloadCurrentA": item.overload_current_a,
                    "efsCapacityKw": item.efs_capacity_kw,
                    "status": item.status,
                }
                for item in self.ordered_substations
            ],
            "feeders": [
                {
                    "feederId": item.feeder_id,
                    "substationId": item.substation_id,
                    "direction": item.direction,
                    "side": item.side,
                    "fromMileageM": item.from_mileage_m,
                    "toMileageM": item.to_mileage_m,
                    "cableResistanceOhm": item.cable_resistance_ohm,
                    "continuousCurrentA": item.continuous_current_a,
                    "shortTimeCurrentA": item.short_time_current_a,
                    "status": item.status,
                }
                for item in self.feeders.values()
            ],
            "contactRailSections": [
                {
                    "sectionId": item.section_id,
                    "direction": item.direction,
                    "fromMileageM": item.from_mileage_m,
                    "toMileageM": item.to_mileage_m,
                    "resistanceOhmPerKm": item.resistance_ohm_per_km,
                    "currentLimitA": item.current_limit_a,
                    "status": item.status,
                }
                for item in self.contact_sections.values()
            ],
            "returnRailSections": [
                {
                    "sectionId": item.section_id,
                    "direction": item.direction,
                    "fromMileageM": item.from_mileage_m,
                    "toMileageM": item.to_mileage_m,
                    "resistanceOhmPerKm": item.resistance_ohm_per_km,
                    "crossBondingGroup": item.cross_bonding_group,
                }
                for item in self.return_sections.values()
            ],
            "switches": [
                {
                    "switchId": item.switch_id,
                    "switchType": item.switch_type,
                    "mileageM": item.mileage_m,
                    "fromNodeId": item.from_node_id,
                    "toNodeId": item.to_node_id,
                    "normalState": item.normal_state,
                    "currentState": item.current_state,
                    "remoteControllable": item.remote_controllable,
                }
                for item in self.switches.values()
            ],
        }

    def _build_supply_sections(self) -> list[PowerSupplySection]:
        ordered = self.ordered_substations
        sections: list[PowerSupplySection] = []
        for left, right in zip(ordered, ordered[1:]):
            for direction in ("UP", "DOWN"):
                sections.append(
                    PowerSupplySection(
                        section_id=f"PWR-{left.substation_id[-4:]}-{right.substation_id[-4:]}-{direction}",
                        direction=direction,
                        left_substation_id=left.substation_id,
                        right_substation_id=right.substation_id,
                        from_mileage_m=left.mileage_m,
                        to_mileage_m=right.mileage_m,
                    )
                )
        return sections
