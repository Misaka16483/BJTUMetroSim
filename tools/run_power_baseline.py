from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.domain.power.flow_solver import DCTractionPowerFlowSolver
from app.domain.power.line9_topology import load_line9_power_network
from app.domain.power.network_models import PowerFlowSnapshot, TrainElectricalLoad
from app.domain.power.validation import validate_power_snapshot
from app.infra.rtdb_power_dictionary import (
    audit_table_definition,
    power_point_contract_document,
    power_point_contract_sha256,
)


TOPOLOGY = ROOT / "data" / "scenarios" / "line9_power_topology.json"
DEFAULT_DEFINITION = ROOT / "188_2.tableData-1(1).csv"
DEFAULT_OUTPUT = ROOT / "outputs" / "power-baseline-v1.json"


def _snapshot_metrics(snapshot: PowerFlowSnapshot) -> dict[str, float | bool]:
    validation = validate_power_snapshot(snapshot)
    return {
        "passed": validation.passed,
        "converged": snapshot.converged,
        "minTrainVoltageV": min((item.voltage_v for item in snapshot.trains), default=750.0),
        "minTractionLimitRatio": min((item.traction_limit_ratio for item in snapshot.trains), default=1.0),
        "rectifierInputKw": sum(item.rectifier_power_kw for item in snapshot.substations),
        "feedbackKw": snapshot.feedback_regen_kw,
        "regenGeneratedKw": snapshot.generated_regen_kw,
        "regenSelfConsumedKw": snapshot.self_consumed_regen_kw,
        "regenAbsorbedKw": snapshot.absorbed_regen_kw,
        "regenStorageChargedKw": sum(item.charge_power_kw for item in snapshot.supercapacitor_flows),
        "regenWastedKw": snapshot.wasted_regen_kw,
        "lossesKw": snapshot.losses_kw,
        "powerBalanceErrorRatio": snapshot.power_balance_error_ratio,
        "regenBalanceErrorKw": validation.metrics["regenBalanceErrorKw"],
    }


def single_train_case() -> dict[str, object]:
    solver = DCTractionPowerFlowSolver(load_line9_power_network(TOPOLOGY))
    snapshot = solver.solve(
        [TrainElectricalLoad(
            train_id="BASE-SINGLE",
            direction="UP",
            mileage_m=2400.0,
            speed_mps=18.0,
            traction_force_n=100_000.0,
            aux_power_kw=80.0,
        )],
        dt_sec=0.25,
        sim_time_ms=0,
    )
    validation = validate_power_snapshot(snapshot)
    train = snapshot.trains[0]
    checks = {
        "physicalValidation": validation.passed,
        "tractionDemandPositive": train.requested_power_kw > 0.0,
        "dc750VoltagePlausible": 500.0 <= train.voltage_v <= 900.0,
        "noSpuriousRegen": abs(snapshot.generated_regen_kw) <= 1e-9,
        "balanceUnder1Percent": snapshot.power_balance_error_ratio < 0.01,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "metrics": _snapshot_metrics(snapshot),
        "validation": validation.to_dict(),
    }


def two_train_regen_case() -> dict[str, object]:
    solver = DCTractionPowerFlowSolver(load_line9_power_network(TOPOLOGY))
    snapshot = solver.solve(
        [
            TrainElectricalLoad(
                train_id="BASE-TRACTION",
                direction="UP",
                mileage_m=2200.0,
                speed_mps=18.0,
                traction_force_n=95_000.0,
                aux_power_kw=60.0,
            ),
            TrainElectricalLoad(
                train_id="BASE-BRAKING",
                direction="UP",
                mileage_m=2600.0,
                speed_mps=18.0,
                brake_force_n=80_000.0,
                aux_power_kw=60.0,
            ),
        ],
        dt_sec=0.25,
        sim_time_ms=1000,
    )
    validation = validate_power_snapshot(snapshot)
    cross_train_paths = [item for item in snapshot.regen_paths if item.sink_type == "TRAIN"]
    checks = {
        "physicalValidation": validation.passed,
        "regenGenerated": snapshot.generated_regen_kw > 0.0,
        "crossTrainAbsorption": snapshot.absorbed_regen_kw > 0.0 and bool(cross_train_paths),
        "exactRegenAccounting": validation.metrics["regenBalanceErrorKw"] <= 1e-6,
        "balanceUnder1Percent": snapshot.power_balance_error_ratio < 0.01,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "metrics": _snapshot_metrics(snapshot),
        "crossTrainPathCount": len(cross_train_paths),
        "validation": validation.to_dict(),
    }


