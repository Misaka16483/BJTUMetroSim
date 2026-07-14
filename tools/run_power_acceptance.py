from __future__ import annotations

import argparse
import json
import math
import sys
import time
from contextlib import closing
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.engine import SimulationEngine
from app.domain.power.flow_solver import DCTractionPowerFlowSolver
from app.domain.power.line9_topology import load_line9_power_network
from app.domain.power.network_models import TrainElectricalLoad
from app.infra.recorder import RunRecorder


SCENARIO = ROOT / "data" / "scenarios" / "line9_5train_power.json"
TOPOLOGY = ROOT / "data" / "scenarios" / "line9_power_topology.json"
LINE_MAP = ROOT / "data" / "cache" / "line_map.json"
STATIONS = ROOT / "data" / "line9" / "stations.csv"


def percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * ratio) - 1)]


def build_engine(scenario_path: Path = SCENARIO, recorder: RunRecorder | None = None) -> SimulationEngine:
    engine = SimulationEngine.load_from_files(
        scenario_path=scenario_path,
        line_map_path=LINE_MAP,
        stations_csv_path=STATIONS,
        recorder=recorder,
    )
    engine.load()
    engine.clock.start()
    return engine


def advance_until_any_train_moves(
    engine: SimulationEngine,
    *,
    min_speed_mps: float = 1.0,
    max_ticks: int = 300,
) -> int:
    """Wait through dwell/interlocking startup before comparing loaded power states."""
    for tick in range(1, max_ticks + 1):
        engine._tick()
        if any(item.speed_mps >= min_speed_mps for item in engine.trains):
            return tick
    raise RuntimeError("no train reached motion state before power acceptance timeout")


def five_train_loads(force_n: float = 100_000.0) -> list[TrainElectricalLoad]:
    return [
        TrainElectricalLoad(f"T{i}", "UP", 2200.0 + i * 400.0, 15.0, traction_force_n=force_n, aux_power_kw=100.0)
        for i in range(5)
    ]


