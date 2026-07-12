from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


JsonDict = dict[str, Any]


@dataclass(frozen=True)
class TractionSubstation:
    substation_id: str
    name: str
    mileage_m: float
    no_load_voltage_v: float = 825.0
    internal_resistance_ohm: float = 0.015
    rated_current_a: float = 5300.0
    overload_current_a: float = 8000.0
    efs_capacity_kw: float = 0.0
    status: str = "IN_SERVICE"
    source_id: str = "UNSPECIFIED"
    quality: str = "ENGINEERING_ESTIMATE"
    parameter_sources: JsonDict = field(default_factory=dict)

    @property
    def in_service(self) -> bool:
        return self.status == "IN_SERVICE"


@dataclass(frozen=True)
class SupercapacitorStorage:
    storage_id: str
    substation_id: str
    rated_energy_kwh: float
    max_charge_power_kw: float
    max_discharge_power_kw: float
    discharge_trigger_power_kw: float = 1000.0
    initial_soc: float = 0.50
    min_soc: float = 0.20
    max_soc: float = 0.90
    charge_efficiency: float = 0.95
    discharge_efficiency: float = 0.95
    standby_power_kw: float = 3.0
    status: str = "IN_SERVICE"
    source_id: str = "UNSPECIFIED"
    quality: str = "ENGINEERING_ESTIMATE"
    parameter_sources: JsonDict = field(default_factory=dict)

    @property
    def in_service(self) -> bool:
        return self.status == "IN_SERVICE"


@dataclass(frozen=True)
class FeederArm:
    feeder_id: str
    substation_id: str
    direction: str
    side: str
    from_mileage_m: float
    to_mileage_m: float
    cable_resistance_ohm: float
    continuous_current_a: float = 4000.0
    short_time_current_a: float = 6000.0
    status: str = "CLOSED"
    source_id: str = "UNSPECIFIED"
    quality: str = "ENGINEERING_ESTIMATE"
    parameter_sources: JsonDict = field(default_factory=dict)

    @property
    def closed(self) -> bool:
        return self.status == "CLOSED"


@dataclass(frozen=True)
class ContactRailSection:
    section_id: str
    direction: str
    from_mileage_m: float
    to_mileage_m: float
    resistance_ohm_per_km: float = 0.0083
    current_limit_a: float = 6000.0
    status: str = "ENERGIZED"
    source_id: str = "UNSPECIFIED"
    quality: str = "ENGINEERING_ESTIMATE"
    parameter_sources: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class ReturnRailSection:
    section_id: str
    direction: str
    from_mileage_m: float
    to_mileage_m: float
    resistance_ohm_per_km: float = 0.0083
    cross_bonding_group: str = "V0"
    source_id: str = "UNSPECIFIED"
    quality: str = "ENGINEERING_ESTIMATE"
    parameter_sources: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class PowerSwitch:
    switch_id: str
    switch_type: str
    mileage_m: float
    from_node_id: str
    to_node_id: str
    normal_state: str
    current_state: str
    remote_controllable: bool = True
    source_id: str = "UNSPECIFIED"
    quality: str = "ENGINEERING_ESTIMATE"
    parameter_sources: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class PowerSupplySection:
    section_id: str
    direction: str
    left_substation_id: str
    right_substation_id: str
    from_mileage_m: float
    to_mileage_m: float

    def contains(self, mileage_m: float) -> bool:
        return self.from_mileage_m <= mileage_m <= self.to_mileage_m


@dataclass(frozen=True)
class TrainElectricalLoad:
    train_id: str
    direction: str
    mileage_m: float
    speed_mps: float
    traction_force_n: float = 0.0
    brake_force_n: float = 0.0
    aux_power_kw: float = 150.0
    traction_efficiency: float = 0.88
    regen_efficiency: float = 0.80
    head_mileage_m: float | None = None
    tail_mileage_m: float | None = None
    pantograph_mileages_m: tuple[float, ...] = ()
    traction_power_request_kw: float | None = None
    regen_power_available_kw: float | None = None

    @property
    def electrical_contact_mileages_m(self) -> tuple[float, ...]:
        return self.pantograph_mileages_m or (self.mileage_m,)

    @property
    def traction_power_kw(self) -> float:
        if self.traction_power_request_kw is not None:
            return max(self.traction_power_request_kw, 0.0)
        if self.traction_force_n <= 0 or self.speed_mps <= 0:
            return 0.0
        return self.traction_force_n * self.speed_mps / 1000.0 / self.traction_efficiency

    @property
    def raw_regen_power_kw(self) -> float:
        if self.regen_power_available_kw is not None:
            return max(self.regen_power_available_kw, 0.0)
        if self.brake_force_n <= 0 or self.speed_mps <= 0:
            return 0.0
        return self.brake_force_n * self.speed_mps / 1000.0 * self.regen_efficiency

    @property
    def regen_power_kw(self) -> float:
        return max(self.raw_regen_power_kw - self.aux_power_kw, 0.0)

    @property
    def self_consumed_regen_kw(self) -> float:
        return min(self.raw_regen_power_kw, self.aux_power_kw)

    @property
    def traction_demand_kw(self) -> float:
        return self.traction_power_kw + max(self.aux_power_kw - self.raw_regen_power_kw, 0.0)

    @property
    def requested_power_kw(self) -> float:
        return self.traction_demand_kw - self.regen_power_kw


