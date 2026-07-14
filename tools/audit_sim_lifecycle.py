"""Repeatable start/stop/restart lifecycle audit for BJTUMetroSim."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.engine import SimulationEngine


def check(condition: bool, name: str, details: object) -> dict[str, object]:
    return {"check": name, "ok": bool(condition), "details": details}


def main() -> int:
    engine = SimulationEngine.load_from_files(
        scenario_path=ROOT / "data" / "scenarios" / "line9_interactive.json",
        line_map_path=ROOT / "data" / "cache" / "line_map.json",
        stations_csv_path=ROOT / "data" / "line9" / "stations.csv",
    )
    # Audit an explicitly user-managed roster rather than the scenario default.
    engine.remove_train("T0901")
    results: list[dict[str, object]] = []
    for payload in (
        {"trainId": "AUDIT-UP", "initialStationCode": "GGZ", "direction": "UP"},
        {"trainId": "AUDIT-DOWN", "initialStationCode": "GTG", "direction": "DOWN"},
    ):
        response = engine.add_train(payload)
        results.append(check(response.get("ok") is True, f"add {payload['trainId']}", response))

    engine.start()
    time.sleep(0.8)
    running = engine.snapshot()
    results.append(check(running is not None and running.clock_state == "RUNNING", "first start", running.clock_state if running else None))
    results.append(check(len(running.trains) == 2, "roster survives first start", [t["trainId"] for t in running.trains]))

    engine.stop()
    stopped = engine.snapshot()
    stopped_train_ids = [t["trainId"] for t in stopped.trains]
    results.extend([
        check(stopped.clock_state == "STOPPED", "stop state", stopped.clock_state),
        check(stopped.tick == 0 and stopped.sim_time_ms == engine.scenario.start_time_ms, "clock reset to scenario start", {"tick": stopped.tick, "simTimeMs": stopped.sim_time_ms}),
        check(all(t["speedMps"] == 0 and t["energyKwh"] == 0 for t in stopped.trains), "train runtime reset", stopped.trains),
        check(all(t["requestedPowerKw"] == 0 for t in stopped.trains), "train power requests reset", [t["requestedPowerKw"] for t in stopped.trains]),
        check(all(int(station.get("waitingPax", 0)) == 0 for station in stopped.stations), "passenger queues reset", stopped.stations),
        check(all(float(section.get("requestedPowerKw", 0)) == 0 for section in stopped.power), "power section transients reset", stopped.power),
        check(len(stopped.dispatch_decisions) == 0, "dispatch reset", stopped.dispatch_decisions),
        check(stopped_train_ids == [], "runtime roster cleared after stop", stopped_train_ids),
    ])

    third = engine.add_train({"trainId": "AUDIT-UP-2", "initialStationCode": "GGZ", "direction": "UP"})
    results.append(check(third.get("ok") is True, "add train while stopped", third))
    engine.start()
    time.sleep(0.5)
    restarted = engine.snapshot()
    restarted_ids = [t["trainId"] for t in restarted.trains]
    results.extend([
        check(restarted.clock_state == "RUNNING", "restart", restarted.clock_state),
        check(restarted_ids == ["AUDIT-UP-2"], "stopped-state roster starts the next run", restarted_ids),
    ])
    engine.stop()

    print(json.dumps({"ok": all(item["ok"] for item in results), "checks": results}, ensure_ascii=False, indent=2))
    return 0 if all(item["ok"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
