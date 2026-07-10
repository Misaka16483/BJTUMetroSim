from __future__ import annotations

from dataclasses import dataclass

from app.domain.vehicle.models import ControlCommand, TrainState, VehicleConfig


MOTOR_SPEED_RPM = (
    0.0, 83.2, 166.4, 249.6, 332.8, 416.0, 499.2, 582.4, 665.6, 748.8, 832.0,
    915.2, 998.4, 1081.6, 1164.8, 1248.0, 1331.2, 1414.4, 1497.6, 1580.8, 1664.0,
    1747.2, 1830.4, 1913.6, 1996.9, 2080.1, 2163.3, 2246.5, 2329.7, 2412.9, 2496.1,
    2579.3, 2662.5, 2745.7, 2828.9, 2912.1, 2995.3, 3078.5, 3161.7, 3244.9, 3328.1,
    3411.3, 3494.5, 3577.7, 3660.9, 3744.1, 3827.3, 3910.5, 3993.7, 4076.9, 4160.1,
    4160.1,
)

TRACTION_TORQUE_NM = (
    1042.9, 1042.9, 1042.9, 1042.9, 1042.9, 1042.9, 1042.9, 1042.9, 1042.9,
    1042.9, 1042.9, 1042.9, 1042.9, 1042.9, 1042.9, 1042.9, 1042.9, 1042.9,
    1042.9, 1042.9, 1042.9, 1042.9, 1042.9, 1042.9, 1042.9, 1042.9, 1042.9,
    1042.9, 1042.9, 1042.9, 1036.8, 971.0, 911.2, 856.9, 807.2, 761.7, 720.0,
    681.6, 646.2, 613.5, 583.2, 555.1, 529.0, 504.7, 482.0, 460.8, 441.0, 422.4,
    405.0, 388.6, 373.2, 373.2,
)

BRAKE_TORQUE_NM = (
    0.0, 0.0, 977.7, 977.7, 977.7, 977.7, 977.7, 977.7, 977.7, 977.7, 977.7,
    977.7, 977.7, 977.7, 977.7, 977.7, 977.7, 977.7, 977.7, 977.7, 977.7, 977.7,
    977.7, 977.7, 977.7, 977.7, 977.7, 977.7, 977.7, 977.7, 977.7, 977.7, 977.7,
    977.7, 977.7, 977.7, 977.7, 977.7, 977.7, 977.7, 977.7, 977.7, 977.7, 977.7,
    977.7, 977.7, 977.7, 977.7, 977.7, 977.7, 977.7, 977.7,
)

TRACTION_NET_CURRENT_A = (
    0.0, 109.9, 219.8, 329.7, 439.6, 549.4, 659.3, 769.2, 879.1, 989.0, 1098.9,
    1208.8, 1318.7, 1428.6, 1538.5, 1648.3, 1758.2, 1868.1, 1978.0, 2087.9,
    2197.8, 2307.7, 2417.6, 2527.5, 2637.3, 2747.2, 2857.1, 2967.0, 3076.9,
    3186.8, 3277.5, 3171.7, 3072.6, 2979.5, 2891.9, 2809.2, 2731.2, 2657.4,
    2587.5, 2521.1, 2458.1, 2398.1, 2341.0, 2286.6, 2234.6, 2185.0, 2137.5,
    2092.0, 2048.4, 2006.6, 1966.5, 1966.5,
)

BRAKE_NET_CURRENT_A = (
    0.0, 0.0, 145.7, 218.6, 291.4, 364.3, 437.1, 510.0, 582.9, 655.7, 728.6,
    801.4, 874.3, 947.1, 1020.0, 1092.9, 1165.7, 1238.6, 1311.4, 1384.3, 1457.1,
    1530.0, 1602.9, 1675.7, 1748.6, 1821.4, 1894.3, 1967.1, 2040.0, 2112.9,
    2185.7, 2258.6, 2331.4, 2404.3, 2477.1, 2550.0, 2622.9, 2695.7, 2768.6,
    2841.4, 2914.3, 2987.1, 3060.0, 3132.9, 3205.7, 3278.6, 3351.4, 3424.3,
    3497.2, 3570.0, 3642.9, 3642.9,
)


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)


