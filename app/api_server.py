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
from typing import Any
from urllib.parse import urlparse

from app.core.engine import SimulationEngine
from app.domain.line.services import LineMapRepository, TrackQueryService
from app.domain.power.line9_topology import load_line9_power_network
from app.domain.operations.member_c_demo import MemberCDemoRunner
from app.domain.operations.member_d_demo import Phase2MemberDDemoRunner
from app.domain.operations.phase0_member_d_demo import Phase0MemberDDemoRunner
from app.domain.operations.phase1_member_d_demo import Phase1MemberDDemoRunner
from app.domain.operations.phase2_member_d_full_demo import Phase2MemberDFullDemoRunner
from app.infra.recorder import RunRecorder


JsonDict = dict[str, Any]

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE = ROOT / "data" / "cache" / "line_map.json"
DEFAULT_RUN_DIR = ROOT / "outputs" / "runs"
REPO_STATIONS = ROOT / "MetroDynamicsJavaDemo" / "data" / "stations.csv"
WORKSPACE_STATIONS = (
    ROOT / "external" / "BJTUMetroSim" / "MetroDynamicsJavaDemo" / "data" / "stations.csv"
)
DEFAULT_STATIONS = REPO_STATIONS if REPO_STATIONS.exists() else WORKSPACE_STATIONS
DEFAULT_SCENARIO = ROOT / "data" / "scenarios" / "line9_single.json"
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
    ) -> None:
        self.cache_path = cache_path
        self.stations_path = stations_path
        self.run_dir = run_dir
        self._line_map: JsonDict | None = None
        self._stations: list[JsonDict] | None = None
        self._sim_runner: MemberCDemoRunner | None = None
        self._power_topology: JsonDict | None = None

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

    def health(self) -> JsonDict:
        validation = self.line_map.get("validation", {})
        return {
            "ok": True,
            "service": "BJTUMetroSim Phase1 API",
            "lineId": "9",
            "cache": str(self.cache_path),
            "cacheExists": self.cache_path.exists(),
            "validationOk": validation.get("ok"),
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

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
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
                async for _ in websocket:
                    pass  # 客户端发来的消息忽略
            finally:
                self._clients.discard(websocket)

        async def serve():
            self._server = await websockets.serve(handler, self.host, self.port)
            print(f"[ws] WebSocket server started on ws://{self.host}:{self.port}")
            await self._server.wait_closed()

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
            elif path == "/api/sim/topology-state":
                self._send_json(self._main_engine_topology_state())
            elif path == "/api/sim/power/state":
                self._send_json(self._sim_power_state())
            elif path == "/api/sim/speed-profile":
                self._send_json(self._speed_profile())
            elif path == "/api/sim/run/export":
                if self.engine is None:
                    self._send_json({"ok": False, "error": "ENGINE_NOT_INITIALIZED"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    self._send_json({"ok": True, "data": self.engine.export_current_run()})
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
                self._serve_static_file(
                    ROOT / "member-c-topology.js", "application/javascript; charset=utf-8"
                )
            elif path == "/api/phase2/member-c/routes":
                self._serve_html_file(ROOT / "member-c-routes.html")
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

            if path in {"/api/sim/start", "/api/sim/pause", "/api/sim/resume", "/api/sim/stop", "/api/sim/step", "/api/sim/tick-interval"}:
                if not self.engine:
                    self._send_json(
                        {"ok": False, "error": "ENGINE_NOT_INITIALIZED"},
                        HTTPStatus.SERVICE_UNAVAILABLE,
                    )
                    return

            if path == "/api/sim/start":
                self.engine.start()
                snap = self.engine.snapshot()
                self._send_json({
                    "ok": True,
                    "action": "start",
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
            elif path == "/api/sim/vehicle-config":
                payload = self._read_json_body()
                vcfg = self.engine.set_vehicle_config(payload)
                self._send_json({"ok": True, "vehicleConfig": vcfg.to_dict()})
            elif path == "/api/sim/manual-mode":
                payload = self._read_json_body()
                enabled = bool(payload.get("enabled", False))
                train_id = str(payload.get("trainId", self.engine.trains[0].train_id if self.engine.trains else "T0901"))
                self._send_json(self.engine.set_manual_mode(train_id, enabled))
            elif path == "/api/sim/manual-command":
                payload = self._read_json_body()
                train_id = str(payload.get("trainId", self.engine.trains[0].train_id if self.engine.trains else "T0901"))
                self._send_json(self.engine.set_manual_command(
                    train_id,
                    float(payload.get("tractionPercent", 0)),
                    float(payload.get("brakePercent", 0)),
                ))
            elif path == "/api/sim/power/faults":
                payload = self._read_json_body()
                self._send_json(self._apply_power_fault(payload))
            elif path == "/api/sim/power/reset":
                self._send_json(self._reset_power_network())
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
                options.append({
                    "segmentId": int(segment_id),
                    "stationCode": station["stationCode"],
                    "stationName": station["stationName"],
                    "directions": list(self.engine.available_initial_directions(str(station["stationCode"]), int(segment_id))),
                })
        return options
    def _main_engine_topology_state(self) -> JsonDict:
        """Adapt the main engine snapshot to the established Member C canvas contract."""
        static = self.service.member_c_static_routes()
        if self.engine is None or self.engine.snapshot() is None:
            return {
                "tick": 0, "simTimeMs": 0, "clockState": "IDLE", "segments": static.get("segments", []),
                "trains": [], "routes": [], "signals": [], "axleSections": [],
                "segTrainColors": {}, "events": [], "occupiedCount": 0,
                "totalAxleSections": len(self.service.line_map.get("axleSections", [])),
                "lockedRouteCount": 0, "requestedRoutesCount": 0,
            }
        snap = self.engine.snapshot()
        assert snap is not None
        interlocking = snap.interlocking
        sections = interlocking.get("sections", [])
        signal_aspects = {
            str(item.get("signalId")): item.get("aspect", "RED")
            for item in interlocking.get("signals", [])
        }
        colors = ("#58a6ff", "#f0c040", "#f778ba", "#39d0c8", "#8fc31f")
        trains = []
        # One occupied detector section can contain a turnout common segment
        # and both branches. The map must paint the physical train footprint,
        # not every Seg that shares its detector state.
        seg_train_colors: dict[int, str] = {}
        for index, train in enumerate(snap.trains):
            segment_id = train.get("currentSegmentId")
            color = colors[index % len(colors)]
            for covered_segment_id in self.engine.section_occupation.covered_segments_for(train.get("trainId", "")):
                seg_train_colors[covered_segment_id] = color
            trains.append({
                "id": train.get("trainId"), "segId": segment_id,
                "offsetM": train.get("currentSegmentOffsetM", 0.0),
                "positionM": train.get("pathPositionM", 0.0),
                "speedMps": train.get("speedMps", 0.0),
                "direction": "FORWARD" if train.get("direction") == "UP" else "BACKWARD",
                "lengthM": 120.0, "phase": train.get("phase", "IDLE"),
                "routeFailureReason": train.get("routeFailureReason"),
                "movementAuthorityReason": train.get("movementAuthorityReason"), "dwellRemainingSec": train.get("dwellRemainingSec", 0.0),
                "routeRetryAtMs": train.get("routeRetryAtMs"), "color": color,
            })
        signals = [
            {"id": item.get("id"), "segId": item.get("segId"), "name": item.get("name", ""),
             "aspect": signal_aspects.get(str(item.get("id")), "RED")}
            for item in static.get("signals", [])
        ]
        routes = interlocking.get("routes", [])
        return {
            "tick": snap.tick, "simTimeMs": snap.sim_time_ms, "clockState": snap.clock_state,
            "segments": static.get("segments", []), "startOptions": self._topology_start_options(),
            "trains": trains, "routes": routes,
            "signals": signals, "axleSections": sections, "switches": interlocking.get("switches", []),
            "segTrainColors": seg_train_colors, "events": [],
            "occupiedCount": sum(1 for item in sections if item.get("occupied")),
            "totalAxleSections": len(sections),
            "lockedRouteCount": sum(1 for item in routes if item.get("state") in ("LOCKED", "APPROACH_LOCKED")),
            "requestedRoutesCount": len(routes),
        }
    def _sim_state(self) -> JsonDict:
        if self.engine is None:
            return {
                "clock": {"state": "IDLE", "simTime": "--:--:--", "tick": 0},
                "trains": [],
                "stations": self.service.station_mappings(),
                "source": "static",
            }
        snap = self.engine.snapshot()
        if snap is None:
            return {"clock": {"state": "STOPPED", "simTime": "--:--:--", "tick": 0}, "trains": [], "source": "static"}
        return {
            "clock": {
                "state": snap.clock_state,
                "simTime": snap.sim_time_str,
                "tick": snap.tick,
                "simTimeMs": snap.sim_time_ms,
                "tickIntervalMs": round(self.engine._tick_interval_seconds * 1000),
            },
            "trains": snap.trains,
            "stations": snap.stations,
            "power": snap.power,
            "powerNetwork": snap.power_network,
            "dispatchDecisions": snap.dispatch_decisions,
            "kpi": snap.kpi,
            "source": "simulation-engine",
        }

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
        if fault_type != "SUBSTATION_OUTAGE" or not target_id:
            return {"ok": False, "error": "UNSUPPORTED_POWER_FAULT"}
        result = self.engine.queue_power_command(
            "SUBSTATION_OUTAGE",
            {
                "targetId": target_id,
                "bigBilateral": str(payload.get("mode", "N_MINUS_1_BIG_BILATERAL")) == "N_MINUS_1_BIG_BILATERAL",
            },
        )
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

    def _read_json_body(self) -> JsonDict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

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
        return self.engine.set_manual_mode(train_id, bool(payload.get("enabled", False)))

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

    def _serve_html_file(self, file_path: Path) -> None:
        self._serve_static_file(file_path, "text/html; charset=utf-8")

    def _serve_static_file(self, file_path: Path, content_type: str) -> None:
        body = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self._send_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
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

    # ── WebSocket ──
    ws = WebSocketBroadcaster(args.host, args.ws_port)
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
    print("  POST /api/sim/start")
    print("  POST /api/sim/pause")
    print("  POST /api/sim/resume")
    print("  POST /api/sim/stop")
    try:
        server.serve_forever()
    finally:
        if ApiHandler.engine:
            ApiHandler.engine.stop()


if __name__ == "__main__":
    main()
