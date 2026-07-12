from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from app.domain.signal.models import TrainState  # canonical B/C/D train state

JsonDict = dict[str, Any]


class CommandSource(str, Enum):
    MANUAL = "MANUAL"
    ATO = "ATO"
    ATP_OVERRIDE = "ATP_OVERRIDE"
    PLATFORM = "PLATFORM"
    MOCK = "MOCK"


def _require_non_empty(value: str, field_name: str) -> None:
    if not value:
        raise ValueError(f"{field_name} must not be empty")


def _require_non_negative(value: float, field_name: str) -> None:
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")


def _require_positive(value: float, field_name: str) -> None:
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")


def _require_percent(value: float, field_name: str) -> None:
    if value < 0 or value > 100:
        raise ValueError(f"{field_name} must be between 0 and 100")


@dataclass(frozen=True)
class VehicleConfig:
    train_id: str = "T001"
    mass_kg: float = 225_000.0
    max_speed_mps: float = 22.22
    max_traction_force_n: float = 300_000.0
    max_service_brake_force_n: float = 300_000.0
    emergency_brake_force_n: float = 337_500.0
    basic_resistance_n: float = 4_560.0
    stop_speed_threshold_mps: float = 0.05
    train_length_m: float = 118.0
    average_passenger_mass_kg: float = 65.0
    motor_count: int = 16
    wheel_radius_m: float = 0.46
    gear_ratio: float = 9.02
    drivetrain_efficiency: float = 0.90
    regen_efficiency: float = 0.80
    auxiliary_power_kw: float = 150.0
    nominal_line_voltage_v: float = 750.0
    parameter_quality: str = "TEACHER_CURVE_WITH_CALIBRATED_DRIVETRAIN"
    formation: str = "Tc-M-M-M-M-Tc"
    car_masses_kg: list[float] | None = None
    head_car_length_m: float = 20.2
    middle_car_length_m: float = 19.4
    pantograph_offsets_from_head_m: tuple[float, ...] = (29.5, 88.5)

    def __post_init__(self) -> None:
        _require_non_empty(self.train_id, "train_id")
        _require_positive(self.max_speed_mps, "max_speed_mps")
        _require_positive(self.max_traction_force_n, "max_traction_force_n")
        _require_positive(self.max_service_brake_force_n, "max_service_brake_force_n")
        _require_positive(self.emergency_brake_force_n, "emergency_brake_force_n")
        _require_non_negative(self.basic_resistance_n, "basic_resistance_n")
        _require_positive(self.stop_speed_threshold_mps, "stop_speed_threshold_mps")
        _require_positive(self.average_passenger_mass_kg, "average_passenger_mass_kg")
        _require_positive(float(self.motor_count), "motor_count")
        _require_positive(self.wheel_radius_m, "wheel_radius_m")
        _require_positive(self.gear_ratio, "gear_ratio")
        _require_positive(self.drivetrain_efficiency, "drivetrain_efficiency")
        _require_positive(self.regen_efficiency, "regen_efficiency")
        _require_non_negative(self.auxiliary_power_kw, "auxiliary_power_kw")
        _require_positive(self.nominal_line_voltage_v, "nominal_line_voltage_v")
        _require_positive(self.head_car_length_m, "head_car_length_m")
        _require_positive(self.middle_car_length_m, "middle_car_length_m")
        formation_cars = self.formation.split("-")
        car_count = len(formation_cars)
        if car_count < 1:
            raise ValueError("formation must have at least 1 car")
        total_mass = self.mass_kg
        total_length = self.train_length_m
        if self.car_masses_kg is not None:
            if len(self.car_masses_kg) != car_count:
                raise ValueError(f"car_masses_kg length ({len(self.car_masses_kg)}) != formation car count ({car_count})")
            for m in self.car_masses_kg:
                _require_positive(m, "each element in car_masses_kg")
            total_mass = sum(self.car_masses_kg)
        head_count = sum(1 for c in formation_cars if c == "Tc")
        middle_count = car_count - head_count
        total_length = head_count * self.head_car_length_m + middle_count * self.middle_car_length_m
        _require_positive(total_mass, "mass_kg (computed from car_masses)")
        _require_positive(total_length, "train_length_m (computed from car lengths)")
        object.__setattr__(self, "mass_kg", total_mass)
        object.__setattr__(self, "train_length_m", total_length)
        pantograph_offsets = tuple(float(value) for value in self.pantograph_offsets_from_head_m)
        if not pantograph_offsets:
            raise ValueError("pantograph_offsets_from_head_m must not be empty")
        if any(value < 0 or value > total_length for value in pantograph_offsets):
            raise ValueError("pantograph offset must be within train length")
        object.__setattr__(self, "pantograph_offsets_from_head_m", pantograph_offsets)

    @property
    def empty_mass_kg(self) -> float:
        if self.car_masses_kg is not None:
            return sum(self.car_masses_kg)
        return 225_000.0

    @classmethod
    def for_load(cls, train_id: str, onboard_pax: int, average_passenger_mass_kg: float = 65.0) -> VehicleConfig:
        if onboard_pax < 0:
            raise ValueError("onboard_pax must be non-negative")
        empty = 225_000.0
        return cls(
            train_id=train_id,
            mass_kg=empty + onboard_pax * average_passenger_mass_kg,
            average_passenger_mass_kg=average_passenger_mass_kg,
        )

    @classmethod
    def from_user_config(cls, train_id: str, data: JsonDict) -> VehicleConfig:
        formation = str(data.get("formation", "Tc-M-M-M-M-Tc"))
        car_masses = data.get("carMassesKg")
        if car_masses is not None and isinstance(car_masses, list):
            car_masses = [float(m) for m in car_masses]
        head_car_length_m = float(data.get("headCarLengthM", 20.2))
        middle_car_length_m = float(data.get("middleCarLengthM", 19.4))
        wheel_radius_m = float(data.get("wheelRadiusM", 0.46))
        max_speed_mps = float(data.get("maxSpeedMps", 22.22))
        max_traction_force_n = float(data.get("maxTractionForceN", 300_000.0))
        max_service_brake_force_n = float(data.get("maxServiceBrakeForceN", 300_000.0))
        emergency_brake_force_n = float(data.get("emergencyBrakeForceN", 337_500.0))
        head_count = sum(1 for car in formation.split("-") if car == "Tc")
        car_count = len(formation.split("-"))
        total_length_m = head_count * head_car_length_m + (car_count - head_count) * middle_car_length_m
        raw_offsets = data.get("pantographOffsetsFromHeadM")
        pantograph_offsets = (
            tuple(float(value) for value in raw_offsets)
            if isinstance(raw_offsets, list) and raw_offsets
            else (total_length_m * 0.25, total_length_m * 0.75)
        )
        return cls(
            train_id=train_id,
            formation=formation,
            car_masses_kg=car_masses,
            head_car_length_m=head_car_length_m,
            middle_car_length_m=middle_car_length_m,
            wheel_radius_m=wheel_radius_m,
            max_speed_mps=max_speed_mps,
            max_traction_force_n=max_traction_force_n,
            max_service_brake_force_n=max_service_brake_force_n,
            emergency_brake_force_n=emergency_brake_force_n,
            pantograph_offsets_from_head_m=pantograph_offsets,
        )

    def to_dict(self) -> JsonDict:
        return {
            "trainId": self.train_id,
            "formation": self.formation,
            "carMassesKg": self.car_masses_kg,
            "headCarLengthM": self.head_car_length_m,
            "middleCarLengthM": self.middle_car_length_m,
            "wheelRadiusM": self.wheel_radius_m,
            "massKg": self.mass_kg,
            "trainLengthM": self.train_length_m,
            "maxSpeedMps": self.max_speed_mps,
            "maxTractionForceN": self.max_traction_force_n,
            "maxServiceBrakeForceN": self.max_service_brake_force_n,
            "emergencyBrakeForceN": self.emergency_brake_force_n,
            "basicResistanceN": self.basic_resistance_n,
            "motorCount": self.motor_count,
            "gearRatio": self.gear_ratio,
            "drivetrainEfficiency": self.drivetrain_efficiency,
            "regenEfficiency": self.regen_efficiency,
            "auxiliaryPowerKw": self.auxiliary_power_kw,
            "nominalLineVoltageV": self.nominal_line_voltage_v,
            "pantographOffsetsFromHeadM": list(self.pantograph_offsets_from_head_m),
        }


@dataclass(frozen=True)
class ControlCommand:
    train_id: str
    traction_percent: float = 0.0
    brake_percent: float = 0.0
    emergency_brake: bool = False
    source: CommandSource = CommandSource.MANUAL

    def __post_init__(self) -> None:
        _require_non_empty(self.train_id, "train_id")
        _require_percent(self.traction_percent, "traction_percent")
        _require_percent(self.brake_percent, "brake_percent")
        if not isinstance(self.source, CommandSource):
            object.__setattr__(self, "source", CommandSource(str(self.source)))
        if self.traction_percent > 0 and self.brake_percent > 0:
            raise ValueError("traction_percent and brake_percent cannot both be active")
        if self.emergency_brake and self.traction_percent > 0:
            raise ValueError("emergency_brake requires traction_percent to be 0")

    @classmethod
    def coast(cls, train_id: str, source: CommandSource = CommandSource.MANUAL) -> ControlCommand:
        return cls(train_id=train_id, source=source)

    def to_dict(self) -> JsonDict:
        payload = asdict(self)
        payload["source"] = self.source.value
        return payload
