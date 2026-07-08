from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PowerSection:
    power_section_id: str
    name: str
    max_traction_power_kw: float
    warning_power_kw: float
    regen_absorb_limit_kw: float = 0.0
    min_limit_ratio: float = 0.35
    outage: bool = False


@dataclass(frozen=True)
class TrainPowerRequest:
    train_id: str
    power_section_id: str
    speed_mps: float
    traction_force_n: float = 0.0
    brake_force_n: float = 0.0


@dataclass(frozen=True)
class PowerState:
    power_section_id: str
    requested_power_kw: float
    available_power_kw: float
    traction_limit_ratio: float
    voltage_level: str
    energy_kwh: float
    regen_energy_kwh: float
    absorbed_regen_kw: float
    wasted_regen_kw: float
    source: str = "SELF_SIM"
    quality: str = "ESTIMATED"


class PowerService:
    """Phase 2 self-developed reduced traction-power model."""

    def __init__(
        self,
        sections: list[PowerSection],
        *,
        traction_efficiency: float = 0.88,
        regen_efficiency: float = 0.65,
        limited_slope: float = 0.6,
    ) -> None:
        self.sections = {section.power_section_id: section for section in sections}
        self.traction_efficiency = traction_efficiency
        self.regen_efficiency = regen_efficiency
        self.limited_slope = limited_slope
        self._energy_kwh_by_section: dict[str, float] = {section.power_section_id: 0.0 for section in sections}
        self._regen_kwh_by_section: dict[str, float] = {section.power_section_id: 0.0 for section in sections}

    def update(self, requests: list[TrainPowerRequest], dt_sec: float) -> dict[str, PowerState]:
        states: dict[str, PowerState] = {}
        requests_by_section: dict[str, list[TrainPowerRequest]] = {}
        for request in requests:
            requests_by_section.setdefault(request.power_section_id, []).append(request)

        for section_id, section in self.sections.items():
            section_requests = requests_by_section.get(section_id, [])
            gross_traction_kw = sum(self._traction_power_kw(item) for item in section_requests)
            generated_regen_kw = sum(self._regen_power_kw(item) for item in section_requests)
            absorbed_regen_kw = min(generated_regen_kw, gross_traction_kw, section.regen_absorb_limit_kw)
            wasted_regen_kw = max(generated_regen_kw - absorbed_regen_kw, 0.0)
            net_requested_kw = max(gross_traction_kw - absorbed_regen_kw, 0.0)

            self._energy_kwh_by_section[section_id] += net_requested_kw * dt_sec / 3600.0
            self._regen_kwh_by_section[section_id] += generated_regen_kw * dt_sec / 3600.0

            voltage_level, traction_limit_ratio = self._voltage_and_limit(section, net_requested_kw)
            states[section_id] = PowerState(
                power_section_id=section_id,
                requested_power_kw=net_requested_kw,
                available_power_kw=0.0 if section.outage else section.max_traction_power_kw,
                traction_limit_ratio=traction_limit_ratio,
                voltage_level=voltage_level,
                energy_kwh=self._energy_kwh_by_section[section_id],
                regen_energy_kwh=self._regen_kwh_by_section[section_id],
                absorbed_regen_kw=absorbed_regen_kw,
                wasted_regen_kw=wasted_regen_kw,
            )
        return states

    def _traction_power_kw(self, request: TrainPowerRequest) -> float:
        if request.traction_force_n <= 0 or request.speed_mps <= 0:
            return 0.0
        return request.traction_force_n * request.speed_mps / 1000.0 / self.traction_efficiency

    def _regen_power_kw(self, request: TrainPowerRequest) -> float:
        if request.brake_force_n <= 0 or request.speed_mps <= 0:
            return 0.0
        return request.brake_force_n * request.speed_mps / 1000.0 * self.regen_efficiency

    def _voltage_and_limit(self, section: PowerSection, requested_power_kw: float) -> tuple[str, float]:
        if section.outage:
            return "OUTAGE", 0.0
        if requested_power_kw <= 0:
            return "NORMAL", 1.0
        if section.max_traction_power_kw <= 0:
            return "OUTAGE", 0.0

        warning_ratio = min(max(section.warning_power_kw / section.max_traction_power_kw, 0.0), 1.0)
        load_ratio = requested_power_kw / section.max_traction_power_kw
        if load_ratio <= warning_ratio:
            return "NORMAL", 1.0
        if load_ratio <= 1.0:
            limit = 1.0 - self.limited_slope * (load_ratio - warning_ratio)
            return "LIMITED", max(section.min_limit_ratio, min(1.0, limit))
        return "UNDERVOLTAGE", max(section.min_limit_ratio, min(1.0, 1.0 / load_ratio))

