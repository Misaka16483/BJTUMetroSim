"""区段占用检测服务 —— 成员 C Phase 2。

职责：根据列车位置（TrainState）推导计轴区段/逻辑区段的占用状态。

此服务是联锁子系统的"感知层"，对应真实铁路中的轨道电路检测：
- 主循环（成员 A）每 tick 调用 update()，传入所有列车的 TrainState 列表
- 联锁规则引擎调用 is_occupied() / occupied_by() 查询区段是否空闲
- RouteService 的 _release_cleared_sections() 调用 occupied_by() 判断列车是否已离开

数据流：
  VehicleService(成员B) → TrainState[] → 主循环(成员A)
    → SectionOccupationService.update(train_states, track_query)
    → 内部更新 self._occupancy 缓存
    → is_occupied() / occupied_by() 供 InterlockingRuleEngine / RouteService 查询
"""

from __future__ import annotations

from typing import Any

from app.domain.interlocking.models import AxleSectionDef, SectionOccupation

JsonDict = dict[str, Any]


class SectionOccupationService:
    """维护计轴区段和逻辑区段的占用状态。

    不直接读 MessageBus，也不读 TrainState——所有输入由主循环传入。
    内部维护一个 self._occupancy 字典（section_id → SectionOccupation），
    每个 tick 全量重算：先清空，再根据传入的所有列车位置重新判定。

    设计要点：
    - 每个计轴区段包含一组 Seg ID（从 line_map 加载）
    - 列车的 seg_id 落在某区段的 Seg 集合内 → 该区段被占用
    - 车长可能跨越多个 Seg（车尾拖在前一个 Seg 上）→ 拓扑回溯
    - 逻辑区段（用于 MA 计算）暂不做占用检测，Phase 2 后期补
    """

    def __init__(self, line_map: JsonDict) -> None:
        """从 line_map.json 加载区段定义和 Seg 长度。

        由主循环或测试代码构造。不依赖任何外部服务。
        """

        # ---- 计轴区段定义（区段ID → AxleSectionDef）----
        self._axle_defs: dict[str, AxleSectionDef] = {}
        for raw in line_map.get("axleSections", []):
            if raw.get("id") is None:
                continue
            section_id = str(raw["id"])
            seg_ids: frozenset[int] = frozenset(
                int(s) for s in (raw.get("segmentIds") or []) if s is not None
            )
            self._axle_defs[section_id] = AxleSectionDef(
                section_id=section_id,
                name=str(raw.get("name", section_id)),
                segment_ids=seg_ids,
            )

        # ---- 逻辑区段定义（暂存储原始数据，Phase 2 后期使用）----
        self._logical_defs: dict[str, Any] = {}
        for raw in line_map.get("logicalSections", []):
            if raw.get("id") is None:
                continue
            self._logical_defs[str(raw["id"])] = raw

        # ---- Seg 长度表（用于车尾跨 Seg 回溯计算）----
        self._seg_lengths: dict[int, float] = {}
        for seg in line_map.get("segments", []):
            if seg.get("id") is not None and seg.get("lengthM") is not None:
                self._seg_lengths[int(seg["id"])] = float(seg["lengthM"])

        # ---- 运行时占用缓存（每个 tick 全量重算） ----
        self._occupancy: dict[str, SectionOccupation] = {
            sid: SectionOccupation(section_id=sid, section_type="AXLE")
            for sid in self._axle_defs
        }
        for sid in self._logical_defs:
            self._occupancy[sid] = SectionOccupation(section_id=sid, section_type="LOGICAL")

    # ==================================================================
    # 主循环接口 —— 成员 A 的 SimulationRunner 每 tick 调用
    # ==================================================================

    def update(self, train_states: list[Any], track_query: Any) -> None:
        """每 tick 由主循环调用。根据所有列车的当前位置重算区段占用。

        Args:
            train_states: TrainState 列表（成员 B 的 VehicleService 产出）
            track_query: TrackQueryService 实例（成员 A 的线路查询），
                         用于拓扑回溯（车尾跨 Seg 时找前一个 Seg）
        """
        # 第一步：清空所有区段的占用状态
        for occ in self._occupancy.values():
            occ.occupied = False
            occ.train_ids.clear()

        # 第二步：遍历所有列车，找出每辆车覆盖的 Seg 集合
        for train in train_states:
            # 调用内部的拓扑回溯方法，找出车体覆盖的所有 Seg
            covered_segs = self._segments_covered_by_train(train, track_query)

            # 第三步：对每个计轴区段，判断是否与列车覆盖的 Seg 有交集
            for section_id, axle_def in self._axle_defs.items():
                if covered_segs & axle_def.segment_ids:
                    occ = self._occupancy[section_id]
                    occ.occupied = True
                    if train.train_id not in occ.train_ids:
                        occ.train_ids.append(train.train_id)

    # ==================================================================
    # 查询接口 —— 供 InterlockingRuleEngine / RouteService / 前端使用
    # ==================================================================

    def is_occupied(self, section_id: str | int) -> bool:
        """查询某个区段当前是否被占用。

        由 InterlockingRuleEngine.check() 在进路办理前调用。
        """
        occ = self._occupancy.get(str(section_id))
        return occ.occupied if occ else False

    def occupied_by(self, section_id: str | int) -> list[str]:
        """查询某个区段当前被哪些列车占用。

        由 RouteService._release_cleared_sections() 调用，
        用于判断"本进路的列车是否已经离开该区段"。
        """
        occ = self._occupancy.get(str(section_id))
        return list(occ.train_ids) if occ else []

    @property
    def all_occupied_sections(self) -> set[str]:
        """返回当前所有被占用的区段 ID 集合。"""
        return {sid for sid, occ in self._occupancy.items() if occ.occupied}

    def snapshot(self) -> list[dict]:
        """返回所有区段的完整快照（前端 API 用）。"""
        return [occ.to_dict() for occ in self._occupancy.values()]

    @property
    def axle_section_ids(self) -> list[str]:
        """返回所有计轴区段 ID 列表。"""
        return list(self._axle_defs)

    # ==================================================================
    # 内部算法：车体覆盖的 Seg 集合 —— 沿拓扑回溯车长
    # ==================================================================

    def _segments_covered_by_train(self, train: Any, track_query: Any) -> set[int]:
        """返回列车当前覆盖的所有 Seg ID（车头到尾，沿拓扑回溯）。

        算法：
        1. 从车头所在 Seg 开始，沿行驶反方向回溯 train.length_m 米
        2. 遇到当前 Seg 不够走 → 通过 track_query.get_next_segments()
           找到前一个 Seg（成员 A 的 TrackQueryService），继续回溯
        3. 直到走完车长或到达线路尽头

        注意：
        - Phase 2 简化：回溯时不考虑道岔分叉，只沿主干方向
        - 当前只依赖 TrackQueryService.get_next_segments()，
          不需要 TrackNavigator（成员 A 尚未完成）
        """
        covered: set[int] = set()
        seg_id = int(train.seg_id)
        remaining = train.length_m  # 还需要回溯的剩余长度（米）

        # 行驶方向 → 回溯方向：FORWARD 行驶时车尾在后方，沿 "backward" 回溯
        direction_sign = 1 if train.direction.upper() in ("FORWARD", "UP") else -1

        current_seg = seg_id
        # 当前 Seg 内还能往回走多少米
        if direction_sign == 1:
            # FORWARD 行驶：车尾在后方，offset_m 往 0 方向回溯
            space_in_current = train.offset_m
        else:
            # BACKWARD 行驶：车尾在前方，从 offset_m 往 Seg 末端方向回溯
            seg_len = self._seg_lengths.get(current_seg, 0.0)
            space_in_current = seg_len - train.offset_m

        while remaining > 0:
            covered.add(current_seg)
            if space_in_current >= remaining:
                break  # 车体完全在当前 Seg 内结束

            remaining -= space_in_current
            # 找前一个 Seg
            prev_direction = "backward" if direction_sign == 1 else "forward"
            prev_segs = track_query.get_next_segments(current_seg, prev_direction)
            if not prev_segs:
                break  # 线路尽头

            # Phase 2 简化：默认走第一个前驱 Seg（不考虑道岔分叉）
            prev = prev_segs[0]
            current_seg = int(prev["id"])
            space_in_current = self._seg_lengths.get(current_seg, 0.0)

        return covered
