"""进路生命周期管理服务 —— 成员 C Phase 2。

管理进路的完整生命周期：请求 → 规则检查 → 锁闭道岔和区段 →
列车通过后逐区段释放 → 全部通过后完全释放。

这是联锁子系统的核心编排层，协调 RuleEngine / SwitchLockService /
SectionOccupationService 三个服务完成一次进路办理的全流程。

数据流：
  外部调用（调度/API/司机台）
    → RouteService.request(RouteRequest)
    → InterlockingRuleEngine.check()  ← 读 SectionOccupSvc + SwitchLockSvc + RouteCatalog
    → SwitchLockService.lock()         ← 锁道岔
    → 返回 RouteResult（成功/失败）

  主循环每 tick：
    → RouteService.update()
    → _release_cleared_sections()  ← 读 SectionOccupSvc.occupied_by()
    → 所有区段清空 → release() → SwitchLockService.unlock()

调用关系：
  - RouteService → InterlockingRuleEngine.check()       (成员C)
  - RouteService → SwitchLockService.lock() / unlock()   (成员C)
  - RouteService → SectionOccupationService.occupied_by()(成员C)
  - RouteService ← DispatchService / API / 司机台         (成员D/E/B)
"""

from __future__ import annotations

from typing import Any

from app.domain.interlocking.models import (
    RouteDef,
    RouteRequest,
    RouteResult,
    RouteState,
)
from app.domain.interlocking.route_catalog import RouteCatalog
from app.domain.interlocking.rule_engine import InterlockingRuleEngine
from app.domain.interlocking.section_occupation import SectionOccupationService
from app.domain.interlocking.switch_lock import SwitchLockService


