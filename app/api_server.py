"""Phase 1 API Server — 接入仿真引擎 + WebSocket 推送."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from app.adapters.cab import DriverCabHardwareController
from app.adapters.vision import COMPACT_LAYOUT, VisionUdpPublisher
from app.core.engine import SimulationEngine
from app.domain.line.services import LineMapRepository, LineScope, TrackQueryService
from app.domain.power.line9_topology import load_line9_power_network
from app.domain.power.experiments import PowerExperimentRegistry
from app.domain.operations.member_c_demo import MemberCDemoRunner
from app.domain.operations.member_d_demo import Phase2MemberDDemoRunner
from app.domain.operations.phase0_member_d_demo import Phase0MemberDDemoRunner
from app.domain.operations.phase1_member_d_demo import Phase1MemberDDemoRunner
from app.domain.operations.phase2_member_d_full_demo import Phase2MemberDFullDemoRunner
from app.domain.station.independent_sim import IndependentPassengerSimulation
from app.infra.recorder import RunRecorder


JsonDict = dict[str, Any]

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE = ROOT / "data" / "cache" / "line_map.json"
DEFAULT_RUN_DIR = ROOT / "outputs" / "runs"
REPO_STATIONS = ROOT / "data" / "line9" / "stations.csv"
WORKSPACE_STATIONS = (
    ROOT / "external" / "BJTUMetroSim" / "MetroDynamicsJavaDemo" / "data" / "stations.csv"
)
DEFAULT_STATIONS = REPO_STATIONS if REPO_STATIONS.exists() else WORKSPACE_STATIONS
DEFAULT_SCENARIO = ROOT / "data" / "scenarios" / "line9_interactive.json"
DEFAULT_MAINLINE_SCOPE = ROOT / "data" / "scenarios" / "line9_mainline_scope.json"
DEFAULT_POWER_TOPOLOGY = ROOT / "data" / "scenarios" / "line9_power_topology.json"

LINE9_COLOR = "#8FC31F"
LINE9_COORDS: dict[str, tuple[float, float]] = {
    "GGZ": (39.814322, 116.301889),
    "FSP": (39.825233, 116.297176),
    "KYL": (39.832480, 116.297432),
    "FTN": (39.840444, 116.296748),
    "FTD": (39.855111, 116.293857),
    "QLZ": (39.866773, 116.294292),
    "LLQ": (39.880239, 116.302808),
    "LLE": (39.886886, 116.315142),
    "BWR": (39.894706, 116.321218),
    "JBG": (39.907422, 116.323380),
    "BDZ": (39.923818, 116.325762),
    "BQS": (39.933021, 116.325680),
    "GTG": (39.943114, 116.325190),
}


class Line9DataService:
    """静态 9号线数据服务（保持不变）."""

    def __init__(
        self,
        cache_path: Path = DEFAULT_CACHE,
        stations_path: Path = DEFAULT_STATIONS,
        run_dir: Path = DEFAULT_RUN_DIR,
        mainline_scope_path: Path = DEFAULT_MAINLINE_SCOPE,
    ) -> None:
        self.cache_path = cache_path
        self.stations_path = stations_path
        self.run_dir = run_dir
        self.mainline_scope_path = mainline_scope_path
        self._line_map: JsonDict | None = None
        self._stations: list[JsonDict] | None = None
        self._mainline_scope: LineScope | None = None
        self._sim_runner: MemberCDemoRunner | None = None
        self._power_topology: JsonDict | None = None
        self._passenger_sim: IndependentPassengerSimulation | None = None

    @property
    def passenger_sim(self) -> IndependentPassengerSimulation:
        if self._passenger_sim is None:
            self._passenger_sim = IndependentPassengerSimulation(self.stations)
        return self._passenger_sim

    @property
    def line_map(self) -> JsonDict:
        if self._line_map is None:
            self._line_map = LineMapRepository(self.cache_path).load()
        return self._line_map

    @property
    def stations(self) -> list[JsonDict]:
        if self._stations is None:
            self._stations = self._load_station_catalog()
        return self._stations

    @property
    def mainline_scope(self) -> LineScope:
        if self._mainline_scope is None:
            self._mainline_scope = LineScope.load(self.mainline_scope_path)
        return self._mainline_scope

    def health(self) -> JsonDict:
        validation = self.line_map.get("validation", {})
        return {
            "ok": True,
            "service": "BJTUMetroSim Phase1 API",
            "lineId": "9",
            "cache": str(self.cache_path),
            "cacheExists": self.cache_path.exists(),
            "validationOk": validation.get("ok"),
            "mainlineScopeId": self.mainline_scope.scope_id,
            "mainlineSegmentCount": len(self.mainline_scope.segment_ids),
            "generatedAt": datetime.now(timezone.utc).isoformat(),
        }

    def macro_line(self) -> JsonDict:
        station_mappings = self.station_mappings()
        stations = [
            {
                "name": item["stationName"],
                "lat": item["lat"],
                "lng": item["lng"],
                "code": item["stationCode"],
                "mileageM": item["mileageM"],
                "platformIds": item["platformIds"],
                "platformSegmentIds": item["platformSegmentIds"],
            }
            for item in station_mappings
        ]
        coordinates = [[[station["lat"], station["lng"]] for station in stations]]
        return {
            "id": "9",
            "name": "9号线",
            "color": LINE9_COLOR,
            "coordinates": coordinates,
            "stations": stations,
            "source": "phase1-backend",
        }

    def station_mappings(self) -> list[JsonDict]:
        platforms_by_mileage: dict[float, list[JsonDict]] = {}
        for platform in self.line_map.get("platforms", []):
            mileage = platform.get("mileageM")
            if mileage is None or mileage < 100:
                continue
            platforms_by_mileage.setdefault(round(float(mileage), 3), []).append(platform)
        mappings: list[JsonDict] = []
        for station in self.stations:
            mileage = round(float(station["mileageM"]), 3)
            platforms = sorted(
                platforms_by_mileage.get(mileage, []), key=lambda item: item["id"]
            )
            lat, lng = LINE9_COORDS.get(station["code"], (0.0, 0.0))
            platform_ids = [item["id"] for item in platforms]
            segment_ids = [
                item["segmentId"]
                for item in platforms
                if item.get("segmentId") is not None
            ]
            mappings.append({
                "lineId": "9",
                "stationId": station["id"],
                "stationCode": station["code"],
                "stationName": station["name"],
                "mileageM": station["mileageM"],
                "speedLimitToNextKmh": station["speedLimitToNextKmh"],
                "dwellSeconds": station["dwellSeconds"],
                "lat": lat,
                "lng": lng,
                "platformIds": platform_ids,
                "platformSegmentIds": segment_ids,
                "platforms": [
                    {
                        "id": item["id"],
                        "segmentId": item["segmentId"],
                        "direction": item.get("direction"),
                        "mileageM": item.get("mileageM"),
                    }
                    for item in platforms
                ],
            })
        return mappings

    def track_map(self) -> JsonDict:
        station_mappings = self.station_mappings()
        station_by_platform = {
            platform_id: station
            for station in station_mappings
            for platform_id in station["platformIds"]
        }
        platform_by_seg = {
            platform["segmentId"]: platform
            for platform in self.line_map.get("platforms", [])
            if platform.get("segmentId") is not None and platform.get("mileageM", 0) >= 100
        }
        return {
            "lineId": "9",
            "name": "9号线轨道级视图",
            "scope": {
                "activeForSimulation": self.mainline_scope.scope_id,
                "mainlineSegmentCount": len(self.mainline_scope.segment_ids),
                "fullMapRetained": True,
                "segmentIds": sorted(self.mainline_scope.segment_ids),
            },
            "lengthM": self.stations[-1]["mileageM"] - self.stations[0]["mileageM"],
            "counts": {
                "segments": len(self.line_map.get("segments", [])),
                "signals": len(self.line_map.get("signals", [])),
                "platforms": len(self.line_map.get("platforms", [])),
                "balises": len(self.line_map.get("balises", [])),
                "speedRestrictions": len(self.line_map.get("speedRestrictions", [])),
                "gradients": len(self.line_map.get("gradients", [])),
                "routes": len(self.line_map.get("routes", [])),
                "axleSections": len(self.line_map.get("axleSections", [])),
                "logicalSections": len(self.line_map.get("logicalSections", [])),
            },
            "stations": station_mappings,
            "segments": [
                {
                    "id": item["id"],
                    "lengthM": item.get("lengthM"),
                    "startEndpointId": item.get("startEndpointId"),
                    "endEndpointId": item.get("endEndpointId"),
                    "nextSegmentIds": [
                        next_id
                        for next_id in [
                            item.get("startForwardSegId"),
                            item.get("startDivergingSegId"),
                            item.get("endForwardSegId"),
                            item.get("endDivergingSegId"),
                        ]
                        if next_id is not None
                    ],
                    "ciAreaId": item.get("ciAreaId"),
                    "zcAreaId": item.get("zcAreaId"),
                    "stationName": self._station_name_for_seg(
                        item["id"], platform_by_seg, station_by_platform
                    ),
                }
                for item in self.line_map.get("segments", [])
            ],
            "platforms": self._pick_fields(
                self.line_map.get("platforms", []),
                ["id", "mileageM", "segmentId", "direction", "clearPassengerFlag"],
            ),
            "signals": self._pick_fields(
                self.line_map.get("signals", []),
                ["id", "name", "type", "segmentId", "offsetM", "direction", "aspectInfo"],
            ),
            "speedRestrictions": self._pick_fields(
                self.line_map.get("speedRestrictions", []),
                ["id", "segmentId", "startOffsetM", "endOffsetM", "speedLimitMps"],
            ),
            "gradients": self._pick_fields(
                self.line_map.get("gradients", []),
                ["id", "startSegmentId", "startOffsetM", "endSegmentId", "endOffsetM", "slopePermille"],
            ),
        }

    def power_topology(self) -> JsonDict:
        if self._power_topology is None:
            self._power_topology = load_line9_power_network(DEFAULT_POWER_TOPOLOGY).topology_dict()
        return self._power_topology

    def segment_context(self, seg_id: int) -> JsonDict:
        service = TrackQueryService(self.line_map)
        return {
            "segment": service.get_segment(seg_id),
            "nextSegments": service.get_next_segments(seg_id),
            "speedLimit": service.get_speed_limit(seg_id, 0.0),
            "gradient": service.get_gradient(seg_id, 0.0),
            "nearestPlatform": service.get_nearest_platform(seg_id, 0.0),
            "nextSignal": service.get_next_signal(seg_id, 0.0),
        }

    def member_d_demo(self) -> JsonDict:
        db_path = self.run_dir / "phase2_member_d_demo.sqlite"
        summary = Phase2MemberDDemoRunner(db_path).run()
        return {
            "ok": True,
            "lineId": "9",
            "phase": 2,
            "module": "member-d",
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
        }

    def member_d_phase0_demo(self) -> JsonDict:
        db_path = self.run_dir / "phase0_member_d_demo.sqlite"
        return Phase0MemberDDemoRunner(db_path).run()

    def member_d_phase1_demo(self) -> JsonDict:
        db_path = self.run_dir / "phase1_member_d_demo.sqlite"
        return Phase1MemberDDemoRunner(db_path).run()

    def member_d_phase2_full_demo(self) -> JsonDict:
        db_path = self.run_dir / "phase2_member_d_full_demo.sqlite"
        return Phase2MemberDFullDemoRunner(db_path).run()

    @property
    def sim_runner(self) -> MemberCDemoRunner:
        if self._sim_runner is None:
            self._sim_runner = MemberCDemoRunner(self.cache_path)
        return self._sim_runner

    def member_c_state(self) -> JsonDict:
        return self.sim_runner.state_snapshot()

    def member_c_step(self) -> JsonDict:
        self.sim_runner.step()
        return self.sim_runner.state_snapshot()

    def member_c_reset(self) -> JsonDict:
        self._sim_runner = MemberCDemoRunner(self.cache_path)
        return self.sim_runner.state_snapshot()

    def member_c_place_train(self, segment_id: int) -> JsonDict:
        self._sim_runner = MemberCDemoRunner(self.cache_path)
        return self._sim_runner.place_manual_train(segment_id)

    def member_c_place_train_for_route(self, route_id: str) -> JsonDict:
        self._sim_runner = MemberCDemoRunner(self.cache_path)
        return self._sim_runner.place_train_for_route(route_id)

    def member_c_request_manual_route(self, route_id: str | None = None) -> JsonDict:
        return self.sim_runner.request_manual_route(route_id)

    def member_c_static_routes(self) -> JsonDict:
        """返回全部进路静态数据 + 完整拓扑图（基于原始 Segment 连接字段）。

        不计算里程——直接用 Seg 的四向连接指针做 BFS，
        分出主干（row=0）和侧枝（row=1,2,3...），
        每个 Seg 返回行号和序号（col），前端按行画轨道。
        """
        from app.domain.interlocking.route_catalog import RouteCatalog
        cat = RouteCatalog(self.line_map)

        seg_by_id = {s["id"]: s for s in self.line_map.get("segments", [])}

        # 1) 从郭公庄上行站台的 Seg 找主干入口 → 沿 endForwardSegId 走
        up_platforms = sorted(
            [p for p in self.line_map.get("platforms", [])
             if p.get("direction") == "0x55"],
            key=lambda p: p.get("mileageM", 0),
        )
        start_seg = int(up_platforms[0]["segmentId"]) if up_platforms else 13

        # 2) BFS 分配 (row, col)：主干 row=0，分支 row+1
        segs_out: list[dict] = []
        assigned: dict[int, tuple[int, int]] = {}  # seg_id → (row, col)

        def walk_chain(seg_id: int, row: int) -> int:
            """沿 endForwardSegId 走一条链，返回分配的列数。
            栈式处理：先走完当前链，再递归分支（不用Python递归栈以避免主干被分支抢占）。"""
            chain_segs: list[tuple[int, int, bool, int | None]] = []  # (sid, col, has_div, div_seg)
            sid = seg_id
            col = 0
            while sid is not None and sid not in assigned:
                seg = seg_by_id.get(sid)
                efd = seg.get("endForwardSegId") if seg else None
                edv = seg.get("endDivergingSegId") if seg else None
                edv_int = int(edv) if edv is not None else None
                chain_segs.append((sid, col, edv_int is not None, edv_int))
                col += 1
                nxt = int(efd) if efd is not None else None
                sid = nxt if nxt is not None and nxt not in assigned else None
            # 先分配当前链所有Seg
            for sid2, col2, has_div2, div2 in chain_segs:
                assigned[sid2] = (row, col2)
                seg = seg_by_id.get(sid2)
                seg_len = float(seg.get("lengthM", 0)) if seg else 0
                stn = None
                for p in self.line_map.get("platforms", []):
                    if p.get("segmentId") == sid2 and p.get("direction") == "0x55":
                        stn = p.get("id"); break
                segs_out.append({
                    "id": sid2, "row": row, "col": col2,
                    "len": seg_len, "sw": has_div2, "stn": stn,
                    "endFwd": int(seg.get("endForwardSegId")) if seg and seg.get("endForwardSegId") is not None else None,
                    "endDiv": div2,
                })
            # 然后递归分支（此时主干Seg已分配完毕，不会被分支抢占）
            for sid2, col2, has_div2, div2 in chain_segs:
                if div2 is not None and div2 not in assigned:
                    walk_chain(div2, row + 1)
            return col

        walk_chain(start_seg, 0)

        # 3) 处理独立岛屿（无法从郭公庄站台 BFS 到达的 Seg）—— 按连通分量分组
        unassigned = [sid for sid in seg_by_id if sid not in assigned]
        island_row = 10  # 孤岛从row=10开始
        for root_sid in unassigned:
            if root_sid in assigned:
                continue
            # 从这个根出发 BFS 一个连通分量
            walk_chain(root_sid, island_row)
            island_row += 1

        # 4) 信号（assigned 的 Seg 上标注）
        # The running demo only contains the train's current mainline chain.
        # Rebuild the presentation topology from every imported Seg so depot,
        # opposite-direction, and non-simulated branches stay visible.
        seg_by_id = {
            int(segment["id"]): segment
            for segment in self.line_map.get("segments", [])
            if segment.get("id") is not None
        }
        # The raw platform table contains non-operational placeholder records
        # (for example S22 / platform 29 has mileage 1m and 0xff flags).  Keep
        # those IDs for diagnostics, but only mapped passenger platforms may
        # drive the green platform rendering or topology start controls.
        raw_platform_by_seg: dict[int, list[int]] = {}
        platform_by_seg: dict[int, list[int]] = {}
        operational_platform_ids = {
            int(platform_id)
            for station in self.station_mappings()
            for platform_id in station.get("platformIds", [])
        }
        for platform in self.line_map.get("platforms", []):
            segment_id = platform.get("segmentId")
            platform_id = platform.get("id")
            if segment_id is None or platform_id is None:
                continue
            segment_key = int(segment_id)
            platform_key = int(platform_id)
            raw_platform_by_seg.setdefault(segment_key, []).append(platform_key)
            if platform_key in operational_platform_ids:
                platform_by_seg.setdefault(segment_key, []).append(platform_key)
        def seg_ref(segment: JsonDict, field: str) -> int | None:
            value = segment.get(field)
            return int(value) if value is not None and int(value) in seg_by_id else None

        # A row is a continuous normal path. Each diverging path starts on its
        # own row beside the point that creates it, which produces a stable,
        # complete schematic instead of a misleading single-line chain.
        assigned = {}
        pending: list[tuple[int, int, int]] = []
        roots = sorted(
            sid for sid, segment in seg_by_id.items()
            if seg_ref(segment, "startForwardSegId") is None
        )
        for row, root_id in enumerate(roots):
            pending.append((root_id, row, 0))

        next_row = len(pending)
        while pending:
            start_id, row, start_col = pending.pop(0)
            sid = start_id
            col = start_col
            while sid is not None and sid not in assigned:
                segment = seg_by_id[sid]
                assigned[sid] = (row, col)
                diverging_id = seg_ref(segment, "endDivergingSegId")
                if diverging_id is not None and diverging_id not in assigned:
                    pending.append((diverging_id, next_row, col + 1))
                    next_row += 1
                sid = seg_ref(segment, "endForwardSegId")
                col += 1
            if not pending:
                remaining = sorted(set(seg_by_id) - set(assigned))
                if remaining:
                    pending.append((remaining[0], next_row, 0))
                    next_row += 1

        # The first pass preserves every directed relation but gives each branch
        # its own row.  Compact those row fragments into reusable side lanes.
        # The two longest chains remain the main tracks; fragments with
        # non-overlapping horizontal spans can share a lane without appearing
        # connected.  Fully isolated fragments are grouped after a visible gap.
        row_segments: dict[int, list[int]] = {}
        for sid, (row, _) in assigned.items():
            row_segments.setdefault(row, []).append(sid)
        for segment_ids in row_segments.values():
            segment_ids.sort(key=lambda item: assigned[item][1])

        main_rows = {
            row for row, _ in sorted(
                row_segments.items(), key=lambda item: (-len(item[1]), item[0])
            )[:2]
        }
        row_by_segment = {sid: row for sid, (row, _) in assigned.items()}
        fragment_neighbors: dict[int, set[int]] = {
            row: set() for row in row_segments
        }
        for sid, segment in seg_by_id.items():
            source_row = row_by_segment[sid]
            for field in (
                "startForwardSegId", "startDivergingSegId",
                "endForwardSegId", "endDivergingSegId",
            ):
                neighbor = seg_ref(segment, field)
                if neighbor is None:
                    continue
                target_row = row_by_segment[neighbor]
                if target_row != source_row:
                    fragment_neighbors[source_row].add(target_row)

        def fragment_span(row: int) -> tuple[int, int]:
            columns = [assigned[sid][1] for sid in row_segments[row]]
            return min(columns), max(columns)

        side_rows = sorted(
            row for row in row_segments
            if row not in main_rows and fragment_neighbors[row]
        )
        side_rows.sort(key=lambda row: (*fragment_span(row), row))
        lane_spans: list[list[tuple[int, int]]] = []
        compact_row: dict[int, int] = {}
        for row in side_rows:
            start, end = fragment_span(row)
            for lane_index, spans in enumerate(lane_spans):
                if all(end + 2 < used_start or start - 2 > used_end for used_start, used_end in spans):
                    spans.append((start, end))
                    compact_row[row] = 2 + lane_index
                    break
            else:
                lane_spans.append([(start, end)])
                compact_row[row] = 2 + len(lane_spans) - 1

        main_max_column = max(
            assigned[sid][1]
            for row in main_rows for sid in row_segments[row]
        )
        island_rows = sorted(
            row for row in row_segments
            if row not in main_rows and not fragment_neighbors[row]
        )
        island_row = 2 + len(lane_spans) + (1 if lane_spans else 0)
        island_cursor = main_max_column + 6
        compact_column: dict[int, int] = {}
        for row in island_rows:
            start, end = fragment_span(row)
            compact_row[row] = island_row
            for sid in row_segments[row]:
                compact_column[sid] = island_cursor + assigned[sid][1] - start
            island_cursor += end - start + 5

        for sid, (row, column) in list(assigned.items()):
            if row in main_rows:
                assigned[sid] = (0 if row == min(main_rows) else 1, column)
            else:
                assigned[sid] = (compact_row[row], compact_column.get(sid, column))

        segs_out = []
        for sid, segment in sorted(seg_by_id.items()):
            row, col = assigned[sid]
            segs_out.append({
                "id": sid,
                "lengthM": float(segment.get("lengthM") or 0),
                "row": row,
                "col": col,
                "platformIds": platform_by_seg.get(sid, []),
                "rawPlatformIds": raw_platform_by_seg.get(sid, []),
                "startForward": seg_ref(segment, "startForwardSegId"),
                "startDiverging": seg_ref(segment, "startDivergingSegId"),
                "endForward": seg_ref(segment, "endForwardSegId"),
                "endDiverging": seg_ref(segment, "endDivergingSegId"),
            })

        signals = []
        for s in self.line_map.get("signals", []):
            sig_id = s.get("id")
            seg_id = s.get("segmentId")
            if sig_id is None or seg_id is None or int(seg_id) not in assigned:
                continue
            signals.append({
                "id": int(sig_id), "name": str(s.get("name", "")),
                "segId": int(seg_id),
                "offsetM": float(s.get("offsetM") or 0),
                "direction": str(s.get("direction", "")),
                "type": s.get("type"),
            })

        # 5) 道岔
        switches = []
        for sw_id_str in cat.switch_ids:
            sw = cat.get_switch(sw_id_str)
            if sw is None: continue
            in_assigned = sw.frog_seg_id is not None and sw.frog_seg_id in assigned
            switches.append({
                "id": sw.switch_id, "name": sw.name,
                "frogSeg": sw.frog_seg_id, "normSeg": sw.normal_seg_id,
                "revSeg": sw.reverse_seg_id,
                "onMain": in_assigned,
            })

        # 6) 计轴区段→Seg 映射 + 进路
        axle_segs = {}
        for a in self.line_map.get("axleSections", []):
            aid = a.get("id")
            sl = [int(s) for s in a.get("segmentIds", []) if s is not None]
            if aid is not None and sl: axle_segs[str(aid)] = sl

        def _sig_seg(sig_id):
            for s in self.line_map.get("signals", []):
                if s.get("id") == sig_id:
                    rs = s.get("segmentId")
                    return int(rs) if rs is not None else 0
            return 0

        def _sig_name(sig_id):
            for s in self.line_map.get("signals", []):
                if s.get("id") == sig_id: return str(s.get("name", ""))
            return ""

        topology_neighbors: dict[int, set[int]] = {sid: set() for sid in seg_by_id}
        for sid, segment in seg_by_id.items():
            for field in (
                "startForwardSegId", "startDivergingSegId",
                "endForwardSegId", "endDivergingSegId",
            ):
                neighbor = seg_ref(segment, field)
                if neighbor is not None:
                    topology_neighbors[sid].add(neighbor)
                    topology_neighbors[neighbor].add(sid)

        def _ordered_route_segments(
            segment_ids: list[int], start_signal_id: int, end_signal_id: int,
        ) -> tuple[list[int], bool]:
            """Order a route's Seg set from its entry signal to its exit signal.

            Route rows identify axle sections, not a traversal sequence.  The
            order must therefore be recovered from the imported Seg topology.
            """
            covered = list(dict.fromkeys(segment_ids))
            if len(covered) < 2:
                return covered, True
            covered_set = set(covered)
            start_seg = _sig_seg(start_signal_id)
            end_seg = _sig_seg(end_signal_id)

            def attached_to(signal_seg: int) -> list[int]:
                if signal_seg in covered_set:
                    return [signal_seg]
                return [
                    sid for sid in covered
                    if signal_seg in topology_neighbors[sid]
                ]

            start_candidates = attached_to(start_seg)
            end_candidates = attached_to(end_seg)
            endpoints = [
                sid for sid in covered
                if len(topology_neighbors[sid] & covered_set) <= 1
            ]
            if not start_candidates:
                start_candidates = endpoints or [covered[0]]
            if not end_candidates:
                end_candidates = endpoints or [covered[-1]]

            best_path: list[int] | None = None
            for source in start_candidates:
                queue: list[list[int]] = [[source]]
                visited = {source}
                while queue:
                    path = queue.pop(0)
                    current = path[-1]
                    if current in end_candidates:
                        if best_path is None or len(path) > len(best_path):
                            best_path = path
                        continue
                    for neighbor in sorted(topology_neighbors[current] & covered_set):
                        if neighbor not in visited:
                            visited.add(neighbor)
                            queue.append(path + [neighbor])

            if best_path is not None and set(best_path) == covered_set:
                return best_path, True
            return covered, False

        routes = []
        for rid in cat.route_ids:
            rd = cat.get(rid)
            if rd is None: continue
            raw_path_segs = []
            for sec_id in rd.axle_section_ids:
                for s in axle_segs.get(sec_id, []):
                    if s not in raw_path_segs: raw_path_segs.append(s)
            path_segs, path_order_complete = _ordered_route_segments(
                raw_path_segs, rd.start_signal_id, rd.end_signal_id,
            )
            routes.append({
                "id": rid, "name": rd.name,
                "startSig": rd.start_signal_id, "startSigName": _sig_name(rd.start_signal_id),
                "endSig": rd.end_signal_id, "endSigName": _sig_name(rd.end_signal_id),
                "pathSegs": path_segs,
                "rawPathSegs": raw_path_segs,
                "pathOrderComplete": path_order_complete,
                "switches": rd.required_switches,
                "conflicts": sorted(cat.conflicts_with(rid)),
                "axleSections": rd.axle_section_ids,
            })

        return {
            "segments": segs_out, "signals": signals, "switches": switches,
            "routes": routes,
            "layout": {
                "rows": max(row for row, _ in assigned.values()) + 1,
                "segmentCount": len(segs_out),
            },
        }

    def _load_station_catalog(self) -> list[JsonDict]:
        with self.stations_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        return [
            {
                "id": int(row["id"]),
                "code": row["code"],
                "name": row["name"],
                "mileageM": float(row["mileage_m"]),
                "speedLimitToNextKmh": int(row["speed_limit_to_next_kmh"]),
                "dwellSeconds": int(row["dwell_s"]),
            }
            for row in rows
        ]

    @staticmethod
    def _pick_fields(items: list[JsonDict], fields: list[str]) -> list[JsonDict]:
        return [{field: item.get(field) for field in fields} for item in items]

    @staticmethod
    def _station_name_for_seg(
        seg_id: int,
        platform_by_seg: dict[int, JsonDict],
        station_by_platform: dict[int, JsonDict],
    ) -> str | None:
        platform = platform_by_seg.get(seg_id)
        if not platform:
            return None
        station = station_by_platform.get(platform["id"])
        return station["stationName"] if station else None


# ═══════════════════════════════════════════════════════════════
#  WebSocket Server (port 8001)
# ═══════════════════════════════════════════════════════════════

class WebSocketBroadcaster:
    """WebSocket 推送服务器 — 引擎每个 tick 向所有客户端广播状态."""

    def __init__(self, host: str, port: int, state_provider: Callable[[], JsonDict | None] | None = None) -> None:
        self.host = host
        self.port = port
        self.state_provider = state_provider
        self._clients: set = set()
        self._server = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            import websockets
        except ImportError:
            print("[ws] websockets 未安装，跳过 WebSocket 服务")
            return

        async def handler(websocket):
            self._clients.add(websocket)
            try:
                if self.state_provider is not None:
                    initial = self.state_provider()
                    if initial is not None:
                        await websocket.send(json.dumps(initial, ensure_ascii=False))
                async for _ in websocket:
                    pass  # 客户端发来的消息忽略
            finally:
                self._clients.discard(websocket)

        async def serve():
            self._server = await websockets.serve(handler, self.host, self.port)
            print(f"[ws] WebSocket server started on ws://{self.host}:{self.port}")
            last_sequence = -1
            while self._server is not None:
                if self.state_provider is not None and self._clients:
                    payload = self.state_provider()
                    sequence = int(payload.get("snapshotSequence", -1)) if payload else -1
                    if payload is not None and sequence != last_sequence:
                        last_sequence = sequence
                        await self.broadcast(json.dumps(payload, ensure_ascii=False))
                await asyncio.sleep(0.05)

        try:
            asyncio.run(serve())
        except RuntimeError:
            # asyncio.run() 在已有事件循环时会报错
            pass

    async def broadcast(self, message: str) -> None:
        if not self._clients:
            return
        try:
            import websockets
        except ImportError:
            return
        tasks = [client.send(message) for client in list(self._clients)]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def broadcast_sync(self, message: str) -> None:
        """同步包装，供 HTTP handler 调用."""
        try:
            asyncio.run(self.broadcast(message))
        except RuntimeError:
            pass


# ═══════════════════════════════════════════════════════════════
#  HTTP Router
# ═══════════════════════════════════════════════════════════════

class ApiHandler(BaseHTTPRequestHandler):
    service: Line9DataService
    engine: SimulationEngine | None = None
    ws_broadcaster: WebSocketBroadcaster | None = None
    experiment_registry: PowerExperimentRegistry | None = None
    cab_hardware_controller: DriverCabHardwareController | None = None
    cab_hardware_lock = threading.RLock()
    vision_publisher: VisionUdpPublisher | None = None
    vision_hardware_lock = threading.RLock()
    replay_lock = threading.RLock()
    replay_run_id: int | None = None
    replay_snapshot: JsonDict | None = None

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"

            if path == "/api/health":
                self._send_json(self._health())
            elif path == "/api/lines/9/macro":
                self._send_json(self.service.macro_line())
            elif path == "/api/lines/9/stations":
                self._send_json({"lineId": "9", "stations": self.service.station_mappings()})
            elif path == "/api/lines/9/track-map":
                self._send_json(self.service.track_map())
            elif path == "/api/lines/9/power-topology":
                self._send_json(self.service.power_topology())
            elif path == "/api/sim/state":
                self._send_json(self._sim_state())
            elif match := re.fullmatch(r"/api/sim/runs/(\d+)/snapshots", path):
                if self.engine is None or self.engine.recorder is None:
                    self._send_json({"ok": False, "error": "RECORDER_NOT_AVAILABLE"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    run_id = int(match.group(1))
                    self._send_json({
                        "ok": True,
                        "runId": run_id,
                        "snapshots": self.engine.recorder.list_world_snapshots(run_id),
                    })
            elif match := re.fullmatch(r"/api/sim/runs/(\d+)/snapshots/(\d+)", path):
                if self.engine is None or self.engine.recorder is None:
                    self._send_json({"ok": False, "error": "RECORDER_NOT_AVAILABLE"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    try:
                        self._send_json({
                            "ok": True,
                            "data": self.engine.recorder.read_world_snapshot(
                                int(match.group(1)), sequence=int(match.group(2)),
                            ),
                        })
                    except KeyError:
                        self._send_json({"ok": False, "error": "SNAPSHOT_NOT_FOUND"}, HTTPStatus.NOT_FOUND)
            elif path == "/api/sim/topology-state":
                self._send_json(self._main_engine_topology_state())
            elif path == "/api/sim/interlocking/state":
                snap = self.engine.snapshot() if self.engine else None
                self._send_json(snap.interlocking if snap else {
                    "mode": "UNAVAILABLE", "routes": [], "sections": [], "switches": [], "signals": []
                })
            elif path == "/api/sim/dispatch/state":
                snap = self.engine.snapshot() if self.engine else None
                self._send_json(snap.dispatch_runtime if snap else {
                    "registeredTrainCount": 0, "departureCount": 0, "recentDepartures": []
                })
            elif path == "/api/sim/timetable":
                if self.engine is None:
                    self._send_json({"ok": False, "error": "ENGINE_NOT_INITIALIZED"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    state = self.engine.operation_plan_state()
                    self._send_json({
                        "ok": True,
                        "enabled": state["enabled"],
                        "timetables": state["timetables"],
                        "services": state["services"],
                    })
            elif path == "/api/sim/duties":
                if self.engine is None:
                    self._send_json({"ok": False, "error": "ENGINE_NOT_INITIALIZED"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    state = self.engine.operation_plan_state()
                    self._send_json({
                        "ok": True,
                        "enabled": state["enabled"],
                        "duties": state["duties"],
                        "recentEvents": state["recentEvents"],
                    })
            elif path == "/api/hardware/driver-cab/status":
                controller = self._driver_cab_controller()
                if controller is None:
                    self._send_json({"ok": False, "error": "ENGINE_NOT_INITIALIZED"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    self._send_json(controller.status())
            elif path == "/api/hardware/vision/status":
                publisher = self._vision_publisher()
                if publisher is None:
                    self._send_json({"ok": False, "error": "ENGINE_NOT_INITIALIZED"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    self._send_json(publisher.status())
            elif match := re.fullmatch(r"/api/sim/passenger-history/([^/]+)", path):
                if self.engine is None:
                    self._send_json({"ok": False, "error": "ENGINE_NOT_INITIALIZED"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    query = parse_qs(parsed.query)
                    since = query.get("sinceSimTimeMs", [None])[0]
                    self._send_json(self.engine.station_passenger_history(
                        match.group(1),
                        int(since) if since is not None else None,
                    ))
            elif path == "/api/passenger-sim/state":
                self._send_json(self.service.passenger_sim.snapshot())
            elif path == "/api/sim/power/state":
                self._send_json(self._sim_power_state())
            elif path == "/api/sim/power/commands":
                self._send_json({"ok": True, "data": self.engine.power_command_status() if self.engine else []})
            elif match := re.fullmatch(r"/api/sim/power/commands/([^/]+)", path):
                commands = self.engine.power_command_status(match.group(1)) if self.engine else []
                if commands:
                    self._send_json({"ok": True, "data": commands[0]})
                else:
                    self._send_json({"ok": False, "error": "POWER_COMMAND_NOT_FOUND"}, HTTPStatus.NOT_FOUND)
            elif path == "/api/sim/speed-profile":
                self._send_json(self._speed_profile())
            elif path == "/api/sim/run/export":
                if self.engine is None:
                    self._send_json({"ok": False, "error": "ENGINE_NOT_INITIALIZED"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    self._send_json({"ok": True, "data": self.engine.export_current_run()})
            elif path == "/api/sim/reports":
                if self.engine is None:
                    self._send_json({"ok": False, "error": "ENGINE_NOT_INITIALIZED"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    self._send_json({"ok": True, "reports": self.engine.list_reports(3)})
            elif path == "/api/sim/report":
                if self.engine is None:
                    self._send_json({"ok": False, "error": "ENGINE_NOT_INITIALIZED"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    report = self.engine.get_report()
                    if report is None:
                        self._send_json({"ok": False, "error": "NO_REPORT_AVAILABLE"}, HTTPStatus.NOT_FOUND)
                    else:
                        self._send_json({"ok": True, "report": report})
            elif match := re.fullmatch(r"/api/sim/report/(\d+)", path):
                if self.engine is None:
                    self._send_json({"ok": False, "error": "ENGINE_NOT_INITIALIZED"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    report = self.engine.get_report(int(match.group(1)))
                    if report is None:
                        self._send_json({"ok": False, "error": "REPORT_NOT_FOUND"}, HTTPStatus.NOT_FOUND)
                    else:
                        self._send_json({"ok": True, "report": report})
            elif path == "/api/power/experiments":
                self._send_json({"ok": True, "data": self._power_experiment_registry().list()})
            elif match := re.fullmatch(r"/api/power/experiments/([^/]+)/trials", path):
                try:
                    result = self._power_experiment_registry().get(match.group(1), include_trials=True)
                    self._send_json({"ok": True, "data": result["trials"]})
                except KeyError:
                    self._send_json({"ok": False, "error": "POWER_EXPERIMENT_NOT_FOUND"}, HTTPStatus.NOT_FOUND)
            elif match := re.fullmatch(r"/api/power/experiments/([^/]+)", path):
                try:
                    result = self._power_experiment_registry().get(match.group(1), include_trials=False)
                    self._send_json({"ok": True, "data": result})
                except KeyError:
                    self._send_json({"ok": False, "error": "POWER_EXPERIMENT_NOT_FOUND"}, HTTPStatus.NOT_FOUND)
            elif path == "/api/phase0/member-d/demo":
                self._send_json(self.service.member_d_phase0_demo())
            elif path == "/api/phase1/member-d/demo":
                self._send_json(self.service.member_d_phase1_demo())

            elif path == "/api/phase2/member-d/demo":
                self._send_json(self.service.member_d_demo())
            elif path == "/api/phase2/member-d/full-demo":
                self._send_json(self.service.member_d_phase2_full_demo())
            elif path == "/api/phase2/member-c/demo":
                self._serve_html_file(ROOT / "member-c-demo.html")
            elif path == "/api/phase2/member-c/member-c-topology.js":
                self._serve_static_file(ROOT / "member-c-topology.js", "application/javascript; charset=utf-8")
            elif path == "/api/phase2/member-c/state":
                self._send_json(self.service.member_c_state())
            elif path == "/api/phase2/member-c/step":
                self._send_json(self.service.member_c_step())
            elif path == "/api/phase2/member-c/reset":
                self._send_json(self.service.member_c_reset())
            elif path == "/api/phase2/member-c/static-routes":
                self._send_json(self.service.member_c_static_routes())
            elif match := re.fullmatch(r"/api/track/segments/(\d+)/context", path):
                self._send_json(self.service.segment_context(int(match.group(1))))
            else:
                self._send_json({"error": "not found", "path": path}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        try:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"

            with ApiHandler.replay_lock:
                replay_active = ApiHandler.replay_snapshot is not None
            if (
                replay_active
                and path.startswith("/api/sim/")
                and path not in {"/api/sim/replay/load", "/api/sim/replay/seek", "/api/sim/replay/exit"}
            ):
                self._send_json(
                    {"ok": False, "error": "REPLAY_READ_ONLY", "message": "退出回放后才能发送实时仿真命令"},
                    HTTPStatus.CONFLICT,
                )
                return

            if path == "/api/phase2/member-c/manual/place":
                payload = self._read_json_body()
                self._send_json(self.service.member_c_place_train(int(payload.get("segmentId", 0))))
                return
            if path == "/api/phase2/member-c/manual/place-route":
                payload = self._read_json_body()
                self._send_json(self.service.member_c_place_train_for_route(str(payload.get("routeId", ""))))
                return
            if path == "/api/phase2/member-c/manual/request-route":
                payload = self._read_json_body()
                self._send_json(self.service.member_c_request_manual_route(
                    str(payload["routeId"]) if payload.get("routeId") is not None else None
                ))
                return
            if path.startswith("/api/passenger-sim/"):
                payload = self._read_json_body()
                sim = self.service.passenger_sim
                action = path.rsplit("/", 1)[-1]
                if action == "start": sim.start()
                elif action == "pause": sim.pause()
                elif action == "resume": sim.resume()
                elif action == "stop": sim.stop()
                elif action == "step": sim.step(int(payload.get("seconds", 1)))
                else:
                    self._send_json({"ok": False, "error": "UNKNOWN_PASSENGER_SIM_ACTION"}, HTTPStatus.NOT_FOUND)
                    return
                self._send_json({"ok": True, "action": action, "state": sim.snapshot()})
                return

            if path in {"/api/sim/start", "/api/sim/pause", "/api/sim/resume", "/api/sim/stop", "/api/sim/speed", "/api/sim/step", "/api/sim/tick-interval"}:
                if not self.engine:
                    self._send_json(
                        {"ok": False, "error": "ENGINE_NOT_INITIALIZED"},
                        HTTPStatus.SERVICE_UNAVAILABLE,
                    )
                    return

            if path == "/api/sim/start":
                start_result = self.engine.start()
                snap = self.engine.snapshot()
                self._send_json({
                    "ok": True,
                    "action": "start",
                    "result": start_result,
                    "clockState": self.engine.clock.state.value,
                    "initializationSteps": [
                        "LOAD_SCENARIO", "RESET_STATE", "CREATE_RECORDER_RUN",
                        "INITIALIZE_POWER_NETWORK", "START_TICK_THREAD",
                    ],
                    "simTimeMs": snap.sim_time_ms if snap else int(self.engine.clock.sim_time_seconds * 1000),
                })
            elif path == "/api/sim/pause":
                self.engine.pause()
                self._send_json({"ok": True, "action": "pause"})
            elif path == "/api/sim/resume":
                self.engine.resume()
                self._send_json({"ok": True, "action": "resume"})
            elif path == "/api/sim/stop":
                self.engine.stop()
                self._send_json({"ok": True, "action": "stop"})
            elif path == "/api/sim/step":
                self.engine.step_once()
                self._send_json(self._main_engine_topology_state())
            elif path == "/api/sim/tick-interval":
                payload = self._read_json_body()
                interval_ms = float(payload.get("intervalMs", self.engine._tick_interval_seconds * 1000))
                applied_seconds = self.engine.set_tick_interval_seconds(interval_ms / 1000.0)
                self._send_json({"ok": True, "tickIntervalMs": round(applied_seconds * 1000)})
            elif path == "/api/sim/speed":
                payload = self._read_json_body()
                try:
                    multiplier = self.engine.set_speed_multiplier(int(payload.get("multiplier", 1)))
                    self._send_json({"ok": True, "speedMultiplier": multiplier})
                except (TypeError, ValueError) as exc:
                    self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            elif path in {"/api/sim/replay/load", "/api/sim/replay/seek"}:
                if self.engine is None or self.engine.recorder is None:
                    self._send_json({"ok": False, "error": "RECORDER_NOT_AVAILABLE"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    payload = self._read_json_body()
                    try:
                        run_id = int(payload.get("runId", ApiHandler.replay_run_id or 0))
                        sequence = payload.get("sequence")
                        sim_time_ms = payload.get("simTimeMs")
                        snapshot = self.engine.recorder.read_world_snapshot(
                            run_id,
                            sequence=int(sequence) if sequence is not None else None,
                            sim_time_ms=int(sim_time_ms) if sim_time_ms is not None else None,
                        )
                        with ApiHandler.replay_lock:
                            ApiHandler.replay_run_id = run_id
                            ApiHandler.replay_snapshot = snapshot
                        self._send_json({"ok": True, "data": self._replay_state(snapshot, run_id)})
                    except (KeyError, ValueError):
                        self._send_json({"ok": False, "error": "SNAPSHOT_NOT_FOUND"}, HTTPStatus.NOT_FOUND)
            elif path == "/api/sim/replay/exit":
                self._exit_replay()
                self._send_json({"ok": True, "dataMode": "LIVE_SIM"})
            elif path == "/api/sim/train/add":
                self._send_json(self._add_train())
            elif path == "/api/sim/train/remove":
                self._send_json(self._remove_train())
            elif path == "/api/sim/train/vehicle-config":
                self._send_json(self._set_train_vehicle_config())
            elif path == "/api/sim/train/manual-mode":
                self._send_json(self._set_train_manual_mode())
            elif path == "/api/sim/train/manual-command":
                self._send_json(self._send_train_manual_command())
            elif path == "/api/sim/train/door-command":
                self._send_json(self._send_train_door_command())
            elif path == "/api/sim/vehicle-config":
                payload = self._read_json_body()
                vcfg = self.engine.set_vehicle_config(payload)
                self._send_json({"ok": True, "vehicleConfig": vcfg.to_dict()})
            elif path == "/api/sim/manual-mode":
                payload = self._read_json_body()
                enabled = bool(payload.get("enabled", False))
                train_id = str(payload.get("trainId", self.engine.trains[0].train_id if self.engine.trains else "T0901"))
                self._send_json(self._set_manual_mode_from_frontend(train_id, enabled))
            elif path == "/api/sim/manual-command":
                payload = self._read_json_body()
                train_id = str(payload.get("trainId", self.engine.trains[0].train_id if self.engine.trains else "T0901"))
                self._send_json(self.engine.set_manual_command(
                    train_id,
                    float(payload.get("tractionPercent", 0)),
                    float(payload.get("brakePercent", 0)),
                ))
            elif path == "/api/hardware/driver-cab/connect":
                controller = self._driver_cab_controller()
                if controller is None:
                    self._send_json({"ok": False, "error": "ENGINE_NOT_INITIALIZED"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    payload = self._read_json_body()
                    try:
                        result = controller.connect(
                            host=str(payload["host"]) if payload.get("host") is not None else None,
                            port=int(payload["port"]) if payload.get("port") is not None else None,
                            network_screen_host=(
                                str(payload["networkScreenHost"])
                                if payload.get("networkScreenHost") is not None
                                else None
                            ),
                            signal_screen_host=(
                                str(payload["signalScreenHost"])
                                if payload.get("signalScreenHost") is not None
                                else None
                            ),
                        )
                        self._send_json(result, HTTPStatus.ACCEPTED)
                    except (TypeError, ValueError) as exc:
                        self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            elif path == "/api/hardware/driver-cab/disconnect":
                controller = self._driver_cab_controller()
                if controller is None:
                    self._send_json({"ok": False, "error": "ENGINE_NOT_INITIALIZED"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    self._send_json(controller.disconnect())
            elif path == "/api/hardware/vision/connect":
                if self.engine is None:
                    self._send_json({"ok": False, "error": "ENGINE_NOT_INITIALIZED"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    try:
                        publisher = self._vision_publisher(self._read_json_body())
                        if publisher is None:
                            raise RuntimeError("vision publisher is unavailable")
                        self._send_json(publisher.connect(), HTTPStatus.ACCEPTED)
                    except (TypeError, ValueError) as exc:
                        self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            elif path == "/api/hardware/vision/disconnect":
                publisher = self._vision_publisher()
                if publisher is None:
                    self._send_json({"ok": False, "error": "ENGINE_NOT_INITIALIZED"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    self._send_json(publisher.disconnect())
            elif path == "/api/hardware/logs/clear":
                controller = self._driver_cab_controller()
                publisher = self._vision_publisher()
                if controller is None or publisher is None:
                    self._send_json({"ok": False, "error": "ENGINE_NOT_INITIALIZED"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    self._send_json({
                        "ok": True,
                        "driverCab": controller.clear_logs()["status"],
                        "vision": publisher.clear_logs()["status"],
                    })
            elif path == "/api/hardware/driver-cab/plc/connect":
                controller = self._driver_cab_controller()
                if controller is None:
                    self._send_json({"ok": False, "error": "ENGINE_NOT_INITIALIZED"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    payload = self._read_json_body()
                    try:
                        self._send_json(controller.connect_plc(
                            host=str(payload["host"]) if payload.get("host") is not None else None,
                            port=int(payload["port"]) if payload.get("port") is not None else None,
                        ), HTTPStatus.ACCEPTED)
                    except (TypeError, ValueError) as exc:
                        self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            elif path == "/api/hardware/driver-cab/plc/disconnect":
                controller = self._driver_cab_controller()
                if controller is None:
                    self._send_json({"ok": False, "error": "ENGINE_NOT_INITIALIZED"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    self._send_json(controller.disconnect_plc())
            elif path in {
                "/api/hardware/driver-cab/network-screen/connect",
                "/api/hardware/driver-cab/signal-screen/connect",
            }:
                controller = self._driver_cab_controller()
                if controller is None:
                    self._send_json({"ok": False, "error": "ENGINE_NOT_INITIALIZED"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    payload = self._read_json_body()
                    endpoint = "networkScreen" if "/network-screen/" in path else "signalScreen"
                    try:
                        self._send_json(controller.connect_display(
                            endpoint,
                            host=str(payload["host"]) if payload.get("host") is not None else None,
                        ), HTTPStatus.ACCEPTED)
                    except (TypeError, ValueError) as exc:
                        self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            elif path in {
                "/api/hardware/driver-cab/network-screen/disconnect",
                "/api/hardware/driver-cab/signal-screen/disconnect",
            }:
                controller = self._driver_cab_controller()
                if controller is None:
                    self._send_json({"ok": False, "error": "ENGINE_NOT_INITIALIZED"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    endpoint = "networkScreen" if "/network-screen/" in path else "signalScreen"
                    self._send_json(controller.disconnect_display(endpoint))
            elif path == "/api/sim/power/faults":
                payload = self._read_json_body()
                self._send_json(self._apply_power_fault(payload))
            elif path == "/api/sim/power/reset":
                self._send_json(self._reset_power_network())
            elif path == "/api/sim/power/commands":
                payload = self._read_json_body()
                self._send_json(self._queue_power_command(payload))
            elif path == "/api/sim/power/commands/replay":
                payload = self._read_json_body()
                self._send_json(self._replay_power_commands(payload))
            elif path == "/api/power/experiments":
                payload = self._read_json_body()
                try:
                    result = self._power_experiment_registry().create(payload)
                    self._send_json({"ok": True, "data": result}, HTTPStatus.CREATED)
                except ValueError as exc:
                    self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            elif path == "/api/power/experiments/batch":
                payload = self._read_json_body()
                requests = payload.get("experiments", [])
                if not isinstance(requests, list) or not requests:
                    self._send_json({"ok": False, "error": "POWER_EXPERIMENT_BATCH_REQUIRED"}, HTTPStatus.BAD_REQUEST)
                else:
                    try:
                        results = [self._power_experiment_registry().create(item) for item in requests]
                        self._send_json(
                            {"ok": True, "data": {"count": len(results), "experiments": results}},
                            HTTPStatus.CREATED,
                        )
                    except ValueError as exc:
                        self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            elif match := re.fullmatch(r"/api/sim/power/switches/([^/]+)/operate", path):
                payload = self._read_json_body()
                self._send_json(self._operate_power_switch(match.group(1), payload))
            else:
                self._send_json({"error": "not found", "path": path}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[api] {self.address_string()} - {format % args}")

    def _health(self) -> JsonDict:
        base = self.service.health()
        if self.engine:
            snap = self.engine.snapshot()
            base["simEngine"] = "attached"
            base["clockState"] = self.engine.clock.state.value
            base["simTimeStr"] = snap.sim_time_str if snap else "--:--:--"
            base["activeTrains"] = len([t for t in (snap.trains if snap else []) if t.get("phase") != "IDLE"])
            if ApiHandler.vision_publisher is not None:
                base["visionAdapter"] = ApiHandler.vision_publisher.status()["status"]["state"]
        else:
            base["simEngine"] = "not_attached"
        return base

    def _topology_start_options(self) -> list[JsonDict]:
        if self.engine is None:
            return []
        options: list[JsonDict] = []
        for station in self.service.station_mappings():
            for platform in station.get("platforms", []):
                segment_id = platform.get("segmentId")
                if segment_id is None:
                    continue
                directions = self.engine.available_initial_directions(str(station["stationCode"]), int(segment_id))
                if directions:
                    options.append({"segmentId": int(segment_id), "stationCode": station["stationCode"], "stationName": station["stationName"], "directions": list(directions)})
        return options
    def _main_engine_topology_state(self) -> JsonDict:
        """Project the main engine onto the established Member C topology contract."""
        static = self.service.member_c_static_routes()
        if self.engine is None or self.engine.snapshot() is None:
            return {
                "tick": 0, "simTimeMs": 0, "clockState": "IDLE",
                "segments": static.get("segments", []), "trains": [], "routes": [],
                "signals": static.get("signals", []), "axleSections": [], "switches": [],
                "segTrainColors": {}, "events": [], "startOptions": self._topology_start_options(),
                "occupiedCount": 0, "totalAxleSections": len(self.service.line_map.get("axleSections", [])),
                "lockedRouteCount": 0, "requestedRoutesCount": 0,
            }
        snap = self.engine.snapshot()
        assert snap is not None
        interlocking = snap.interlocking
        sections = interlocking.get("sections", [])
        aspect_by_signal = {str(item.get("signalId")): item.get("aspect", "RED") for item in interlocking.get("signals", [])}
        colors = ("#58a6ff", "#f0c040", "#f778ba", "#39d0c8", "#8fc31f")
        seg_train_colors: dict[int, str] = {}
        trains = []
        for index, train in enumerate(snap.trains):
            color = colors[index % len(colors)]
            segment_id = train.get("currentSegmentId")
            covered_segments = sorted(
                int(item)
                for item in self.engine.section_occupation.covered_segments_for(str(train.get("trainId")))
            ) if self.engine is not None else []
            if segment_id is not None:
                seg_train_colors[int(segment_id)] = color
            for covered_segment_id in covered_segments:
                seg_train_colors[covered_segment_id] = color
            trains.append({
                "id": train.get("trainId"), "segId": segment_id,
                "offsetM": train.get("currentSegmentOffsetM", 0.0),
                "coveredSegmentIds": covered_segments,
                "positionM": train.get("pathPositionM", 0.0),
                "speedMps": train.get("speedMps", 0.0),
                "direction": "FORWARD" if train.get("direction") == "UP" else "BACKWARD",
                "directionCode": train.get("direction"),
                "lengthM": train.get("trainLengthM", 120.0), "phase": train.get("phase", "IDLE"),
                "color": color, "routeFailureReason": train.get("routeFailureReason"),
                "movementAuthorityReason": train.get("movementAuthorityReason"),
                "routeIds": train.get("routeChainIds", []),
                "currentStation": train.get("currentStation"),
                "currentStationCode": train.get("currentStationCode"),
                "nextStation": train.get("nextStation"),
                "nextStationCode": train.get("nextStationCode"),
                "dwellRemainingSec": train.get("dwellRemainingSec", 0.0),
                "operationMode": train.get("operationMode", "ATO"),
                "doorState": train.get("doorState", "CLOSED"),
                "doorSide": train.get("doorSide", "NONE"),
                "doorNotice": train.get("doorNotice", "CLOSED"),
                "doorSystem": train.get("doorSystem"),
                "routeRetryAtMs": train.get("routeRetryAtMs"),
            })
        signals = [{**item, "aspect": aspect_by_signal.get(str(item.get("id")), "RED")} for item in static.get("signals", [])]
        routes = interlocking.get("routes", [])
        events = []
        for decision in snap.dispatch_decisions:
            action = decision.get("action")
            if action == "TURNBACK":
                events.append({
                    "category": "折返",
                    "message": f"列车 {decision.get('trainId')} 在 {decision.get('stationId')} 完成折返",
                    "tick": snap.tick,
                })
            elif action in {"HOLD", "STAGGER_DEPARTURE", "DWELL_EXTEND"}:
                events.append({
                    "category": "等待",
                    "message": f"列车 {decision.get('trainId')} 暂缓发车：{decision.get('reason')}",
                    "tick": snap.tick,
                })
        return {
            "tick": snap.tick, "simTimeMs": snap.sim_time_ms, "clockState": snap.clock_state,
            "segments": static.get("segments", []), "startOptions": self._topology_start_options(), "trains": trains,
            "routes": routes, "signals": signals, "axleSections": sections,
            "switches": interlocking.get("switches", []), "segTrainColors": seg_train_colors,
            "events": events, "occupiedCount": sum(1 for item in sections if item.get("occupied")),
            "totalAxleSections": len(sections),
            "lockedRouteCount": sum(1 for item in routes if item.get("state") in ("LOCKED", "APPROACH_LOCKED")),
            "requestedRoutesCount": len(routes),
        }

    def _sim_state(self) -> JsonDict:
        with ApiHandler.replay_lock:
            if ApiHandler.replay_snapshot is not None and ApiHandler.replay_run_id is not None:
                return self._replay_state(ApiHandler.replay_snapshot, ApiHandler.replay_run_id)
        if self.engine is None:
            return {
                "sessionId": None,
                "runId": None,
                "snapshotSequence": 0,
                "dataMode": "DISCONNECTED",
                "clock": {"state": "IDLE", "simTime": "--:--:--", "tick": 0},
                "trains": [],
                "stations": self.service.station_mappings(),
                "kpi": {},
                "source": "static",
            }
        snap = self.engine.snapshot()
        if snap is None:
            return {"clock": {"state": "STOPPED", "simTime": "--:--:--", "tick": 0}, "trains": [], "source": "static"}
        return snap.to_api_dict(
            tick_interval_ms=round(self.engine._tick_interval_seconds * 1000),
        )

    @staticmethod
    def _replay_state(snapshot: JsonDict, run_id: int) -> JsonDict:
        payload = dict(snapshot)
        payload["recordedSource"] = payload.get("source")
        payload["source"] = "recorded-snapshot"
        payload["dataMode"] = "REPLAY"
        payload["runId"] = run_id
        payload["replayReadOnly"] = True
        return payload

    @staticmethod
    def _exit_replay() -> None:
        with ApiHandler.replay_lock:
            ApiHandler.replay_run_id = None
            ApiHandler.replay_snapshot = None

    def _sim_power_state(self) -> JsonDict:
        if self.engine is None:
            return {
                "simTimeMs": 0,
                "substations": [],
                "feeders": [],
                "trainVoltages": [],
                "regen": {"generatedKw": 0, "absorbedKw": 0, "feedbackKw": 0, "wastedKw": 0},
                "lossesKw": 0,
                "alerts": [],
                "source": "static",
            }
        snap = self.engine.snapshot()
        if snap is None or not snap.power_network:
            return {
                "simTimeMs": 0,
                "substations": [],
                "feeders": [],
                "trainVoltages": [],
                "regen": {"generatedKw": 0, "absorbedKw": 0, "feedbackKw": 0, "wastedKw": 0},
                "lossesKw": 0,
                "alerts": [],
                "source": "simulation-engine",
            }
        return snap.power_network

    def _apply_power_fault(self, payload: JsonDict) -> JsonDict:
        if self.engine is None or self.engine.power_service.network is None:
            return {"ok": False, "error": "POWER_NETWORK_NOT_INITIALIZED"}
        fault_type = str(payload.get("faultType", "SUBSTATION_OUTAGE"))
        target_id = str(payload.get("targetId", ""))
        command_by_fault = {
            "SUBSTATION_OUTAGE": ("SUBSTATION_OUTAGE", {"targetId": target_id, "bigBilateral": str(payload.get("mode", "N_MINUS_1_BIG_BILATERAL")) == "N_MINUS_1_BIG_BILATERAL"}),
            "FEEDER_OPEN": ("SET_FEEDER_STATUS", {"feederId": target_id, "status": "OPEN"}),
            "CONTACT_RAIL_DEENERGIZED": ("SET_CONTACT_SECTION_STATUS", {"sectionId": target_id, "status": "DEENERGIZED"}),
        }
        if fault_type not in command_by_fault or not target_id:
            return {"ok": False, "error": "UNSUPPORTED_POWER_FAULT"}
        command_type, command_payload = command_by_fault[fault_type]
        result = self.engine.queue_power_command(command_type, command_payload)
        return {"ok": True, "data": {"faultId": f"PF-{target_id}", **result}}

    def _reset_power_network(self) -> JsonDict:
        if self.engine is None:
            return {"ok": False, "error": "ENGINE_NOT_INITIALIZED"}
        return {"ok": True, "data": self.engine.queue_power_command("RESET_NETWORK", {})}

    def _operate_power_switch(self, switch_id: str, payload: JsonDict) -> JsonDict:
        if self.engine is None or self.engine.power_service.network is None:
            return {"ok": False, "error": "POWER_NETWORK_NOT_INITIALIZED"}
        state = str(payload.get("state", "")).upper()
        if state not in {"OPEN", "CLOSED"}:
            return {"ok": False, "error": "INVALID_SWITCH_STATE"}
        result = self.engine.queue_power_command(
            "OPERATE_SWITCH",
            {"switchId": switch_id, "state": state},
        )
        switch = self.engine.power_service.network.switches[switch_id]
        return {
            "ok": True,
            "data": {
                "switchId": switch.switch_id,
                "switchType": switch.switch_type,
                "mileageM": switch.mileage_m,
                "fromNodeId": switch.from_node_id,
                "toNodeId": switch.to_node_id,
                "normalState": switch.normal_state,
                "currentState": switch.current_state,
                "remoteControllable": switch.remote_controllable,
                **result,
            },
        }

    def _queue_power_command(self, payload: JsonDict) -> JsonDict:
        if self.engine is None or self.engine.power_service.network is None:
            return {"ok": False, "error": "POWER_NETWORK_NOT_INITIALIZED"}
        command_type = str(payload.get("commandType", ""))
        command_payload = payload.get("payload", {})
        if not command_type or not isinstance(command_payload, dict):
            return {"ok": False, "error": "INVALID_POWER_COMMAND"}
        try:
            return {"ok": True, "data": self.engine.queue_power_command(command_type, command_payload)}
        except (KeyError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def _replay_power_commands(self, payload: JsonDict) -> JsonDict:
        if self.engine is None:
            return {"ok": False, "error": "ENGINE_NOT_INITIALIZED"}
        records = payload.get("commands")
        if records is None and payload.get("runId") is not None and self.engine.recorder is not None:
            records = self.engine.recorder.replay_power_commands(int(payload["runId"]))
        if not isinstance(records, list):
            return {"ok": False, "error": "POWER_COMMAND_REPLAY_DATA_REQUIRED"}
        queued = self.engine.replay_power_commands(
            records,
            base_sim_time_ms=int(payload["baseSimTimeMs"]) if payload.get("baseSimTimeMs") is not None else None,
        )
        return {"ok": True, "data": {"queuedCount": len(queued), "commands": queued}}

    def _read_json_body(self) -> JsonDict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _driver_cab_controller(self) -> DriverCabHardwareController | None:
        if self.engine is None:
            return None
        with ApiHandler.cab_hardware_lock:
            controller = ApiHandler.cab_hardware_controller
            if controller is None or controller.engine is not self.engine:
                if controller is not None:
                    controller.disconnect()
                controller = DriverCabHardwareController(self.engine)
                ApiHandler.cab_hardware_controller = controller
            return controller

    def _vision_publisher(self, payload: JsonDict | None = None) -> VisionUdpPublisher | None:
        if self.engine is None:
            return None
        with ApiHandler.vision_hardware_lock:
            publisher = ApiHandler.vision_publisher
            needs_rebuild = publisher is None or publisher.engine is not self.engine or payload is not None
            if not needs_rebuild:
                return publisher
            if publisher is not None:
                publisher.disconnect()
            config = payload or {}
            signal_source_map = config.get("signalSourceMap")
            switch_source_map = config.get("switchSourceMap")
            if signal_source_map is not None and not isinstance(signal_source_map, dict):
                raise ValueError("signalSourceMap must be an object")
            if switch_source_map is not None and not isinstance(switch_source_map, dict):
                raise ValueError("switchSourceMap must be an object")
            publisher = VisionUdpPublisher(
                self.engine,
                remote_host=str(config.get("remoteHost", "18.32.115.28")),
                remote_port=int(config.get("remotePort", 8303)),
                local_host=str(config.get("localHost", "0.0.0.0")),
                local_port=int(config.get("localPort", 8303)),
                interval_s=float(config.get("intervalMs", 100.0)) / 1000.0,
                layout=str(config.get("layout", COMPACT_LAYOUT)),
                primary_train_id=(
                    str(config["primaryTrainId"])
                    if config.get("primaryTrainId") is not None
                    else None
                ),
                signal_source_map=signal_source_map,
                switch_source_map=switch_source_map,
            )
            ApiHandler.vision_publisher = publisher
            return publisher

    @classmethod
    def _power_experiment_registry(cls) -> PowerExperimentRegistry:
        if cls.experiment_registry is None:
            cls.experiment_registry = PowerExperimentRegistry(
                DEFAULT_POWER_TOPOLOGY,
                ROOT / "outputs" / "power_experiments.sqlite",
            )
        return cls.experiment_registry

    def _add_train(self) -> JsonDict:
        if self.engine is None:
            return {"ok": False, "error": "ENGINE_NOT_INITIALIZED"}
        payload = self._read_json_body()
        return self.engine.add_train(payload)

    def _remove_train(self) -> JsonDict:
        if self.engine is None:
            return {"ok": False, "error": "ENGINE_NOT_INITIALIZED"}
        payload = self._read_json_body()
        return self.engine.remove_train(str(payload.get("trainId", "")))

    def _set_train_vehicle_config(self) -> JsonDict:
        if self.engine is None:
            return {"ok": False, "error": "ENGINE_NOT_INITIALIZED"}
        payload = self._read_json_body()
        train_id = str(payload.get("trainId", ""))
        if not train_id:
            return {"ok": False, "error": "MISSING_TRAIN_ID"}
        vcfg = self.engine.set_train_vehicle_config(train_id, payload)
        return {"ok": True, "vehicleConfig": vcfg.to_dict()}

    def _set_train_manual_mode(self) -> JsonDict:
        if self.engine is None:
            return {"ok": False, "error": "ENGINE_NOT_INITIALIZED"}
        payload = self._read_json_body()
        train_id = str(payload.get("trainId", ""))
        if not train_id:
            return {"ok": False, "error": "MISSING_TRAIN_ID"}
        return self._set_manual_mode_from_frontend(train_id, bool(payload.get("enabled", False)))

    def _set_manual_mode_from_frontend(self, train_id: str, enabled: bool) -> JsonDict:
        """Only the PLC driver cab may change driving mode while connected."""
        if self.engine is None:
            return {"ok": False, "error": "ENGINE_NOT_INITIALIZED"}
        controller = self._driver_cab_controller()
        if controller is not None:
            status = controller.status().get("status", {})
            if status.get("state") == "CONNECTED" and status.get("trainId") == train_id:
                return {
                    "ok": False,
                    "error": "DRIVER_CAB_MODE_CONTROL_EXCLUSIVE",
                    "message": "司机台已连接，驾驶模式只能由司机台切换",
                    "trainId": train_id,
                }
        return self.engine.set_manual_mode(train_id, enabled)

    def _send_train_manual_command(self) -> JsonDict:
        if self.engine is None:
            return {"ok": False, "error": "ENGINE_NOT_INITIALIZED"}
        payload = self._read_json_body()
        train_id = str(payload.get("trainId", ""))
        if not train_id:
            return {"ok": False, "error": "MISSING_TRAIN_ID"}
        return self.engine.set_manual_command(
            train_id,
            float(payload.get("tractionPercent", 0)),
            float(payload.get("brakePercent", 0)),
        )

    def _send_train_door_command(self) -> JsonDict:
        if self.engine is None:
            return {"ok": False, "error": "ENGINE_NOT_INITIALIZED"}
        payload = self._read_json_body()
        train_id = str(payload.get("trainId", ""))
        if not train_id:
            return {"ok": False, "error": "MISSING_TRAIN_ID"}
        return self.engine.set_door_command(
            train_id,
            str(payload.get("action", "")),
            str(payload.get("side", "NONE")),
            source="FRONTEND",
        )

    def _speed_profile(self) -> JsonDict:
        if self.engine is None:
            return {"profiles": {}, "profileMeta": {}, "source": "unavailable"}
        profiles: dict[str, Any] = {}
        profile_meta: dict[str, Any] = {}
        for train in self.engine.trains:
            profiles[train.train_id] = self.engine.export_speed_profile(train.train_id)
            profile_meta[train.train_id] = self.engine.export_speed_profile_meta(train.train_id)
        return {"profiles": profiles, "profileMeta": profile_meta, "source": "simulation-engine"}

    def _send_json(self, payload: JsonDict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _serve_static_file(self, file_path: Path, content_type: str) -> None:
        body = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self._send_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_html_file(self, file_path: Path) -> None:
        body = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self._send_cors_headers()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ═══════════════════════════════════════════════════════════════
#  Server Builder
# ═══════════════════════════════════════════════════════════════

def build_server(host: str, port: int, service: Line9DataService) -> ThreadingHTTPServer:
    class BoundApiHandler(ApiHandler):
        pass

    BoundApiHandler.service = service
    return ThreadingHTTPServer((host, port), BoundApiHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="BJTUMetroSim Phase 1 HTTP API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--cache", default=str(DEFAULT_CACHE))
    parser.add_argument("--stations", default=str(DEFAULT_STATIONS))
    parser.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR))
    parser.add_argument("--scenario", default=str(DEFAULT_SCENARIO))
    parser.add_argument("--ws-port", type=int, default=8001)
    parser.add_argument("--vision-enabled", action="store_true", help="Start the Line 9 vision UDP publisher")
    parser.add_argument("--vision-host", default="18.32.115.28", help="Vision controller IPv4 address")
    parser.add_argument("--vision-port", type=int, default=8303, help="Vision controller receive port")
    parser.add_argument("--vision-local-host", default="0.0.0.0", help="Local IPv4 address to bind")
    parser.add_argument("--vision-local-port", type=int, default=8303, help="Local UDP source port; capture-confirmed default is 8303; 0 chooses an ephemeral port")
    parser.add_argument("--vision-layout", choices=["compact", "fixed"], default="compact")
    parser.add_argument("--vision-train-id", default=None, help="Train to publish as the cab-view train")
    args = parser.parse_args()

    service = Line9DataService(Path(args.cache), Path(args.stations), Path(args.run_dir))

    # ── 初始化仿真引擎 ──
    scenario_path = Path(args.scenario)
    recorder = RunRecorder(Path(args.run_dir) / "phase1_engine.sqlite")
    if scenario_path.exists() and Path(args.cache).exists():
        try:
            engine = SimulationEngine.load_from_files(
                scenario_path=scenario_path,
                line_map_path=Path(args.cache),
                stations_csv_path=Path(args.stations),
                recorder=recorder,
            )
            engine.load()
            print(f"[engine] 场景加载成功: {scenario_path.name}")
            print(f"[engine]   {len(engine.trains)} 列车, {len(engine._station_list)} 站")
            print(f"[engine]   recorder: {recorder.db_path}")

            # 注入到 handler 类
            ApiHandler.engine = engine
        except Exception as exc:
            print(f"[engine] 初始化失败: {exc}")

    if args.vision_enabled and ApiHandler.engine is not None:
        vision = VisionUdpPublisher(
            ApiHandler.engine,
            remote_host=args.vision_host,
            remote_port=args.vision_port,
            local_host=args.vision_local_host,
            local_port=args.vision_local_port,
            layout=args.vision_layout,
            primary_train_id=args.vision_train_id,
        )
        ApiHandler.vision_publisher = vision
        vision.connect()
        mapping = vision.status()["status"]["mapping"]
        print(
            "[vision] UDP publisher enabled: "
            f"{args.vision_local_host}:{args.vision_local_port} -> {args.vision_host}:{args.vision_port}, "
            f"layout={args.vision_layout}, mapped signals={mapping['mappedSignalCount']}/{mapping['protocolSignalCount']}, "
            f"switches={mapping['mappedSwitchCount']}/{mapping['protocolSwitchCount']}"
        )

    # ── WebSocket ──
    def ws_state() -> JsonDict | None:
        engine = ApiHandler.engine
        snap = engine.snapshot() if engine is not None else None
        if engine is None or snap is None:
            return None
        return snap.to_api_dict(
            tick_interval_ms=round(engine._tick_interval_seconds * 1000),
        )

    ws = WebSocketBroadcaster(args.host, args.ws_port, state_provider=ws_state)
    ws.start()
    ApiHandler.ws_broadcaster = ws

    # ── HTTP ──
    server = build_server(args.host, args.port, service)
    print(f"Phase 1 API listening on http://{args.host}:{args.port}")
    print(f"WebSocket on ws://{args.host}:{args.ws_port}")
    print("Available endpoints:")
    print("  GET  /api/health")
    print("  GET  /api/lines/9/macro")
    print("  GET  /api/lines/9/track-map")
    print("  GET  /api/sim/state")
    print("  GET  /api/sim/interlocking/state")
    print("  GET  /api/sim/dispatch/state")
    print("  GET  /api/hardware/vision/status")
    print("  POST /api/sim/start")
    print("  POST /api/sim/pause")
    print("  POST /api/sim/resume")
    print("  POST /api/sim/stop")
    print("  POST /api/hardware/vision/connect")
    print("  POST /api/hardware/vision/disconnect")
    print("  POST /api/hardware/logs/clear")
    try:
        server.serve_forever()
    finally:
        if ApiHandler.cab_hardware_controller:
            ApiHandler.cab_hardware_controller.disconnect()
        if ApiHandler.vision_publisher:
            ApiHandler.vision_publisher.disconnect()
        if ApiHandler.engine:
            ApiHandler.engine.stop()


if __name__ == "__main__":
    main()
