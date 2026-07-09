from __future__ import annotations

from app.domain.vehicle.models import ControlCommand, TrainState, VehicleConfig


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)


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

        traction_limit = _clamp(traction_limit_ratio, 0.0, 1.0)
        traction_force_n = self.config.max_traction_force_n * command.traction_percent / 100.0 * traction_limit
        brake_force_n = self.config.max_service_brake_force_n * command.brake_percent / 100.0
        if command.emergency_brake:
            traction_force_n = 0.0
            brake_force_n = self.config.emergency_brake_force_n

        resistance_force_n = self._running_resistance_n(state.speed_mps, traction_force_n, brake_force_n)
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

    def _running_resistance_n(self, speed_mps: float, traction_force_n: float, brake_force_n: float) -> float:
        if speed_mps <= self.config.stop_speed_threshold_mps and traction_force_n <= 0 and brake_force_n <= 0:
            return 0.0
        return self.config.basic_resistance_n
