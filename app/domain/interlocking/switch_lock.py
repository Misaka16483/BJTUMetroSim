"""道岔锁闭服务 —— 成员 C Phase 2。

管理 60 个道岔的运行时状态：位置（定位/反位）、锁闭归属、故障状态。

对应真实铁路中的转辙机控制和道岔采集继电器。每个道岔默认处于
定位（NORMAL）且未被任何进路锁闭。

数据流：
  RouteCatalog(switch_ids + get_switch()) → SwitchLockService.__init__()
    → RouteService.request() 调 lock() 锁闭道岔
    → RouteService.release() 调 unlock() 解锁道岔
    → InterlockingRuleEngine.check() 调 is_available_for() 检查可用性
    → EnvironmentService 调 set_fault() / clear_fault() 注入故障
"""

from __future__ import annotations

from app.domain.interlocking.models import SwitchDef, SwitchState


class SwitchLockService:
    """道岔位置、锁闭和健康状态管理。

    状态模型：
    - FREE：未被任何进路锁闭，处于定位或反位，health=OK
    - LOCKED by R-X at NORMAL/REVERSE：被进路 R-X 锁在指定位置
    - FAILED：health=FAILED，不可被任何进路使用

    锁闭规则：
    - 同一道岔可以被多条需要相同位置的进路共享（锁不互斥）
    - 已锁在定位的道岔不能被另一条进路锁在反位
    - 故障道岔不可用
    """

    def __init__(self, switch_defs: list[SwitchDef]) -> None:
        """初始化所有道岔状态——默认定位且未被锁闭。

        Args:
            switch_defs: 从 RouteCatalog.get_switch() 获取的静态定义列表
        """
        self._switches: dict[str, SwitchState] = {}
        for sw in switch_defs:
            self._switches[sw.switch_id] = SwitchState(
                switch_id=sw.switch_id,
                actual_position="NORMAL",  # 默认定位
            )

    # ==================================================================
    # 查询接口 —— 供 InterlockingRuleEngine / 前端使用
    # ==================================================================

    def get_position(self, switch_id: str) -> str:
        """返回道岔当前的实际位置。"""
        state = self._switches.get(switch_id)
        return state.actual_position if state else "NORMAL"

    def is_available_for(self, switch_id: str, required_position: str) -> bool:
        """道岔是否可用于满足 *required_position* 的进路。

        由 InterlockingRuleEngine.check() 在进路办理前调用。

        不可用的条件：
        - health != "OK"（故障）
        - 已被其他进路锁在相反位置
        """
        state = self._switches.get(switch_id)
        if state is None:
            return False
        if state.health != "OK":
            return False
        if state.locked_by_route_id is not None:
            # 已被某进路锁闭，只有位置相同时才可用
            return state.actual_position == required_position
        return True

    def is_locked(self, switch_id: str) -> bool:
        """道岔是否被锁闭。"""
        state = self._switches.get(switch_id)
        return state.locked_by_route_id is not None if state else False

    def locked_by(self, switch_id: str) -> str | None:
        """返回锁闭此道岔的进路 ID，未被锁闭则返回 None。"""
        state = self._switches.get(switch_id)
        return state.locked_by_route_id if state else None

    def all_switches(self) -> dict[str, SwitchState]:
        """返回所有道岔的状态字典。"""
        return dict(self._switches)

    # ==================================================================
    # 变更接口 —— 供 RouteService / EnvironmentService 使用
    # ==================================================================

    def lock(self, switch_id: str, position: str, route_id: str) -> bool:
        """将道岔锁在指定位置，归属于 *route_id*。

        由 RouteService.request() 在进路办理成功后调用。

        Returns:
            True 如果锁闭成功，False 如果道岔不可用。
        """
        if not self.is_available_for(switch_id, position):
            return False
        state = self._switches[switch_id]
        state.requested_position = position
        state.actual_position = position
        state.locked_by_route_id = route_id
        return True

    def unlock(self, switch_id: str, route_id: str) -> None:
        """释放 *route_id* 对道岔的锁闭。

        由 RouteService.release() 在进路释放时调用。
        只有锁闭归属匹配时才会解锁（防止误释放其他进路的锁）。
        """
        state = self._switches.get(switch_id)
        if state is None:
            return
        if state.locked_by_route_id == route_id:
            state.locked_by_route_id = None
            state.requested_position = None

    def set_fault(self, switch_id: str) -> None:
        """注入道岔故障 —— 由 EnvironmentService 或测试调用。"""
        state = self._switches.get(switch_id)
        if state is not None:
            state.health = "FAILED"

    def clear_fault(self, switch_id: str) -> None:
        """清除道岔故障 —— 由 EnvironmentService 或测试调用。"""
        state = self._switches.get(switch_id)
        if state is not None:
            state.health = "OK"

    def snapshot(self) -> list[dict]:
        """返回所有道岔状态的序列化快照（前端 API 用）。"""
        return [s.to_dict() for s in self._switches.values()]
