"""联锁子系统数据模型 —— 成员 C Phase 2。

本文件包含两类数据模型：
1. 线路导入时一次性加载的静态定义（AxleSectionDef, LogicalSectionDef, RouteDef, SwitchDef）
2. 仿真运行时持续更新的可变状态（SectionOccupation, RouteState, SwitchState）
   以及外部接口入参/出参（RouteRequest, RouteResult）

数据流向：
  线路 Excel → LineDataImporter(成员A) → line_map.json
    → RouteCatalog 读取 routes / axleSections / switches
    → SectionOccupationService / SwitchLockService 维护运行时状态
    → InterlockingRuleEngine 读静态定义 + 运行时状态 → RouteCheckResult
    → RouteService 调 RuleEngine，读写 SectionOccupSvc / SwitchLockSvc → RouteResult
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# 区段定义（从 line_map.json 一次性加载，之后不变）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AxleSectionDef:
    """计轴区段静态定义 —— 从 line_map.json 的 axleSections 加载。

    每个计轴区段由一组 Seg 组成，联锁通过检测这些 Seg 上是否有列车
    来判断区段占用状态。这些数据在仿真全过程不变。
    """

    section_id: str                      # 区段 ID（如 "1", "JZ17"）
    name: str                            # 区段名称（如 "JZ1"）
    segment_ids: frozenset[int]          # 该区段包含的 Seg ID 集合（不可变）


@dataclass(frozen=True)
class LogicalSectionDef:
    """逻辑区段静态定义 —— 从 line_map.json 的 logicalSections 加载。

    逻辑区段用于列控 MA 计算和追踪间隔管理，不同于计轴区段（用于联锁进路）。
    Phase 2 暂时只做计轴区段占用检测，逻辑区段数据预留用于后续 MA 升级。
    """

    section_id: str                      # 区段 ID
    name: str                            # 区段名称
    start_segment_id: int                # 起始 Seg ID
    start_offset_m: float                # 起始 Seg 偏移量（米）
    end_segment_id: int                  # 终止 Seg ID
    end_offset_m: float                  # 终止 Seg 偏移量（米）


# ---------------------------------------------------------------------------
# 运行时状态 —— 区段占用
# ---------------------------------------------------------------------------


@dataclass
class SectionOccupation:
    """单个区段的运行时占用状态（每 tick 由 SectionOccupationService.update() 更新）。

    由 SectionOccupationService 产出：
    - RouteService 的 update() 用它判断进路区段是否可释放
    - InterlockingRuleEngine.check() 用它判断区段是否空闲
    - 前端可视化用它着色
    """

    section_id: str                      # 区段 ID
    section_type: str = "AXLE"           # "AXLE"（计轴）| "LOGICAL"（逻辑）
    occupied: bool = False               # 当前是否被占用
    train_ids: list[str] = field(default_factory=list)  # 占用该区段的列车 ID 列表
    source: str = "DERIVED_FROM_POSITION"  # 占用信息来源

    def to_dict(self) -> dict:
        """序列化为前端 API 返回格式。"""
        return {
            "sectionId": self.section_id,
            "sectionType": self.section_type,
            "occupied": self.occupied,
            "trainIds": list(self.train_ids),
            "source": self.source,
        }


# ---------------------------------------------------------------------------
# 进路定义（从 line_map.json 一次性加载，之后不变）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RouteDef:
    """进路静态定义 —— 从 line_map.json 的 routes 加载，由 RouteCatalog 解析。

    RouteCatalog 在加载后调用 _derive_switch_requirements() 补填
    required_switches 字段（根据进路经过的 Seg 集合推导需要哪些道岔
    在定位还是反位）。
    """

    route_id: str                        # 进路 ID（如 "1", "R-GGZ-FSP"）
    name: str                            # 进路名称
    route_type: str                      # 进路类型，如 "0x0001"（主进路）
    start_signal_id: int                 # 始端信号机 ID（进路从这里开始）
    end_signal_id: int                   # 终端信号机 ID（进路到这里结束）
    axle_section_ids: list[str]          # 进路包含的计轴区段 ID 列表
    protection_section_ids: list[str]    # 保护区段 ID 列表
    ci_area_id: int | None = None        # CI 区域 ID
    # 以下由 RouteCatalog._derive_switch_requirements() 补填：
    required_switches: dict[str, str] = field(default_factory=dict)
    # switch_id → "NORMAL" | "REVERSE"（办理此进路需要的道岔位置）
    conflicting_route_ids: frozenset[str] = frozenset()
    # 与此进路敌对的进路 ID 集合（RouteCatalog._compute_conflicts() 预计算）


# ---------------------------------------------------------------------------
# 进路运行时模型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RouteRequest:
    """进路办理请求 —— 外部模块（调度/API/司机台）向 RouteService 发起的请求。

    是 InterlockingRuleEngine.check() 的入口参数之一。
    来源可以是：
    - DispatchService（调度自动决策）
    - HTTP API（前端/测试手动办理）
    - 司机台（人工操作）
    """

    request_id: str                      # 请求唯一 ID
    route_id: str                        # 要办理的进路 ID
    train_id: str                        # 申请进路的列车 ID
    source: str = "DISPATCH"             # 请求来源："DISPATCH" | "API" | "DRIVER"


@dataclass(frozen=True)
class RouteResult:
    """进路办理结果 —— RouteService.request() 的返回值。

    无论成功还是失败都返回此结构：
    - accepted=True  → 进路已锁闭，locked_sections 和 locked_switches 非空
    - accepted=False → 进路办理失败，failure_reason 说明原因
    """

    accepted: bool                       # 是否办理成功
    route_id: str                        # 进路 ID
    train_id: str                        # 列车 ID
    state: str                           # "LOCKED" | "FAILED"
    failure_reason: str | None = None     # 失败原因：SECTION_OCCUPIED / SWITCH_UNAVAILABLE / ...
    locked_sections: list[str] = field(default_factory=list)  # 成功时锁定的区段 ID 列表
    locked_switches: dict[str, str] = field(default_factory=dict)  # 成功时锁定的道岔及位置

    def to_dict(self) -> dict:
        """序列化为前端/测试的返回格式。"""
        return {
            "accepted": self.accepted,
            "routeId": self.route_id,
            "trainId": self.train_id,
            "state": self.state,
            "failureReason": self.failure_reason,
        }


@dataclass
class RouteState:
    """进路运行时状态 —— RouteService 内部维护，每 tick 可变。

    生命周期：
      IDLE → REQUESTED → CHECKING → LOCKED → APPROACH_LOCKED → RELEASING → IDLE
         └────────────────────────────────────────────────────────→ FAILED

    - LOCKED：进路已锁闭，区段和道岔归本进路独占，信号可开放
    - APPROACH_LOCKED：列车已进入接近锁闭区段，取消进路需延时
    - RELEASING：列车尾部清出最后一个区段，正在解锁道岔和区段
    - FAILED：办理失败，记录失败原因（保留用于调试）
    """

    route_id: str                        # 进路 ID
    state: str = "IDLE"                  # 当前状态
    train_id: str | None = None          # 关联的列车 ID
    locked_sections: list[str] = field(default_factory=list)  # 已被锁定的区段列表
    locked_switches: dict[str, str] = field(default_factory=dict)  # 已锁定的道岔及位置
    failure_reason: str | None = None    # 办理失败时的原因
    lock_time_ms: int | None = None      # 锁闭时刻（仿真毫秒）


# ---------------------------------------------------------------------------
# 道岔运行时模型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SwitchDef:
    """道岔静态拓扑定义 —— 从 line_map.json 的 switches 加载。

    由线路导入器（成员 A）从 Excel 提取，RouteCatalog 加载后供
    _derive_switch_requirements() 使用。

    字段含义（以 9 号线实际道岔为例）：
    - frogSegId：岔心 Seg（道岔公共端，列车从这进入分叉）
    - normalSegId：定位方向去的 Seg（道岔直股）
    - reverseSegId：反位方向去的 Seg（道岔曲股/侧线）
    """

    switch_id: str                       # 道岔 ID
    name: str                            # 道岔名称
    normal_seg_id: int | None = None     # 定位 Seg ID
    reverse_seg_id: int | None = None    # 反位 Seg ID
    frog_seg_id: int | None = None       # 岔心 Seg ID


@dataclass
class SwitchState:
    """道岔运行时状态 —— SwitchLockService 内部维护。

    默认所有道岔处于定位（NORMAL）且未被任何进路锁闭。
    办理进路时 RouteService 调用 SwitchLockService.lock() 锁闭道岔；
    进路释放时调用 unlock() 解锁。

    故障模式（health=FAILED）不可被任何进路使用，EnvironmentService
    可调用 set_fault() 注入故障。
    """

    switch_id: str                       # 道岔 ID
    requested_position: str | None = None  # 请求位置："NORMAL" | "REVERSE" | None
    actual_position: str = "NORMAL"       # 实际位置："NORMAL" | "REVERSE" | "FOUR_WAY"
    locked_by_route_id: str | None = None  # 被哪条进路锁闭（None = 自由）
    health: str = "OK"                    # "OK" | "FAILED"

    def to_dict(self) -> dict:
        """序列化为前端/API 返回格式。"""
        return {
            "switchId": self.switch_id,
            "requestedPosition": self.requested_position,
            "actualPosition": self.actual_position,
            "lockedByRouteId": self.locked_by_route_id,
            "health": self.health,
        }
