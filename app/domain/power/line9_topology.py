from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.domain.power.network import TractionPowerNetwork
from app.domain.power.network_models import (
    ContactRailSection,
    FeederArm,
    PowerSwitch,
    ReturnRailSection,
    TractionSubstation,
)


DEFAULT_CONTACT_RAIL_RESISTANCE_OHM_PER_KM = 0.0083
DEFAULT_RETURN_RAIL_RESISTANCE_OHM_PER_KM = 0.0083
DEFAULT_FEEDER_CABLE_RESISTANCE_OHM = 0.0036


def load_line9_power_network(path: str | Path) -> TractionPowerNetwork:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return build_line9_power_network(data)


def build_line9_power_network(data: dict[str, Any]) -> TractionPowerNetwork:
    substations = [
        TractionSubstation(
            substation_id=str(item["substationId"]),
            name=str(item["name"]),
            mileage_m=float(item["mileageM"]),
            no_load_voltage_v=float(item.get("noLoadVoltageV", 825.0)),
            internal_resistance_ohm=float(item.get("internalResistanceOhm", 0.015)),
            rated_current_a=float(item.get("ratedCurrentA", 5300.0)),
            overload_current_a=float(item.get("overloadCurrentA", 8000.0)),
            efs_capacity_kw=float(item.get("efsCapacityKw", 0.0)),
            status=str(item.get("status", "IN_SERVICE")),
        )
        for item in data.get("substations", [])
    ]
    if len(substations) < 2:
        raise ValueError("At least two traction substations are required")

    feeders = [
        FeederArm(
            feeder_id=str(item["feederId"]),
            substation_id=str(item["substationId"]),
            direction=str(item["direction"]).upper(),
            side=str(item["side"]).upper(),
            from_mileage_m=float(item["fromMileageM"]),
            to_mileage_m=float(item["toMileageM"]),
            cable_resistance_ohm=float(item.get("cableResistanceOhm", DEFAULT_FEEDER_CABLE_RESISTANCE_OHM)),
            continuous_current_a=float(item.get("continuousCurrentA", 4000.0)),
            short_time_current_a=float(item.get("shortTimeCurrentA", 6000.0)),
            status=str(item.get("status", "CLOSED")),
        )
        for item in data.get("feeders", [])
    ]
    contact_sections = [
        ContactRailSection(
            section_id=str(item["sectionId"]),
            direction=str(item["direction"]).upper(),
            from_mileage_m=float(item["fromMileageM"]),
            to_mileage_m=float(item["toMileageM"]),
            resistance_ohm_per_km=float(
                item.get("resistanceOhmPerKm", DEFAULT_CONTACT_RAIL_RESISTANCE_OHM_PER_KM)
            ),
            current_limit_a=float(item.get("currentLimitA", 6000.0)),
            status=str(item.get("status", "ENERGIZED")),
        )
        for item in data.get("contactRailSections", [])
    ]
    return_sections = [
        ReturnRailSection(
            section_id=str(item["sectionId"]),
            direction=str(item["direction"]).upper(),
            from_mileage_m=float(item["fromMileageM"]),
            to_mileage_m=float(item["toMileageM"]),
            resistance_ohm_per_km=float(item.get("resistanceOhmPerKm", DEFAULT_RETURN_RAIL_RESISTANCE_OHM_PER_KM)),
            cross_bonding_group=str(item.get("crossBondingGroup", "V0")),
        )
        for item in data.get("returnRailSections", [])
    ]
    switches = [
        PowerSwitch(
            switch_id=str(item["switchId"]),
            switch_type=str(item["switchType"]),
            mileage_m=float(item["mileageM"]),
            from_node_id=str(item["fromNodeId"]),
            to_node_id=str(item["toNodeId"]),
            normal_state=str(item["normalState"]),
            current_state=str(item.get("currentState", item["normalState"])),
            remote_controllable=bool(item.get("remoteControllable", True)),
        )
        for item in data.get("switches", [])
    ]

    if not feeders or not contact_sections:
        generated_feed, generated_contact, generated_return, generated_switches = _generate_v0_sections(substations)
        feeders = feeders or generated_feed
        contact_sections = contact_sections or generated_contact
        return_sections = return_sections or generated_return
        switches = switches or generated_switches

    return TractionPowerNetwork(
        line_id=str(data.get("lineId", "9")),
        nominal_voltage_v=float(data.get("nominalVoltageV", 750.0)),
        quality=str(data.get("quality", "ENGINEERING_ESTIMATE")),
        substations=substations,
        feeders=feeders,
        contact_sections=contact_sections,
        return_sections=return_sections,
        switches=switches,
    )


def _generate_v0_sections(
    substations: list[TractionSubstation],
) -> tuple[list[FeederArm], list[ContactRailSection], list[ReturnRailSection], list[PowerSwitch]]:
    ordered = sorted(substations, key=lambda item: item.mileage_m)
    feeders: list[FeederArm] = []
    contact_sections: list[ContactRailSection] = []
    return_sections: list[ReturnRailSection] = []
    switches: list[PowerSwitch] = []

    for index, substation in enumerate(ordered):
        left_m = ordered[index - 1].mileage_m if index > 0 else substation.mileage_m
        right_m = ordered[index + 1].mileage_m if index < len(ordered) - 1 else substation.mileage_m
        for direction in ("UP", "DOWN"):
            if index > 0:
                feeders.append(
                    FeederArm(
                        feeder_id=f"FD-{substation.substation_id[-4:]}-{direction}-LEFT",
                        substation_id=substation.substation_id,
                        direction=direction,
                        side="LEFT",
                        from_mileage_m=substation.mileage_m,
                        to_mileage_m=left_m,
                        cable_resistance_ohm=DEFAULT_FEEDER_CABLE_RESISTANCE_OHM,
                    )
                )
            if index < len(ordered) - 1:
                feeders.append(
                    FeederArm(
                        feeder_id=f"FD-{substation.substation_id[-4:]}-{direction}-RIGHT",
                        substation_id=substation.substation_id,
                        direction=direction,
                        side="RIGHT",
                        from_mileage_m=substation.mileage_m,
                        to_mileage_m=right_m,
                        cable_resistance_ohm=DEFAULT_FEEDER_CABLE_RESISTANCE_OHM,
                    )
                )

    for idx, (left, right) in enumerate(zip(ordered, ordered[1:]), start=1):
        for direction in ("UP", "DOWN"):
            contact_sections.append(
                ContactRailSection(
                    section_id=f"CR-09-{idx:02d}-{direction}",
                    direction=direction,
                    from_mileage_m=left.mileage_m,
                    to_mileage_m=right.mileage_m,
                )
            )
            return_sections.append(
                ReturnRailSection(
                    section_id=f"RR-09-{idx:02d}-{direction}",
                    direction=direction,
                    from_mileage_m=left.mileage_m,
                    to_mileage_m=right.mileage_m,
                )
            )
        switches.append(
            PowerSwitch(
                switch_id=f"SW-TIE-{right.substation_id[-4:]}",
                switch_type="TIE",
                mileage_m=right.mileage_m,
                from_node_id=right.substation_id,
                to_node_id=left.substation_id,
                normal_state="OPEN",
                current_state="OPEN",
            )
        )

    return feeders, contact_sections, return_sections, switches
