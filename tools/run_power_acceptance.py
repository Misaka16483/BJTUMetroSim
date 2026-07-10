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
STATIONS = ROOT / "MetroDynamicsJavaDemo" / "data" / "stations.csv"


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


def five_train_loads(force_n: float = 100_000.0) -> list[TrainElectricalLoad]:
    return [
        TrainElectricalLoad(f"T{i}", "UP", 2200.0 + i * 400.0, 15.0, traction_force_n=force_n, aux_power_kw=100.0)
        for i in range(5)
    ]


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
    for _tick in range(40):
        engine._tick()
    delay_before = sum(item.power_constraint_delay_sec for item in engine.trains)
    engine.queue_power_command("SUBSTATION_OUTAGE", {"targetId": "TS-0905", "bigBilateral": True})
    for _tick in range(40):
        engine._tick()
    delay_outage = sum(item.power_constraint_delay_sec for item in engine.trains)
    engine.queue_power_command("RESET_NETWORK", {})
    for _tick in range(20):
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
    }


def regen_check() -> dict:
    snapshot = DCTractionPowerFlowSolver(load_line9_power_network(TOPOLOGY)).solve(
        [
            TrainElectricalLoad("TRACTION", "UP", 2200.0, 18.0, traction_force_n=95_000.0, aux_power_kw=60.0),
            TrainElectricalLoad("BRAKING-1", "UP", 2600.0, 22.0, brake_force_n=180_000.0, aux_power_kw=60.0),
            TrainElectricalLoad("BRAKING-2", "UP", 3000.0, 22.0, brake_force_n=180_000.0, aux_power_kw=60.0),
            TrainElectricalLoad("BRAKING-3", "UP", 3400.0, 22.0, brake_force_n=180_000.0, aux_power_kw=60.0),
        ],
        dt_sec=0.25,
    )
    split = snapshot.absorbed_regen_kw + snapshot.feedback_regen_kw + snapshot.wasted_regen_kw
    return {
        "generatedKw": snapshot.generated_regen_kw,
        "absorbedKw": snapshot.absorbed_regen_kw,
        "feedbackKw": snapshot.feedback_regen_kw,
        "wastedKw": snapshot.wasted_regen_kw,
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
            for tick in range(360):
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
                "timeTo1000mSec": reach_tick * 0.25,
            }

            stressed = build_engine(path)
            stressed_network = stressed.power_service.network
            assert stressed_network is not None
            stressed_network.substations = {
                key: replace(value, no_load_voltage_v=740.0, internal_resistance_ohm=0.025)
                for key, value in stressed_network.substations.items()
            }
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
        export = recorder.export_run(run_id)
        counts = {key: len(value) for key, value in export["tables"].items()}
    finally:
        recorder.close()
        db_path.unlink(missing_ok=True)
    return {"exportPath": str(export_path), "replayTrainStateCount": replay_count, "tableCounts": counts}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run quantitative Line 9 traction-power acceptance checks")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs" / "power-acceptance-report.json")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    report = {
        "engine": engine_benchmark(),
        "solver": solver_benchmark(),
        "determinism": deterministic_check(),
        "switch": switch_check(),
        "nMinusOne": n_minus_one_check(),
        "regen": regen_check(),
        "passenger": passenger_check(args.output.parent),
        "recording": recording_check(args.output.parent),
    }
    report["criteria"] = {
        "fiveTrain250msStable": report["engine"]["trainCount"] == 5 and report["engine"]["tickP95Ms"] < 250.0 and report["engine"]["allSubstationsNormal"],
        "solverP95Under50ms": report["solver"]["p95Ms"] < 50.0,
        "balanceErrorUnder1Percent": report["solver"]["maxBalanceErrorRatio"] < 0.01,
        "deterministic": report["determinism"]["repeatable"],
        "switchChangesPath": report["switch"]["pathChanged"],
        "nMinusOneExplainable": report["nMinusOne"]["outageMinTractionLimitRatio"] < 1.0 and report["nMinusOne"]["delayDuringOutageSec"] > report["nMinusOne"]["delayBeforeSec"] and report["nMinusOne"]["delayAfterRecoverySec"] == report["nMinusOne"]["delayDuringOutageSec"],
        "regenConservative": report["regen"]["conservationErrorKw"] < 1e-9 and report["regen"]["absorbedKw"] > 0.0 and report["regen"]["feedbackKw"] > 0.0 and report["regen"]["wastedKw"] > 0.0,
        "passengerChain": report["passenger"]["high"]["totalMassKg"] > report["passenger"]["low"]["totalMassKg"] and report["passenger"]["high"]["energyKwh"] > report["passenger"]["low"]["energyKwh"] and report["passenger"]["high"]["timeTo1000mSec"] > report["passenger"]["low"]["timeTo1000mSec"] and report["passenger"]["low"]["stressedMinTractionLimitRatio"] < 1.0 and report["passenger"]["high"]["stressedMinTractionLimitRatio"] < 1.0 and round(report["passenger"]["low"]["stressedMinTractionLimitRatio"], 4) != round(report["passenger"]["high"]["stressedMinTractionLimitRatio"], 4),
        "recordReplayExport": report["recording"]["replayTrainStateCount"] > 0 and report["recording"]["tableCounts"]["power_solver_records"] > 0 and report["recording"]["tableCounts"]["power_command_records"] > 0,
    }
    report["passed"] = all(report["criteria"].values())
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
