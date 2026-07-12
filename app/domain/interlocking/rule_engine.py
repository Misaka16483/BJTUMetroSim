"""联锁规则检查引擎 —— 成员 C Phase 2。

无状态（stateless）的规则检查器——在进路办理前，评估所有前置条件：
  1. 进路是否存在
  2. 计轴区段是否空闲（调用 SectionOccupationService.is_occupied()）
  3. 保护区段是否空闲
  4. 是否有敌对进路已锁闭（查询 RouteCatalog.conflicts_with() + locked_route_ids）
  5. 所需道岔是否可用（调用 SwitchLockService.is_available_for()）

数据流：
  RouteService.request()
    → InterlockingRuleEngine.check(route_id, train_id, locked_route_ids=...)
    → 依次查询 SectionOccupationService / RouteCatalog / SwitchLockService
    → 返回 RouteCheckResult(ok=True/False, failure_reason=...)
    → RouteService 根据结果决定锁闭还是拒绝
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.interlocking.models import RouteDef
from app.domain.interlocking.route_catalog import RouteCatalog
from app.domain.interlocking.section_occupation import SectionOccupationService
from app.domain.interlocking.switch_lock import SwitchLockService


@dataclass(frozen=True)
class RouteCheckResult:
    """进路办理前的规则检查结果 —— 由 InterlockingRuleEngine.check() 产出。

    被 RouteService.request() 消费：
    - ok=True  → 可以锁闭进路
    - ok=False → 拒绝，failure_reason 说明原因（传给 RouteResult）

    failed_section_id / failed_switch_id / conflicting_route_id 是详细的
    失败上下文，用于前端展示或日志记录。
    """

    ok: bool                                # 是否通过全部检查
    route_id: str                           # 被检查的进路 ID
    failure_reason: str | None = None       # 失败原因枚举
    # 失败上下文（用于调试和前端展示）：
    failed_section_id: str | None = None    # 被占用的区段 ID
    failed_switch_id: str | None = None     # 不可用的道岔 ID
    conflicting_route_id: str | None = None # 已锁闭的敌对进路 ID

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "routeId": self.route_id,
            "failureReason": self.failure_reason,
            "failedSectionId": self.failed_section_id,
            "failedSwitchId": self.failed_switch_id,
            "conflictingRouteId": self.conflicting_route_id,
        }


class InterlockingRuleEngine:
    """无状态规则检查器 —— 评估进路办理的全部前置条件。

    不持有任何自身状态，所有数据从三个有状态服务和 RouteCatalog 读取：
    - SectionOccupationService：区段占用情况（轨道电路）
    - SwitchLockService：道岔位置和锁闭状态（转辙机）
    - RouteCatalog：静态进路/道岔定义 + 预计算的敌对进路表

    locked_route_ids 参数由 RouteService 传入（当前已锁闭的进路集合），
    用于检查"待办理进路是否与已锁闭进路冲突"。
    """

    def __init__(
        self,
        catalog: RouteCatalog,
        section_occ: SectionOccupationService,
        switch_lock: SwitchLockService,
    ) -> None:
        """构造规则引擎——注入三个外部数据源。

        Args:
            catalog: 进路表（成员 C 的 RouteCatalog，含预计算的敌对关系）
            section_occ: 区段占用检测服务（成员 C 的 SectionOccupationService）
            switch_lock: 道岔锁闭服务（成员 C 的 SwitchLockService）
        """
        self._catalog = catalog
        self._section_occ = section_occ
        self._switch_lock = switch_lock

    def check(
        self,
        route_id: str,
        train_id: str,
        *,
        locked_route_ids: frozenset[str] = frozenset(),
    ) -> RouteCheckResult:
        """执行进路办理前的全部联锁规则检查。

        由 RouteService.request() 调用。

        检查顺序（按设计文档 9.3.2 节）：
        1. 进路是否存在（查 RouteCatalog.get()）
        2. 所有计轴区段是否空闲（调 SectionOccupationService.is_occupied()）
        3. 保护区段是否空闲（同上）
        4. 敌对进路是否已锁闭（查 locked_route_ids + RouteCatalog.conflicts_with()）
        5. 所需道岔是否可用（调 SwitchLockService.is_available_for()）

        Args:
            route_id: 要办理的进路 ID
            train_id: 申请进路的列车 ID（用于日志）
            locked_route_ids: 当前已被锁闭的进路 ID 集合（从 RouteService 获取）

        Returns:
            RouteCheckResult —— ok=True 表示可以办理，否则携带失败原因
        """

        # 检查 1：进路是否存在
        route_def = self._catalog.get(route_id)
        if route_def is None:
            return RouteCheckResult(
                ok=False, route_id=route_id,
                failure_reason="ROUTE_NOT_FOUND",
            )

        # 检查 2：进路包含的计轴区段是否全部空闲
        for section_id in route_def.axle_section_ids:
            if not self._section_occ.is_occupied(section_id):
                continue
            # The requesting train may occupy its own start/approach section.
            # Foreign, shared, and unidentified occupations still block the route.
            occupants = set(self._section_occ.occupied_by(section_id))
            if occupants == {train_id}:
                continue
            return RouteCheckResult(
                ok=False, route_id=route_id,
                failure_reason="SECTION_OCCUPIED",
                failed_section_id=section_id,
            )
        for section_id in route_def.protection_section_ids:
            if self._section_occ.is_occupied(section_id):
                return RouteCheckResult(
                    ok=False, route_id=route_id,
                    failure_reason="SECTION_OCCUPIED",
                    failed_section_id=section_id,
                )

        # 检查 4：是否有敌对进路已锁闭
        for conflict_id in self._catalog.conflicts_with(route_id):
            if conflict_id in locked_route_ids:
                return RouteCheckResult(
                    ok=False, route_id=route_id,
                    failure_reason="CONFLICT_ROUTE_LOCKED",
                    conflicting_route_id=conflict_id,
                )

        # 检查 5：所需道岔是否可用
        #   - 已有的锁（相同位置）可以共享
        #   - 故障道岔不可用
        #   - 被其他进路锁在相反位置不可用
        for switch_id, required_position in route_def.required_switches.items():
            if not self._switch_lock.is_available_for(switch_id, required_position):
                return RouteCheckResult(
                    ok=False, route_id=route_id,
                    failure_reason="SWITCH_UNAVAILABLE",
                    failed_switch_id=switch_id,
                )

        return RouteCheckResult(ok=True, route_id=route_id)