@dataclass(frozen=True)
class TrainPowerFlow:
    train_id: str
    power_section_id: str
    mileage_m: float
    voltage_v: float
    current_a: float
    requested_power_kw: float
    traction_limit_ratio: float
    regen_limit_ratio: float
    voltage_level: str
    traction_power_request_kw: float = 0.0
    traction_power_delivered_kw: float = 0.0
    auxiliary_power_kw: float = 0.0
    regen_power_available_kw: float = 0.0
    regen_power_self_consumed_kw: float = 0.0
    regen_power_exported_kw: float = 0.0
    regen_power_accepted_kw: float = 0.0
    regen_power_wasted_kw: float = 0.0
    left_substation_id: str | None = None
    right_substation_id: str | None = None
    head_mileage_m: float | None = None
    tail_mileage_m: float | None = None
    pantograph_mileages_m: tuple[float, ...] = ()
    spanned_power_section_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SubstationPowerFlow:
    substation_id: str
    name: str
    mileage_m: float
    voltage_v: float
    current_a: float
    power_kw: float
    energy_kwh: float
    load_ratio: float
    status: str
    rectifier_power_kw: float = 0.0
    feedback_power_kw: float = 0.0


@dataclass(frozen=True)
class FeederPowerFlow:
    feeder_id: str
    substation_id: str
    direction: str
    side: str
    current_a: float
    power_kw: float
    load_ratio: float
    status: str


@dataclass(frozen=True)
class ContactRailPowerFlow:
    section_id: str
    direction: str
    current_a: float
    power_kw: float
    load_ratio: float
    status: str


@dataclass(frozen=True)
class SupercapacitorPowerFlow:
    storage_id: str
    substation_id: str
    soc: float
    stored_energy_kwh: float
    available_charge_energy_kwh: float
    available_discharge_energy_kwh: float
    charge_power_kw: float
    discharge_power_kw: float
    conversion_losses_kw: float
    cumulative_charged_kwh: float
    cumulative_discharged_kwh: float
    state: str
    status: str


@dataclass(frozen=True)
class RegenPathFlow:
    source_train_id: str
    sink_type: str
    sink_id: str
    via_substation_id: str | None
    source_feeder_id: str | None
    sink_feeder_id: str | None
    generated_kw: float
    delivered_kw: float
    losses_kw: float
    current_a: float
    path_resistance_ohm: float