def _interpolate(xs: tuple[float, ...], ys: tuple[float, ...], x: float) -> float:
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for idx in range(1, len(xs)):
        if x <= xs[idx]:
            span = xs[idx] - xs[idx - 1]
            if span <= 0:
                return ys[idx]
            ratio = (x - xs[idx - 1]) / span
            return ys[idx - 1] + ratio * (ys[idx] - ys[idx - 1])
    return ys[-1]


@dataclass(frozen=True)
class VehicleForceDemand:
    traction_force_n: float
    total_brake_force_n: float
    candidate_electric_brake_force_n: float


@dataclass(frozen=True)
class BrakeBlendResult:
    electric_brake_force_n: float
    pneumatic_brake_force_n: float

    @property
    def total_brake_force_n(self) -> float:
        return self.electric_brake_force_n + self.pneumatic_brake_force_n


class TractionDriveModel:
    """Teacher-provided 52-point motor curves with an explicit calibrated drivetrain mapping."""

    def __init__(self, config: VehicleConfig | None = None) -> None:
        self.config = config or VehicleConfig()

    def motor_speed_rpm(self, speed_mps: float) -> float:
        return _clamp(speed_mps / self.config.max_speed_mps, 0.0, 1.0) * MOTOR_SPEED_RPM[-1]

    def traction_capacity_n(self, speed_mps: float) -> float:
        torque_nm = _interpolate(MOTOR_SPEED_RPM, TRACTION_TORQUE_NM, self.motor_speed_rpm(speed_mps))
        wheel_force_n = (
            torque_nm
            * self.config.motor_count
            * self.config.gear_ratio
            * self.config.drivetrain_efficiency
            / self.config.wheel_radius_m
        )
        if speed_mps > 0.5:
            current_a = _interpolate(MOTOR_SPEED_RPM, TRACTION_NET_CURRENT_A, self.motor_speed_rpm(speed_mps))
            electrical_cap_n = (
                current_a
                * self.config.nominal_line_voltage_v
                * self.config.drivetrain_efficiency
                / speed_mps
            )
            wheel_force_n = min(wheel_force_n, electrical_cap_n)
        return min(wheel_force_n, self.config.max_traction_force_n)

    def electric_brake_capacity_n(self, speed_mps: float) -> float:
        torque_nm = _interpolate(MOTOR_SPEED_RPM, BRAKE_TORQUE_NM, self.motor_speed_rpm(speed_mps))
        wheel_force_n = (
            torque_nm
            * self.config.motor_count
            * self.config.gear_ratio
            * self.config.drivetrain_efficiency
            / self.config.wheel_radius_m
        )
        if speed_mps > 0.5:
            current_a = _interpolate(MOTOR_SPEED_RPM, BRAKE_NET_CURRENT_A, self.motor_speed_rpm(speed_mps))
            electrical_cap_n = (
                current_a
                * self.config.nominal_line_voltage_v
                / max(self.config.regen_efficiency, 1e-6)
                / speed_mps
            )
            wheel_force_n = min(wheel_force_n, electrical_cap_n)
        return min(wheel_force_n, self.config.max_service_brake_force_n)

    def demand(self, command: ControlCommand, speed_mps: float) -> VehicleForceDemand:
        if command.emergency_brake:
            return VehicleForceDemand(0.0, self.config.emergency_brake_force_n, 0.0)
        traction = self.traction_capacity_n(speed_mps) * command.traction_percent / 100.0
        total_brake = self.config.max_service_brake_force_n * command.brake_percent / 100.0
        electric = min(
            total_brake,
            self.electric_brake_capacity_n(speed_mps) * command.brake_percent / 100.0,
        )
        return VehicleForceDemand(traction, total_brake, electric)


