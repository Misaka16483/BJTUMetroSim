"""Smoke test for Module 7 backend."""
import numpy as np
from app.domain.station.services import (
    PoissonPassengerFlowGenerator, StationFlowConfig, FlowScenario,
    DayType, StationService, DwellTimeConfig, TrainLoadState,
)
from app.domain.dispatch.timetable import TimetableService, HeadwayConfig
from app.domain.dispatch.kpi import DispatchKpiTracker


def test_poisson_generation():
    configs = [
        StationFlowConfig("GGZ", base_arrival_rate_pax_per_min=60.0, alighting_ratio=0.08),
        StationFlowConfig("FSP", base_arrival_rate_pax_per_min=72.0, alighting_ratio=0.14),
    ]
    gen = PoissonPassengerFlowGenerator(
        configs, FlowScenario(day_type=DayType.MON_THU), use_poisson=True
    )

    # 早高峰 (8:00)
    sim_time_ms = 8 * 3600 * 1000
    rate = gen.arrival_rate_pax_per_min("GGZ", "UP", sim_time_ms)
    expected = 60.0 * 1.45
    assert abs(rate - expected) < 0.01, f"GGZ AM peak rate {rate} != expected {expected}"

    # 平峰 (14:00)
    sim_time_ms2 = 14 * 3600 * 1000
    rate2 = gen.arrival_rate_pax_per_min("GGZ", "UP", sim_time_ms2)
    expected2 = 60.0 * 0.55
    assert abs(rate2 - expected2) < 0.01, f"GGZ midday rate {rate2} != expected {expected2}"

    # 周五早高峰
    gen_fri = PoissonPassengerFlowGenerator(
        configs, FlowScenario(day_type=DayType.FRI), use_poisson=True
    )
    rate3 = gen_fri.arrival_rate_pax_per_min("GGZ", "UP", sim_time_ms)
    expected3 = 60.0 * 1.45 * 1.08
    assert abs(rate3 - expected3) < 0.01, f"GGZ Fri AM rate {rate3} != expected {expected3}"

    # Poisson 采样
    samples = [gen.arrivals("GGZ", "UP", sim_time_ms, dt_sec=1.0) for _ in range(500)]
    lam = rate / 60.0
    mean = np.mean(samples)
    var = np.var(samples)
    assert abs(mean - lam) < 0.1, f"Poisson mean {mean:.3f} far from lambda {lam:.3f}"
    print(f"  Poisson mean={mean:.3f} (lambda={lam:.3f}), var={var:.3f}")

    print("  [PASS] Poisson generation")


def test_station_service_boarding():
    configs = [StationFlowConfig("GGZ", base_arrival_rate_pax_per_min=60.0, alighting_ratio=0.08)]
    gen = PoissonPassengerFlowGenerator(configs, use_poisson=True)
    svc = StationService(gen, DwellTimeConfig(base_dwell_sec=30.0, door_capacity_pax_per_sec=3.0))

    # 生成客流
    sim_time_ms = 8 * 3600 * 1000
    svc.ensure_platform("GGZ", "UP")
    result = svc.update_arrivals(sim_time_ms, dt_sec=300.0)  # 5 分钟
    waiting = svc.platforms[("GGZ", "UP")].waiting_pax
    print(f"  GGZ after 5min arrivals: waiting={waiting} (expected ~{60*1.45*5:.0f})")

    # 上下客
    train_load = TrainLoadState(train_id="T001", onboard_pax=100, capacity_pax=600)
    result, plan = svc.process_train_stop(
        sim_time_ms=sim_time_ms,
        station_id="GGZ",
        direction="UP",
        train_load=train_load,
    )
    print(f"  Boarding: {result.boarding}, Alighting: {result.alighting}")
    print(f"  Load after: {result.updated_load.onboard_pax}, LF={result.updated_load.load_factor:.3f}")
    print(f"  Dwell: planned={plan.planned_dwell_sec:.1f}s, estimated={plan.estimated_dwell_sec:.1f}s")
    assert result.boarding >= 0
    assert result.alighting == int(100 * 0.08)  # 8人下车
    assert result.updated_load.onboard_pax == 100 - result.alighting + result.boarding

    print("  [PASS] Station service boarding")


def test_timetable_generation():
    stations = [
        {"code": "GGZ", "name": "郭公庄", "mileageM": 0, "dwellSeconds": 30},
        {"code": "FSP", "name": "丰台科技园", "mileageM": 1200, "dwellSeconds": 30},
        {"code": "KYL", "name": "科怡路", "mileageM": 2200, "dwellSeconds": 30},
        {"code": "FTN", "name": "丰台南路", "mileageM": 3400, "dwellSeconds": 30},
        {"code": "FTD", "name": "丰台东大街", "mileageM": 4600, "dwellSeconds": 30},
    ]
    svc = TimetableService(headway_config=HeadwayConfig())
    tt = svc.generate(
        timetable_id="TT-UP-001",
        line_id="LINE9",
        direction="UP",
        stations=stations,
        start_time_s=6 * 3600,   # 6:00 首班
        end_time_s=8 * 3600,     # 8:00 末班
    )
    print(f"  Timetable: {tt.service_count} services generated")
    assert tt.service_count > 0, "No services generated"
    for svc_item in tt.services[:3]:
        print(f"    {svc_item.service_id}: {svc_item.origin_station_code} -> {svc_item.terminal_station_code}, "
              f"{svc_item.stop_count} stops, run time={svc_item.planned_run_time_s:.0f}s")

    # 早高峰 headway 应该更小
    first_dep = tt.services[0].stops[0].planned_departure_s
    second_dep = tt.services[1].stops[0].planned_departure_s
    gap = second_dep - first_dep
    print(f"  Gap between first two services: {gap:.0f}s")
    assert gap >= 90, f"Gap {gap}s < min headway 90s"

    print("  [PASS] Timetable generation")


def test_kpi_tracker():
    tracker = DispatchKpiTracker()
    tracker.record_arrival("T001", 0, "GGZ", planned_arrival_s=100, actual_arrival_s=110)
    tracker.record_arrival("T001", 1, "FSP", planned_arrival_s=250, actual_arrival_s=245)
    tracker.record_arrival("T001", 2, "KYL", planned_arrival_s=400, actual_arrival_s=530)  # 晚点 130s
    tracker.record_load(0.5)
    tracker.record_load(1.1)
    tracker.record_load(1.25)  # 超载
    tracker.record_boarding(50, 50 * 45.0)
    tracker.record_boarding(30, 30 * 60.0)

    snap = tracker.snapshot(current_time_s=600)
    print(f"  On-time rate: {snap.on_time_rate:.2%} (expected 2/3)")
    print(f"  Max load: {snap.max_load_factor:.3f}")
    print(f"  Overload events: {snap.overload_events}")
    print(f"  Avg wait: {snap.avg_wait_sec:.0f}s")
    print(f"  First delay at: {snap.first_delay_time_s}")

    assert snap.total_stops == 3
    assert snap.on_time_stops == 2
    assert snap.overload_events == 1
    assert snap.max_load_factor == 1.25

    print("  [PASS] KPI tracker")


if __name__ == "__main__":
    test_poisson_generation()
    test_station_service_boarding()
    test_timetable_generation()
    test_kpi_tracker()
    print("\nAll tests passed!")
