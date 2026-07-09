"""Phase 0: default power states for Member D."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DefaultPowerState:
    power_section_id: str
    name: str
    max_traction_power_kw: float
    available_power_kw: float
    warning_power_kw: float
    requested_power_kw: float = 0.0
    voltage_level: str = "NORMAL"
    traction_limit_ratio: float = 1.0
    regen_absorb_limit_kw: float = 0.0
    source: str = "DEFAULT"
    quality: str = "ESTIMATED"


DEFAULT_POWER_SECTIONS: list[DefaultPowerState] = [
    DefaultPowerState(
        power_section_id="PWR-09-UP",
        name="Line 9 Up-track",
        max_traction_power_kw=1000.0,
        available_power_kw=1000.0,
        warning_power_kw=800.0,
        regen_absorb_limit_kw=200.0,
    ),
    DefaultPowerState(
        power_section_id="PWR-09-DOWN",
        name="Line 9 Down-track",
        max_traction_power_kw=1000.0,
        available_power_kw=1000.0,
        warning_power_kw=800.0,
        regen_absorb_limit_kw=200.0,
    ),
]


def generate_default_power_state(
    power_section_id: str,
    name: str,
    max_traction_power_kw: float,
    available_power_kw: float,
    warning_power_kw: float,
    regen_absorb_limit_kw: float = 0.0,
) -> DefaultPowerState:
    return DefaultPowerState(
        power_section_id=power_section_id,
        name=name,
        max_traction_power_kw=max_traction_power_kw,
        available_power_kw=available_power_kw,
        warning_power_kw=warning_power_kw,
        regen_absorb_limit_kw=regen_absorb_limit_kw,
    )
