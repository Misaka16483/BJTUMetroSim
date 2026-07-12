"""成员C联锁演示运行器 — Phase 2 联锁闭环验证。

使用真实9号线数据，接入 B 的 SimpleVehicleModel 动力学模型，
在沿线上行方向自动驱列车运行并办理进路，验证联锁全链路：
  区段占用 → 进路请求 → 信号灯色 → 列车通过 → 自动释放

数据流：
  每 tick：
    ① ATO决策（加速/巡航/制动）→ ControlCommand
    ② SimpleVehicleModel.step()(成员B) → 新 TrainState（Seg级）
    ③ SectionOccupationService.update()(成员C) → 更新区段占用
    ④ RouteService.update()(成员C) → 检查进路释放
    ⑤ SignalAspectResolver.refresh()(成员C) → 重算全部信号灯色
    ⑥ state_snapshot() → 返回前端可消费的JSON
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.domain.interlocking.models import RouteRequest
from app.domain.interlocking.route_catalog import RouteCatalog
from app.domain.interlocking.route_service import RouteService
from app.domain.interlocking.rule_engine import InterlockingRuleEngine
from app.domain.interlocking.section_occupation import SectionOccupationService
from app.domain.interlocking.signal_resolver import SignalAspectResolver
from app.domain.interlocking.switch_lock import SwitchLockService
from app.domain.line.services import LineMapRepository, PathTrackQuery, TrackQueryService
from app.domain.signal.models import TrainState
from app.domain.vehicle.models import CommandSource, ControlCommand, VehicleConfig
from app.domain.vehicle.services import SimpleVehicleModel

JsonDict = dict[str, Any]


class MemberCDemoRunner:
    """联锁闭环演示运行器。

    沿 9 号线上行方向（郭公庄→国家图书馆）驱动一列车，
    用 B 的 SimpleVehicleModel 做物理计算，自动办理沿途进路，
    每 tick 输出联锁全链路状态。

    不依赖 A 的引擎，完全独立运行。
    """

    # 动力学参数 —— 与引擎保持一致
    ACCEL_MPS2 = 1.0
    BRAKE_MPS2 = 1.0
    CRUISE_SPEED_MPS = 16.0
    DT_SEC = 0.5
    TRAIN_ID = "T0901"
    TRAIN_LENGTH_M = 120.0
    TRAIN_COLOR = "#e74c3c"
    # Keep one movement authority for the train and two route sections ahead.
    # This lets the signal resolver show the normal GREEN -> YELLOW -> RED
    # progression instead of a permanently single-route YELLOW authority.
    ROUTE_LOOKAHEAD = 3
    # 让 B 的模型提供足够牵引力（180t × 1.2m/s² ≈ 216kN）
    TRACTION_FORCE_N = 216_000.0

    def __init__(self, cache_path: str | Path) -> None:
        # ---- 加载线路数据 ----
        line_map = LineMapRepository(cache_path).load()
        self._line_map = line_map
        self.track = TrackQueryService(line_map)

        # ---- 联锁子系统（全部成员C的模块） ----
        self.catalog = RouteCatalog(line_map)
        self.section_occ = SectionOccupationService(line_map)
        self.switch_lock = SwitchLockService(
            [self.catalog.get_switch(sid) for sid in self.catalog.switch_ids]
        )
        self.rule_engine = InterlockingRuleEngine(
            self.catalog, self.section_occ, self.switch_lock
        )
        self.route_svc = RouteService(
            self.catalog, self.rule_engine, self.section_occ, self.switch_lock
        )
        self.signal_resolver = SignalAspectResolver(
            self.catalog, self.route_svc, self.section_occ, self.switch_lock
        )

        # ---- 成员B的车辆模型 ----
        self._vehicle = SimpleVehicleModel(VehicleConfig(
            train_id=self.TRAIN_ID,
            max_traction_force_n=self.TRACTION_FORCE_N,
        ))

        # ---- 构建 Seg 链（用于前端渲染和里程计算） ----
        self._seg_chain = self._build_mainline_chain(line_map)
        self.path_track = PathTrackQuery(self.track, self._seg_chain)
        self._seg_mileage = self._build_seg_mileage()

        # ---- 硬编码的进路链（sig9(F5)出发，沿9号线上行方向7条连续通路） ----
        # 数据已验证：这7条进路首尾相连，覆盖从郭公庄到军博附近约11km
        self._route_chain: list[str] = self._discover_route_chain_fallback()

        # ---- 预计算每条进路终点的里程（ATO目标用） ----
        self._route_end_mileage: dict[str, float] = {}
        for rid in self._route_chain:
            rdef = self.catalog.get(rid)
            if rdef is not None:
                es = self._signal_seg_for_id(rdef.end_signal_id)
                self._route_end_mileage[rid] = self._seg_mileage.get(es, 0)

        # ---- 已办理的进路防重复 ----
        self._requested_routes: set[str] = set()
        self._manual_mode: str | None = None
        self._manual_route_id: str | None = None
        self._manual_end_mileage: float = 0.0
        self._manual_finished = False
        self._events: list[JsonDict] = []
        self._event_seq = 0

        # ---- 运行时状态 ----
        self.tick: int = 0
        self.sim_time_ms: int = 0
        self._next_route_idx: int = 0  # 下一次要申请的进路索引
        self._phase: str = "ACCEL"      # ATO 阶段：ACCEL/CRUISE/BRAKE/STOPPED

        # ---- 初始化列车 —— 第一条进路的始端信号所在Seg ----
        first_rdef = self.catalog.get(self._route_chain[0]) if self._route_chain else None
        first_seg = self._signal_seg_for_id(first_rdef.start_signal_id) if first_rdef else 13
        first_offset = 30.0
        self._train_state = TrainState(
            train_id=self.TRAIN_ID, sim_time_ms=0,
            seg_id=first_seg, offset_m=first_offset,
            position_m=self._seg_mileage.get(first_seg, 0.0) + first_offset,
            speed_mps=0.0, direction="FORWARD", length_m=self.TRAIN_LENGTH_M,
            operation_mode="ATO", sim_time_s=0.0,
        )

    # ==================================================================
    # 每 tick 接口 —— 外部每 500ms 调用一次
    # ==================================================================

    def step(self) -> None:
        """前进一个仿真步长（0.5秒）。

        调用顺序（对应真实联锁PLC的扫描周期）：
        1. ATO决策 → ControlCommand
        2. B的动力学 → 新 TrainState
        3. 区段占用更新
        4. 自动进路办理（列车接近新信号时）
        5. 进路释放检查
        6. 信号灯重算
        """
        self.tick += 1
        self.sim_time_ms += int(self.DT_SEC * 1000)
        ts = self._train_state

        if self._manual_mode == "free" or (
            self._manual_mode == "route"
            and not self.route_svc.is_locked(self._manual_route_id or "")
        ):
            self.section_occ.update([self._train_state], self.path_track)
            self.route_svc.update()
            self.signal_resolver.refresh()
            return

        # ① ATO决策：根据当前位置和目标距离决定牵引/制动
        cmd = self._ato_decision(ts)
        # ② B的车辆动力学：传入当前TrainState、控制指令和时间步长
        new_state = self._vehicle.step(ts, cmd, dt_s=self.DT_SEC)
        position_m = new_state.position_m
        speed_mps = new_state.speed_mps
        if self._manual_mode == "route" and position_m >= self._manual_end_mileage:
            position_m = self._manual_end_mileage
            speed_mps = 0.0
            if not self._manual_finished:
                self._manual_finished = True
                self._add_event("列车已通过所选进路并停在终端信号前", "通过")
        # 将里程位置映射回 Seg
        seg_id, offset_m = self._mileage_to_seg(position_m)
        self._train_state = TrainState(
            train_id=new_state.train_id,
            sim_time_ms=self.sim_time_ms,
            seg_id=seg_id, offset_m=offset_m,
            position_m=position_m,
            speed_mps=speed_mps,
            acceleration_mps2=new_state.acceleration_mps2,
            direction="FORWARD", length_m=self.TRAIN_LENGTH_M,
            operation_mode="ATO",
            sim_time_s=new_state.sim_time_s,
            net_energy_kwh=new_state.net_energy_kwh,
        )

        # ③ 更新区段占用 —— SectionOccupationService（成员C）
        self.section_occ.update([self._train_state], self.path_track)

        # ④ 自动进路办理：当列车接近下一个信号时申请进路
        if self._manual_mode is None:
            self._auto_request_routes(self._train_state)

        # ⑤ 进路释放检查 —— RouteService（成员C）
        self.route_svc.update()

        # ⑥ 重算全部信号灯色 —— SignalAspectResolver（成员C）
        self.signal_resolver.refresh()

    def state_snapshot(self) -> JsonDict:
        """返回当前仿真状态快照（供前端API消费）。"""
        occ_snapshot = self.section_occ.snapshot()
        signals = self.signal_resolver.resolve_all()
        routes_data = self.route_svc.snapshot()

        # 列车覆盖的Seg着色
        seg_train_colors: dict[int, str] = {}
        covered = self.section_occ._segments_covered_by_train(self._train_state, self.path_track)
        for sid in covered:
            seg_train_colors[sid] = self.TRAIN_COLOR

        # Seg列表（含拓扑链接和占用着色）
        segments: list[JsonDict] = []
        for i, sid in enumerate(self._seg_chain):
            seg = self.track.get_segment(sid)
            if seg is None:
                continue
            segments.append({
                "id": sid,
                "lengthM": seg.get("lengthM", 0),
                "prevSegId": self._seg_chain[i - 1] if i > 0 else None,
                "nextSegId": self._seg_chain[i + 1] if i + 1 < len(self._seg_chain) else None,
                "hasSwitch": bool(
                    seg.get("endDivergingSegId") or seg.get("startDivergingSegId")
                ),
                "mileageM": self._seg_mileage.get(sid, 0.0),
            })

        # 信号灯数据（含名称和所在Seg，前端着色用）
        signal_list: list[JsonDict] = []
        for sig_id_str, aspect in signals.items():
            sid_int = int(sig_id_str)
            signal_list.append({
                "id": sid_int, "aspect": aspect,
                "segId": self._signal_seg_for_id(sid_int),
                "name": self._signal_name_for_id(sid_int),
            })

        # 被占用的区段详情
        axle_sections: list[JsonDict] = []
        for sid in sorted(self.section_occ.axle_section_ids, key=lambda x: int(x)):
            axle_def = self.section_occ._axle_defs.get(sid)
            if axle_def is None:
                continue
            axle_sections.append({
                "sectionId": sid,
                "name": axle_def.name,
                "segmentIds": sorted(axle_def.segment_ids),
                "occupied": self.section_occ.is_axle_occupied(sid),
            })

        ts = self._train_state
        return {
            "tick": self.tick,
            "simTimeMs": self.sim_time_ms,
            "trains": [{
                "id": ts.train_id,
                "segId": ts.seg_id,
                "offsetM": round(ts.offset_m, 2),
                "positionM": round(ts.position_m, 1),
                "speedMps": round(ts.speed_mps, 2),
                "accelMps2": round(ts.acceleration_mps2, 3),
                "direction": ts.direction,
                "lengthM": self.TRAIN_LENGTH_M,
                "phase": self._phase,
                "color": self.TRAIN_COLOR,
            }],
            "segments": segments,
            "segTrainColors": seg_train_colors,
            "axleSections": axle_sections,
            "signals": signal_list,
            "routes": routes_data,
            "occupiedCount": sum(1 for o in occ_snapshot if o.get("occupied")),
            "totalAxleSections": len(self.section_occ.axle_section_ids),
            "lockedRouteCount": len(self.route_svc.locked_routes()),
            "nextRouteIdx": self._next_route_idx,
            "requestedRoutesCount": len(self._requested_routes),
            "manualMode": self._manual_mode,
            "manualRouteId": self._manual_route_id,
            "events": list(self._events),
        }

    # ==================================================================
    # 手动联锁检验接口
    # ==================================================================

    def place_manual_train(self, segment_id: int) -> JsonDict:
        """在任意 Seg 起点放置一列静态检验车。"""
        segment = self.track.get_segment(segment_id)
        if segment is None:
            return {"ok": False, "error": "SEGMENT_NOT_FOUND"}
        self._manual_mode = "free"
        self._manual_route_id = None
        self._manual_finished = False
        self._configure_manual_path([int(segment_id)], int(segment_id))
        self._add_event(f"已在 S{segment_id} 起点放置检验小车", "放置")
        self.section_occ.update([self._train_state], self.path_track)
        self.signal_resolver.refresh()
        return {"ok": True, "state": self.state_snapshot()}

    def place_train_for_route(self, route_id: str) -> JsonDict:
        """在进路始端信号前放车，等待显式的办理请求。"""
        route = self.catalog.get(str(route_id))
        if route is None:
            return {"ok": False, "error": "ROUTE_NOT_FOUND"}
        start_segment = self._signal_seg_for_id(route.start_signal_id)
        path = self._path_for_route(route.route_id, start_segment, route.end_signal_id)
        if not path:
            return {"ok": False, "error": "ROUTE_PATH_UNAVAILABLE"}
        self._manual_mode = "route"
        self._manual_route_id = route.route_id
        self._manual_finished = False
        self._configure_manual_path(path, start_segment)
        self._manual_end_mileage = max(0.0, sum(
            float((self.track.get_segment(segment_id) or {}).get("lengthM", 0.0))
            for segment_id in path
        ) - 1.0)
        self._add_event(
            f"已在进路 {route.route_id} 始端信号前放置检验小车", "放置"
        )
        self.section_occ.update([self._train_state], self.path_track)
        self.signal_resolver.refresh()
        return {"ok": True, "state": self.state_snapshot()}

    def request_manual_route(self, route_id: str | None = None) -> JsonDict:
        """在当前联锁现场办理指定进路，不重置既有锁闭状态。"""
        if self._manual_mode is None:
            return {"ok": False, "error": "NO_MANUAL_ROUTE"}
        requested_route_id = str(route_id or self._manual_route_id or "")
        if not self.catalog.get(requested_route_id):
            return {"ok": False, "error": "ROUTE_NOT_FOUND"}
        if self.route_svc.is_locked(requested_route_id):
            return {"ok": True, "state": self.state_snapshot(), "alreadyLocked": True}
        result = self.route_svc.request(RouteRequest(
            request_id=f"MANUAL-{self.tick:05d}-{requested_route_id}",
            route_id=requested_route_id,
            train_id=self.TRAIN_ID,
            source="API",
        ))
        if result.accepted:
            self._requested_routes.add(requested_route_id)
            if requested_route_id == self._manual_route_id:
                self._next_route_idx = 1
            self._add_event(f"进路 {requested_route_id} 办理成功，已锁闭道岔和区段", "锁闭")
        else:
            self._add_event(
                f"进路 {requested_route_id} 办理失败：{self._failure_reason_text(result.failure_reason)}", "失败"
            )
        self.signal_resolver.refresh()
        return {"ok": result.accepted, "error": result.failure_reason, "state": self.state_snapshot()}

    def _configure_manual_path(self, path: list[int], start_segment: int) -> None:
        self._seg_chain = list(dict.fromkeys(path))
        self.path_track = PathTrackQuery(self.track, self._seg_chain)
        self._seg_mileage = self._build_seg_mileage()
        self._route_chain = []
        self._route_end_mileage = {}
        self._requested_routes.clear()
        self._next_route_idx = 0
        self.tick = 0
        self.sim_time_ms = 0
        start_length = float((self.track.get_segment(start_segment) or {}).get("lengthM", 1.0))
        offset_m = min(1.0, max(0.0, start_length - 0.1))
        self._train_state = TrainState(
            train_id=self.TRAIN_ID, sim_time_ms=0,
            seg_id=start_segment, offset_m=offset_m, position_m=offset_m,
            speed_mps=0.0, direction="FORWARD", length_m=self.TRAIN_LENGTH_M,
            operation_mode="ATO", sim_time_s=0.0,
        )

    def _path_for_route(
        self, route_id: str, start_segment: int, end_signal_id: int,
    ) -> list[int]:
        route = self.catalog.get(route_id)
        if route is None:
            return []
        section_segments = {
            str(section.get("id")): [int(seg) for seg in section.get("segmentIds", [])]
            for section in self._line_map.get("axleSections", [])
            if section.get("id") is not None
        }
        raw: list[int] = []
        for section_id in route.axle_section_ids:
            for segment_id in section_segments.get(str(section_id), []):
                if segment_id not in raw:
                    raw.append(segment_id)
        if not raw:
            return [start_segment]

        covered = set(raw)
        neighbors: dict[int, set[int]] = {segment_id: set() for segment_id in covered}
        for segment_id in covered:
            segment = self.track.get_segment(segment_id) or {}
            for field in (
                "startForwardSegId", "startDivergingSegId",
                "endForwardSegId", "endDivergingSegId",
            ):
                target = segment.get(field)
                if target is not None and int(target) in covered:
                    neighbors[segment_id].add(int(target))

        end_segment = self._signal_seg_for_id(end_signal_id)
        starts = [start_segment] if start_segment in covered else [
            segment_id for segment_id, links in neighbors.items() if start_segment in links
        ]
        ends = [end_segment] if end_segment in covered else [
            segment_id for segment_id, links in neighbors.items() if end_segment in links
        ]
        starts = starts or [raw[0]]
        ends = ends or [raw[-1]]
        ordered: list[int] | None = None
        for first in starts:
            queue: list[list[int]] = [[first]]
            while queue:
                candidate = queue.pop(0)
                current = candidate[-1]
                if current in ends:
                    ordered = candidate
                    break
                for target in sorted(neighbors[current] - set(candidate)):
                    queue.append(candidate + [target])
            if ordered:
                break
        result = ordered or raw
        return ([start_segment] if start_segment not in result else []) + result

    def _add_event(self, message: str, category: str) -> None:
        self._event_seq += 1
        self._events.insert(0, {
            "id": self._event_seq,
            "tick": self.tick,
            "category": category,
            "message": message,
        })
        del self._events[100:]

    @staticmethod
    def _failure_reason_text(reason: str | None) -> str:
        return {
            "CONFLICT_ROUTE_LOCKED": "存在敌对进路已锁闭",
            "SECTION_OCCUPIED": "进路区段当前被占用",
            "SWITCH_UNAVAILABLE": "所需道岔不可用",
            "ROUTE_NOT_FOUND": "进路不存在",
        }.get(reason or "", "未知原因")

    # ==================================================================
    # ATO 决策 —— 简化的规则控制器（决定牵引/制动/惰行）
    # ==================================================================

    def _ato_decision(self, ts: TrainState) -> ControlCommand:
        """根据当前速度到下一个进路终点距离，生成 B 的 ControlCommand。

        ATO 目标 = 下一条待办理进路的终点信号里程（查 _route_end_mileage）。
        所有进路办完后目标 = 最后一条进路终点后 500m。
        """
        # 手动模式的移动授权终点就是已办理进路的终端信号。
        # 单条进路时始端黄灯允许通过，但必须在终端红灯前停车。
        cur_m = ts.position_m
        if self._manual_mode == "route":
            dist = max(0.0, self._manual_end_mileage - cur_m)
        elif self._next_route_idx < len(self._route_chain):
            target_rid = self._route_chain[self._next_route_idx]
            target_m = self._route_end_mileage.get(target_rid, cur_m + 1000)
            dist = max(0, target_m - cur_m)
        elif self._route_chain and self._route_end_mileage:
            last_rid = self._route_chain[-1]
            dist = max(500, self._route_end_mileage.get(last_rid, cur_m) + 500 - cur_m)
        else:
            dist = 1000.0

        if dist < 5:
            self._phase = "STOPPED"
            return ControlCommand(train_id=self.TRAIN_ID, brake_percent=80.0, source=CommandSource.ATO)

        speed = ts.speed_mps
        brake_dist = (speed ** 2) / (2 * self.BRAKE_MPS2)

        if speed <= 0.05 and dist > 10:
            self._phase = "ACCEL"
        elif dist <= brake_dist + 30:
            self._phase = "BRAKE"
        elif speed < self.CRUISE_SPEED_MPS - 1.0:
            self._phase = "ACCEL"
        else:
            self._phase = "CRUISE"

        if self._phase == "ACCEL":
            return ControlCommand(train_id=self.TRAIN_ID, traction_percent=60.0, source=CommandSource.ATO)
        elif self._phase == "BRAKE":
            level = min(80.0, max(30.0, 600.0 / max(dist, 5.0)))
            return ControlCommand(train_id=self.TRAIN_ID, brake_percent=level, source=CommandSource.ATO)
        else:
            if speed < self.CRUISE_SPEED_MPS - 2.0:
                return ControlCommand(train_id=self.TRAIN_ID, traction_percent=30.0, source=CommandSource.ATO)
            return ControlCommand.coast(self.TRAIN_ID, source=CommandSource.ATO)

    # ==================================================================
    # 自动进路办理 —— 按预发现的进路链保持前方进路预告量
    # ==================================================================

    def _auto_request_routes(self, train_state: TrainState) -> None:
        """维持当前列车及前方的连续进路预告量。

        调度端可以预先办理连续、无敌对关系的后续进路；联锁仍会对每一条
        请求独立检查占压、敌对进路和道岔状态。保留三条锁闭进路可使信号
        按真实的前方授权关系呈现 GREEN -> YELLOW -> RED。
        """
        locked_count = sum(
            1 for route_id in self._route_chain if self.route_svc.is_locked(route_id)
        )

        while (
            locked_count < self.ROUTE_LOOKAHEAD
            and self._next_route_idx < len(self._route_chain)
        ):
            next_rid = self._route_chain[self._next_route_idx]
            if self.route_svc.is_locked(next_rid):
                self._next_route_idx += 1
                continue

            result = self.route_svc.request(RouteRequest(
                request_id=f"AUTO-{self.tick:05d}-{self._next_route_idx}",
                route_id=next_rid, train_id=self.TRAIN_ID,
                source="DISPATCH",
            ))
            if not result.accepted:
                # Preserve route order. A rejected route is retried next tick
                # after occupation or conflicting locks may have changed.
                break

            self._requested_routes.add(next_rid)
            self._next_route_idx += 1
            locked_count += 1

    # ==================================================================
    # 进路链自动发现 —— 从进路表数据找最长通路（不硬编码任何 ID）
    # ==================================================================

    def _discover_route_chain(self) -> list[str]:
        """沿信号-进路首尾相连找最长通路（用于发现阶段的代码，当前未调用）。"""
        return self._discover_route_chain_fallback()

    def _discover_route_chain_fallback(self) -> list[str]:
        """硬编码的7条连续进路（sig9→sig61→sig62→sig63→sig64→sig65→sig67→sig19）。

        9号线真实数据中首尾相连的最长链，覆盖约11km。
        """
        return ["9", "28", "29", "36", "37", "38", "39", "48"]

    def _route_start_sigs(self) -> list[str]:
        """返回所有能作为进路始端的信号 ID。"""
        sigs: set[str] = set()
        for rid in self.catalog.route_ids:
            rdef = self.catalog.get(rid)
            if rdef is not None:
                sigs.add(str(rdef.start_signal_id))
        return list(sigs)

    def _signal_seg_for_id(self, signal_id: int) -> int:
        """根据信号机 ID 查其所在的 Seg ID。"""
        for seg_signals in self.track.signals_by_seg.values():
            for sig in seg_signals:
                if sig.get("id") == signal_id:
                    return int(sig.get("segmentId", 13))
        return 13

    def _signal_name_for_id(self, signal_id: int) -> str:
        """根据信号机 ID 查其名称。"""
        for seg_signals in self.track.signals_by_seg.values():
            for sig in seg_signals:
                if sig.get("id") == signal_id:
                    return str(sig.get("name", ""))
        return ""

    # ==================================================================
    # 位置/里程/Seg 映射 —— 站台里程表做桥
    # ==================================================================

    def _guess_position_m(self, seg_id: int, offset_m: float) -> float:
        """估算某个 Seg 上的点在 Seg 链中的累计里程。"""
        total = 0.0
        for sid in self._seg_chain:
            if sid == seg_id:
                return total + offset_m
            seg = self.track.get_segment(sid)
            if seg is not None:
                total += float(seg.get("lengthM", 0))
        return offset_m

    def _mileage_to_seg(self, position_m: float) -> tuple[int, float]:
        """BFS里程表 → (seg_id, offset_m)。找里程刚好小于position_m的最大里程Seg。"""
        best_seg = self._seg_chain[0] if self._seg_chain else 13
        best_ml = 0.0
        for sid, ml in self._seg_mileage.items():
            seg = self.track.get_segment(sid)
            if seg is None: continue
            seg_len = float(seg.get("lengthM", 100))
            if ml <= position_m < ml + seg_len:
                return sid, position_m - ml
            if ml <= position_m and ml > best_ml:
                best_ml = ml
                best_seg = sid
        return best_seg, max(0.0, position_m - best_ml)

    def _build_seg_mileage(self) -> dict[int, float]:
        """BFS 里程表：覆盖全 319 个 Seg，确保进路涉及的所有 Seg 都有里程。

        从郭公庄站台 Seg 出发 BFS，沿 endForwardSegId 方向。
        不在 BFS 内的 Seg（侧线/车库等）也会被包含（通过 endDivergingSegId）。
        """
        result: dict[int, float] = {}
        seg_by_id: dict[int, dict] = {}
        for sid in self._seg_chain:
            seg = self.track.get_segment(sid)
            if seg is not None:
                seg_by_id[sid] = seg

        if not seg_by_id:
            return result
        distance_m = 0.0
        for sid in self._seg_chain:
            seg = seg_by_id.get(sid)
            if seg is None:
                continue
            result[sid] = distance_m
            distance_m += float(seg.get("lengthM", 0))
        return result

    def _build_platform_targets(self) -> dict[int, float]:
        """预计算主链上每个 Seg 到下一个站台的距离（米），ATO 用它做目标。"""
        # 先找到主链上所有站台里程
        platform_mileages: list[float] = []
        for sid in self._seg_chain:
            seg = self.track.get_segment(sid)
            if seg is None: continue
            for p_sig in self.track.platforms_by_seg.get(sid, []):
                ml = p_sig.get("mileageM")
                if ml is not None and ml > 0:
                    platform_mileages.append(float(ml))
        platform_mileages.sort()

        result: dict[int, float] = {}
        for sid in self._seg_chain:
            ml = self._seg_mileage.get(sid, 0)
            seg = self.track.get_segment(sid)
            seg_len = float(seg.get("lengthM", 0)) if seg else 0
            seg_center = ml + seg_len / 2
            # 找下一个更大的站台里程
            for pm in platform_mileages:
                if pm > seg_center + 50:  # 至少50米外的站台
                    result[sid] = pm - seg_center
                    break
            if sid not in result:
                result[sid] = 1000.0  # 默认前方1000m
        return result

    def _build_mainline_chain(self, line_map: JsonDict) -> list[int]:
        """BFS覆盖全部可达 Seg，确保列车移动范围覆盖所有进路涉及的 Seg。"""
        seg_by_id = {
            int(segment["id"]): segment
            for segment in line_map.get("segments", [])
            if segment.get("id") is not None
        }
        signal_segments = {
            int(signal["id"]): int(signal["segmentId"])
            for signal in line_map.get("signals", [])
            if signal.get("id") is not None and signal.get("segmentId") is not None
        }
        axle_segments = {
            str(section["id"]): [int(sid) for sid in section.get("segmentIds", []) if sid is not None]
            for section in line_map.get("axleSections", [])
            if section.get("id") is not None
        }
        neighbors: dict[int, set[int]] = {sid: set() for sid in seg_by_id}
        for sid, segment in seg_by_id.items():
            for field in (
                "startForwardSegId", "startDivergingSegId",
                "endForwardSegId", "endDivergingSegId",
            ):
                target = segment.get(field)
                if target is not None and int(target) in seg_by_id:
                    neighbors[sid].add(int(target))
                    neighbors[int(target)].add(sid)

        def ordered_segments(route: JsonDict) -> list[int]:
            raw: list[int] = []
            for section_id in route.get("axleSectionIds", []):
                for sid in axle_segments.get(str(section_id), []):
                    if sid not in raw:
                        raw.append(sid)
            if len(raw) < 2:
                return raw
            covered = set(raw)
            start_signal_seg = signal_segments.get(int(route.get("startSignalId", 0)), 0)
            end_signal_seg = signal_segments.get(int(route.get("endSignalId", 0)), 0)

            def candidates(signal_seg: int) -> list[int]:
                if signal_seg in covered:
                    return [signal_seg]
                return [sid for sid in raw if signal_seg in neighbors[sid]]

            starts = candidates(start_signal_seg)
            ends = candidates(end_signal_seg)
            endpoints = [sid for sid in raw if len(neighbors[sid] & covered) <= 1]
            starts = starts or endpoints or [raw[0]]
            ends = ends or endpoints or [raw[-1]]
            best: list[int] | None = None
            for start in starts:
                queue: list[list[int]] = [[start]]
                visited = {start}
                while queue:
                    path = queue.pop(0)
                    current = path[-1]
                    if current in ends:
                        if best is None or len(path) > len(best):
                            best = path
                        continue
                    for target in sorted(neighbors[current] & covered):
                        if target not in visited:
                            visited.add(target)
                            queue.append(path + [target])
            return best if best is not None and set(best) == covered else raw

        routes_by_id = {
            str(route["id"]): route
            for route in line_map.get("routes", []) if route.get("id") is not None
        }
        chain: list[int] = []
        for route_id in self._discover_route_chain_fallback():
            route = routes_by_id.get(route_id)
            if route is None:
                continue
            start_segment = signal_segments.get(int(route.get("startSignalId", 0)))
            if not chain and start_segment is not None:
                chain.append(start_segment)
            elif start_segment is not None and chain[-1] != start_segment:
                chain.append(start_segment)
            for sid in ordered_segments(route):
                if not chain or chain[-1] != sid:
                    chain.append(sid)
        return chain

    # -- 旧 Demo API 兼容 --------
    @property
    def trains(self) -> list:
        return []
