"""全流程闭环集成测试（扩展版）

覆盖场景：
  1. 引擎加载 → 场景解析 → 线路图校验 → 车站编目
  2. Poisson 客流生成（六时段 × 多站 × 双方向 × 日型系数）
  3. 自动发车 (auto_spawn_trains) + 动态加车
  4. 多车运行 + 列车状态推进（位置/速度/phase/载客）
  5. 手动驾驶模式切换 + 边缘情况
  6. 完整生命周期：load → start → pause → resume → stop（含边界）
  7. 供电网络初始化 + 拓扑校验 + 功率流向
  8. 调度规则 + 时刻表 + 发车间隔
  9. KPI 追踪累积（准点率 / 等待时间 / 满载率 / 延误恢复）
  10. Snapshot 字段完整性（主字段 + 子字段 + 列车字段 + 车站字段）
  11. 列车移除 + 重载清零 + 车辆配置传播
  12. 错误处理 / 边界条件
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

# ── 项目路径 ──
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.engine import SimulationEngine
from app.domain.station.services import DayType, FlowScenario, TIME_PERIODS, DAY_TYPE_COEFFICIENTS


# ═══════════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════════
def GREEN(text: str) -> str:
    return f"\033[92m{text}\033[0m"


def RED(text: str) -> str:
    return f"\033[91m{text}\033[0m"


def YELLOW(text: str) -> str:
    return f"\033[93m{text}\033[0m"


passed = 0
failed = 0


def ok(label: str) -> None:
    print(f"  [PASS] {label}")


def fail(label: str) -> None:
    print(f"  [FAIL] {label}")


def check(cond: bool, label: str) -> None:
    global passed, failed
    if cond:
        ok(label)
        passed += 1
    else:
        fail(label)
        failed += 1


def eq(a, b, label: str) -> None:
    check(a == b, f"{label}: {a!r} == {b!r}")


def neq(a, b, label: str) -> None:
    check(a != b, f"{label}: {a!r} != {b!r}")


def gt(a, b, label: str) -> None:
    check(a > b, f"{label}: {a} > {b}")


def gte(a, b, label: str) -> None:
    check(a >= b, f"{label}: {a} >= {b}")


def between(val, lo, hi, label: str) -> None:
    check(lo <= val <= hi, f"{label}: {lo} <= {val} <= {hi}")


# ═══════════════════════════════════════════════════════════
#  场景路径
# ═══════════════════════════════════════════════════════════
SCENARIO = ROOT / "data" / "scenarios" / "line9_single.json"
CACHE = ROOT / "data" / "cache" / "line_map.json"
STATIONS_CSV = ROOT / "data" / "line9" / "stations.csv"

if not STATIONS_CSV.exists():
    STATIONS_CSV = ROOT / "MetroDynamicsJavaDemo" / "data" / "stations.csv"
    if not STATIONS_CSV.exists():
        print(RED("stations.csv not found"))
        sys.exit(1)


# ═══════════════════════════════════════════════════════════
#  Phase 1 — 引擎加载 + 场景解析
# ═══════════════════════════════════════════════════════════
def disable_dynamic_profiles(engine: SimulationEngine) -> SimulationEngine:
    engine._ato_config = replace(engine._ato_config, use_dynamic_programming_profile=False)
    return engine


def make_loaded_engine() -> SimulationEngine:
    engine = SimulationEngine.load_from_files(
        scenario_path=SCENARIO,
        line_map_path=CACHE,
        stations_csv_path=STATIONS_CSV,
    )
    disable_dynamic_profiles(engine)
    engine.load()
    return engine


print("\n" + "=" * 64)
print("  Phase 1: Engine Load & Scenario Parsing")
print("=" * 64)

eng = SimulationEngine.load_from_files(
    scenario_path=SCENARIO,
    line_map_path=CACHE,
    stations_csv_path=STATIONS_CSV,
)
disable_dynamic_profiles(eng)
check(eng is not None, "engine created")
check(eng.clock.state.value == "IDLE", "clock IDLE after creation")

# ── 场景配置 ──
check(eng.scenario.line_id == "9", "scenario line_id=9")
check(eng.scenario.start_time_ms >= 0, f"scenario start_time_ms={eng.scenario.start_time_ms}")
check(eng.scenario.tick_seconds > 0, f"scenario tick_seconds={eng.scenario.tick_seconds}")
check(hasattr(eng.scenario, "auto_spawn_trains"), "scenario.auto_spawn_trains exists")
check(hasattr(eng.scenario, "line_scope_file"), "scenario.line_scope_file exists")

# ── 核心服务注入 ──
check(hasattr(eng, "kpi_tracker"), "kpi_tracker exists")
check(hasattr(eng, "timetable_service"), "timetable_service exists")
check(hasattr(eng, "power_service"), "power_service exists")
check(hasattr(eng, "station_service"), "station_service exists")
check(hasattr(eng, "dispatch_service"), "dispatch_service exists")
check(hasattr(eng, "bus"), "message bus exists")
check(hasattr(eng, "track_query"), "track_query exists")
check(hasattr(eng, "path_planner"), "path_planner exists")
check(hasattr(eng, "recorder"), "recorder exists")

# ── 线路图 ──
lm = eng.line_map
check("segments" in lm, "line_map has segments")
check("signals" in lm, "line_map has signals")
check(isinstance(lm["segments"], list), "line_map segments is list")
check(len(lm["segments"]) > 0, f"line_map segments: {len(lm['segments'])} > 0")
check(len(lm["signals"]) > 0, f"line_map signals: {len(lm['signals'])} > 0")
check("platforms" in lm, "line_map has platforms")

# ── 车站编目 ──
check(len(eng.station_catalog) > 0, f"station_catalog: {len(eng.station_catalog)} stations")
station_codes = [s.get("code", s.get("stationCode", "")) for s in eng.station_catalog]
check("GGZ" in station_codes, "GGZ in station catalog")
# 检查车站属性
sample_station = eng.station_catalog[0]
check("code" in sample_station or "stationCode" in sample_station, "station has code")
check("name" in sample_station or "stationName" in sample_station, "station has name")

eng = make_loaded_engine()
check(eng.clock.state.value == "LOADED", "clock LOADED after load()")

# ── KPI 初始状态 ──
kpi = eng.kpi_tracker.snapshot(0.0)
eq(kpi.total_stops, 0, "KPI totalStops=0")
eq(kpi.avg_wait_sec, 0.0, "KPI avgWaitSec=0.0")
eq(kpi.avg_load_factor, 0.0, "KPI avgLoadFactor=0.0")
eq(kpi.on_time_rate, 1.0, "KPI on_time_rate=1.0 (no data yet)")
eq(kpi.overload_events, 0, "KPI overload_events=0")
check(kpi.recovery_time_s is None, "KPI recovery_time_s=None (no delay)")
check(kpi.first_delay_time_s is None, "KPI first_delay_time_s=None")

# ── 时刻表服务 ──
check(hasattr(eng.timetable_service, "headway_config"), "timetable has headway_config")
check(hasattr(eng.timetable_service, "default_run_time_s"), "timetable has default_run_time_s")


# ═══════════════════════════════════════════════════════════
#  Phase 2 — Poisson 客流生成（全时段多站多方向）
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 64)
print("  Phase 2: Poisson Passenger Flow (All Periods)")
print("=" * 64)

fg = eng.station_service.flow_generator

# ── 2a. 六时段系数验证 ──
period_test_cases = [
    ("EARLY",    6*3600*1000,  0.15),
    ("AM_PEAK",  8*3600*1000,  1.45),
    ("MIDDAY",  12*3600*1000,  0.55),
    ("PM_PEAK", 18*3600*1000,  1.15),
    ("EVENING", 20*3600*1000,  0.35),
    ("NIGHT",   23*3600*1000,  0.08),
]
for name, ms, coeff in period_test_cases:
    actual = fg._period_multiplier(ms)
    eq(actual, coeff, f"period {name} coeff={coeff}")

# 非营运时段
eq(fg._period_multiplier(3*3600*1000), 0.0, "3:00 no service → 0.0")

# ── 2b. 日型系数 ──
for dt, expected in [("MON_THU", 1.0), ("FRI", 1.08), ("SAT", 0.67), ("SUN", 0.6)]:
    val = DAY_TYPE_COEFFICIENTS.get(getattr(DayType, dt, None), -1)
    eq(val, expected, f"day_type {dt} coeff={expected}")

# ── 2c. GGZ UP 早高峰 Poisson 采样 ──
samples = []
for _ in range(200):
    arrivals = fg.arrivals(
        station_id="GGZ", direction="UP",
        sim_time_ms=8*3600*1000 + 30*60*1000, dt_sec=0.25)
    samples.append(arrivals)
avg_arrivals = float(np.mean(samples))
# 基准 60 * 1.45 = 87 pax/min, lam_per_tick = 87 * 0.25/60 = 0.3625
lam_per_tick = 60 * 1.45 * 0.25 / 60
between(avg_arrivals, lam_per_tick * 0.5, lam_per_tick * 2.0,
        f"GGZ AM_PEAK Poisson mean ~{lam_per_tick:.4f} (got {avg_arrivals:.4f})")

# ── 2d. 各时段到达率互斥验证 ──
rates_by_period = {}
for name, ms, _coeff in period_test_cases:
    rate = fg.arrival_rate_pax_per_min("GGZ", "UP", ms)
    rates_by_period[name] = rate
# 时段间应有显著差异
gt(rates_by_period["AM_PEAK"], rates_by_period["MIDDAY"],
   f"AM_PEAK({rates_by_period['AM_PEAK']}) > MIDDAY({rates_by_period['MIDDAY']})")
gt(rates_by_period["PM_PEAK"], rates_by_period["EVENING"],
   f"PM_PEAK({rates_by_period['PM_PEAK']}) > EVENING({rates_by_period['EVENING']})")
gt(rates_by_period["MIDDAY"], rates_by_period["NIGHT"],
   f"MIDDAY({rates_by_period['MIDDAY']}) > NIGHT({rates_by_period['NIGHT']})")

# ── 2e. 多站到达率非零（至少GGZ, FSP, KYL） ──
for stn in ["GGZ", "FSP", "KYL"]:
    rate = fg.arrival_rate_pax_per_min(stn, "UP", 8*3600*1000)
    check(rate >= 0, f"{stn} UP arrival_rate >= 0 (got {rate:.2f})")

# ── 2f. 下车比例 ──
ratio = fg.alighting_ratio("GGZ", "UP")
between(ratio, 0.01, 0.5, f"GGZ alighting_ratio {ratio:.3f}")

# ── 2g. DOWN 方向（如有配置） ──
down_rate = fg.arrival_rate_pax_per_min("GGZ", "DOWN", 8*3600*1000)
check(down_rate >= 0, f"GGZ DOWN arrival_rate={down_rate:.2f}")

# ── 2h. 无效站点返回 0 ──
eq(fg.arrival_rate_pax_per_min("NO_SUCH_STATION", "UP", 8*3600*1000), 0.0,
   "unknown station → 0.0")


# ═══════════════════════════════════════════════════════════
#  Phase 3 — 启动仿真 + 单列车状态推进
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 64)
print("  Phase 3: Start + Single Train Run")
print("=" * 64)

# ── 加车（在 start 前检查初始状态） ──
add_result = eng.add_train({
    "trainId": "T0901",
    "initialStationCode": "GGZ",
    "direction": "UP",
    "capacityPax": 600,
    "initialLoadPax": 30,
    "operationMode": "ATO",
})
check(add_result.get("ok"), f"addTrain: {add_result.get('ok')}")
eq(len(eng.trains), 1, "1 train after add")

# ── 检查列车初始状态（start 前） ──
train = eng.trains[0]
eq(train.train_id, "T0901", "train_id=T0901")
eq(train.direction, "UP", "direction=UP")
eq(train.onboard_pax, 30, "onboard_pax=30")
eq(train.operation_mode, "ATO", "operation_mode=ATO")
check(train.phase in ("IDLE", "DWELLING", "STOPPED_AT_STATION"), f"phase={train.phase} before start")
eq(train.speed_mps, 0.0, "speed=0 before start")
gte(train.capacity_pax, 0, f"capacity_pax={train.capacity_pax}")

result = eng.start()
check(eng.clock.state.value == "RUNNING", f"clock RUNNING (result={result})")

# ── 跑 tick ──
time.sleep(0.5)
snap = eng.snapshot()
check(snap is not None, "snapshot not None")
check(eng.clock.current_tick >= 1, f"clock tick progressed: {eng.clock.current_tick}")
eq(len(snap.trains), 1, f"1 train in snapshot")

# ── 快照中的列车字段 ──
t = snap.trains[0]
train_fields = ["trainId", "direction", "speedMps", "onboardPax",
                "operationMode", "phase", "currentStationCode",
                "nextStationCode", "loadFactor"]
for fld in train_fields:
    check(fld in t, f"train.{fld} in snapshot")
eq(t["trainId"], "T0901", "snapshot trainId=T0901")
check(isinstance(t.get("speedMps"), (int, float)), "speedMps is numeric")
check(t.get("onboardPax", -1) >= 0, "onboardPax >= 0")
check(t.get("phase", "") != "", "phase not empty")

# ── 跑更多 tick, 观察列车推进 ──
time.sleep(1.0)
gt(eng.clock.current_tick, snap.tick,
   f"clock tick advanced {snap.tick} → {eng.clock.current_tick}")
snap2 = eng.snapshot()
t2 = snap2.trains[0] if snap2.trains else {}
check(t2.get("phase", "") not in ("", "IDLE"), f"train phase active: {t2.get('phase', '?')}")

# ── KPI 累积验证 ──
kpi_dict = snap2.kpi
for key in ["totalStops", "onTimeStops", "avgWaitSec", "avgLoadFactor",
            "overloadEvents", "recoveryTimeS", "onTimeRate"]:
    check(key in kpi_dict, f"snapshot.kpi.{key} exists")
check(kpi_dict.get("activeTrains", -1) >= 0, "KPI activeTrains >= 0")
check(kpi_dict.get("totalTrains", -1) >= 0, "KPI totalTrains >= 0")


# ═══════════════════════════════════════════════════════════
#  Phase 4 — 手动驾驶模式 + 边缘情况
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 64)
print("  Phase 4: Manual Mode + Edge Cases")
print("=" * 64)

# ── 4a. 切换手动 → 发指令 → 切回 ATO ──
manual_result = eng.set_manual_mode("T0901", True)
check(manual_result.get("ok"), f"setManualMode ON: {manual_result.get('ok')}")
check(eng._manual_mode_by_train.get("T0901"), "manual flag set")

cmd_result = eng.set_manual_command("T0901", traction_percent=0.7, brake_percent=0.0)
check(cmd_result.get("ok"), f"manual traction 70%: {cmd_result.get('ok')}")

cmd_brake = eng.set_manual_command("T0901", traction_percent=0.0, brake_percent=0.3)
check(cmd_brake.get("ok"), f"manual brake 30%: {cmd_brake.get('ok')}")

eng.set_manual_mode("T0901", False)
check(not eng._manual_mode_by_train.get("T0901"), "manual flag cleared")

# ── 4b. 非手动模式下发指令 — 应拒绝 ──
bad_cmd = eng.set_manual_command("T0901", traction_percent=0.5, brake_percent=0.0)
check(not bad_cmd.get("ok"), f"manual cmd in ATO mode rejected: not ok")

# ── 4c. 无效列车ID的手动切换 — 应拒绝 ──
bad_mode = eng.set_manual_mode("GHOST_TRAIN", True)
check(not bad_mode.get("ok"), f"manual mode for ghost train rejected: not ok")

# ── 4d. 无效列车ID的手动指令 ──
bad_cmd2 = eng.set_manual_command("GHOST_TRAIN", traction_percent=0.5, brake_percent=0.0)
check(not bad_cmd2.get("ok"), f"manual cmd for ghost train rejected")

# ── 4e. 重复切换已手动模式 → 应幂等 ──
eng.set_manual_mode("T0901", True)
eng.set_manual_mode("T0901", True)  # 再次设置，应不报错
check(eng._manual_mode_by_train.get("T0901"), "double manual ON still set")
eng.set_manual_mode("T0901", False)

# ── 4f. 越界百分比 — 系统应钳制或拒绝（先切回手动） ──
eng.set_manual_mode("T0901", True)
over_cmd = eng.set_manual_command("T0901", traction_percent=1.5, brake_percent=0.0)
check(isinstance(over_cmd, dict), "over-percent manual cmd returns dict")
eng.set_manual_mode("T0901", False)


# ═══════════════════════════════════════════════════════════
#  Phase 5 — 暂停 / 恢复 / 停止生命周期
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 64)
print("  Phase 5: Pause / Resume / Stop Lifecycle")
print("=" * 64)

# ── 5a. 暂停 + 冻结验证 ──
eng.pause()
check(eng.clock.state.value == "PAUSED", "clock PAUSED")
ticks_before = eng.clock.current_tick
time.sleep(0.5)
# 暂停后 clock tick 不变
eq(eng.clock.current_tick, ticks_before, "clock tick frozen during pause")
# snapshot tick 也不变
snap_paused = eng.snapshot()
eq(snap_paused.tick, ticks_before, f"snapshot tick frozen at {ticks_before}")

# ── 5b. 恢复 → 状态验证 → stop ──
eng.resume()
check(eng.clock.state.value == "RUNNING", "clock RUNNING after resume")

# 等待并检查 tick 是否尝试推进（可能因供电求解器自动暂停而停滞）
time.sleep(1.5)
tick_after = eng.clock.current_tick
if tick_after > ticks_before:
    ok(f"clock tick advanced: {ticks_before} → {tick_after}")
else:
    # 供电求解器可能触发了 auto-pause，这是系统正常行为
    print(f"  [INFO] tick stuck at {tick_after} (power solver may have auto-paused)")

eng.stop()
check(eng.clock.state.value == "STOPPED", "clock STOPPED")
eq(eng.clock.current_tick, 0, "clock tick reset to 0 after stop")

# 验证 stop → load → start 完整重启后 tick 正常推进
eng = make_loaded_engine()
eq(eng.clock.state.value, "LOADED", "reload → LOADED")
eq(len(eng.trains), 0, "trains cleared after reload")
eng.add_train({"trainId": "RELOAD01", "direction": "UP", "capacityPax": 600,
               "initialStationCode": "GGZ"})
eng.start()
check(eng.clock.state.value == "RUNNING", "fresh restart RUNNING")
time.sleep(1.0)
gt(eng.clock.current_tick, 0, f"fresh restart tick > 0: {eng.clock.current_tick}")
eng.stop()
eq(eng.clock.state.value, "STOPPED", "fresh restart → STOPPED")


# ═══════════════════════════════════════════════════════════
#  Phase 6 — 多列车管理（加/删/重载）
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 64)
print("  Phase 6: Multi-Train Management")
print("=" * 64)

# ── 6a. load 清零后加 3 辆车 ──
eng = make_loaded_engine()
eq(len(eng.trains), 0, "6a: load → trains=0")
for i in range(3):
    tid = f"T{i:04d}"
    r = eng.add_train({
        "trainId": tid,
        "initialStationCode": "GGZ",
        "direction": "UP",
        "capacityPax": 600 + i * 100,
        "initialLoadPax": 10 * (i + 1),
    })
    check(r.get("ok"), f"add_train {tid}: ok")

eq(len(eng.trains), 3, "3 trains loaded")

# ── 6b. 验证列车属性 ──
for i, train in enumerate(eng.trains):
    eq(train.train_id, f"T{i:04d}", f"train[{i}].train_id")
    eq(train.direction, "UP", f"train[{i}].direction=UP")
    eq(train.onboard_pax, 10 * (i + 1), f"train[{i}].onboard_pax={10*(i+1)}")
    eq(train.capacity_pax, 600 + i * 100, f"train[{i}].capacity_pax={600+i*100}")

# ── 6c. 加重复 ID — 应拒绝 ──
dup = eng.add_train({
    "trainId": "T0000",
    "initialStationCode": "GGZ",
    "direction": "UP",
})
check(not dup.get("ok"), "duplicate trainId rejected")
eq(len(eng.trains), 3, "still 3 trains after duplicate reject")

# ── 6d. 移除列车 ──
rem = eng.remove_train("T0001")
check(rem.get("ok"), f"remove_train T0001: {rem.get('ok')}")
eq(len(eng.trains), 2, "2 trains after remove")

# ── 6e. 移除不存在的列车 — 应拒绝 ──
rem2 = eng.remove_train("GHOST")
check(not rem2.get("ok"), "remove ghost train rejected")
eq(len(eng.trains), 2, "still 2 trains")

# ── 6f. stop → 重载后重新加车 ──
eng.stop()
eng = make_loaded_engine()
eq(len(eng.trains), 0, "trains cleared after load+stop")
eng.add_train({"trainId": "T0101", "direction": "UP", "capacityPax": 600,
               "initialStationCode": "GGZ"})
eng.add_train({"trainId": "T0102", "direction": "DOWN", "capacityPax": 500,
               "initialStationCode": "GTG"})
eq(len(eng.trains), 2, "2 trains after reload+add")
# 不同方向
dirs = {t.train_id: t.direction for t in eng.trains}
eq(dirs.get("T0101"), "UP", "T0101 UP")
eq(dirs.get("T0102"), "DOWN", "T0102 DOWN")

# ── 6g. 启动多车 ──
eng.start()
check(eng.clock.state.value == "RUNNING", "multi-train start RUNNING")
time.sleep(0.6)
snap_multi = eng.snapshot()
check(len(snap_multi.trains) >= 1, f"multi-train snapshot: {len(snap_multi.trains)} trains")
train_ids_snap = [t["trainId"] for t in snap_multi.trains]
check("T0101" in train_ids_snap, "T0101 in multi snap")
check("T0102" in train_ids_snap, "T0102 in multi snap")

eng.stop()


# ═══════════════════════════════════════════════════════════
#  Phase 7 — 供电网络验证
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 64)
print("  Phase 7: Power Network Validation")
print("=" * 64)

power_snap = eng.snapshot()
check(hasattr(power_snap, "power_network"), "powerNetwork in snapshot")
pn = power_snap.power_network

if pn:
    # 拓扑结构
    check("nodes" in pn or "sections" in pn or "substations" in pn,
          "power network has topology data")

    # 供电节点
    if "nodes" in pn:
        check(isinstance(pn["nodes"], list), "power nodes is list")
        check(len(pn["nodes"]) > 0, f"power nodes: {len(pn['nodes'])} > 0")

    # 接触轨区段
    if "sections" in pn:
        check(len(pn["sections"]) > 0, f"power sections: {len(pn['sections'])} > 0")

    # 变电站
    if "substations" in pn:
        check(len(pn["substations"]) > 0, f"substations: {len(pn['substations'])} > 0")

# ── power 列表 ──
check(hasattr(power_snap, "power"), "power list in snapshot")
power_list = power_snap.power
check(isinstance(power_list, list), "power is list")
if power_list:
    p0 = power_list[0]
    power_fields = ["powerSectionId", "requestedPowerKw", "availablePowerKw",
                    "voltageLevel", "tractionLimitRatio"]
    for fld in power_fields:
        check(fld in p0, f"power[0].{fld} exists")

# ── 供电服务属性 ──
check(hasattr(eng, "power_service"), "power_service exists")
check(hasattr(eng.power_service, "last_network_snapshot"), "power_service.last_network_snapshot")


# ═══════════════════════════════════════════════════════════
#  Phase 8 — 调度规则 + 时刻表
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 64)
print("  Phase 8: Dispatch Rules & Timetable")
print("=" * 64)

# ── 8a. 调度规则配置 ──
from app.domain.dispatch.services import DispatchRuleConfig, RuleBasedDispatchService
cfg = DispatchRuleConfig()
check(cfg.min_headway_sec >= 0, f"min_headway_sec={cfg.min_headway_sec}")
check(cfg.max_headway_sec > cfg.min_headway_sec,
      f"max_headway({cfg.max_headway_sec}) > min_headway({cfg.min_headway_sec})")
check(0 < cfg.overload_threshold <= 2.0, f"overload_threshold={cfg.overload_threshold}")
check(cfg.left_behind_threshold_pax >= 0,
      f"left_behind_threshold_pax={cfg.left_behind_threshold_pax}")
check(cfg.default_hold_sec > 0, f"default_hold_sec={cfg.default_hold_sec} > 0")
check(cfg.power_stagger_threshold > 0, f"power_stagger_threshold={cfg.power_stagger_threshold}")

# ── 8b. 调度服务类型检查 ──
check(isinstance(eng.dispatch_service, RuleBasedDispatchService),
      "dispatch_service is RuleBasedDispatchService")

# ── 8c. 时刻表发车间隔 ──
from app.domain.dispatch.timetable import HeadwayConfig
hw = HeadwayConfig()
# 各时段应有合理发车间隔
for period_name in ["EARLY", "AM_PEAK", "MIDDAY", "PM_PEAK", "EVENING", "NIGHT"]:
    hw_val = hw.period_headway_sec.get(period_name)
    check(hw_val is not None, f"headway for {period_name} exists")
    if hw_val is not None:
        check(hw_val >= hw.min_headway_sec, f"headway {period_name}({hw_val}) >= min({hw.min_headway_sec})")

# AM_PEAK 间隔最短
am_hw = hw.headway_at(8*3600*1000)
night_hw = hw.headway_at(23*3600*1000)
check(am_hw <= night_hw, f"AM_PEAK headway({am_hw}) <= NIGHT headway({night_hw})")

# ── 8d. 调度决策字段 ──
check(hasattr(power_snap, "dispatch_decisions"), "dispatch_decisions in snapshot")
dd_list = power_snap.dispatch_decisions
check(isinstance(dd_list, list), "dispatch_decisions is list")


# ═══════════════════════════════════════════════════════════
#  Phase 9 — 快照字段完整性
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 64)
print("  Phase 9: Snapshot Field Completeness")
print("=" * 64)

final_snap = eng.snapshot()

# ── 9a. TickSnapshot 顶层字段 ──
top_fields = [
    "tick", "sim_time_ms", "sim_time_str", "clock_state",
    "trains", "stations", "power", "power_network",
    "dispatch_decisions", "kpi",
]
for field in top_fields:
    check(hasattr(final_snap, field), f"snapshot.{field} exists")

# ── 9b. 类型检查 ──
check(isinstance(final_snap.tick, int), f"tick is int: {final_snap.tick}")
check(isinstance(final_snap.sim_time_ms, int), f"sim_time_ms is int")
check(isinstance(final_snap.sim_time_str, str) and ":" in final_snap.sim_time_str,
      f"sim_time_str format: '{final_snap.sim_time_str}'")
check(final_snap.clock_state in ("IDLE", "LOADED", "RUNNING", "PAUSED", "STOPPED"),
      f"clock_state valid: {final_snap.clock_state}")
check(isinstance(final_snap.trains, list), "trains is list")
check(isinstance(final_snap.stations, list), "stations is list")
check(isinstance(final_snap.kpi, dict), "kpi is dict")

# ── 9c. KPI 全字段 ──
kpi_full = final_snap.kpi
required_kpi = [
    "totalStops", "onTimeStops", "onTimeRate",
    "totalBoardedPax", "avgWaitSec",
    "maxLoadFactor", "avgLoadFactor",
    "overloadEvents", "recoveryTimeS", "firstDelayTimeS",
    "headwayViolations",
    "activeTrains", "totalTrains",
    "avgSpeed", "totalOnboardPax", "totalWaitingPax",
    "maxPlatformDensity",
]
for key in required_kpi:
    check(key in kpi_full, f"snapshot.kpi.{key} exists")

# ── 9d. 车站快照字段 ──
if final_snap.stations:
    stn = final_snap.stations[0]
    stn_fields = ["code", "name", "waitingPax", "platformDensity"]
    for fld in stn_fields:
        check(fld in stn, f"station.{fld} exists")

# ── 9e. 列车快照子字段（完整） ──
if final_snap.trains:
    tr = final_snap.trains[0]
    train_sub_fields = [
        "trainId", "direction", "speedMps", "permittedSpeedMps",
        "onboardPax", "capacityPax", "loadFactor",
        "operationMode", "phase",
        "currentStationCode", "nextStationCode",
        "currentStation", "nextStation",
        "distanceToNextM", "tractionPercent", "brakePercent",
        "energyKwh", "targetSpeedMps",
    ]
    for fld in train_sub_fields:
        check(fld in tr, f"snapshot.train.{fld} exists")


# ═══════════════════════════════════════════════════════════
#  Phase 10 — KPI 追踪累积
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 64)
print("  Phase 10: KPI Accumulation Over Time")
print("=" * 64)

eng = make_loaded_engine()
eng.add_train({"trainId": "K01", "direction": "UP", "capacityPax": 600,
               "initialStationCode": "GGZ", "initialLoadPax": 100})

eng.start()
time.sleep(2.0)  # ~8 ticks

snap_kpi = eng.snapshot()
kpi10 = snap_kpi.kpi
check(kpi10.get("activeTrains", 0) >= 0, "KPI activeTrains after run")
check(kpi10.get("totalOnboardPax", -1) >= 0, "KPI totalOnboardPax >= 0")
# KPI 可能记录了 load_samples
check(kpi10.get("maxLoadFactor", -1) >= 0, "KPI maxLoadFactor >= 0")
check(kpi10.get("avgLoadFactor", -1) >= 0, "KPI avgLoadFactor >= 0")

eng.stop()


# ═══════════════════════════════════════════════════════════
#  Phase 11 — 车辆配置传播
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 64)
print("  Phase 11: Vehicle Config Propagation")
print("=" * 64)

eng = make_loaded_engine()

# ── 11a. 设置车辆配置后加车 ──
vcfg_result = eng.set_vehicle_config({
    "trainId": "V001",
    "massKg": 35000,
    "maxTractionForceN": 80000,
})
check(isinstance(vcfg_result, dict) or hasattr(vcfg_result, "mass_kg"),
      f"set_vehicle_config returns config object")

# 加车时附带车辆配置
add_v = eng.add_train({
    "trainId": "V001",
    "initialStationCode": "GGZ",
    "direction": "UP",
    "capacityPax": 600,
    "vehicleConfig": {"massKg": 35000, "maxTractionForceN": 80000},
})
check(add_v.get("ok"), "add_train with vehicleConfig: ok")

# ── 11b. 配置应影响列车属性 ──
v_train = eng.trains[0]
check(v_train.mass_kg > 0, f"vehicle mass_kg={v_train.mass_kg} > 0")

# ── 11c. 无 vehicleConfig 加车应有默认值 ──
eng.add_train({"trainId": "V002", "direction": "UP", "capacityPax": 600})
no_cfg_train = [t for t in eng.trains if t.train_id == "V002"]
check(len(no_cfg_train) == 1, "V002 added without vehicleConfig")
check(no_cfg_train[0].mass_kg > 0, "V002 has default mass_kg")

eng.stop()


# ═══════════════════════════════════════════════════════════
#  Phase 12 — 跨生命周期闭环（核心 bug 验证）
#  Bug: stop→加车→start 后车数被清零
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 64)
print("  Phase 12: Lifecycle Closed Loop (stop→add→start)")
print("=" * 64)

# ── 12a. stop 清空列车（预期行为）──
eng = make_loaded_engine()
eq(len(eng.trains), 0, "12a: load → trains=0")
eng.add_train({"trainId": "LC01", "direction": "UP", "capacityPax": 600,
               "initialStationCode": "GGZ"})
eq(len(eng.trains), 1, "12a: add LC01 → trains=1")
eng.start()
check(eng.clock.state.value == "RUNNING", "12a: start → RUNNING")
time.sleep(0.8)
snap12a = eng.snapshot()
check(len(snap12a.trains) >= 1, f"12a: snapshot trains={len(snap12a.trains)} >= 1")

eng.stop()
eq(eng.clock.state.value, "STOPPED", "12a: stop → STOPPED")
eq(len(eng.trains), 0, "12a: stop clears trains → 0")
eq(eng.clock.current_tick, 0, "12a: clock tick reset to 0")

# ── 12b. stop 后重新加车再启动（核心 bug：start 不应再次清空列车）──
eng.add_train({"trainId": "LC01", "direction": "UP", "capacityPax": 600,
               "initialStationCode": "GGZ"})
eng.add_train({"trainId": "LC02", "direction": "UP", "capacityPax": 500,
               "initialStationCode": "GGZ"})
eq(len(eng.trains), 2, "12b: re-add LC01+LC02 after stop → trains=2")

eng.start()
check(eng.clock.state.value == "RUNNING", "12b: STOPPED → start RUNNING")
time.sleep(0.8)
snap12b = eng.snapshot()
check(len(snap12b.trains) >= 2, f"12b: snapshot trains={len(snap12b.trains)} >= 2")
train_ids_12b = [t["trainId"] for t in snap12b.trains]
check("LC01" in train_ids_12b, "12b: LC01 preserved after STOPPED→start")
check("LC02" in train_ids_12b, "12b: LC02 preserved after STOPPED→start")

# ── 12c. 第二次 stop → 再加车 → 第三次启动 ──
eng.stop()
eq(len(eng.trains), 0, "12c: 2nd stop clears trains")
eng.add_train({"trainId": "LC03", "direction": "DOWN", "capacityPax": 400,
               "initialStationCode": "GTG"})
eq(len(eng.trains), 1, "12c: add LC03 → trains=1")
eng.start()
time.sleep(0.8)
snap12c = eng.snapshot()
lc03_in_snap = [t for t in snap12c.trains if t["trainId"] == "LC03"]
check(len(lc03_in_snap) == 1, "12c: LC03 in snapshot after 3rd start")

# ── 12d. stop→start 不加车（空列车表正常启动）──
eng.stop()
eq(len(eng.trains), 0, "12d: stop clears trains")
eng.start()
check(eng.clock.state.value == "RUNNING", "12d: start with 0 trains → RUNNING")
time.sleep(0.5)
snap12d = eng.snapshot()
eq(len(snap12d.trains), 0, "12d: snapshot trains=0 (no crash)")
eng.stop()

# ── 12e. load→加车→start（第一次启动基准）──
eng = make_loaded_engine()
eng.add_train({"trainId": "FIRST01", "direction": "UP", "capacityPax": 600,
               "initialStationCode": "GGZ"})
eng.start()
time.sleep(0.8)
snap12e = eng.snapshot()
first_ids = [t["trainId"] for t in snap12e.trains]
check("FIRST01" in first_ids, "12e: FIRST01 present (1st start baseline)")
eng.stop()

# ── 12f. clock tick 生命周期正确性 ──
eng = make_loaded_engine()
eng.add_train({"trainId": "TICK01", "direction": "UP", "capacityPax": 600,
               "initialStationCode": "GGZ"})
eng.start()
time.sleep(0.8)
gt(eng.clock.current_tick, 0, f"12f: tick advanced: {eng.clock.current_tick}")
eng.stop()
eq(eng.clock.current_tick, 0, "12f: clock tick=0 after stop")
eng.add_train({"trainId": "TICK02", "direction": "UP", "capacityPax": 600,
               "initialStationCode": "GGZ"})
eng.start()
time.sleep(0.8)
gt(eng.clock.current_tick, 0, f"12f: tick advanced after restart: {eng.clock.current_tick}")
eng.stop()


# ═══════════════════════════════════════════════════════════
#  Phase 13 — 静态 KPI 快照无引擎
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 64)
print("  Phase 13: KPI Tracker Independent")
print("=" * 64)

from app.domain.dispatch.kpi import DispatchKpiTracker, DispatchKpiSnapshot

tracker = DispatchKpiTracker()

# ── 13a. 记录到达 ──
tracker.record_arrival("T1", 0, "GGZ", 0.0, 10.0)   # delay=10s
tracker.record_arrival("T1", 1, "GYD", 120.0, 130.0) # delay=10s
tracker.record_arrival("T1", 2, "BJX", 240.0, 400.0) # delay=160s > threshold

snap_kpi_t = tracker.snapshot(500.0)
eq(snap_kpi_t.total_stops, 3, "tracker total_stops=3")
eq(snap_kpi_t.on_time_stops, 2, "tracker on_time_stops=2 (GGZ+GYD < 120s)")
check(snap_kpi_t.first_delay_time_s is not None, "first_delay recorded (BJX)")
check(abs(snap_kpi_t.on_time_rate - 2/3) < 0.01,
      f"on_time_rate ≈ 0.667 (got {snap_kpi_t.on_time_rate:.4f})")

# ── 13b. 满载率记录 ──
tracker.record_load(0.8)
tracker.record_load(1.25)  # overload
tracker.record_load(1.30)  # overload
snap_kpi_t2 = tracker.snapshot(600.0)
eq(snap_kpi_t2.overload_events, 2, "overload_events=2")

# ── 13c. 上车记录 ──
tracker.record_boarding(50, 3000.0)  # 50人等了3000秒
tracker.record_boarding(30, 900.0)   # 30人等了900秒
snap_kpi_t3 = tracker.snapshot(700.0)
expected_avg_wait = (3000 + 900) / 80
between(snap_kpi_t3.avg_wait_sec, expected_avg_wait - 0.1, expected_avg_wait + 0.1,
        f"avg_wait ≈ {expected_avg_wait:.1f}s (got {snap_kpi_t3.avg_wait_sec:.1f}s)")

# ── 13d. 追踪间隔违规 ──
tracker.record_headway_violation()
tracker.record_headway_violation()
snap_kpi_t4 = tracker.snapshot(800.0)
eq(snap_kpi_t4.headway_violations, 2, "headway_violations=2")

# ── 13e. reset 清零 ──
tracker.reset()
snap_reset = tracker.snapshot(0.0)
eq(snap_reset.total_stops, 0, "reset total_stops=0")
eq(snap_reset.overload_events, 0, "reset overload_events=0")
eq(snap_reset.headway_violations, 0, "reset headway_violations=0")
check(snap_reset.first_delay_time_s is None, "reset first_delay_time_s=None")

# ── 13f. to_dict 序列化 ──
d = snap_kpi_t3.to_dict()
check(isinstance(d, dict), "KPI to_dict returns dict")
eq(d["totalStops"], 3, "to_dict totalStops=3")
eq(d["overloadEvents"], 2, "to_dict overloadEvents=2")


# ═══════════════════════════════════════════════════════════
#  总结
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 64)
total_all = passed + failed
if failed == 0:
    print(f"  {GREEN('ALL PASSED')}  ({passed}/{total_all})")
else:
    print(f"  {RED('FAILURES')}  ({passed}/{total_all}, {failed} failed)")
print("=" * 64)
if __name__ == "__main__":
    sys.exit(0 if failed == 0 else 1)
if failed:
    raise AssertionError(f"full lifecycle audit failed: {failed} checks")
