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
            approach_sections=list(route_def.approach_section_ids) if route_def else [],
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
        """手动释放进路 —— 根据释放类型走不同的状态转换。

        - "CANCEL"：调度员取消进路（LOCKED → RELEASING → IDLE），
          如果已是 APPROACH_LOCKED 则拒绝（需延时）。
        - "APPROACH_RELEASE"：接近锁闭延时释放（APPROACH_LOCKED → IDLE）
        - "EMERGENCY"：故障/人工强制释放（任何状态 → IDLE）
        - "AUTO"：列车通过后自动释放（RELEASING → IDLE）
        """
        state = self._routes.get(route_id)
        if state is None:
            return RouteResult(accepted=False, route_id=route_id,
                              train_id="", state="FAILED",
                              failure_reason="ROUTE_NOT_FOUND")

        if state.state == "IDLE":
            return RouteResult(accepted=True, route_id=route_id,
                              train_id="", state="IDLE")

        # CANCEL：只允许 LOCKED 状态取消
        if release_type == "CANCEL":
            if state.state == "APPROACH_LOCKED":
                return RouteResult(accepted=False, route_id=route_id,
                                  train_id=state.train_id or "", state="FAILED",
                                  failure_reason="APPROACH_LOCKED_CANNOT_CANCEL")
            self._do_release(route_id, state, release_type)
            return RouteResult(accepted=True, route_id=route_id,
                              train_id=state.train_id or "", state="IDLE")

        # EMERGENCY / AUTO / APPROACH_RELEASE：直接释放
        self._do_release(route_id, state, release_type)
        return RouteResult(accepted=True, route_id=route_id,
                          train_id=state.train_id or "", state="IDLE")

    # -- main-loop interface ----------------------------------------------

    def update(self) -> None:
        """联锁扫描周期 —— 每 tick 被主循环调用一次。

        对每条已锁闭/接近锁闭进路，按顺序推进状态机：
        1. LOCKED + 列车进入接近区段 → APPROACH_LOCKED
        2. APPROACH_LOCKED + 列车首次进入进路区段 → 开始监控释放
        3. 已进入后每 tick 检查：车尾离开区段X → 释放X
        4. 全部区段释放完 → RELEASING → IDLE
        """
        for route_id, state in list(self._routes.items()):
            if state.state not in ("LOCKED", "APPROACH_LOCKED"):
                continue

            train_id = state.train_id
            if train_id is None:
                self._do_release(route_id, state, "AUTO")
                continue

            # ── 状态1：LOCKED → 检查接近区段 → APPROACH_LOCKED ──
            if state.state == "LOCKED":
                in_approach = any(
                    train_id in self._section_occ.axle_occupied_by(axle_section_id)
                    for approach_id in state.approach_sections
                    for axle_section_id in self._catalog.point_approach_axle_section_ids(approach_id)
                )
                if in_approach:
                    state.state = "APPROACH_LOCKED"

            # ── 状态2：APPROACH_LOCKED 或 LOCKED → 检查是否已进入进路区段 ──
            if not state.has_entered:
                entered = any(
                    train_id in self._section_occ.axle_occupied_by(sid)
                    for sid in state.locked_sections
                )
                if not entered:
                    continue  # 列车尚未进入，不检查释放
                state.has_entered = True

            # ── 状态3：列车已在进路中 → 逐区段检查释放 ──
            self._release_cleared_sections(route_id, state)

    def _do_release(self, route_id: str, state: RouteState, release_type: str) -> None:
        """执行进路实际释放操作：解锁道岔，清除锁闭状态，转入 IDLE。"""
        for sw_id in state.locked_switches:
            self._switch_lock.unlock(sw_id, route_id)
        state.state = "RELEASING"  # 短暂经过 RELEASING
        state.train_id = None
        state.locked_sections.clear()
        state.locked_switches.clear()
        state.approach_sections.clear()
        state.failure_reason = None
        state.has_entered = False
        state.last_entered_section_id = None
        state.state = "IDLE"

    def _release_cleared_sections(self, route_id: str, state: RouteState) -> None:
        """Release only sections cleared behind the assigned train.

        ``locked_sections`` stays ordered as defined by the route table.  A
        section which has not been reached yet must remain locked; only the
        prefix behind the first currently occupied section may be released.
        """
        train_id = state.train_id
        if train_id is None:
            self._do_release(route_id, state, "AUTO")
            return

        occupied_indexes = [
            index
            for index, section_id in enumerate(state.locked_sections)
            if train_id in self._section_occ.axle_occupied_by(section_id)
        ]
        if occupied_indexes:
            first_occupied = occupied_indexes[0]
            last_occupied = occupied_indexes[-1]
            state.last_entered_section_id = state.locked_sections[last_occupied]
            # Prefix sections are behind the train tail.  Keep the occupied
            # section and every not-yet-reached section ahead of it locked.
            del state.locked_sections[:first_occupied]
            return

        # A gap between route sections is normal.  Only after the assigned
        # train has occupied and then cleared the final remaining section can
        # the complete route be released.
        if (
            state.locked_sections
            and state.last_entered_section_id == state.locked_sections[-1]
        ):
            self._do_release(route_id, state, "AUTO")

    def release_routes_owned_by(self, train_id: str) -> list[str]:
        """Emergency-release routes owned by a train that is being removed.

        Removing a simulated train is equivalent to taking it out of service.
        Its route locks must not survive the train, otherwise a later train can
        wait forever on ``CONFLICT_ROUTE_LOCKED`` for an owner that no longer
        exists.  Only locked routes with this exact owner are released.
        """
        released: list[str] = []
        for route_id, state in list(self._routes.items()):
            if state.train_id != train_id or state.state not in ("LOCKED", "APPROACH_LOCKED"):
                continue
            self.release(route_id, "EMERGENCY")
            released.append(route_id)
        return released
    # -- query interface --------------------------------------------------

    def state_of(self, route_id: str) -> str:
        """返回进路当前状态，未知进路返回 ``"IDLE"``。"""
        rs = self._routes.get(route_id)
        return rs.state if rs else "IDLE"

    def is_locked(self, route_id: str) -> bool:
        """进路是否处于已锁闭状态（LOCKED 或 APPROACH_LOCKED）。"""
        return self.state_of(route_id) in ("LOCKED", "APPROACH_LOCKED")

    def locked_by(self, route_id: str) -> str | None:
        """Return the train currently owning a locked route, if any."""
        state = self._routes.get(route_id)
        if state is None or state.state not in ("LOCKED", "APPROACH_LOCKED"):
            return None
        return state.train_id

    def has_entered(self, route_id: str) -> bool:
        """Whether the owner has entered a currently locked route.

        Signal control uses this to return the departure signal to red after
        the train has passed it, while the route remains locked for tail clear.
        """
        state = self._routes.get(route_id)
        return bool(
            state is not None
            and state.state in ("LOCKED", "APPROACH_LOCKED")
            and state.has_entered
        )

    def locked_routes(self) -> list[str]:
        """返回所有已锁闭/接近锁闭的进路 ID 列表。"""
        return [
            rid for rid, rs in self._routes.items()
            if rs.state in ("LOCKED", "APPROACH_LOCKED")
        ]

    def snapshot(self) -> list[dict[str, Any]]:
        """返回所有进路的运行时状态快照（前端 API 用）。"""
        return [
            {
                "routeId": rs.route_id,
                "state": rs.state,
                "trainId": rs.train_id,
                "lockedSections": list(rs.locked_sections),
                "lockedSwitches": dict(rs.locked_switches),
                "approachSections": list(rs.approach_sections),
                "hasEntered": rs.has_entered,
                "lastEnteredSectionId": rs.last_entered_section_id,
                "failureReason": rs.failure_reason,
            }
            for rs in self._routes.values()
        ]
