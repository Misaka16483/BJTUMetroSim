"""Post-merge end-to-end audit: scope, fleet, lifecycle, power and turnback."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.engine import SimulationEngine


def main() -> int:
    checks: list[dict[str, object]] = []

    def verify(name: str, condition: bool, detail: object = None) -> None:
        checks.append({"name": name, "ok": bool(condition), "detail": detail})

    engine = SimulationEngine.load_from_files(
        scenario_path=ROOT / "data" / "scenarios" / "line9_interactive.json",
        line_map_path=ROOT / "data" / "cache" / "line_map.json",
        stations_csv_path=ROOT / "data" / "line9" / "stations.csv",
    )
    engine.load()
    verify("mainline scope loaded", engine.line_scope is not None, getattr(engine.line_scope, "scope_id", None))

    up = engine.add_train({"trainId": "FLOW-UP", "initialStationCode": "郭公庄", "direction": "UP"})
    down = engine.add_train({"trainId": "FLOW-DOWN", "initialStationCode": "国家图书馆", "direction": "DOWN"})
    intermediate = engine.add_train({"trainId": "FLOW-MIDDLE", "initialStationCode": "丰台科技园", "direction": "UP"})
    verify("UP fixed origin accepts station name", up.get("ok") is True, up)
    verify("DOWN fixed origin accepts station name", down.get("ok") is True, down)
    verify("intermediate origin accepted", intermediate.get("ok") is True, intermediate)

    verify("start", engine.start() == "STARTED")
    time.sleep(0.8)
    running = engine.snapshot()
    verify("clock running", running.clock_state == "RUNNING", running.clock_state)
    verify(
        "three-train roster",
        {t["trainId"] for t in running.trains} == {"FLOW-UP", "FLOW-DOWN", "FLOW-MIDDLE"},
        running.trains,
    )
    verify("scope exposed in KPI", running.kpi.get("lineScopeEnforced") is True, running.kpi)
    verify("power topology available", bool(running.power_network.get("substations")), running.power_network.get("solver"))

    engine.pause()
    paused_tick = engine.snapshot().tick
    time.sleep(0.4)
    verify("pause freezes ticks", engine.snapshot().tick == paused_tick, {"before": paused_tick, "after": engine.snapshot().tick})
    engine.resume()
    resume_deadline = time.monotonic() + 5.0
    while engine.snapshot().tick <= paused_tick and time.monotonic() < resume_deadline:
        time.sleep(0.1)
    verify("resume advances ticks", engine.snapshot().tick > paused_tick, engine.snapshot().tick)

    up_train = next(train for train in engine.trains if train.train_id == "FLOW-UP")
    up_train.station_index = len(engine._station_list) - 1
    up_train.current_station_code = str(engine._station_list[-1]["code"])
    up_train.current_station_name = str(engine._station_list[-1]["name"])
    up_train.direction = "UP"
    engine._turn_train_at_terminal(up_train)
    verify("terminal turnback switches UP to DOWN", up_train.direction == "DOWN" and up_train.phase == "DWELLING", up_train.to_dict())

    engine.stop()
    stopped = engine.snapshot()
    verify("stop state", stopped.clock_state == "STOPPED", stopped.clock_state)
    verify("stop resets tick", stopped.tick == 0, stopped.tick)
    verify("stop resets dynamics", all(t["speedMps"] == 0 and t["energyKwh"] == 0 for t in stopped.trains), stopped.trains)
    verify("stop clears roster", stopped.trains == [], stopped.trains)

    verify("restart", engine.start() == "STARTED")
    verify("restart with empty roster", engine.snapshot().trains == [], engine.snapshot().trains)
    engine.stop()

    result = {"ok": all(check["ok"] for check in checks), "checks": checks}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
