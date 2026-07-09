"""Phase 1: lightweight energy estimation for Member D.

Uses the simple formula: power_kw = traction_force_n * speed_mps / 1000.
No power sections, no regenerative braking, no efficiency factors.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SimpleEnergyEstimate:
    train_id: str
    segment_id: str
    traction_force_n: float
    speed_mps: float
    power_kw: float
    energy_kwh: float
    duration_sec: float
    method: str = "SELF_SIM_PHASE1"


def estimate_traction_energy(
    train_id: str,
    segment_id: str,
    traction_force_n: float,
    speed_mps: float,
    dt_sec: float,
) -> SimpleEnergyEstimate:
    if traction_force_n <= 0.0 or speed_mps <= 0.0 or dt_sec <= 0.0:
        return SimpleEnergyEstimate(
            train_id=train_id,
            segment_id=segment_id,
            traction_force_n=traction_force_n,
            speed_mps=speed_mps,
            power_kw=0.0,
            energy_kwh=0.0,
            duration_sec=dt_sec,
        )
    power_kw = traction_force_n * speed_mps / 1000.0
    energy_kwh = power_kw * dt_sec / 3600.0
    return SimpleEnergyEstimate(
        train_id=train_id,
        segment_id=segment_id,
        traction_force_n=traction_force_n,
        speed_mps=speed_mps,
        power_kw=power_kw,
        energy_kwh=energy_kwh,
        duration_sec=dt_sec,
    )