class BrakeBlendService:
    """Allocate the requested service brake between electric and pneumatic braking."""

    @staticmethod
    def blend(demand: VehicleForceDemand, regen_limit_ratio: float) -> BrakeBlendResult:
        electric = demand.candidate_electric_brake_force_n * _clamp(regen_limit_ratio, 0.0, 1.0)
        pneumatic = max(demand.total_brake_force_n - electric, 0.0)
        return BrakeBlendResult(electric, pneumatic)


class SimpleVehicleModel:
    """One-dimensional vehicle dynamics model for Phase 0/1 simulation."""

    def __init__(self, config: VehicleConfig | None = None) -> None:
        self.config = config or VehicleConfig()

    def step(
        self,
        state: TrainState,
        command: ControlCommand,
        dt_s: float = 1.0,
        traction_limit_ratio: float = 1.0,
        gradient_force_n: float = 0.0,
    ) -> TrainState:
        if dt_s <= 0:
            raise ValueError("dt_s must be positive")
        if state.train_id != self.config.train_id or command.train_id != self.config.train_id:
            raise ValueError("state, command, and config train_id must match")

        demand = TractionDriveModel(self.config).demand(command, state.speed_mps)
        traction_force_n = demand.traction_force_n * _clamp(traction_limit_ratio, 0.0, 1.0)
        brake_force_n = BrakeBlendService.blend(demand, 1.0).total_brake_force_n

        return self.step_with_forces(
            state,
            traction_force_n=traction_force_n,
            brake_force_n=brake_force_n,
            dt_s=dt_s,
            gradient_force_n=gradient_force_n,
        )

    def step_with_forces(
        self,
        state: TrainState,
        *,
        traction_force_n: float,
        brake_force_n: float,
        dt_s: float = 1.0,
        gradient_force_n: float = 0.0,
    ) -> TrainState:
        if dt_s <= 0:
            raise ValueError("dt_s must be positive")
        if state.train_id != self.config.train_id:
            raise ValueError("state and config train_id must match")
        traction_force_n = max(0.0, traction_force_n)
        brake_force_n = max(0.0, brake_force_n)

        resistance_force_n = self.running_resistance_n(state.speed_mps, traction_force_n, brake_force_n)
        net_force_n = traction_force_n - brake_force_n - resistance_force_n - gradient_force_n
        raw_acceleration_mps2 = net_force_n / self.config.mass_kg
        raw_next_speed_mps = state.speed_mps + raw_acceleration_mps2 * dt_s
        next_speed_mps = _clamp(raw_next_speed_mps, 0.0, self.config.max_speed_mps)
        acceleration_mps2 = (next_speed_mps - state.speed_mps) / dt_s
        average_speed_mps = (state.speed_mps + next_speed_mps) / 2.0
        next_position_m = state.position_m + average_speed_mps * dt_s
        traction_energy_kwh = traction_force_n * average_speed_mps * dt_s / 3_600_000.0

        return TrainState(
            train_id=state.train_id,
            position_m=next_position_m,
            speed_mps=next_speed_mps,
            acceleration_mps2=acceleration_mps2,
            sim_time_s=state.sim_time_s + dt_s,
            segment_id=state.segment_id,
            net_energy_kwh=state.net_energy_kwh + traction_energy_kwh,
        )

    def running_resistance_n(self, speed_mps: float, traction_force_n: float = 0.0, brake_force_n: float = 0.0) -> float:
        if speed_mps <= self.config.stop_speed_threshold_mps and traction_force_n <= 0 and brake_force_n <= 0:
            return 0.0
        mass_t = self.config.mass_kg / 1000.0
        speed_kmh = max(speed_mps, 0.0) * 3.6
        axle_count = 24.0
        car_count = 6.0
        frontal_area_m2 = 10.6
        return (
            6.4 * mass_t
            + 130.0 * axle_count
            + 0.14 * mass_t * speed_kmh
            + (0.046 + 0.0065 * (car_count - 1.0)) * frontal_area_m2 * speed_kmh * speed_kmh
        )
