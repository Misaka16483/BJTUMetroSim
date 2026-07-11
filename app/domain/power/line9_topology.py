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
    strict = bool(data.get("strictTopology", False))
    _require_explicit_topology(data, strict=strict)
    _require_unique_ids(data)
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
            source_id=str(item.get("sourceId", "UNSPECIFIED")),
            quality=str(item.get("quality", data.get("quality", "ENGINEERING_ESTIMATE"))),
            parameter_sources=dict(item.get("parameterSources", {})),
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
            source_id=str(item.get("sourceId", "UNSPECIFIED")),
            quality=str(item.get("quality", data.get("quality", "ENGINEERING_ESTIMATE"))),
            parameter_sources=dict(item.get("parameterSources", {})),
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
            source_id=str(item.get("sourceId", "UNSPECIFIED")),
            quality=str(item.get("quality", data.get("quality", "ENGINEERING_ESTIMATE"))),
            parameter_sources=dict(item.get("parameterSources", {})),
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
            source_id=str(item.get("sourceId", "UNSPECIFIED")),
            quality=str(item.get("quality", data.get("quality", "ENGINEERING_ESTIMATE"))),
            parameter_sources=dict(item.get("parameterSources", {})),
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
            source_id=str(item.get("sourceId", "UNSPECIFIED")),
            quality=str(item.get("quality", data.get("quality", "ENGINEERING_ESTIMATE"))),
            parameter_sources=dict(item.get("parameterSources", {})),
        )
        for item in data.get("switches", [])
    ]

    if not strict and bool(data.get("allowGeneratedTopology", False)) and (not feeders or not contact_sections):
        generated_feed, generated_contact, generated_return, generated_switches = _generate_v0_sections(substations)
        feeders = feeders or generated_feed
        contact_sections = contact_sections or generated_contact
        return_sections = return_sections or generated_return
        switches = switches or generated_switches

    network = TractionPowerNetwork(
        line_id=str(data.get("lineId", "9")),
        nominal_voltage_v=float(data.get("nominalVoltageV", 750.0)),
        quality=str(data.get("quality", "ENGINEERING_ESTIMATE")),
        model_version=str(data.get("modelVersion", "UNVERSIONED")),
        provenance=dict(data.get("provenance", {})),
        substations=substations,
        feeders=feeders,
        contact_sections=contact_sections,
        return_sections=return_sections,
        switches=switches,
    )
    _validate_network(network, strict=strict)
    return network


def _require_explicit_topology(data: dict[str, Any], *, strict: bool) -> None:
    if not strict:
        return
    required = ("substations", "feeders", "contactRailSections", "returnRailSections", "switches")
    missing = [key for key in required if not data.get(key)]
    if missing:
        raise ValueError(f"Strict power topology requires explicit non-empty arrays: {', '.join(missing)}")
    if not data.get("modelVersion") or not data.get("provenance"):
        raise ValueError("Strict power topology requires modelVersion and provenance")
    for collection in required:
        for item in data[collection]:
            if not item.get("sourceId") or not item.get("quality") or not item.get("parameterSources"):
                identifier = next((value for key, value in item.items() if key.endswith("Id")), "UNKNOWN")
                raise ValueError(f"{collection}/{identifier} requires sourceId, quality and parameterSources")


def _require_unique_ids(data: dict[str, Any]) -> None:
    id_fields = {
        "substations": "substationId",
        "feeders": "feederId",
        "contactRailSections": "sectionId",
        "returnRailSections": "sectionId",
        "switches": "switchId",
    }
    for collection, id_field in id_fields.items():
        identifiers = [str(item.get(id_field, "")) for item in data.get(collection, [])]
        if any(not identifier for identifier in identifiers):
            raise ValueError(f"{collection} contains an empty {id_field}")
        if len(set(identifiers)) != len(identifiers):
            raise ValueError(f"{collection} contains duplicate {id_field}")