def fleet_loads(count: int, time_sec: int = 0) -> list[TrainElectricalLoad]:
    """Create a deterministic line-wide traction/regen load pattern."""
    first_m, last_m = 313.0, 16_048.92
    usable_m = last_m - first_m
    loads: list[TrainElectricalLoad] = []
    for index in range(count):
        direction = "UP" if index % 2 == 0 else "DOWN"
        phase = (time_sec // 20 + index) % 5
        base = first_m + usable_m * ((index + 0.5) / count)
        drift = ((time_sec * (7.0 + index % 4)) % usable_m)
        mileage_m = first_m + ((base - first_m + drift) % usable_m)
        if phase == 3:
            traction_force_n = 0.0
            brake_force_n = 90_000.0
        elif phase == 4:
            traction_force_n = 0.0
            brake_force_n = 0.0
        else:
            traction_force_n = 70_000.0 + (index % 4) * 8_000.0
            brake_force_n = 0.0
        loads.append(TrainElectricalLoad(
            train_id=f"F{index + 1:03d}",
            direction=direction,
            mileage_m=mileage_m,
            speed_mps=12.0 + index % 5,
            traction_force_n=traction_force_n,
            brake_force_n=brake_force_n,
            aux_power_kw=80.0,
        ))
    return loads


def analytical_reference_check() -> dict:
    """Compare one-source constant-power flow against its closed-form solution."""
    network = load_line9_power_network(TOPOLOGY)
    target_feeder = "FD-0901-UP-RIGHT"
    for feeder_id in list(network.feeders):
        if feeder_id != target_feeder:
            network.set_feeder_status(feeder_id, "OPEN")
    load = TrainElectricalLoad(
        "REFERENCE", "UP", 700.0, 15.0,
        traction_force_n=30_000.0, aux_power_kw=40.0,
    )
    solver = DCTractionPowerFlowSolver(network)
    source_paths = solver._load_source_paths(load)
    if len(source_paths) != 1:
        raise RuntimeError(f"analytical reference expected one source, got {source_paths}")
    substation_id, _feeder_id, path_resistance = source_paths[0]
    substation = network.substations[substation_id]
    total_resistance = path_resistance + substation.internal_resistance_ohm
    requested_w = load.traction_demand_kw * 1000.0
    discriminant = substation.no_load_voltage_v ** 2 - 4.0 * total_resistance * requested_w
    expected_voltage_v = (
        substation.no_load_voltage_v + math.sqrt(max(discriminant, 0.0))
    ) / 2.0
    expected_current_a = requested_w / expected_voltage_v
    snapshot = solver.solve([load], dt_sec=1.0)
    actual = snapshot.trains[0]
    return {
        "reference": "closed-form one-source constant-power DC circuit",
        "sourceSubstationId": substation_id,
        "totalResistanceOhm": total_resistance,
        "requestedPowerKw": load.traction_demand_kw,
        "expectedVoltageV": expected_voltage_v,
        "actualVoltageV": actual.voltage_v,
        "voltageAbsErrorV": abs(actual.voltage_v - expected_voltage_v),
        "expectedCurrentA": expected_current_a,
        "actualCurrentA": actual.current_a,
        "currentRelativeError": abs(actual.current_a - expected_current_a) / expected_current_a,
    }


def sensitivity_check() -> dict:
    results: list[dict] = []
    loads = fleet_loads(12, time_sec=37)
    for factor in (0.8, 1.0, 1.2):
        network = load_line9_power_network(TOPOLOGY)
        network.substations = {
            key: replace(item, internal_resistance_ohm=item.internal_resistance_ohm * factor)
            for key, item in network.substations.items()
        }
        network.feeders = {
            key: replace(item, cable_resistance_ohm=item.cable_resistance_ohm * factor)
            for key, item in network.feeders.items()
        }
        network.contact_sections = {
            key: replace(item, resistance_ohm_per_km=item.resistance_ohm_per_km * factor)
            for key, item in network.contact_sections.items()
        }
        network.return_sections = {
            key: replace(item, resistance_ohm_per_km=item.resistance_ohm_per_km * factor)
            for key, item in network.return_sections.items()
        }
        snapshot = DCTractionPowerFlowSolver(network).solve(loads, dt_sec=1.0)
        results.append({
            "resistanceFactor": factor,
            "minVoltageV": min(item.voltage_v for item in snapshot.trains),
            "lossesKw": snapshot.losses_kw,
            "balanceErrorRatio": snapshot.power_balance_error_ratio,
            "converged": snapshot.converged,
        })
    return {
        "cases": results,
        "voltageMonotonic": all(
            results[index]["minVoltageV"] > results[index + 1]["minVoltageV"]
            for index in range(len(results) - 1)
        ),
        "lossMonotonic": all(
            results[index]["lossesKw"] < results[index + 1]["lossesKw"]
            for index in range(len(results) - 1)
        ),
    }


def fleet_scalability_check() -> dict:
    cases: list[dict] = []
    for count in (10, 20, 40):
        durations_ms: list[float] = []
        snapshots = []
        for sample in range(20):
            solver = DCTractionPowerFlowSolver(load_line9_power_network(TOPOLOGY))
            started = time.perf_counter()
            snapshot = solver.solve(fleet_loads(count, sample * 7), dt_sec=1.0)
            durations_ms.append((time.perf_counter() - started) * 1000.0)
            snapshots.append(snapshot)
        cases.append({
            "trainCount": count,
            "p95Ms": percentile(durations_ms, 0.95),
            "maxMs": max(durations_ms),
            "allConverged": all(item.converged for item in snapshots),
            "maxBalanceErrorRatio": max(item.power_balance_error_ratio for item in snapshots),
            "minVoltageV": min(train.voltage_v for item in snapshots for train in item.trains),
        })
    return {"cases": cases}


def one_hour_continuous_check() -> dict:
    network = load_line9_power_network(TOPOLOGY)
    solver = DCTractionPowerFlowSolver(network)
    durations_ms: list[float] = []
    max_balance_error_ratio = 0.0
    min_voltage_v = float("inf")
    failed_steps = 0
    first_energy_kwh = None
    last_energy_kwh = 0.0
    for second in range(3600):
        started = time.perf_counter()
        snapshot = solver.solve(fleet_loads(20, second), dt_sec=1.0, sim_time_ms=second * 1000)
        durations_ms.append((time.perf_counter() - started) * 1000.0)
        if not snapshot.converged or snapshot.power_balance_error_ratio >= 0.01:
            failed_steps += 1
        max_balance_error_ratio = max(max_balance_error_ratio, snapshot.power_balance_error_ratio)
        min_voltage_v = min(min_voltage_v, *(item.voltage_v for item in snapshot.trains))
        energy_kwh = sum(item.energy_kwh for item in snapshot.substations)
        if first_energy_kwh is None:
            first_energy_kwh = energy_kwh
        last_energy_kwh = energy_kwh
    return {
        "simulatedDurationSec": 3600,
        "timeStepSec": 1,
        "trainCount": 20,
        "wallTimeSec": sum(durations_ms) / 1000.0,
        "solveP95Ms": percentile(durations_ms, 0.95),
        "solveMaxMs": max(durations_ms),
        "failedSteps": failed_steps,
        "maxBalanceErrorRatio": max_balance_error_ratio,
        "minVoltageV": min_voltage_v,
        "energyIncreaseKwh": last_energy_kwh - (first_energy_kwh or 0.0),
    }


def engine_benchmark() -> dict:
    engine = build_engine()
    durations_ms: list[float] = []
    for _tick in range(240):
        started = time.perf_counter()
        engine._tick()
        durations_ms.append((time.perf_counter() - started) * 1000.0)
    snapshot = engine.snapshot()
    assert snapshot is not None
    return {
        "trainCount": len(snapshot.trains),
        "tickSeconds": engine.clock.tick_seconds,
        "tickP95Ms": percentile(durations_ms[20:], 0.95),
        "tickMaxMs": max(durations_ms[20:]),
        "minVoltageV": min(item["pantographVoltageV"] for item in snapshot.trains),
        "minTractionLimitRatio": min(item["tractionLimitRatio"] for item in snapshot.trains),
        "allSubstationsNormal": all(item["status"] == "NORMAL" for item in snapshot.power_network["substations"]),
    }


def solver_benchmark() -> dict:
    durations_ms: list[float] = []
    snapshots = []
    for _ in range(30):
        solver = DCTractionPowerFlowSolver(load_line9_power_network(TOPOLOGY))
        started = time.perf_counter()
        snapshots.append(solver.solve(five_train_loads(), dt_sec=0.25))
        durations_ms.append((time.perf_counter() - started) * 1000.0)
    return {
        "p95Ms": percentile(durations_ms, 0.95),
        "maxBalanceErrorRatio": max(item.power_balance_error_ratio for item in snapshots),
        "allConverged": all(item.converged for item in snapshots),
        "maxIterations": max(item.iterations for item in snapshots),
    }


def deterministic_check() -> dict:
    values = []
    for _ in range(2):
        snapshot = DCTractionPowerFlowSolver(load_line9_power_network(TOPOLOGY)).solve(five_train_loads(), dt_sec=0.25)
        values.append([round(item.voltage_v, 9) for item in snapshot.trains])
    return {"repeatable": values[0] == values[1], "voltagesV": values[0]}


def switch_check() -> dict:
    network = load_line9_power_network(TOPOLOGY)
    loads = [TrainElectricalLoad("T1", "UP", 2400.0, 18.0, traction_force_n=130_000.0, aux_power_kw=100.0)]
    before = DCTractionPowerFlowSolver(network).solve(loads, dt_sec=0.25)
    network.operate_switch("SW-TIE-0902", "CLOSED")
    after = DCTractionPowerFlowSolver(network).solve(loads, dt_sec=0.25)
    before_sources = sorted(item.substation_id for item in before.substations if item.current_a > 1.0)
    after_sources = sorted(item.substation_id for item in after.substations if item.current_a > 1.0)
    return {
        "switchId": "SW-TIE-0902",
        "beforeVoltageV": before.trains[0].voltage_v,
        "afterVoltageV": after.trains[0].voltage_v,
        "beforeSources": before_sources,
        "afterSources": after_sources,
        "pathChanged": before_sources != after_sources,
    }


def n_minus_one_check() -> dict:
    loads = [
        TrainElectricalLoad(f"T{i}", "UP", 6100.0 + i * 180.0, 18.0, traction_force_n=170_000.0, aux_power_kw=100.0)
        for i in range(3)
    ]
    normal_network = load_line9_power_network(TOPOLOGY)
    baseline = DCTractionPowerFlowSolver(normal_network).solve(loads, dt_sec=0.25)
    normal_network.apply_substation_outage("TS-0905", big_bilateral=True)
    outage = DCTractionPowerFlowSolver(normal_network).solve(loads, dt_sec=0.25)
    restored = DCTractionPowerFlowSolver(load_line9_power_network(TOPOLOGY)).solve(loads, dt_sec=0.25)
    outage_limit = min(item.traction_limit_ratio for item in outage.trains)

    engine = build_engine()
    network = engine.power_service.network
    assert network is not None
    network.substations = {
        key: replace(value, no_load_voltage_v=740.0, internal_resistance_ohm=0.025)
        for key, value in network.substations.items()
    }
    for _tick in range(math.ceil(45.0 / engine.clock.tick_seconds)):
        engine._tick()
    delay_before = sum(item.power_constraint_delay_sec for item in engine.trains)
    engine.queue_power_command("SUBSTATION_OUTAGE", {"targetId": "TS-0905", "bigBilateral": True})
    min_outage_limit = 1.0
    for _tick in range(math.ceil(20.0 / engine.clock.tick_seconds)):
        engine._tick()
        current = engine.snapshot()
        assert current is not None
        min_outage_limit = min(min_outage_limit, current.kpi["minTractionLimitRatio"])
    delay_outage = sum(item.power_constraint_delay_sec for item in engine.trains)
    engine.queue_power_command("RESET_NETWORK", {})
    for _tick in range(math.ceil(10.0 / engine.clock.tick_seconds)):
        engine._tick()
    delay_recovered = sum(item.power_constraint_delay_sec for item in engine.trains)
    recovered_snapshot = engine.snapshot()
    assert recovered_snapshot is not None
    return {
        "baselineMinVoltageV": min(item.voltage_v for item in baseline.trains),
        "outageMinVoltageV": min(item.voltage_v for item in outage.trains),
        "outageMinTractionLimitRatio": outage_limit,
        "projectedDelayRate": 1.0 / outage_limit - 1.0,
        "restoredMinVoltageV": min(item.voltage_v for item in restored.trains),
        "delayBeforeSec": delay_before,
        "delayDuringOutageSec": delay_outage,
        "delayAfterRecoverySec": delay_recovered,
        "recoveredTractionLimitRatio": recovered_snapshot.kpi["minTractionLimitRatio"],
        "engineOutageMinTractionLimitRatio": min_outage_limit,
    }


def regen_check() -> dict:
    network = load_line9_power_network(TOPOLOGY)
    network.operate_switch("SW-TIE-0902", "CLOSED")
    snapshot = DCTractionPowerFlowSolver(network).solve(
        [
            TrainElectricalLoad("TRACTION", "UP", 2200.0, 18.0, traction_force_n=95_000.0, aux_power_kw=60.0),
            TrainElectricalLoad("BRAKING-1", "UP", 2600.0, 22.0, brake_force_n=180_000.0, aux_power_kw=60.0),
            TrainElectricalLoad("BRAKING-2", "UP", 3000.0, 22.0, brake_force_n=180_000.0, aux_power_kw=60.0),
            TrainElectricalLoad("BRAKING-3", "UP", 3400.0, 22.0, brake_force_n=180_000.0, aux_power_kw=60.0),
        ],
        dt_sec=0.25,
    )
    split = (
        snapshot.self_consumed_regen_kw
        + snapshot.absorbed_regen_kw
        + sum(item.charge_power_kw for item in snapshot.supercapacitor_flows)
        + snapshot.feedback_regen_kw
        + snapshot.wasted_regen_kw
        + snapshot.regen_transfer_losses_kw
    )
    return {
        "generatedKw": snapshot.generated_regen_kw,
        "selfConsumedKw": snapshot.self_consumed_regen_kw,
        "absorbedKw": snapshot.absorbed_regen_kw,
        "storageChargedKw": sum(item.charge_power_kw for item in snapshot.supercapacitor_flows),
        "feedbackKw": snapshot.feedback_regen_kw,
        "wastedKw": snapshot.wasted_regen_kw,
        "transferLossesKw": snapshot.regen_transfer_losses_kw,
        "pathCount": len(snapshot.regen_paths),
        "conservationErrorKw": abs(snapshot.generated_regen_kw - split),
    }


def passenger_check(output_dir: Path) -> dict:
    base = json.loads(SCENARIO.read_text(encoding="utf-8"))
    results = []
    try:
        for passengers in (0, 600):
            scenario = json.loads(json.dumps(base))
            for train in scenario["trains"]:
                train["initialLoadPax"] = passengers
            path = output_dir / f"acceptance-passengers-{passengers}.json"
            path.write_text(json.dumps(scenario, ensure_ascii=False), encoding="utf-8")
            engine = build_engine(path)
            peak_power_kw = 0.0
            min_voltage_v = 1000.0
            reach_tick = None
            max_ticks = math.ceil(180.0 / engine.clock.tick_seconds)
            for tick in range(max_ticks):
                engine._tick()
                snapshot = engine.snapshot()
                assert snapshot is not None
                peak_power_kw = max(peak_power_kw, sum(max(item["requestedPowerKw"], 0.0) for item in snapshot.trains))
                min_voltage_v = min(min_voltage_v, snapshot.kpi["minTrainVoltageV"])
                if snapshot.trains[0]["pathPositionM"] >= 1000.0:
                    reach_tick = tick + 1
                    break
            snapshot = engine.snapshot()
            assert snapshot is not None and reach_tick is not None
            result = {
                "passengersPerTrain": passengers,
                "totalMassKg": sum(item["massKg"] for item in snapshot.trains),
                "peakPowerKw": peak_power_kw,
                "minVoltageV": min_voltage_v,
                "energyKwh": sum(item["energyKwh"] for item in snapshot.trains),
                "timeTo1000mSec": reach_tick * engine.clock.tick_seconds,
            }

            stressed = build_engine(path)
            stressed_network = stressed.power_service.network
            assert stressed_network is not None
            stressed_network.substations = {
                key: replace(value, no_load_voltage_v=740.0, internal_resistance_ohm=0.025)
                for key, value in stressed_network.substations.items()
            }
            advance_until_any_train_moves(stressed)
            stressed_min_limit = 1.0
            for _tick in range(200):
                stressed._tick()
                stressed_snapshot = stressed.snapshot()
                assert stressed_snapshot is not None
                stressed_min_limit = min(stressed_min_limit, stressed_snapshot.kpi["minTractionLimitRatio"])
            result["stressedMinTractionLimitRatio"] = stressed_min_limit
            result["stressedPowerDelaySec"] = sum(item.power_constraint_delay_sec for item in stressed.trains)
            results.append(result)
    finally:
        for passengers in (0, 600):
            (output_dir / f"acceptance-passengers-{passengers}.json").unlink(missing_ok=True)
    return {"low": results[0], "high": results[1]}


def recording_check(output_dir: Path) -> dict:
    db_path = output_dir / "power-acceptance.sqlite"
    export_path = output_dir / "power-acceptance-run.json"
    db_path.unlink(missing_ok=True)
    recorder = RunRecorder(db_path)
    try:
        engine = build_engine(recorder=recorder)
        engine.queue_power_command("OPERATE_SWITCH", {"switchId": "SW-TIE-0902", "state": "CLOSED"})
        for _tick in range(12):
            engine._tick()
        run_id = engine._run_id
        assert run_id is not None
        recorder.export_run_json(run_id, export_path)
        replay_count = len(recorder.replay_events(run_id, "train.state"))
        replay_commands = recorder.replay_power_commands(run_id)
        export = recorder.export_run(run_id)
        counts = {key: len(value) for key, value in export["tables"].items()}
    finally:
        recorder.close()
        db_path.unlink(missing_ok=True)
    return {
        "exportPath": str(export_path),
        "replayTrainStateCount": replay_count,
        "replayPowerCommandCount": len(replay_commands),
        "replayPowerCommandRequest": replay_commands[0]["requestPayload"] if replay_commands else {},
        "tableCounts": counts,
    }


def _legacy_write_markdown_report(report: dict, path: Path) -> None:
    criteria_rows = "\n".join(
        f"| `{name}` | {'通过' if passed else '失败'} |"
        for name, passed in report["criteria"].items()
    )
    fleet_rows = "\n".join(
        f"| {item['trainCount']} | {item['p95Ms']:.3f} | {item['maxBalanceErrorRatio'] * 100:.5f}% | "
        f"{'是' if item['allConverged'] else '否'} | {item['minVoltageV']:.2f} |"
        for item in report["fleetScalability"]["cases"]
    )
    sensitivity_rows = "\n".join(
        f"| {item['resistanceFactor']:.1f} | {item['minVoltageV']:.2f} | {item['lossesKw']:.3f} |"
        for item in report["sensitivity"]["cases"]
    )
    reference = report["analyticalReference"]
    hour = report["oneHour"]
    n1 = report["nMinusOne"]
    regen = report["regen"]
    recording = report["recording"]
    content = f"""# 9号线牵引供电仿真自动验收报告

生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}
总体结论：**{'通过' if report['passed'] else '未通过'}**

## 验收判据

| 判据 | 结果 |
|---|:---:|
{criteria_rows}

## 解析参考校核

采用单电源恒功率直流电路闭式解作为独立参考。期望电压 {reference['expectedVoltageV']:.4f} V，
仿真电压 {reference['actualVoltageV']:.4f} V，绝对误差 {reference['voltageAbsErrorV']:.4f} V；
电流相对误差 {reference['currentRelativeError'] * 100:.5f}%。

## 参数敏感性

| 全网电阻倍率 | 最低列车电压/V | 线路损耗/kW |
|---:|---:|---:|
{sensitivity_rows}

## 多车规模

| 列车数 | 求解P95/ms | 最大平衡误差 | 全部收敛 | 最低电压/V |
|---:|---:|---:|:---:|---:|
{fleet_rows}

## 一小时连续仿真

- 20列车、1秒步长、3600步；墙钟耗时 {hour['wallTimeSec']:.2f} s。
- 求解P95 {hour['solveP95Ms']:.3f} ms，最大 {hour['solveMaxMs']:.3f} ms。
- 失败步数 {hour['failedSteps']}，最大功率平衡误差 {hour['maxBalanceErrorRatio'] * 100:.5f}%。
- 最低列车电压 {hour['minVoltageV']:.2f} V，累计整流电量增加 {hour['energyIncreaseKwh']:.3f} kWh。

## N-1与再生能量闭环

- `TS-0905`停运前最低电压 {n1['baselineMinVoltageV']:.2f} V，停运后 {n1['outageMinVoltageV']:.2f} V，
  最低限牵系数 {n1['outageMinTractionLimitRatio']:.4f}；复归后最低电压 {n1['restoredMinVoltageV']:.2f} V、限牵系数 {n1['recoveredTractionLimitRatio']:.1f}。
- 再生生成 {regen['generatedKw']:.3f} kW = 列车吸收 {regen['absorbedKw']:.3f} kW + 回馈 {regen['feedbackKw']:.3f} kW
  + 路径损耗 {regen['transferLossesKw']:.3f} kW + 浪费 {regen['wastedKw']:.3f} kW；守恒误差 {regen['conservationErrorKw']:.9f} kW。
- 共形成 {regen['pathCount']} 条可追踪再生路径。

## 记录与回放

- 回放列车状态 {recording['replayTrainStateCount']} 条，回放供电命令 {recording['replayPowerCommandCount']} 条。
- 命令原始请求已保存：`{json.dumps(recording['replayPowerCommandRequest'], ensure_ascii=False)}`。
- SQLite包含 {recording['tableCounts']['power_solver_records']} 条潮流求解记录、{recording['tableCounts']['power_command_records']} 条供电命令记录。

## 边界说明

本报告验证的是 `ENGINEERING_ESTIMATE` 教学仿真模型的数值正确性、稳定性和软件契约，
不等价于真实运营线路的保护整定或工程投运认证。真实设备参数仍需以运营方图纸、PSCADA或实测数据替换。
"""
    path.write_text(content, encoding="utf-8")


def _legacy_write_traceability_matrix(path: Path) -> None:
    content = """# 供电仿真需求追溯矩阵

| 需求编号 | 需求/验收目标 | 设计与实现 | 自动验证 | 证据输出 |
|---|---|---|---|---|
| PWR-R01 | 显式拓扑，不静默生成 | `line9_power_topology.json`、`line9_topology.py` | `test_power_network_models.py` | 拓扑V1参数与溯源文档 |
| PWR-R02 | 参数有来源和质量 | 网络设备 `sourceId/quality/parameterSources` | 拓扑严格校验测试 | API拓扑响应、SQLite静态表 |
| PWR-R03 | 多车有符号DC潮流 | `flow_solver.py` | 10/20/40车矩阵 | `fleetScalability` |
| PWR-R04 | 功率平衡残差小于1% | 求解器平衡核算 | 全场景与一小时测试 | `powerBalanceErrorRatio` |
| PWR-R05 | 再生能量逐路径守恒 | `RegenPathFlow`、路径分配 | 再生守恒测试 | `regen_path_records` |
| PWR-R06 | 车头车尾和多受电弓跨段 | 车辆几何及多取流点 | `test_power_collection_geometry.py` | API列车受电字段 |
| PWR-R07 | 故障、保护、复归可解释 | 同周期跳闸重算、孤岛告警 | 供电服务与引擎故障测试 | 命令结果、保护事件 |
| PWR-R08 | 不收敛不得发布坏结果 | 最后有效快照及自动暂停 | 求解失败注入测试 | `solverFailure` |
| PWR-R09 | 命令可记录和回放 | 延时命令队列、SQLite命令记录 | 命令回放测试 | `power_command_records` |
| PWR-R10 | 有量纲有符号可视化 | 供电专用页面 | TypeScript构建、浏览器检查 | kW/A/V曲线与表格 |
| PWR-R11 | P95不超过50ms | 求解性能预算 | 10/20/40车及一小时矩阵 | 自动验收报告 |
| PWR-R12 | 同输入结果可重复 | 确定性排序与固定场景 | 重复运行对比 | `determinism` |
| PWR-R13 | 支持批量实验与优化 | `power_experiments.py` | 优化接口测试 | 实验JSON/SQLite |
"""
    path.write_text(content, encoding="utf-8")


def write_markdown_report(report: dict, path: Path) -> None:
    status = "通过" if report["passed"] else "未通过"
    criteria_rows = "\n".join(
        f"| `{name}` | {'通过' if passed else '失败'} |"
        for name, passed in report["criteria"].items()
    )
    fleet_rows = "\n".join(
        f"| {item['trainCount']} | {item['p95Ms']:.3f} | {item['maxMs']:.3f} | "
        f"{item['maxBalanceErrorRatio'] * 100:.5f}% | {'是' if item['allConverged'] else '否'} |"
        for item in report["fleetScalability"]["cases"]
    )
    reference = report["analyticalReference"]
    hour = report["oneHour"]
    regen = report["regen"]
    content = f"""# 9号线牵引供电仿真自动验收报告

生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}
总体结论：**{status}**

## 验收判据

| 判据 | 结果 |
|---|:---:|
{criteria_rows}

## 解析基准

- 参考模型：单电源恒功率直流电路闭式解。
- 电压绝对误差：{reference['voltageAbsErrorV']:.6f} V（阈值 0.5 V）。
- 电流相对误差：{reference['currentRelativeError'] * 100:.6f}%（阈值 0.1%）。

## 多车性能

| 列车数 | 求解 P95/ms | 最大值/ms | 最大平衡误差 | 全部收敛 |
|---:|---:|---:|---:|:---:|
{fleet_rows}

## 再生能量守恒

生成 {regen['generatedKw']:.3f} kW；本车自用 {regen['selfConsumedKw']:.3f} kW；跨车吸收 {regen['absorbedKw']:.3f} kW；
储能充电 {regen['storageChargedKw']:.3f} kW；交流回馈 {regen['feedbackKw']:.3f} kW；线路损耗 {regen['transferLossesKw']:.3f} kW；浪费 {regen['wastedKw']:.3f} kW。
守恒误差：{regen['conservationErrorKw']:.9f} kW。

## 长时稳定性

- 20 列车、1 秒步长、3600 步；失败步数 {hour['failedSteps']}。
- 求解 P95 {hour['solveP95Ms']:.3f} ms，最大 {hour['solveMaxMs']:.3f} ms。
- 最大功率平衡误差 {hour['maxBalanceErrorRatio'] * 100:.6f}%。
- 最低列车电压 {hour['minVoltageV']:.2f} V。

## 适用边界

本验收证明 `ENGINEERING_ESTIMATE` 教学仿真模型的软件正确性、数值稳定性和契约一致性，
不等同于真实线路工程投运认证。真实设备参数仍需用运营方图纸、SCADA 或实测数据校准。
"""
    path.write_text(content, encoding="utf-8")


def write_traceability_matrix(path: Path) -> None:
    content = """# 供电仿真需求追溯矩阵

| 编号 | 验收目标 | 实现位置 | 自动验证 |
|---|---|---|---|
| PWR-R01 | 显式拓扑和参数来源 | `line9_power_topology.json`、`line9_topology.py` | `test_power_network_models.py`、`test_power_validation_properties.py` |
| PWR-R02 | 多车有符号 DC 潮流与功率平衡 | `flow_solver.py` | `test_power_flow_solver.py`、随机多车性质测试 |
| PWR-R03 | 再生能量逐路径守恒 | `RegenPathFlow`、再生分配器 | `test_power_flow_solver.py`、`test_supercapacitor_storage.py` |
| PWR-R04 | 超级电容 SOC、效率和功率边界 | 储能状态账本 | `test_supercapacitor_storage.py`、步长不变性测试 |
| PWR-R05 | 故障、联络供电与无漂移恢复 | `network.py`、供电命令队列 | 网络和引擎故障恢复测试 |
| PWR-R06 | 求解失败不发布坏结果 | 最后有效快照和自动暂停 | `test_engine_power_network_loop.py` |
| PWR-R07 | 数据可记录、导出和回放 | `recorder.py` | 引擎记录闭环与自动验收脚本 |
| PWR-R08 | 10/20/40 车性能和一小时稳定性 | 验收运行器 | `run_power_acceptance.py` |
| PWR-R09 | 前端量纲、符号和历史连续性 | `PowerSystemView.tsx` | TypeScript 构建与浏览器验收 |
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run quantitative Line 9 traction-power acceptance checks")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs" / "power-acceptance-report.json")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    report = {
        "analyticalReference": analytical_reference_check(),
        "sensitivity": sensitivity_check(),
        "fleetScalability": fleet_scalability_check(),
        "engine": engine_benchmark(),
        "solver": solver_benchmark(),
        "determinism": deterministic_check(),
        "switch": switch_check(),
        "nMinusOne": n_minus_one_check(),
        "regen": regen_check(),
        "passenger": passenger_check(args.output.parent),
        "recording": recording_check(args.output.parent),
        "oneHour": one_hour_continuous_check(),
    }
    report["criteria"] = {
        "fiveTrain250msStable": report["engine"]["trainCount"] == 5 and report["engine"]["tickP95Ms"] < 250.0 and report["engine"]["allSubstationsNormal"],
        "solverP95Under50ms": report["solver"]["p95Ms"] < 50.0,
        "balanceErrorUnder1Percent": report["solver"]["maxBalanceErrorRatio"] < 0.01,
        "deterministic": report["determinism"]["repeatable"],
        "switchChangesPath": report["switch"]["pathChanged"],
        "nMinusOneExplainable": report["nMinusOne"]["outageMinTractionLimitRatio"] < 1.0 and report["nMinusOne"]["engineOutageMinTractionLimitRatio"] < 1.0 and report["nMinusOne"]["delayDuringOutageSec"] > report["nMinusOne"]["delayBeforeSec"] and report["nMinusOne"]["delayAfterRecoverySec"] == report["nMinusOne"]["delayDuringOutageSec"],
        "regenConservative": (
            report["regen"]["conservationErrorKw"] < 1e-9
            and report["regen"]["generatedKw"] > 0.0
            and report["regen"]["absorbedKw"] + report["regen"]["storageChargedKw"] + report["regen"]["feedbackKw"] > 0.0
            and report["regen"]["transferLossesKw"] > 0.0
            and report["regen"]["pathCount"] >= 3
        ),
        "passengerChain": report["passenger"]["high"]["totalMassKg"] > report["passenger"]["low"]["totalMassKg"] and report["passenger"]["high"]["energyKwh"] > report["passenger"]["low"]["energyKwh"] and report["passenger"]["high"]["timeTo1000mSec"] > report["passenger"]["low"]["timeTo1000mSec"] and report["passenger"]["low"]["stressedMinTractionLimitRatio"] < 1.0 and report["passenger"]["high"]["stressedMinTractionLimitRatio"] < 1.0 and round(report["passenger"]["low"]["stressedMinTractionLimitRatio"], 4) != round(report["passenger"]["high"]["stressedMinTractionLimitRatio"], 4),
        "recordReplayExport": report["recording"]["replayTrainStateCount"] > 0 and report["recording"]["tableCounts"]["power_solver_records"] > 0 and report["recording"]["tableCounts"]["power_command_records"] > 0,
        "analyticalReference": (
            report["analyticalReference"]["voltageAbsErrorV"] < 0.5
            and report["analyticalReference"]["currentRelativeError"] < 0.001
        ),
        "sensitivityMonotonic": (
            report["sensitivity"]["voltageMonotonic"]
            and report["sensitivity"]["lossMonotonic"]
        ),
        "fleet10To40": all(
            item["p95Ms"] < 50.0
            and item["maxBalanceErrorRatio"] < 0.01
            and item["allConverged"]
            for item in report["fleetScalability"]["cases"]
        ),
        "oneHourContinuous": (
            report["oneHour"]["failedSteps"] == 0
            and report["oneHour"]["solveP95Ms"] < 50.0
            and report["oneHour"]["maxBalanceErrorRatio"] < 0.01
            and report["oneHour"]["energyIncreaseKwh"] > 0.0
        ),
    }
    report["passed"] = all(report["criteria"].values())
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown_report(report, args.output.with_suffix(".md"))
    write_traceability_matrix(ROOT / "docs" / "测试与验收" / "供电仿真需求追溯矩阵.md")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