class RouteService:
    """进路办理、锁闭、释放和生命周期管理。

    对外暴露三个主要接口：
    - request()：处理外部进路办理请求（被调度/API/司机台调用）
    - release()：手动释放进路（取消/紧急解锁）
    - update()：每 tick 被主循环调用，检查已锁闭进路的区段释放情况
    """

    def __init__(
        self,
        catalog: RouteCatalog,
        rule_engine: InterlockingRuleEngine,
        section_occ: SectionOccupationService,
        switch_lock: SwitchLockService,
    ) -> None:
        """构造进路管理服务——注入联锁子系统的其他三个组件。

        Args:
            catalog: 进路表（含预计算的敌对关系和道岔需求）
            rule_engine: 规则检查引擎
            section_occ: 区段占用检测服务
            switch_lock: 道岔锁闭服务
        """
        self._catalog = catalog
        self._rule_engine = rule_engine
        self._section_occ = section_occ
        self._switch_lock = switch_lock
        # 内部状态：当前所有非 IDLE 的进路
        self._routes: dict[str, RouteState] = {}
        self._lock_counter: int = 0  # 进路锁闭计数器（生成唯一锁时间戳）

    # -- external API (called by dispatch / API) --------------------------

    def request(self, req: RouteRequest) -> RouteResult:
        """处理进路办理请求——查规则、锁道岔、锁区段。

        由外部模块（DispatchService / HTTP API / 司机台）调用。
        内部流程（按设计文档 9.3.2 节）：
        1. 检查是否已锁闭（防重复办理）
        2. 调 InterlockingRuleEngine.check() 执行全部联锁前置检查
        3. 全部通过后调 SwitchLockService.lock() 逐个锁闭道岔
        4. 记录 RouteState 到 self._routes

        Returns:
            RouteResult —— accepted=True 表示进路已锁闭，否则携带 failure_reason
        """
        route_id = req.route_id

        # 防重复：同一进路已锁闭 → 直接拒绝
        existing = self._routes.get(route_id)
        if existing is not None and existing.state == "LOCKED":
            return RouteResult(
                accepted=False,
                route_id=route_id,
                train_id=req.train_id,
                state="FAILED",
                failure_reason="CONFLICT_ROUTE_LOCKED",
            )

        # 收集当前所有已锁闭/接近锁闭的进路（用于 RuleEngine 检查敌对关系）
        currently_locked = frozenset(
            rid for rid, rs in self._routes.items()
            if rs.state in ("LOCKED", "APPROACH_LOCKED")
        )
        # 调用联锁规则引擎执行全部前置检查
        check = self._rule_engine.check(
            route_id, req.train_id, locked_route_ids=currently_locked
        )
        if not check.ok:
            # 记录失败原因（调试/API查询用）
            self._routes[route_id] = RouteState(
                route_id=route_id,
                state="FAILED",
                train_id=req.train_id,
                failure_reason=check.failure_reason,
            )
            return RouteResult(
                accepted=False,
                route_id=route_id,
                train_id=req.train_id,
                state="FAILED",
                failure_reason=check.failure_reason,
            )

        # 锁闭所需道岔 —— 调用 SwitchLockService（成员C）
        route_def = self._catalog.get(route_id)
        locked_switches: dict[str, str] = {}
        if route_def is not None:
            for sw_id, pos in route_def.required_switches.items():
                ok = self._switch_lock.lock(sw_id, pos, route_id)
                if not ok:
                    # 理论上不会到这里（RuleEngine 已检查过），但保留回滚逻辑
                    for sid in locked_switches:
                        self._switch_lock.unlock(sid, route_id)
                    return RouteResult(
                        accepted=False,
                        route_id=route_id,
                        train_id=req.train_id,
                        state="FAILED",
                        failure_reason="SWITCH_UNAVAILABLE",
                    )
                locked_switches[sw_id] = pos

        # 全部检查通过 → 记录进路已锁闭
        self._lock_counter += 1
        self._routes[route_id] = RouteState(
            route_id=route_id,
            state="LOCKED",
            train_id=req.train_id,
            locked_sections=list(route_def.axle_section_ids) if route_def else [],
            locked_switches=dict(locked_switches),
            lock_time_ms=self._lock_counter,
        )
        return RouteResult(
            accepted=True,
            route_id=route_id,
            train_id=req.train_id,
            state="LOCKED",
            locked_sections=list(route_def.axle_section_ids) if route_def else [],
            locked_switches=dict(locked_switches),
        )

    def release(self, route_id: str, release_type: str = "CANCEL") -> RouteResult:
        """手动释放进路 —— 解锁道岔，清除进路锁闭状态。

        release_type 含义（与设计文档 9.3.3 节对齐）：
        - "AUTO"：列车通过后自动释放（由 update() 内部调用）
        - "CANCEL"：调度员取消进路（列车尚未接近）
        - "APPROACH_RELEASE"：接近锁闭延时释放
        - "EMERGENCY"：故障/人工强制释放
        """
        state = self._routes.get(route_id)
        if state is None or state.state in ("IDLE", "RELEASING"):
            return RouteResult(
                accepted=False,
                route_id=route_id,
                train_id=state.train_id if state else "",
                state="FAILED",
                failure_reason="ROUTE_NOT_FOUND",
            )

        # 释放所有被锁闭的道岔（调 SwitchLockService.unlock()）
        for sw_id in state.locked_switches:
            self._switch_lock.unlock(sw_id, route_id)

        state.state = "IDLE"
        state.train_id = None
        state.locked_sections.clear()
        state.locked_switches.clear()
        state.failure_reason = None
        return RouteResult(
            accepted=True,
            route_id=route_id,
            train_id=state.train_id or "",
            state="IDLE",
        )

    # -- main-loop interface ----------------------------------------------

    def update(self) -> None:
        """联锁扫描周期 —— 每 tick 被主循环调用一次。

        相当于真实联锁 PLC 的一次 I/O 扫描：检查所有已锁闭进路，
        对已离开区段的进路执行逐步释放。不依赖外部传入任何数据——
        区段占用状态由 SectionOccupationService 自己维护。

        调用顺序（主循环）：
        1. SectionOccupationService.update(train_states, track_query)
        2. RouteService.update()
        """
        for route_id, state in list(self._routes.items()):
            if state.state not in ("LOCKED", "APPROACH_LOCKED"):
                continue
            self._release_cleared_sections(route_id, state)

    # -- query interface --------------------------------------------------

    def state_of(self, route_id: str) -> str:
        """Return the current state string, or ``"IDLE"`` if unknown."""
        rs = self._routes.get(route_id)
        return rs.state if rs else "IDLE"

    def is_locked(self, route_id: str) -> bool:
        return self.state_of(route_id) == "LOCKED"

    def locked_routes(self) -> list[str]:
        return [
            rid for rid, rs in self._routes.items()
            if rs.state in ("LOCKED", "APPROACH_LOCKED")
        ]

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "routeId": rs.route_id,
                "state": rs.state,
                "trainId": rs.train_id,
                "lockedSections": list(rs.locked_sections),
                "lockedSwitches": dict(rs.locked_switches),
                "failureReason": rs.failure_reason,
            }
            for rs in self._routes.values()
        ]

    # -- internal ---------------------------------------------------------

    def _release_cleared_sections(
        self,
        route_id: str,
        state: RouteState,
    ) -> None:
        """释放进路中列车已离开的区段。

        对进路锁闭的每个区段，调用 SectionOccupationService.occupied_by()
        查询当前有哪些列车在上面。如果本进路的列车（state.train_id）不在
        占用列表中，说明列车已离开该区段→从 locked_sections 中移除。

        注意：其他列车仍可能占用同一区段（如追踪运行中后车覆盖了前车
        刚离开的区段），这不影响本进路的释放——我们只看"本车是否已离开"。
        锁释放后区段仍被其他车占用不影响安全（新进路办理时 RuleEngine
        会检查 is_occupied()）。

        全部区段离开后调用 self.release(route_id, "AUTO") 完全释放进路。
        """
        train_id = state.train_id
        if train_id is None:
            self.release(route_id, "AUTO")
            return

        new_locked: list[str] = []
        for sid in state.locked_sections:
            if train_id in self._section_occ.occupied_by(sid):
                new_locked.append(sid)

        state.locked_sections[:] = new_locked

        if not new_locked:
            self.release(route_id, "AUTO")