def _validate_network(network: TractionPowerNetwork, *, strict: bool) -> None:
    ordered = network.ordered_substations
    if len(ordered) < 2:
        raise ValueError("At least two traction substations are required")
    if len({item.substation_id for item in ordered}) != len(ordered):
        raise ValueError("Duplicate traction substation ID")
    if any(right.mileage_m <= left.mileage_m for left, right in zip(ordered, ordered[1:])):
        raise ValueError("Traction substation mileages must be strictly increasing")
    for feeder in network.feeders.values():
        if feeder.substation_id not in network.substations:
            raise ValueError(f"Feeder {feeder.feeder_id} references unknown substation")
        if feeder.direction not in {"UP", "DOWN"} or feeder.side not in {"LEFT", "RIGHT"}:
            raise ValueError(f"Feeder {feeder.feeder_id} has invalid direction or side")
        if feeder.cable_resistance_ohm <= 0 or feeder.continuous_current_a <= 0 or feeder.short_time_current_a <= 0:
            raise ValueError(f"Feeder {feeder.feeder_id} has non-positive electrical parameter")
    for section in network.contact_sections.values():
        if section.direction not in {"UP", "DOWN"} or section.to_mileage_m <= section.from_mileage_m:
            raise ValueError(f"Contact section {section.section_id} has invalid extent")
        if section.resistance_ohm_per_km <= 0 or section.current_limit_a <= 0:
            raise ValueError(f"Contact section {section.section_id} has non-positive electrical parameter")
    for section in network.return_sections.values():
        if section.direction not in {"UP", "DOWN"} or section.to_mileage_m <= section.from_mileage_m:
            raise ValueError(f"Return section {section.section_id} has invalid extent")
        if section.resistance_ohm_per_km <= 0:
            raise ValueError(f"Return section {section.section_id} has non-positive resistance")
    for switch in network.switches.values():
        if switch.from_node_id not in network.substations or switch.to_node_id not in network.substations:
            raise ValueError(f"Switch {switch.switch_id} references unknown node")
        if switch.normal_state not in {"OPEN", "CLOSED"} or switch.current_state not in {"OPEN", "CLOSED"}:
            raise ValueError(f"Switch {switch.switch_id} has invalid state")
    if strict:
        expected_intervals = 2 * (len(ordered) - 1)
        if len(network.contact_sections) != expected_intervals or len(network.return_sections) != expected_intervals:
            raise ValueError("Strict topology requires one contact and return section per direction and interval")
        expected_feeders = 4 * len(ordered) - 4
        if len(network.feeders) != expected_feeders:
            raise ValueError(f"Strict topology requires {expected_feeders} main-line feeder arms")
        if len(network.switches) != len(ordered) - 1:
            raise ValueError("Strict topology requires one tie switch at every internal boundary")
        expected_feeder_keys: set[tuple[str, str, str, float, float]] = set()
        for index, substation in enumerate(ordered):
            for direction in ("UP", "DOWN"):
                if index > 0:
                    expected_feeder_keys.add((
                        substation.substation_id,
                        direction,
                        "LEFT",
                        substation.mileage_m,
                        ordered[index - 1].mileage_m,
                    ))
                if index < len(ordered) - 1:
                    expected_feeder_keys.add((
                        substation.substation_id,
                        direction,
                        "RIGHT",
                        substation.mileage_m,
                        ordered[index + 1].mileage_m,
                    ))
        actual_feeder_keys = {
            (item.substation_id, item.direction, item.side, item.from_mileage_m, item.to_mileage_m)
            for item in network.feeders.values()
        }
        if actual_feeder_keys != expected_feeder_keys:
            raise ValueError("Strict topology feeder arms do not match adjacent substation intervals")

        expected_section_keys = {
            (direction, left.mileage_m, right.mileage_m)
            for left, right in zip(ordered, ordered[1:])
            for direction in ("UP", "DOWN")
        }
        contact_keys = {
            (item.direction, item.from_mileage_m, item.to_mileage_m)
            for item in network.contact_sections.values()
        }
        return_keys = {
            (item.direction, item.from_mileage_m, item.to_mileage_m)
            for item in network.return_sections.values()
        }
        if contact_keys != expected_section_keys or return_keys != expected_section_keys:
            raise ValueError("Strict topology rail sections do not cover every adjacent interval and direction")

        expected_switch_pairs = {
            frozenset((left.substation_id, right.substation_id))
            for left, right in zip(ordered, ordered[1:])
        }
        actual_switch_pairs = {
            frozenset((item.from_node_id, item.to_node_id))
            for item in network.switches.values()
        }
        if actual_switch_pairs != expected_switch_pairs:
            raise ValueError("Strict topology tie switches do not match adjacent substation boundaries")


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