def outage_recovery_case() -> dict[str, object]:
    network = load_line9_power_network(TOPOLOGY)
    topology_before = {
        "substations": {key: item.status for key, item in network.substations.items()},
        "feeders": {key: item.status for key, item in network.feeders.items()},
        "switches": {key: item.current_state for key, item in network.switches.items()},
    }
    loads = [
        TrainElectricalLoad(
            train_id=f"BASE-N1-{index + 1}",
            direction="UP",
            mileage_m=6100.0 + index * 180.0,
            speed_mps=18.0,
            traction_force_n=170_000.0,
            aux_power_kw=100.0,
        )
        for index in range(3)
    ]
    baseline = DCTractionPowerFlowSolver(network).solve(loads, dt_sec=0.25, sim_time_ms=0)
    network.apply_substation_outage("TS-0905", big_bilateral=True)
    outage = DCTractionPowerFlowSolver(network).solve(loads, dt_sec=0.25, sim_time_ms=250)
    network.restore_substation("TS-0905")
    topology_after = {
        "substations": {key: item.status for key, item in network.substations.items()},
        "feeders": {key: item.status for key, item in network.feeders.items()},
        "switches": {key: item.current_state for key, item in network.switches.items()},
    }
    recovered = DCTractionPowerFlowSolver(network).solve(loads, dt_sec=0.25, sim_time_ms=500)

    baseline_validation = validate_power_snapshot(baseline)
    outage_validation = validate_power_snapshot(outage)
    recovered_validation = validate_power_snapshot(recovered)
    baseline_voltage = min(item.voltage_v for item in baseline.trains)
    outage_voltage = min(item.voltage_v for item in outage.trains)
    recovered_voltage = min(item.voltage_v for item in recovered.trains)
    failed = next(item for item in outage.substations if item.substation_id == "TS-0905")
    recovered_substation = next(item for item in recovered.substations if item.substation_id == "TS-0905")
    checks = {
        "allSnapshotsPhysicallyValid": all((
            baseline_validation.passed,
            outage_validation.passed,
            recovered_validation.passed,
        )),
        "failedSourceExcluded": failed.status == "OUTAGE" and abs(failed.current_a) <= 1e-9,
        "outageDegradesVoltage": outage_voltage < baseline_voltage,
        "outageTriggersTractionLimit": min(item.traction_limit_ratio for item in outage.trains) < 1.0,
        "sourceRestored": (
            network.substations["TS-0905"].status == "IN_SERVICE"
            and recovered_substation.status != "OUTAGE"
            and recovered_substation.current_a > 0.0
        ),
        "voltageReturnsToBaseline": abs(recovered_voltage - baseline_voltage) <= 1e-6,
        "topologyRestored": topology_after == topology_before,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "baseline": _snapshot_metrics(baseline),
        "outage": _snapshot_metrics(outage),
        "recovered": _snapshot_metrics(recovered),
        "voltageDropV": baseline_voltage - outage_voltage,
        "recoveryVoltageErrorV": recovered_voltage - baseline_voltage,
    }


def build_report(definition_path: Path | None = None) -> dict[str, object]:
    cases = {
        "singleTrainTraction": single_train_case(),
        "twoTrainRegenCoordination": two_train_regen_case(),
        "substationOutageRecovery": outage_recovery_case(),
    }
    dictionary: dict[str, object]
    if definition_path is not None and definition_path.exists():
        dictionary = audit_table_definition(definition_path).to_dict()
    else:
        dictionary = {
            "passed": False,
            "errors": ["teacher table definition file was not supplied"],
            "warnings": [],
        }
    dictionary["contract"] = power_point_contract_document()
    dictionary["contractSha256"] = power_point_contract_sha256()
    gates = {
        "dictionaryContract": bool(dictionary["passed"]),
        "singleTrainTraction": bool(cases["singleTrainTraction"]["passed"]),
        "twoTrainRegenCoordination": bool(cases["twoTrainRegenCoordination"]["passed"]),
        "substationOutageRecovery": bool(cases["substationOutageRecovery"]["passed"]),
    }
    return {
        "baselineId": "POWER-CREDIBILITY-BASELINE-V1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "passed": all(gates.values()),
        "quality": "ENGINEERING_ESTIMATE",
        "gates": gates,
        "thresholds": {
            "powerBalanceErrorRatioMax": 0.01,
            "regenBalanceErrorKwMax": 1e-6,
            "dictionaryStationPuiErrorRatioMax": 0.01,
            "experimentHardStopBalanceErrorRatio": 0.05,
        },
        "assumptions": [
            "The teacher point table is read-only and is not injected into the DC750V solver.",
            "Point numbers and definition line numbers are 1-based; RTDB transport row indexes are 0-based.",
            "The teacher samples are approximately 1.6 kV, so only semantics, units, signs and identities are compared.",
            "Unknown blank units in repeated train groups inherit the corresponding train-1 unit.",
        ],
        "artifacts": {
            "topologyPath": str(TOPOLOGY.relative_to(ROOT)),
            "topologySha256": hashlib.sha256(TOPOLOGY.read_bytes()).hexdigest(),
            "definitionPath": str(definition_path) if definition_path is not None else None,
        },
        "dictionary": dictionary,
        "cases": cases,
    }


def write_markdown(report: dict[str, object], path: Path) -> None:
    gates = report["gates"]
    cases = report["cases"]
    dictionary = report["dictionary"]
    lines = [
        "# 供电优化实验最小可信基线 V1",
        "",
        f"- 总体结论：{'通过' if report['passed'] else '未通过'}",
        f"- 数据质量：`{report['quality']}`",
        f"- 点表 SHA-256：`{dictionary.get('sha256', 'N/A')}`",
        "",
        "## 准入门",
        "",
        "| 准入项 | 结果 |",
        "|---|:---:|",
    ]
    for name, passed in gates.items():
        lines.append(f"| `{name}` | {'通过' if passed else '失败'} |")
    lines.extend(["", "## 基准工况", ""])
    for name, case in cases.items():
        lines.extend([
            f"### {name}",
            "",
            f"结论：{'通过' if case['passed'] else '失败'}。",
            "",
            "```json",
            json.dumps(case, ensure_ascii=False, indent=2),
            "```",
            "",
        ])
    lines.extend([
        "## 使用边界",
        "",
        "老师点表仅用于字段、单位、正负号、索引及 `P=UI` 关系核对，不作为 DC750V 参数标定真值。",
        "优化实验必须固定本报告中的拓扑哈希、场景、步长和阈值；任一准入门失败时不得发布优化结论。",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the minimum credibility gate for power optimization experiments")
    parser.add_argument("--definition", type=Path, default=DEFAULT_DEFINITION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = build_report(args.definition)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path = args.output.with_suffix(".md")
    write_markdown(report, markdown_path)
    print(json.dumps({
        "passed": report["passed"],
        "output": str(args.output),
        "markdown": str(markdown_path),
        "gates": report["gates"],
    }, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
