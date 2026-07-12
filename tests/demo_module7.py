"""模块7 客流调度演示：展示输入→处理→输出全链路."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.station.services import (
    PoissonPassengerFlowGenerator, StationFlowConfig,
    FlowScenario, DayType, StationService, DwellTimeConfig, TrainLoadState,
)
from app.domain.dispatch.timetable import TimetableService, HeadwayConfig
from app.domain.dispatch.kpi import DispatchKpiTracker


def sep(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ═══════════════════════════
# 1. 输入配置
# ═══════════════════════════
sep("输入1: 车站客流配置 (StationFlowConfig × 13站)")

station_configs = [
    StationFlowConfig("GGZ", base_arrival_rate_pax_per_min=60,  alighting_ratio=0.05, direction="UP"),
    StationFlowConfig("FSP", base_arrival_rate_pax_per_min=72,  alighting_ratio=0.10, direction="UP"),
    StationFlowConfig("KYL", base_arrival_rate_pax_per_min=48,  alighting_ratio=0.12, direction="UP"),
    StationFlowConfig("FTN", base_arrival_rate_pax_per_min=55,  alighting_ratio=0.15, direction="UP"),
    StationFlowConfig("FTD", base_arrival_rate_pax_per_min=40,  alighting_ratio=0.15, direction="UP"),
    StationFlowConfig("QLZ", base_arrival_rate_pax_per_min=65,  alighting_ratio=0.18, direction="UP"),
    StationFlowConfig("LLQ", base_arrival_rate_pax_per_min=90,  alighting_ratio=0.20, direction="UP"),
    StationFlowConfig("LLE", base_arrival_rate_pax_per_min=50,  alighting_ratio=0.18, direction="UP"),
    StationFlowConfig("BWR", base_arrival_rate_pax_per_min=120, alighting_ratio=0.25, direction="UP"),
    StationFlowConfig("JBG", base_arrival_rate_pax_per_min=80,  alighting_ratio=0.20, direction="UP"),
    StationFlowConfig("BDZ", base_arrival_rate_pax_per_min=35,  alighting_ratio=0.15, direction="UP"),
    StationFlowConfig("BQS", base_arrival_rate_pax_per_min=45,  alighting_ratio=0.15, direction="UP"),
    StationFlowConfig("GTG", base_arrival_rate_pax_per_min=70,  alighting_ratio=0.25, direction="UP"),
]

for c in station_configs:
    print(f"  {c.station_id}: base={c.base_arrival_rate_pax_per_min:4.0f} pax/min, "
          f"alight={c.alighting_ratio:.0%}, dir={c.direction}")

sep("输入2: 运行场景 (FlowScenario)")
scenario = FlowScenario(day_type=DayType.MON_THU, line_scale=1.0, random_seed=42)
print(f"  日型: {scenario.day_type.value} (系数={1.00})")
print(f"  线路缩放: {scenario.line_scale}")
print(f"  随机种子: {scenario.random_seed}")

sep("输入3: 六时段系数 (TIME_PERIODS)")
from app.domain.station.services import TIME_PERIODS
for name, start, end, coeff in TIME_PERIODS:
    print(f"  {name:10s}  {start//3600:02d}:00-{end//3600:02d}:00  系数={coeff}")

sep("输入4: 发车间隔配置 (HeadwayConfig)")
hw = HeadwayConfig()
for name, sec in hw.period_headway_sec.items():
    print(f"  {name:10s}  headway={sec:4.0f}s = {sec/60:.0f}min")
print(f"  最小追踪间隔: {hw.min_headway_sec}s")

sep("输入5: 列车配置")
train_capacity = 600  # 定员
initial_load = 0
print(f"  定员: {train_capacity} 人/列")
print(f"  初始载客: {initial_load}")
print(f"  车门通过率: 3.0 pax/s")
print(f"  基础停站: 30s")

# ═══════════════════════════
# 2. 初始化服务
# ═══════════════════════════
sep("初始化服务")

gen = PoissonPassengerFlowGenerator(station_configs, scenario, use_poisson=True)
svc = StationService(gen, DwellTimeConfig(base_dwell_sec=30.0, door_capacity_pax_per_sec=3.0))
kpi = DispatchKpiTracker()

print(f"  PoissonPassengerFlowGenerator: use_poisson=True")
print(f"  StationService: 13 platforms")
print(f"  DispatchKpiTracker: ready")

# ═══════════════════════════
# 3. 仿真运行 (早高峰 7:30-9:00, 1h30min, 10s tick)
# ═══════════════════════════
sep("仿真运行: 周一早高峰 7:30→9:00 (1h30min)")

TICK_SEC = 10.0
START_MS = 7 * 3600 * 1000 + 30 * 60 * 1000  # 7:30
END_MS   = 9 * 3600 * 1000                      # 9:00
DURATION_MIN = (END_MS - START_MS) / 60000

print(f"  时间范围: 7:30 → 9:00 ({DURATION_MIN:.0f} min)")
print(f"  Tick: {TICK_SEC}s")
print(f"  列车: 1列, 定员{train_capacity}人, 初始空车")

# 初始化站台
for config in station_configs:
    svc.ensure_platform(config.station_id, config.direction)

train_load = TrainLoadState(train_id="T001", onboard_pax=0, capacity_pax=train_capacity)
current_station_idx = 0
train_at_station = True
dwell_remaining = 5.0   # 初始等5s发车
station_list = [c.station_id for c in station_configs]
n_stations = len(station_list)

total_stops = 0
total_boarded_all = 0
total_alighted_all = 0

sim_ms = START_MS
while sim_ms < END_MS:
    sim_ms += int(TICK_SEC * 1000)

    # 客流到达
    svc.update_arrivals(sim_ms, dt_sec=TICK_SEC)

    # 列车在站？
    if train_at_station:
        dwell_remaining -= TICK_SEC
        if dwell_remaining <= 0:
            # 上下客
            station_code = station_list[current_station_idx]
            result, plan = svc.process_train_stop(
                sim_time_ms=sim_ms,
                station_id=station_code,
                direction="UP",
                train_load=train_load,
            )
            train_load = result.updated_load
            total_boarded_all += result.boarding
            total_alighted_all += result.alighting
            total_stops += 1

            # KPI 记录
            kpi.record_load(train_load.load_factor)
            kpi.record_boarding(result.boarding, result.boarding * plan.estimated_dwell_sec * 0.5)

            # 移动到下一站
            if current_station_idx < n_stations - 1:
                current_station_idx += 1
                # 站间运行时间（估算）
                run_time = 120.0   # 2分钟区间运行
                dwell_remaining = run_time
                train_at_station = False
            else:
                break  # 终点
    else:
        dwell_remaining -= TICK_SEC
        if dwell_remaining <= 0:
            train_at_station = True
            dwell_remaining = 5.0  # 到站后短暂停留

# ═══════════════════════════
# 4. 输出结果
# ═══════════════════════════
sep("输出1: 各站实时客流状态")

current_hour = sim_ms / 3600000
period_name = "?"
for name, start, end, _ in TIME_PERIODS:
    if start <= (sim_ms // 1000) % 86400 < end:
        period_name = name
        break

print(f"  时刻: {current_hour:.2f}h")
print(f"  时段: {period_name}")
print(f"  时段系数: {gen._period_multiplier(sim_ms):.2f}")
print(f"  日型系数: {gen.day_coefficient:.2f}")
print()
print(f"  {'车站':6s} {'基准到达率':>10s} {'当前到达率':>10s} {'候车人数':>8s} {'下车比':>6s}")
print(f"  {'-'*6} {'-'*10} {'-'*10} {'-'*8} {'-'*6}")

total_waiting = 0
for config in station_configs:
    rate = gen.arrival_rate_pax_per_min(config.station_id, config.direction, sim_ms)
    platform = svc.platforms.get((config.station_id, config.direction))
    waiting = platform.waiting_pax if platform else 0
    total_waiting += waiting
    print(f"  {config.station_id:6s} {config.base_arrival_rate_pax_per_min:8.0f} pax/m "
          f"{rate:8.1f} pax/m {waiting:8d}    {config.alighting_ratio:.0%}")

print(f"\n  全线总候车: {total_waiting} 人")

sep("输出2: 列车运行状态")

print(f"  列车: {train_load.train_id}")
print(f"  当前位置: 第{current_station_idx+1}/{n_stations}站 ({station_list[current_station_idx]})")
print(f"  车上人数: {train_load.onboard_pax} / {train_load.capacity_pax}")
print(f"  满载率: {train_load.load_factor:.1%}")
print(f"  累计上车: {total_boarded_all} 人")
print(f"  累计下车: {total_alighted_all} 人")
print(f"  停站次数: {total_stops}")

sep("输出3: KPI指标 (DispatchKpiTracker)")

snap = kpi.snapshot(current_time_s=sim_ms / 1000.0)
kpi_dict = snap.to_dict()
for key, val in kpi_dict.items():
    print(f"  {key}: {val}")

sep("输出4: 客流生成抽样 (1分钟窗口, 100次采样)")

import numpy as np
sim_peak = 8 * 3600 * 1000  # 早高峰 8:00
sim_off  = 14 * 3600 * 1000  # 平峰 14:00

print(f"  目标站: BWR (base=120 pax/min, 最大站)")
print()

# 早高峰
rate_peak = gen.arrival_rate_pax_per_min("BWR", "UP", sim_peak)
samples_peak = [gen.arrivals("BWR", "UP", sim_peak, dt_sec=60.0) for _ in range(100)]
print(f"  早高峰 8:00:")
print(f"    期望到达率: 120 × 1.45 = {rate_peak:.1f} pax/min")
print(f"    100次采样: mean={np.mean(samples_peak):.1f}, std={np.std(samples_peak):.1f}")
print(f"    范围: [{min(samples_peak)}, {max(samples_peak)}]")

# 平峰
rate_off = gen.arrival_rate_pax_per_min("BWR", "UP", sim_off)
samples_off = [gen.arrivals("BWR", "UP", sim_off, dt_sec=60.0) for _ in range(100)]
print(f"  平峰 14:00:")
print(f"    期望到达率: 120 × 0.55 = {rate_off:.1f} pax/min")
print(f"    100次采样: mean={np.mean(samples_off):.1f}, std={np.std(samples_off):.1f}")
print(f"    范围: [{min(samples_off)}, {max(samples_off)}]")

# 周五 vs 周日
gen_fri = PoissonPassengerFlowGenerator(station_configs, FlowScenario(DayType.FRI), use_poisson=True)
gen_sun = PoissonPassengerFlowGenerator(station_configs, FlowScenario(DayType.SUN), use_poisson=True)
rate_fri = gen_fri.arrival_rate_pax_per_min("BWR", "UP", sim_peak)
rate_sun = gen_sun.arrival_rate_pax_per_min("BWR", "UP", sim_peak)
print(f"\n  日型对比 (BWR 早高峰):")
print(f"    周一至四: {rate_peak:.1f} pax/min")
print(f"    周五:     {rate_fri:.1f} pax/min (+8%)")
print(f"    周日:     {rate_sun:.1f} pax/min (-40%)")

sep("输出5: 运行图生成 (TimetableService)")

stations_for_tt = [
    {"code": c.station_id, "name": c.station_id, "mileageM": i*1200, "dwellSeconds": 30}
    for i, c in enumerate(station_configs[:8])
]
tt_svc = TimetableService(headway_config=HeadwayConfig())
tt = tt_svc.generate(
    timetable_id="TT-DEMO",
    line_id="LINE9",
    direction="UP",
    stations=stations_for_tt,
    start_time_s=7 * 3600,
    end_time_s=8 * 3600,
)

print(f"  线路: LINE9 UP, 8站")
print(f"  时间: 7:00 → 8:00 (1h)")
print(f"  生成车次: {tt.service_count} 列")
print()

# 显示前5列
print(f"  {'车次':10s} {'发车时刻':>10s} {'终到时刻':>10s} {'全程耗时':>10s} {'停站数':>6s}")
for sv in tt.services[:5]:
    dep = sv.stops[0].planned_departure_s
    arr = sv.stops[-1].planned_arrival_s
    h = int(dep // 3600)
    m = int((dep % 3600) // 60)
    s = int(dep % 60)
    dep_str = f"{h:02d}:{m:02d}:{s:02d}"
    h2 = int(arr // 3600)
    m2 = int((arr % 3600) // 60)
    s2 = int(arr % 60)
    arr_str = f"{h2:02d}:{m2:02d}:{s2:02d}"
    print(f"  {sv.service_id:10s} {dep_str:>10s} {arr_str:>10s} {sv.planned_run_time_s:8.0f}s {sv.stop_count:6d}")

# 前两列的间隔
if len(tt.services) >= 2:
    gap = tt.services[1].stops[0].planned_departure_s - tt.services[0].stops[0].planned_departure_s
    print(f"\n  首班与第二班间隔: {gap:.0f}s ({gap/60:.1f}min)")

print("\n" + "="*60)
print("  演示完毕")
print("="*60)
