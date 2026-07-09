"""信号显示解析器 —— 成员 C Phase 2。

根据联锁状态（进路锁闭、区段占用、道岔故障、扰动）决定每架信号机
应该显示什么灯色。这是联锁子系统的最终输出——信号机颜色直接决定
列车能否通过。

数据流：
  RouteCatalog    → 查询"哪些进路以某架信号机为始端"
  RouteService    → 查询"这些进路是否已锁闭（LOCKED）"
  SectionOccupSvc → 查询"进路前方区段是否空闲"
  SwitchLockSvc   → 查询"相关道岔是否故障"
  EnvironmentSvc  → 查询"信号故障扰动是否激活"（Phase 2 后续接入）

输出：
  SignalAspectResolver.resolve(signal_id) → aspect（GREEN / YELLOW / RED）
  TrainControlService 消费此结果来计算 MA 终点和允许速度

真实铁路对标：
  此模块对应联锁计算机中"信号控制逻辑"部分——根据进路状态、
  区段空闲和道岔位置自动开放或关闭信号。
"""

from __future__ import annotations

from typing import Any

from app.domain.interlocking.route_catalog import RouteCatalog
from app.domain.interlocking.route_service import RouteService
from app.domain.interlocking.section_occupation import SectionOccupationService
from app.domain.interlocking.switch_lock import SwitchLockService

JsonDict = dict[str, Any]

# 灯色枚举
GREEN = "GREEN"     # 进路已锁闭，前方区段空闲，列车可通过
YELLOW = "YELLOW"   # 进路已锁闭，但下一个信号是红灯（进路尽头）
RED = "RED"         # 禁止通过——无进路、区段被占、或设备故障


