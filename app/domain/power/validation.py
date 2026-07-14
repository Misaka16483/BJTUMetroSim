from __future__ import annotations

import math
from dataclasses import dataclass

from app.domain.power.network_models import PowerFlowSnapshot


@dataclass(frozen=True)
class PowerValidationIssue:
    code: str
    message: str
    severity: str = "ERROR"

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "severity": self.severity}


@dataclass(frozen=True)
class PowerValidationReport:
    passed: bool
    issues: tuple[PowerValidationIssue, ...]
    metrics: dict[str, float]

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "issues": [item.to_dict() for item in self.issues],
            "metrics": self.metrics,
        }


def validate_power_snapshot(
    snapshot: PowerFlowSnapshot,
    *,
    nominal_voltage_v: float = 750.0,
    balance_error_limit_ratio: float = 0.01,
    regen_error_limit_kw: float = 1e-6,
) -> PowerValidationReport:
    issues: list[PowerValidationIssue] = []

    def error(code: str, message: str) -> None:
        issues.append(PowerValidationIssue(code, message))

    if not snapshot.converged:
        error("SOLVER_NOT_CONVERGED", "DC power-flow solver did not converge")
    if not math.isfinite(snapshot.power_balance_error_ratio):
        error("NON_FINITE_BALANCE", "power balance error is non-finite")
    elif snapshot.power_balance_error_ratio > balance_error_limit_ratio:
        error(
            "POWER_BALANCE_EXCEEDED",
            f"power balance error {snapshot.power_balance_error_ratio:.3%} exceeds "
            f"{balance_error_limit_ratio:.3%}",
        )

    storage_charge_kw = sum(item.charge_power_kw for item in snapshot.supercapacitor_flows)
    regen_accounted_kw = (
        snapshot.self_consumed_regen_kw
        + snapshot.absorbed_regen_kw
        + storage_charge_kw
        + snapshot.feedback_regen_kw
        + snapshot.wasted_regen_kw
        + snapshot.regen_transfer_losses_kw
    )
    regen_error_kw = abs(snapshot.generated_regen_kw - regen_accounted_kw)
    if regen_error_kw > regen_error_limit_kw:
        error(
            "REGEN_BALANCE_EXCEEDED",
            f"regenerative split error {regen_error_kw:.9f} kW exceeds {regen_error_limit_kw:.9f} kW",
        )

    nonnegative_values = {
        "generated": snapshot.generated_regen_kw,
        "selfConsumed": snapshot.self_consumed_regen_kw,
        "absorbed": snapshot.absorbed_regen_kw,
        "feedback": snapshot.feedback_regen_kw,
        "wasted": snapshot.wasted_regen_kw,
        "transferLosses": snapshot.regen_transfer_losses_kw,
        "storageCharge": storage_charge_kw,
        "networkLosses": snapshot.losses_kw,
    }
    for name, value in nonnegative_values.items():
        if not math.isfinite(value) or value < -1e-9:
            error("INVALID_NONNEGATIVE_QUANTITY", f"{name} must be finite and non-negative, got {value}")

    voltage_upper_v = nominal_voltage_v * 4.0 / 3.0
    max_substation_identity_error_kw = 0.0
    for substation in snapshot.substations:
        values = (substation.voltage_v, substation.current_a, substation.power_kw)
        if not all(math.isfinite(value) for value in values):
            error("NON_FINITE_SUBSTATION", f"{substation.substation_id} has non-finite electrical values")
            continue
        if substation.voltage_v < -1e-9 or substation.voltage_v > voltage_upper_v + 1e-9:
            error(
                "SUBSTATION_VOLTAGE_OUT_OF_RANGE",
                f"{substation.substation_id} voltage {substation.voltage_v:.3f} V is outside the DC750V model range",
            )
        identity_error = abs(substation.power_kw - substation.voltage_v * substation.current_a / 1000.0)
        max_substation_identity_error_kw = max(max_substation_identity_error_kw, identity_error)
        if identity_error > 1e-6:
            error(
                "SUBSTATION_POWER_IDENTITY",
                f"{substation.substation_id} violates P=UI by {identity_error:.9f} kW",
            )
        if substation.rectifier_power_kw < -1e-9 or substation.feedback_power_kw < -1e-9:
            error("INVALID_SUBSTATION_POWER_SPLIT", f"{substation.substation_id} has a negative split component")

    for train in snapshot.trains:
        if not all(math.isfinite(value) for value in (
            train.voltage_v,
            train.current_a,
            train.traction_limit_ratio,
            train.regen_limit_ratio,
        )):
            error("NON_FINITE_TRAIN", f"{train.train_id} has non-finite electrical values")
            continue
        if train.voltage_v < -1e-9 or train.voltage_v > voltage_upper_v + 1e-9:
            error("TRAIN_VOLTAGE_OUT_OF_RANGE", f"{train.train_id} voltage is outside the DC750V model range")
        if not 0.0 <= train.traction_limit_ratio <= 1.0:
            error("INVALID_TRACTION_LIMIT", f"{train.train_id} traction limit is outside [0, 1]")
        if not 0.0 <= train.regen_limit_ratio <= 1.0:
            error("INVALID_REGEN_LIMIT", f"{train.train_id} regen limit is outside [0, 1]")

    for storage in snapshot.supercapacitor_flows:
        if not 0.0 <= storage.soc <= 1.0:
            error("STORAGE_SOC_OUT_OF_RANGE", f"{storage.storage_id} SOC is outside [0, 1]")
        if storage.charge_power_kw > 1e-9 and storage.discharge_power_kw > 1e-9:
            error("STORAGE_SIMULTANEOUS_CHARGE_DISCHARGE", f"{storage.storage_id} charges and discharges together")

    return PowerValidationReport(
        passed=not any(item.severity == "ERROR" for item in issues),
        issues=tuple(issues),
        metrics={
            "powerBalanceErrorKw": snapshot.power_balance_error_kw,
            "powerBalanceErrorRatio": snapshot.power_balance_error_ratio,
            "regenBalanceErrorKw": regen_error_kw,
            "regenGeneratedKw": snapshot.generated_regen_kw,
            "regenAccountedKw": regen_accounted_kw,
            "maxSubstationPowerIdentityErrorKw": max_substation_identity_error_kw,
            "minTrainVoltageV": min((item.voltage_v for item in snapshot.trains), default=nominal_voltage_v),
        },
    )