@dataclass(frozen=True)
class PowerFlowSnapshot:
    sim_time_ms: int
    trains: list[TrainPowerFlow] = field(default_factory=list)
    substations: list[SubstationPowerFlow] = field(default_factory=list)
    feeders: list[FeederPowerFlow] = field(default_factory=list)
    contact_rail_flows: list[ContactRailPowerFlow] = field(default_factory=list)
    supercapacitor_flows: list[SupercapacitorPowerFlow] = field(default_factory=list)
    generated_regen_kw: float = 0.0
    self_consumed_regen_kw: float = 0.0
    absorbed_regen_kw: float = 0.0
    feedback_regen_kw: float = 0.0
    wasted_regen_kw: float = 0.0
    regen_transfer_losses_kw: float = 0.0
    regen_paths: list[RegenPathFlow] = field(default_factory=list)
    losses_kw: float = 0.0
    converged: bool = True
    iterations: int = 0
    solve_time_ms: float = 0.0
    power_balance_error_kw: float = 0.0
    power_balance_error_ratio: float = 0.0
    alerts: list[JsonDict] = field(default_factory=list)
    source: str = "SELF_SIM"
    quality: str = "ENGINEERING_ESTIMATE"

    def to_dict(self) -> JsonDict:
        return {
            "simTimeMs": self.sim_time_ms,
            "substations": [
                {
                    "substationId": item.substation_id,
                    "name": item.name,
                    "mileageM": round(item.mileage_m, 3),
                    "voltageV": round(item.voltage_v, 2),
                    "currentA": round(item.current_a, 2),
                    "powerKw": round(item.power_kw, 3),
                    "energyKwh": round(item.energy_kwh, 4),
                    "loadRatio": round(item.load_ratio, 4),
                    "status": item.status,
                    "rectifierPowerKw": round(item.rectifier_power_kw, 3),
                    "feedbackPowerKw": round(item.feedback_power_kw, 3),
                }
                for item in self.substations
            ],
            "feeders": [
                {
                    "feederId": item.feeder_id,
                    "substationId": item.substation_id,
                    "direction": item.direction,
                    "side": item.side,
                    "currentA": round(item.current_a, 2),
                    "powerKw": round(item.power_kw, 3),
                    "loadRatio": round(item.load_ratio, 4),
                    "status": item.status,
                }
                for item in self.feeders
            ],
            "contactRailFlows": [
                {
                    "sectionId": item.section_id,
                    "direction": item.direction,
                    "currentA": round(item.current_a, 2),
                    "powerKw": round(item.power_kw, 3),
                    "loadRatio": round(item.load_ratio, 4),
                    "status": item.status,
                }
                for item in self.contact_rail_flows
            ],
            "supercapacitorStorageSystems": [
                {
                    "storageId": item.storage_id,
                    "substationId": item.substation_id,
                    "soc": round(item.soc, 5),
                    "storedEnergyKwh": round(item.stored_energy_kwh, 5),
                    "availableChargeEnergyKwh": round(item.available_charge_energy_kwh, 5),
                    "availableDischargeEnergyKwh": round(item.available_discharge_energy_kwh, 5),
                    "chargePowerKw": round(item.charge_power_kw, 3),
                    "dischargePowerKw": round(item.discharge_power_kw, 3),
                    "conversionLossesKw": round(item.conversion_losses_kw, 3),
                    "cumulativeChargedKwh": round(item.cumulative_charged_kwh, 5),
                    "cumulativeDischargedKwh": round(item.cumulative_discharged_kwh, 5),
                    "state": item.state,
                    "status": item.status,
                }
                for item in self.supercapacitor_flows
            ],
            "trainVoltages": [
                {
                    "trainId": item.train_id,
                    "powerSectionId": item.power_section_id,
                    "mileageM": round(item.mileage_m, 3),
                    "voltageV": round(item.voltage_v, 2),
                    "currentA": round(item.current_a, 2),
                    "requestedPowerKw": round(item.requested_power_kw, 3),
                    "tractionPowerRequestKw": round(item.traction_power_request_kw, 3),
                    "tractionPowerDeliveredKw": round(item.traction_power_delivered_kw, 3),
                    "auxiliaryPowerKw": round(item.auxiliary_power_kw, 3),
                    "regenPowerAvailableKw": round(item.regen_power_available_kw, 3),
                    "regenPowerSelfConsumedKw": round(item.regen_power_self_consumed_kw, 3),
                    "regenPowerExportedKw": round(item.regen_power_exported_kw, 3),
                    "regenPowerAcceptedKw": round(item.regen_power_accepted_kw, 3),
                    "regenPowerWastedKw": round(item.regen_power_wasted_kw, 3),
                    "tractionLimitRatio": round(item.traction_limit_ratio, 4),
                    "regenLimitRatio": round(item.regen_limit_ratio, 4),
                    "voltageLevel": item.voltage_level,
                    "leftSubstationId": item.left_substation_id,
                    "rightSubstationId": item.right_substation_id,
                    "headMileageM": round(item.head_mileage_m, 3) if item.head_mileage_m is not None else None,
                    "tailMileageM": round(item.tail_mileage_m, 3) if item.tail_mileage_m is not None else None,
                    "pantographMileagesM": [round(value, 3) for value in item.pantograph_mileages_m],
                    "spannedPowerSectionIds": list(item.spanned_power_section_ids),
                }
                for item in self.trains
            ],
            "regen": {
                "generatedKw": round(self.generated_regen_kw, 3),
                "selfConsumedKw": round(self.self_consumed_regen_kw, 3),
                "absorbedKw": round(self.absorbed_regen_kw, 3),
                "feedbackKw": round(self.feedback_regen_kw, 3),
                "storageChargedKw": round(sum(item.charge_power_kw for item in self.supercapacitor_flows), 3),
                "storageDischargedKw": round(sum(item.discharge_power_kw for item in self.supercapacitor_flows), 3),
                "wastedKw": round(self.wasted_regen_kw, 3),
                "transferLossesKw": round(self.regen_transfer_losses_kw, 3),
                "paths": [
                    {
                        "sourceTrainId": item.source_train_id,
                        "sinkType": item.sink_type,
                        "sinkId": item.sink_id,
                        "viaSubstationId": item.via_substation_id,
                        "sourceFeederId": item.source_feeder_id,
                        "sinkFeederId": item.sink_feeder_id,
                        "generatedKw": round(item.generated_kw, 3),
                        "deliveredKw": round(item.delivered_kw, 3),
                        "lossesKw": round(item.losses_kw, 3),
                        "currentA": round(item.current_a, 3),
                        "pathResistanceOhm": round(item.path_resistance_ohm, 6),
                    }
                    for item in self.regen_paths
                ],
            },
            "lossesKw": round(self.losses_kw, 3),
            "solver": {
                "converged": self.converged,
                "iterations": self.iterations,
                "solveTimeMs": round(self.solve_time_ms, 3),
                "powerBalanceErrorKw": round(self.power_balance_error_kw, 4),
                "powerBalanceErrorRatio": round(self.power_balance_error_ratio, 6),
            },
            "alerts": self.alerts,
            "source": self.source,
            "quality": self.quality,
        }