class SignalAspectResolver:
    """根据联锁状态为每架信号机计算灯色。

    判断规则（优先级从高到低，第一条命中即返回）：
    1. 信号机自身故障（EnvironmentService 注入） → RED
    2. 此信号机是始端信号机的进路中，有已锁闭且道岔故障的 → RED
    3. 没有以此信号为始端的已锁闭进路 → RED（信号关闭，禁止发车）
    4. 进路前方计轴区段被占用 → RED
    5. 进路终端信号机解析为 RED → YELLOW（预告前方停车）
    6. 以上都不满足 → GREEN（进路已锁，前方空闲，可通过）
    """

    def __init__(
        self,
        catalog: RouteCatalog,                     # 进路表（查始端信号→进路映射）
        route_svc: RouteService,                    # 进路管理（查进路是否已锁闭）
        section_occ: SectionOccupationService,      # 区段占用（查前方是否空闲）
        switch_lock: SwitchLockService,             # 道岔锁闭（查道岔是否故障）
    ) -> None:
        self._catalog = catalog
        self._route_svc = route_svc
        self._section_occ = section_occ
        self._switch_lock = switch_lock

        # 信号故障集合 —— 由 EnvironmentService 注入/清除（Phase 2 后续接入）
        self._faulted_signals: set[str] = set()

        # 灯色缓存 —— refresh() 每 tick 重算后写入，resolve() 直接读
        # 初始为空字典，首次调用 resolve() 时自动触发 refresh()
        self._cache: dict[str, str] = {}

    # ==================================================================
    # 对外接口 —— 供 TrainControlService(成员C Phase1) 消费
    # ==================================================================

    def resolve(self, signal_id: int | str) -> str:
        """返回 *signal_id* 这架信号机当前应显示的灯色。

        由 TrainControlService._resolve_signal_aspect() 调用，
        替代 Phase 1 时永远返回 GREEN 的占位逻辑。
        """
        return self._resolve(str(signal_id), depth=0)

    def refresh(self) -> None:
        """每 tick 由主循环调用 —— 重算所有信号机的灯色并写入缓存。

        调用时机：在 RouteService.update() 之后、TrainControlService 之前。
        主循环中的调用顺序（联锁部分）：
        1. SectionOccupationService.update(train_states, track_query)
        2. RouteService.update()
        3. SignalAspectResolver.refresh()  ← 此时区段占用和进路状态都是最新的

        采用两遍扫描策略（避免递归）：
        - 第一遍：为每架信号机计算"基础灯色"（RED 或 GREEN），
          不提 YELLOW（因为此时终端信号可能还没算出来）
        - 第二遍：对第一遍中为 GREEN 的信号机，检查其进路的终端信号机。
          终端是 RED → 改为 YELLOW（预告前方停车）
        """
        # ---- 第一遍：收集所有信号机 ID，计算基础灯色 ----
        signal_ids: set[str] = set()
        # 建立 信号ID → 进路ID 的映射（取第一个已锁闭进路）
        signal_route: dict[str, str] = {}   # signal_id → locked_route_id

        for route_id in self._catalog.route_ids:
            rdef = self._catalog.get(route_id)
            if rdef is None:
                continue
            signal_ids.add(str(rdef.start_signal_id))
            signal_ids.add(str(rdef.end_signal_id))

            # 记录以本信号为始端的第一条已锁闭进路
            sid = str(rdef.start_signal_id)
            if sid not in signal_route and self._route_svc.is_locked(route_id):
                signal_route[sid] = route_id

        # 计算每架信号机的基础灯色
        base: dict[str, str] = {}
        for sid in signal_ids:
            base[sid] = self._compute_base_aspect(sid, signal_route.get(sid))

        # ---- 第二遍：GREEN → YELLOW（如果终端信号是 RED） ----
        new_cache: dict[str, str] = dict(base)
        for sid, aspect in base.items():
            if aspect != GREEN:
                continue
            route_id = signal_route.get(sid)
            if route_id is None:
                continue
            rdef = self._catalog.get(route_id)
            if rdef is None:
                continue
            end_sid = str(rdef.end_signal_id)
            # 终端信号在 base 中的值（第一遍算出来的基础灯色）
            if base.get(end_sid) == RED:
                new_cache[sid] = YELLOW

        self._cache = new_cache

    # ==================================================================
    # 查询接口 —— 供 TrainControlService / 前端 API 使用（读缓存）
    # ==================================================================

    def resolve(self, signal_id: int | str) -> str:
        """返回 *signal_id* 这架信号机当前应显示的灯色。

        如果缓存为空（refresh() 尚未被调用），自动触发一次 refresh()。
        正常流程中主循环每 tick 调 refresh()，resolve() 直接读缓存。
        """
        if not self._cache:
            self.refresh()
        return self._cache.get(str(signal_id), RED)

    # ==================================================================
    # 内部计算 —— 确定单架信号机的基础灯色（不考虑 YELLOW 规则）
    # ==================================================================

    def _compute_base_aspect(
        self,
        signal_id: str,
        locked_route_id: str | None,
    ) -> str:
        """计算信号机的基础灯色（RED 或 GREEN）。

        判断规则（按优先级）：
        1. 信号故障   → RED
        2. 无锁闭进路 → RED
        3. 道岔故障   → RED
        4. 区段被占   → RED
        5. 全通过     → GREEN

        不处理 YELLOW 规则——YELLOW 由 refresh() 在第二遍扫描中处理。
        """
        # 规则 1：信号故障
        if signal_id in self._faulted_signals:
            return RED

        # 规则 2：无锁闭进路
        if locked_route_id is None:
            return RED

        rdef = self._catalog.get(locked_route_id)
        if rdef is None:
            return RED

        # 规则 3：道岔故障
        for sw_id, pos in rdef.required_switches.items():
            if not self._switch_lock.is_available_for(sw_id, pos):
                return RED

        # 规则 4：区段被占
        for section_id in rdef.axle_section_ids:
            if self._section_occ.is_occupied(section_id):
                return RED

        # 规则 5：基础灯色 = GREEN（第二遍可能改成 YELLOW）
        return GREEN

    # ==================================================================
    # 批量查询 —— 供前端 API / 平台信号适配器使用
    # ==================================================================

    def resolve_all(self) -> dict[str, str]:
        """返回所有已知信号机的灯色快照（读缓存，不重算）。"""
        if not self._cache:
            self.refresh()
        return dict(self._cache)

    def snapshot(self) -> list[dict[str, Any]]:
        """返回信号机状态的序列化快照（前端 API 用）。"""
        aspects = self.resolve_all()
        return [
            {
                "signalId": sid,
                "aspect": aspect,
                "faulted": sid in self._faulted_signals,
            }
            for sid, aspect in sorted(aspects.items(), key=lambda x: int(x[0]))
        ]

    # ==================================================================
    # 故障注入 —— 供 EnvironmentService(成员C Phase2) 调用
    # ==================================================================

    def set_fault(self, signal_id: str) -> None:
        """注入信号故障 —— 使信号机强制显示 RED。

        由 EnvironmentService 在信号故障扰动激活时调用。
        """
        self._faulted_signals.add(signal_id)

    def clear_fault(self, signal_id: str) -> None:
        """清除信号故障 —— 恢复自动灯色计算。

        由 EnvironmentService 在信号故障扰动解除时调用。
        """
        self._faulted_signals.discard(signal_id)
