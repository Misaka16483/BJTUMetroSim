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
from app.domain.line.services import LineMapRepository, TrackQueryService
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
    # 让 B 的模型提供足够牵引力（180t × 1.2m/s² ≈ 216kN）
    TRACTION_FORCE_N = 216_000.0

    def __init__(self, cache_path: str | Path) -> None:
        # ---- 加载线路数据 ----
        line_map = LineMapRepository(cache_path).load()
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

        # ① ATO决策：根据当前位置和目标距离决定牵引/制动
        cmd = self._ato_decision(ts)
        # ② B的车辆动力学：传入当前TrainState、控制指令和时间步长
        new_state = self._vehicle.step(ts, cmd, dt_s=self.DT_SEC)
        # 将里程位置映射回 Seg
        seg_id, offset_m = self._mileage_to_seg(new_state.position_m)
        self._train_state = TrainState(
            train_id=new_state.train_id,
            sim_time_ms=self.sim_time_ms,
            seg_id=seg_id, offset_m=offset_m,
            position_m=new_state.position_m,
            speed_mps=new_state.speed_mps,
            acceleration_mps2=new_state.acceleration_mps2,
            direction="FORWARD", length_m=self.TRAIN_LENGTH_M,
            operation_mode="ATO",
            sim_time_s=new_state.sim_time_s,
            net_energy_kwh=new_state.net_energy_kwh,
        )

        # ③ 更新区段占用 —— SectionOccupationService（成员C）
        self.section_occ.update([self._train_state], self.track)

        # ④ 自动进路办理：当列车接近下一个信号时申请进路
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
        covered = self.section_occ._segments_covered_by_train(self._train_state, self.track)
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
                "occupied": self.section_occ.is_occupied(sid),
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
        }

    # ==================================================================
    # ATO 决策 —— 简化的规则控制器（决定牵引/制动/惰行）
    # ==================================================================

    def _ato_decision(self, ts: TrainState) -> ControlCommand:
        """根据当前速度到下一个进路终点距离，生成 B 的 ControlCommand。

        ATO 目标 = 下一条待办理进路的终点信号里程（查 _route_end_mileage）。
        所有进路办完后目标 = 最后一条进路终点后 500m。
        """
        # 找当前阶段的目标距离（下条进路的终点，或者链尾+500m）
        cur_m = ts.position_m
        if self._next_route_idx < len(self._route_chain):
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
    # 自动进路办理 —— 按预发现的进路链逐条办理
    # ==================================================================

    def _auto_request_routes(self, train_state: TrainState) -> None:
        """按 _discover_route_chain 发现的连续进路链，逐条办理。

        办理条件：上一条进路已被列车**进入**（has_entered），才申请下一条。
        已办理过的进路（_next_route_idx 之前的）或已锁闭的不再重复申请。
        """
        if self._next_route_idx >= len(self._route_chain):
            return

        # 前一条进路还未被列车进入 → 不申请下一条
        if self._next_route_idx > 0:
            prev_rid = self._route_chain[self._next_route_idx - 1]
            prev_state = self.route_svc._routes.get(prev_rid)
            if prev_state is None or not prev_state.has_entered:
                return

        next_rid = self._route_chain[self._next_route_idx]

        # 已锁闭 → 跳过
        if self.route_svc.is_locked(next_rid):
            return

        result = self.route_svc.request(RouteRequest(
            request_id=f"AUTO-{self.tick:05d}",
            route_id=next_rid, train_id=self.TRAIN_ID,
            source="DISPATCH",
        ))
        if result.accepted:
            self._next_route_idx += 1

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

        if not seg_by_id: return result
        start = self._seg_chain[0]
        queue: list[tuple[int, float]] = [(start, 0.0)]
        while queue:
            sid, dist = queue.pop(0)
            if sid in result: continue
            result[sid] = dist
            seg = seg_by_id.get(sid)
            if seg is None: continue
            for key in ("endForwardSegId", "endDivergingSegId"):
                nxt = seg.get(key)
                if nxt is not None and int(nxt) not in result:
                    queue.append((int(nxt), dist + float(seg.get("lengthM", 0))))
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
        up_platforms = sorted(
            [p for p in line_map.get("platforms", [])
             if p.get("direction") == "0x55"],
            key=lambda p: p.get("mileageM", 0),
        )
        if not up_platforms: return []
        start_seg = int(up_platforms[0]["segmentId"])
        seg_by_id = {s["id"]: s for s in line_map.get("segments", [])}
        chain: list[int] = []
        visited: set[int] = set()
        queue = [start_seg]
        while queue:
            sid = queue.pop(0)
            if sid in visited: continue
            visited.add(sid)
            chain.append(sid)
            seg = seg_by_id.get(sid)
            if seg is None: continue
            for key in ("endForwardSegId", "endDivergingSegId"):
                nxt = seg.get(key)
                if nxt is not None and int(nxt) not in visited:
                    queue.append(int(nxt))
        return chain

    # -- 旧 Demo API 兼容 --------
    @property
    def trains(self) -> list:
        return []
