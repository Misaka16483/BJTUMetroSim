"""仿真报告生成器。

从 RunRecorder 的 SQLite 数据库提取一次仿真运行的多维数据，
生成结构化报告（含统计指标与图表时间序列），供前端渲染。

报告维度：
- summary   仿真概览
- dynamics  动力性能（能耗、速度、里程、再生电能）
- passenger 客流统计（进出站、滞留、拥挤）
- power     供电性能（电压、能耗、再生、变电站负载）
- kpi       调度 KPI（准点率、等待时间、满载率、追踪间隔）
- charts    各维度图表时间序列数据
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

try:  # 避免 kpi 模块异常影响报告（理论上不会失败）
    from app.domain.dispatch.kpi import DispatchKpiSnapshot
except Exception:  # pragma: no cover
    DispatchKpiSnapshot = None  # type: ignore

JsonDict = dict[str, Any]

_MAX_SAMPLE_POINTS = 120
_DEFAULT_TICK_SECONDS = 0.25


def _num(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt_duration(ms: float) -> str:
    if ms is None or ms < 0:
        ms = 0
    total = int(ms // 1000)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def _sim_time_label(start_ms: float, tick: int, tick_seconds: float) -> str:
    sim_ms = start_ms + tick * tick_seconds * 1000.0
    return _fmt_duration(sim_ms)


def _sample_indices(total: int, max_points: int) -> list[int]:
    if total <= max_points:
        return list(range(total))
    step = total / max_points
    return [int(i * step) for i in range(max_points)]


class ReportGenerator:
    """从 RunRecorder 生成结构化仿真报告。"""

    def __init__(self, recorder: Any) -> None:
        self.recorder = recorder

    def generate(self, run_id: int, kpi_snapshot: Any = None) -> JsonDict:
        conn = self.recorder.connection
        run_row = conn.execute(
            "SELECT id, name, started_at, metadata_json FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        if run_row is None:
            raise KeyError(f"run_id={run_id} not found")

        metadata = json.loads(run_row[3]) if run_row[3] else {}
        scenario_name = run_row[1]
        start_real = run_row[2]
        start_sim_ms = _num(metadata.get("startTimeMs"), 0.0) or 0.0
        tick_seconds = _num(metadata.get("tickSeconds"), _DEFAULT_TICK_SECONDS) or _DEFAULT_TICK_SECONDS

        report: JsonDict = {
            "runId": run_id,
            "scenarioName": scenario_name,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
        }

        report["summary"] = self._extract_summary(
            conn, run_id, scenario_name, start_real, start_sim_ms, tick_seconds, metadata
        )
        dynamics, dyn_charts = self._extract_dynamics(conn, run_id, start_sim_ms, tick_seconds)
        report["dynamics"] = dynamics
        passenger, pax_charts = self._extract_passenger(conn, run_id, start_sim_ms, tick_seconds)
        report["passenger"] = passenger
        power, power_charts = self._extract_power(conn, run_id, start_sim_ms, tick_seconds)
        report["power"] = power

        # 供电再生电能与动力侧保持一致（均来自列车累计 kWh）
        power["totalRegenGeneratedKwh"] = dynamics["regenGeneratedKwh"]
        power["totalRegenAbsorbedKwh"] = dynamics["regenAcceptedKwh"]
        power["totalRegenWastedKwh"] = dynamics["regenWastedKwh"]

        kpi = self._extract_kpi(kpi_snapshot)
        report["kpi"] = kpi
        if kpi.get("avgWaitSec") is not None:
            passenger["avgWaitingSec"] = kpi["avgWaitSec"]

        report["charts"] = {
            "dynamics": dyn_charts,
            "passenger": pax_charts,
            "power": power_charts,
        }
        return report

    # ───────────────────────── summary ─────────────────────────
    def _extract_summary(
        self,
        conn: Any,
        run_id: int,
        scenario_name: str,
        start_real: str,
        start_sim_ms: float,
        tick_seconds: float,
        metadata: JsonDict,
    ) -> JsonDict:
        total_events = conn.execute(
            "SELECT COUNT(*) FROM events WHERE run_id=?", (run_id,)
        ).fetchone()[0] or 0
        tick_row = conn.execute(
            "SELECT MIN(tick), MAX(tick), COUNT(DISTINCT tick) FROM events WHERE run_id=?", (run_id,)
        ).fetchone()
        total_ticks = tick_row[2] or 0

        station_count = conn.execute(
            "SELECT COUNT(DISTINCT station_id) FROM station_passenger_records WHERE run_id=?", (run_id,)
        ).fetchone()[0] or 0

        train_count = metadata.get("trainCount")
        if not train_count:
            trow = conn.execute(
                "SELECT COUNT(DISTINCT train_id) FROM train_load_records WHERE run_id=?", (run_id,)
            ).fetchone()
            train_count = trow[0] or 0

        end_sim_ms = start_sim_ms
        for tbl in (
            "station_passenger_records",
            "train_load_records",
            "power_records",
            "train_voltage_records",
            "substation_power_records",
            "regen_energy_records",
            "supercapacitor_power_records",
        ):
            row = conn.execute(
                f"SELECT MAX(sim_time_ms) FROM {tbl} WHERE run_id=?", (run_id,)
            ).fetchone()
            if row and row[0] is not None:
                end_sim_ms = max(end_sim_ms, float(row[0]))

        duration_ms = max(0, int(end_sim_ms - start_sim_ms))
        return {
            "runId": run_id,
            "scenarioName": scenario_name,
            "startTime": start_real,
            "startSimMs": start_sim_ms,
            "endSimMs": end_sim_ms,
            "durationMs": duration_ms,
            "durationStr": _fmt_duration(duration_ms),
            "trainCount": train_count,
            "stationCount": station_count,
            "totalEvents": total_events,
            "totalTicks": total_ticks,
        }

    # ───────────────────────── dynamics ─────────────────────────
    def _extract_dynamics(
        self, conn: Any, run_id: int, start_sim_ms: float, tick_seconds: float
    ) -> tuple[JsonDict, JsonDict]:
        rows = conn.execute(
            "SELECT tick, payload_json FROM events WHERE run_id=? AND topic='train.state' ORDER BY tick",
            (run_id,),
        ).fetchall()

        tick_data: dict[int, dict[str, dict]] = {}
        train_last: dict[str, dict] = {}
        speeds: list[float] = []
        max_speed = 0.0

        for tick, payload_json in rows:
            payload = json.loads(payload_json)
            tid = payload.get("trainId") or payload.get("train_id")
            if tid is None:
                continue
            speed_mps = _num(payload.get("speedMps", payload.get("speed_mps")))
            energy = _num(payload.get("energyKwh", payload.get("energy_kwh")))
            traction = _num(payload.get("tractionEnergyKwh", payload.get("traction_energy_kwh")))
            regen = _num(payload.get("regenGeneratedKwh", payload.get("regen_generated_kwh")))
            regen_acc = _num(payload.get("regenAcceptedKwh", payload.get("regen_accepted_kwh")))
            regen_waste = _num(payload.get("regenWastedKwh", payload.get("regen_wasted_kwh")))
            aux = _num(payload.get("auxiliaryEnergyKwh", payload.get("auxiliary_energy_kwh")))
            path_pos = _num(payload.get("pathPositionM", payload.get("path_position_m")))

            if speed_mps is not None:
                speeds.append(speed_mps)
                max_speed = max(max_speed, speed_mps)

            entry = {
                "speed": speed_mps,
                "energy": energy,
                "traction": traction,
                "regen": regen,
                "regenAcc": regen_acc,
                "regenWaste": regen_waste,
                "aux": aux,
                "pathPos": path_pos,
            }
            tick_data.setdefault(tick, {})[tid] = entry
            train_last[tid] = entry

        total_energy = sum(e["energy"] for e in train_last.values() if e["energy"] is not None) if train_last else 0.0
        total_traction = sum(e["traction"] for e in train_last.values() if e["traction"] is not None) if train_last else 0.0
        total_regen = sum(e["regen"] for e in train_last.values() if e["regen"] is not None) if train_last else 0.0
        total_regen_acc = sum(e["regenAcc"] for e in train_last.values() if e["regenAcc"] is not None) if train_last else 0.0
        total_regen_waste = sum(e["regenWaste"] for e in train_last.values() if e["regenWaste"] is not None) if train_last else 0.0
        total_aux = sum(e["aux"] for e in train_last.values() if e["aux"] is not None) if train_last else 0.0
        if total_aux == 0.0 and total_energy > 0:
            total_aux = total_energy - total_traction
        total_distance = sum(e["pathPos"] for e in train_last.values() if e["pathPos"] is not None) if train_last else 0.0
        regen_util = (total_regen_acc / total_regen) if total_regen > 0 else None
        avg_speed = (sum(speeds) / len(speeds)) if speeds else 0.0

        dynamics = {
            "totalEnergyKwh": round(total_energy, 3),
            "tractionEnergyKwh": round(total_traction, 3),
            "auxiliaryEnergyKwh": round(total_aux, 3),
            "regenGeneratedKwh": round(total_regen, 3),
            "regenAcceptedKwh": round(total_regen_acc, 3),
            "regenWastedKwh": round(total_regen_waste, 3),
            "regenUtilizationRate": round(regen_util, 4) if regen_util is not None else None,
            "maxSpeedKmh": round(max_speed * 3.6, 2) if max_speed else 0.0,
            "avgSpeedKmh": round(avg_speed * 3.6, 2) if avg_speed else 0.0,
            "totalDistanceKm": round(total_distance / 1000.0, 3) if total_distance else 0.0,
        }

        ticks = sorted(tick_data.keys())
        indices = _sample_indices(len(ticks), _MAX_SAMPLE_POINTS)
        sampled_ticks = [ticks[i] for i in indices]
        train_ids = sorted(train_last.keys())

        speed_series: list[JsonDict] = []
        energy_series: list[JsonDict] = []
        for tk in sampled_ticks:
            per = tick_data[tk]
            spoint = {"time": _sim_time_label(start_sim_ms, tk, tick_seconds)}
            for tid in train_ids:
                e = per.get(tid)
                spoint[tid] = round((e["speed"] or 0) * 3.6, 2) if e and e["speed"] is not None else 0
            speed_series.append(spoint)

            epoint = {"time": _sim_time_label(start_sim_ms, tk, tick_seconds)}
            tsum = asum = rsum = 0.0
            for tid in train_ids:
                e = per.get(tid)
                if e:
                    tsum += e["traction"] or 0
                    asum += e["aux"] or 0
                    rsum += e["regen"] or 0
            epoint["traction"] = round(tsum, 3)
            epoint["auxiliary"] = round(asum, 3)
            epoint["regen"] = round(rsum, 3)
            energy_series.append(epoint)

        comparison = [
            {"trainId": tid, "energyKwh": round((train_last[tid]["energy"] or 0), 3)}
            for tid in train_ids
        ]
        charts = {
            "speedTimeSeries": speed_series,
            "energyCumulative": energy_series,
            "trainEnergyComparison": comparison,
            "trainIds": train_ids,
        }
        return dynamics, charts

    # ───────────────────────── passenger ─────────────────────────
    def _extract_passenger(
        self, conn: Any, run_id: int, start_sim_ms: float, tick_seconds: float
    ) -> tuple[JsonDict, JsonDict]:
        rows = conn.execute(
            """
            SELECT station_id, direction,
                   MAX(arrivals), SUM(boarding), SUM(alighting), SUM(left_behind),
                   MAX(waiting), MAX(platform_density_pax_per_m2)
            FROM station_passenger_records WHERE run_id=? GROUP BY station_id, direction
            """,
            (run_id,),
        ).fetchall()

        total_arr = total_board = total_alight = total_left = 0
        station_stats: dict[str, JsonDict] = {}
        for sid, _direction, arr, board, alight, left, wait, dens in rows:
            arr = arr or 0
            board = board or 0
            alight = alight or 0
            left = left or 0
            total_arr += arr
            total_board += board
            total_alight += alight
            total_left += left
            if sid not in station_stats:
                station_stats[sid] = {
                    "arrivals": 0,
                    "boarding": 0,
                    "alighting": 0,
                    "waiting": 0,
                    "density": None,
                }
            station_stats[sid]["arrivals"] += arr
            station_stats[sid]["boarding"] += board
            station_stats[sid]["alighting"] += alight
            station_stats[sid]["waiting"] = max(station_stats[sid]["waiting"], wait or 0)
            if dens is not None and (station_stats[sid]["density"] is None or dens > station_stats[sid]["density"]):
                station_stats[sid]["density"] = dens

        peak_station = None
        peak_level = None
        best_density = -1.0
        for sid, st in station_stats.items():
            dens = st["density"] if st["density"] is not None else -1
            if dens > best_density:
                best_density = dens
                peak_station = sid
        if peak_station is None and station_stats:
            peak_station = max(station_stats, key=lambda s: station_stats[s]["arrivals"])
        if peak_station:
            lvl = conn.execute(
                "SELECT crowding_level FROM station_passenger_records WHERE run_id=? AND station_id=? ORDER BY id DESC LIMIT 1",
                (run_id, peak_station),
            ).fetchone()
            peak_level = lvl[0] if lvl and lvl[0] else None

        passenger = {
            "totalArrivals": total_arr,
            "totalBoardings": total_board,
            "totalAlightings": total_alight,
            "totalLeftBehind": total_left,
            "avgWaitingSec": None,
            "maxWaitingSec": None,
            "maxWaitingPax": max((st["waiting"] for st in station_stats.values()), default=0),
            "peakCrowdingStation": peak_station,
            "peakCrowdingLevel": peak_level,
        }

        # 进站人数趋势：arrivals 是累计值，按方向求相邻记录差值得到增量，再按站+时间分桶
        max_sim = conn.execute(
            "SELECT MAX(sim_time_ms) FROM station_passenger_records WHERE run_id=?", (run_id,)
        ).fetchone()[0] or 0
        edges = self._time_buckets(start_sim_ms, max_sim, tick_seconds)
        series_rows = conn.execute(
            """
            SELECT sim_time_ms, station_id, direction, arrivals
            FROM station_passenger_records
            WHERE run_id=? AND arrivals>0
            ORDER BY sim_time_ms, station_id, direction, id
            """,
            (run_id,),
        ).fetchall()
        last_arrival_by_key: dict[tuple[str, str], int] = {}
        bucket_station: dict[int, dict[str, float]] = {}
        for sim_ms, sid, direction, arr in series_rows:
            key = (sid, direction)
            prev = last_arrival_by_key.get(key, 0)
            delta = max(0, (arr or 0) - prev)
            last_arrival_by_key[key] = arr or 0
            if delta <= 0:
                continue
            b = self._bucket_index(edges, sim_ms)
            if b < 0:
                continue
            bucket_station.setdefault(b, {}).setdefault(sid, 0.0)
            bucket_station[b][sid] += delta


        station_ids = sorted(station_stats.keys())
        arrival_series: list[JsonDict] = []
        for b in range(len(edges) - 1):
            point = {"time": _fmt_duration(edges[b])}
            for sid in station_ids:
                point[sid] = round(bucket_station.get(b, {}).get(sid, 0.0), 1)
            arrival_series.append(point)

        ranking = sorted(
            station_stats.items(),
            key=lambda kv: (kv[1]["arrivals"] + kv[1]["boarding"]),
            reverse=True,
        )[:10]
        station_ranking = [
            {"station": sid, "total": st["arrivals"] + st["boarding"]} for sid, st in ranking
        ]
        boarding_alighting = [
            {"station": sid, "boarding": st["boarding"], "alighting": st["alighting"]}
            for sid, st in station_stats.items()
        ]
        charts = {
            "arrivalTimeSeries": arrival_series,
            "stationPassengerRanking": station_ranking,
            "boardingAlightingComparison": boarding_alighting,
        }
        return passenger, charts

    # ───────────────────────── power ─────────────────────────
    def _extract_power(
        self, conn: Any, run_id: int, start_sim_ms: float, tick_seconds: float
    ) -> tuple[JsonDict, JsonDict]:
        vrow = conn.execute(
            "SELECT AVG(voltage_v), MIN(voltage_v), MAX(voltage_v), COUNT(*) "
            "FROM train_voltage_records WHERE run_id=?",
            (run_id,),
        ).fetchone()
        avg_v, min_v, max_v, vcount = vrow[0], vrow[1], vrow[2], vrow[3] or 0
        overload = 0
        if vcount:
            overload = conn.execute(
                "SELECT COUNT(*) FROM train_voltage_records WHERE run_id=? "
                "AND voltage_level IN ('LOW','OVERLOAD','UNDERVOLTAGE','CRITICAL')",
                (run_id,),
            ).fetchone()[0] or 0

        prow = conn.execute(
            "SELECT SUM(energy_kwh) FROM power_records WHERE run_id=?", (run_id,)
        ).fetchone()
        consumed = prow[0] or 0.0

        power = {
            "totalPowerConsumedKwh": round(consumed, 3),
            "totalRegenGeneratedKwh": 0.0,
            "totalRegenAbsorbedKwh": 0.0,
            "totalRegenWastedKwh": 0.0,
            "totalLossesKwh": None,
            "avgVoltageV": round(avg_v, 2) if avg_v is not None else None,
            "minVoltageV": round(min_v, 2) if min_v is not None else None,
            "maxVoltageV": round(max_v, 2) if max_v is not None else None,
            "overloadEvents": int(overload),
        }

        # 电压趋势
        max_vsim = conn.execute(
            "SELECT MAX(sim_time_ms) FROM train_voltage_records WHERE run_id=?", (run_id,)
        ).fetchone()[0] or 0
        vedges = self._time_buckets(start_sim_ms, max_vsim, tick_seconds)
        vseries = conn.execute(
            "SELECT sim_time_ms, voltage_v FROM train_voltage_records WHERE run_id=?", (run_id,)
        ).fetchall()
        bucket_voltages: dict[int, list[float]] = {}
        for sim_ms, v in vseries:
            b = self._bucket_index(vedges, sim_ms)
            if b < 0:
                continue
            bucket_voltages.setdefault(b, []).append(v)
        voltage_series: list[JsonDict] = []
        for b in range(len(vedges) - 1):
            vals = bucket_voltages.get(b, [])
            point = {"time": _fmt_duration(vedges[b])}
            if vals:
                point["min"] = round(min(vals), 2)
                point["avg"] = round(sum(vals) / len(vals), 2)
                point["max"] = round(max(vals), 2)
            else:
                point["min"] = point["avg"] = point["max"] = None
            voltage_series.append(point)

        # 功率趋势
        max_psim = conn.execute(
            "SELECT MAX(sim_time_ms) FROM power_records WHERE run_id=?", (run_id,)
        ).fetchone()[0] or 0
        pedges = self._time_buckets(start_sim_ms, max_psim, tick_seconds)
        pseries = conn.execute(
            "SELECT sim_time_ms, requested_power_kw, absorbed_regen_kw FROM power_records WHERE run_id=?",
            (run_id,),
        ).fetchall()
        bucket_power: dict[int, list[tuple[float, float]]] = {}
        for sim_ms, req, abs_r in pseries:
            b = self._bucket_index(pedges, sim_ms)
            if b < 0:
                continue
            bucket_power.setdefault(b, []).append((req or 0, abs_r or 0))
        power_series: list[JsonDict] = []
        for b in range(len(pedges) - 1):
            vals = bucket_power.get(b, [])
            point = {"time": _fmt_duration(pedges[b])}
            if vals:
                point["traction"] = round(sum(x[0] for x in vals) / len(vals), 2)
                point["regen"] = round(sum(x[1] for x in vals) / len(vals), 2)
            else:
                point["traction"] = 0
                point["regen"] = 0
            power_series.append(point)

        # 变电站负载
        sub_rows = conn.execute(
            "SELECT substation_id, AVG(load_ratio) FROM substation_power_records "
            "WHERE run_id=? GROUP BY substation_id",
            (run_id,),
        ).fetchall()
        substation_load = [
            {"substation": s, "avgLoad": round(r, 4)} for s, r in sub_rows
        ]

        charts = {
            "voltageTimeSeries": voltage_series,
            "powerTimeSeries": power_series,
            "substationLoad": substation_load,
        }
        return power, charts

    # ───────────────────────── kpi ─────────────────────────
    def _extract_kpi(self, kpi_snapshot: Any) -> JsonDict:
        if kpi_snapshot is None:
            return {
                "available": False,
                "onTimeRate": None,
                "avgWaitSec": None,
                "avgLoadFactor": None,
                "maxLoadFactor": None,
                "overloadEvents": None,
                "headwayViolations": None,
                "recoveryTimeSec": None,
            }
        if hasattr(kpi_snapshot, "to_dict"):
            d = kpi_snapshot.to_dict()
        else:
            d = dict(kpi_snapshot)
        d["available"] = True
        return d

    # ───────────────────────── helpers ─────────────────────────
    def _time_buckets(self, start_ms: float, end_ms: float, _tick_seconds: float) -> list[float]:
        if end_ms <= start_ms:
            return [start_ms, start_ms + 1.0]
        span = end_ms - start_ms
        bucket_ms = max(1.0, span / _MAX_SAMPLE_POINTS)
        return [start_ms + i * bucket_ms for i in range(_MAX_SAMPLE_POINTS + 1)]

    def _bucket_index(self, edges: list[float], sim_ms: float) -> int:
        if sim_ms < edges[0] or sim_ms > edges[-1]:
            return -1
        for i in range(len(edges) - 1):
            if edges[i] <= sim_ms <= edges[i + 1]:
                return i
        return len(edges) - 2
