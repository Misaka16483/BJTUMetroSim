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


@dataclass(frozen=True)
class ReturnRailSection:
    section_id: str
    direction: str
    from_mileage_m: float
    to_mileage_m: float
    resistance_ohm_per_km: float = 0.0083
    cross_bonding_group: str = "V0"


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

    @property
    def traction_power_kw(self) -> float:
        if self.traction_force_n <= 0 or self.speed_mps <= 0:
            return 0.0
        return self.traction_force_n * self.speed_mps / 1000.0 / self.traction_efficiency

    @property
    def raw_regen_power_kw(self) -> float:
        if self.brake_force_n <= 0 or self.speed_mps <= 0:
            return 0.0
        return self.brake_force_n * self.speed_mps / 1000.0 * self.regen_efficiency

    @property
    def regen_power_kw(self) -> float:
        return max(self.raw_regen_power_kw - self.aux_power_kw, 0.0)

    @property
    def requested_power_kw(self) -> float:
        return self.traction_power_kw + self.aux_power_kw - self.regen_power_kw


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
    left_substation_id: str | None = None
    right_substation_id: str | None = None


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
class PowerFlowSnapshot:
    sim_time_ms: int
    trains: list[TrainPowerFlow] = field(default_factory=list)
    substations: list[SubstationPowerFlow] = field(default_factory=list)
    feeders: list[FeederPowerFlow] = field(default_factory=list)
    generated_regen_kw: float = 0.0
    absorbed_regen_kw: float = 0.0
    feedback_regen_kw: float = 0.0
    wasted_regen_kw: float = 0.0
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
            "trainVoltages": [
                {
                    "trainId": item.train_id,
                    "powerSectionId": item.power_section_id,
                    "mileageM": round(item.mileage_m, 3),
                    "voltageV": round(item.voltage_v, 2),
                    "currentA": round(item.current_a, 2),
                    "requestedPowerKw": round(item.requested_power_kw, 3),
                    "tractionLimitRatio": round(item.traction_limit_ratio, 4),
                    "regenLimitRatio": round(item.regen_limit_ratio, 4),
                    "voltageLevel": item.voltage_level,
                    "leftSubstationId": item.left_substation_id,
                    "rightSubstationId": item.right_substation_id,
                }
                for item in self.trains
            ],
            "regen": {
                "generatedKw": round(self.generated_regen_kw, 3),
                "absorbedKw": round(self.absorbed_regen_kw, 3),
                "feedbackKw": round(self.feedback_regen_kw, 3),
                "wastedKw": round(self.wasted_regen_kw, 3),
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
