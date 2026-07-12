# 成员 C 拓扑与主引擎修复记录

> 目的：记录已经验证过的故障、根因和不可随意删除的约束。后续重构时，应先确认下表的行为仍有对应测试或端到端验证。
>
> 范围：九号线主引擎、进路/联锁、MA、成员 C 拓扑视图及其 React 容器。

## 修复总表

| 编号 | 用户可见现象 | 已确认根因 | 当前修复与不可破坏的约束 | 关键位置 | 验证方式 |
|---|---|---|---|---|---|
| F01 | 拓扑图缺少部分 Seg、车库和分歧关系不完整 | 旧 demo 使用简化布局/独立状态，未完整适配导入线路数据 | Canvas 以完整静态线路数据绘制 Seg、道岔、信号和计轴区段；主引擎模式只替换动态状态 | `member-c-demo.html`、`member-c-topology.js` | Phase 2 拓扑测试 |
| F02 | 在拓扑图加车后，车跑到同站另一股道或错误方向 | 加车只按站码选站台，丢失点击的具体 Seg | `initialSegmentId` 必须随 API 传递；后端必须校验它属于该站且方向存在合法进路链 | `engine.add_train`、`_topology_start_options` | S13/S39/S55/S69 起车验证 |
| F03 | 宏观图把郭公庄车辆画到国家图书馆 | Amap 站序与后端站序不同，按 `stationIndex` 插值发生错位 | 宏观定位必须按后端 `currentStationCode/nextStationCode` 和进度映射，不得混用两套站序 | `useSimStore.updateFromBackend` | 宏观地图人工验证 |
| F04 | 到站前最后几米低速爬行、长期不进入停站 | 离散物理积分无法稳定落入过小的到站容差 | PathPlan 主流程的到站捕获距离保持 `10m`，且仍要求低速；不要把 MA 终点向前硬截断 | `SimulationEngine.STATION_CAPTURE_DISTANCE_M` | 长时运行验证 |
| F05 | PathPlan 按最短 Seg 走，和进路表不一致 | 站间规划未使用进路表链 | 普通客运站间运行先由 `RouteChainPlanner` 得到进路链，再构造 PathPlan；无合法链时停车，不回退最短路 | `route_chain_planner.py`、`engine._ensure_interval_path` | `test_route_chain_planner.py` |
| F06 | 两车/冲突进路可以同时办理，或进路过早释放 | 联锁没有统一处理锁闭、敌对关系、列车归属与尾部释放 | 进路必须由 `RouteService` 办理；释放只能依据同一列车已经越过的区段前缀，未来区段不得提前释放 | `route_service.py`、`models.py` | 联锁测试 |
| F07 | 车辆从站台出发时永远 `SECTION_OCCUPIED`，停站后不再启动 | 进路表包含始发站台计轴区段；规则把申请车自身占压当成外来占压 | 仅当占用者集合**恰为申请列车自身**时，允许办理该进路的计轴区段；外来、共享、未知占压和保护区段仍必须拒绝 | `rule_engine.py` | `test_own_occupied_entry_section_allows_route` |
| F08 | 信号机全红，没有黄/绿 | 信号解析器同样把锁闭进路中本车的自占用判为红；且出发进路到停站结束才办理，开放时间极短 | 信号计算与 MA 共用“进路所有权 + 占压”语义：自车独占允许开放，后续信号/进路决定黄或绿；停站期间提前办理下一进路 | `signal_resolver.py`、`engine._prepare_train_step` | S69 上行：SC 黄灯、进路 36 锁闭 |
| F09 | ATO 看似自行通过红灯或速度行为与 MA 无关 | ATO 目标未接入按锁闭进路计算的 MA | ATO 目标速度/终点必须来自 `MovementAuthorityService`；ATP 继续以 MA 制动点兜底 | `movement_authority.py`、`engine.py` | `test_movement_authority.py` |
| F10 | 停站后为什么不动难以判断 | 状态机未向拓扑日志暴露到站、停站、等待进路等事件 | 运行阶段固定为 `DWELLING -> DEPARTING` 或 `WAITING_ROUTE`；等待时保留失败原因并按 2 秒仿真时间重试 | `engine._prepare_train_step`、`member-c-topology.js` | 拓扑日志人工验证 |
| F11 | 切换页面回来，拓扑日志消失 | 日志仅保存在 iframe JS 内存，iframe 被重新创建即清空 | 拓扑日志写入 `sessionStorage`；同一浏览器标签页切换页面后必须恢复。关闭标签页后允许清空 | `member-c-topology.js` | 页面切换人工验证 |
| F12 | 拓扑画面长时间不更新，下一次跳数百 tick | iframe 只在自身“播放”按钮启动后轮询；外层加车/顶栏启动只刷新一次 | 引擎模式必须始终保持单一、自调度的刷新循环；请求完成后再安排下次请求，禁止多个 `setInterval` 并发轮询 | `member-c-topology.js`、`member-c-demo.html` | 150ms 刷新人工验证 |
| F13 | Tick 速度控件改变仿真物理结果 | 把现实播放间隔和 `clock.tick_seconds` 混为一谈 | 滑杆只控制 `_tick_interval_seconds`（墙钟时间）；物理、客流和动力学继续使用 `clock.tick_seconds` | `engine.py`、`api_server.py`、`App.tsx` | Tick API 回读验证 |
| F14 | Vite 报 `newPositions has already been declared`，或后端状态无法同步 | `updateFromBackend` 被错误拼入本地 demo `tick()`，造成重复声明和同步函数丢失 | `updateFromBackend` 必须独立保留；主引擎与本地演示状态不可混合进同一函数 | `useSimStore.ts` | `npx vite build` |
| F15 | 删除一辆车后，另一辆车永久 `CONFLICT_ROUTE_LOCKED` | 删车只移除了车辆对象，没有释放该车持有的进路/道岔锁，形成孤儿进路 | `remove_train` 必须先紧急释放**该车**拥有的锁闭进路，再清除车辆并刷新占压；不得释放其他车的进路 | `engine.remove_train`、`route_service.release_routes_owned_by` | `test_remove_owner_releases_only_its_locked_routes` |
| F16 | 终点站列车到达 S13 后从拓扑图消失 | 到站处理无条件清空 `currentSegmentId`；终点没有下一段 PathPlan 来恢复显示位置 | 到站时保留最后一个路径约束的 Seg/偏移量；中间站由下一段 PathPlan 覆盖，终点 `IDLE` 车停留在站台图上 | `engine._complete_path_arrival` | S55 下行至郭公庄人工验证 |
| F17 | 列车到终点后永久 `IDLE`，无法继续运营 | 终点守卫只终止列车，没有调度层折返决策和反向进路办理 | 两端站执行同站台折返：终点停站、调度生成 `TURNBACK`、确认反向进路链、释放本车进站锁、切换方向并重新按 MA 发车；无同站台链时保守终到 | `dispatch.services`、`engine._handle_terminal_turnback` | `test_terminal_turnback_reverses_on_same_platform` |
| F18 | S22 等非运营 Seg 被浅绿误画成站台 | 原始平台表包含里程 1m、`0xff` 标记的占位记录；前端直接按原始 `platformIds` 着色 | 静态拓扑分为运营 `platformIds` 与审计 `rawPlatformIds`；仅映射到车站表的前者画浅绿并提供加车语义 | `api_server.member_c_static_routes` | S22 不再浅绿，原始 ID 29 仍可查询 |

| F19 | 列车经道岔走进路 9 时，未走的 S41 也在拓扑图中显示红色占压 | 占压服务的车尾回溯使用全局拓扑的第一个前驱；地图又把一个已占压计轴区段的全部 Seg 均涂红。S43 是道岔公共 Seg，原始表同时把它列进 JZ33（含 S41）和 JZ35（含 S44） | 联锁占压仍按原始计轴区段计算；每列车另携带已批准 `PathTrackQuery` 供车尾沿实际进路回溯。地图红色/车色仅绘制列车实体覆盖的 Seg，不把共享检测区段的未走支路伪装成车体占压 | `engine._refresh_interlocking`、`section_occupation.py`、`api_server._topology_engine_state` | `test_tail_follows_approved_turnout_branch_not_global_first_predecessor` |

| F20 | 多辆车可从同一站台 Seg 重复加入，随后互相占压并经保护区段阻塞其他列车 | 加车 API 只校验站台/方向和可规划进路；列车直到后续 tick 才请求进路，缺少加入前的车体足迹冲突检查 | 加车前先解析出发 PathPlan 和当前 Seg/偏移，按车长投影实体足迹；与任一既有列车足迹相交时拒绝 `INITIAL_PLACEMENT_OCCUPIED` 并返回冲突列车 ID。此规则属于加车准入，不能由 MA 替代 | `engine.add_train`、`SectionOccupationService.physical_footprint`、`TrainManagementPanel` | `test_add_train_rejects_an_overlapping_platform_placement`；API 双加车验证 |

| F21 | 相距很远的列车错误地占用某进路的保护区段，例如 S235 阻塞进路 36 | `RouteDef.protection_section_ids` 保存的是保护区段表 ID，规则引擎却直接用该数值查询计轴区段；保护区段 18 与计轴 JZ18 同号，产生跨表 ID 碰撞 | 必须按 `进路 -> 保护区段表 -> axleSectionIds -> 计轴占压` 两跳解析。进路 36 的保护区段 18 实际映射 JZ61/S73，S235/JZ18 不再误拦截 | `RouteCatalog.protection_axle_section_ids`、`InterlockingRuleEngine.check` | `test_route_protection_uses_mapped_axle_section_not_same_numbered_section` |

## 运行状态机
| F22 | 逻辑区段 ID 与计轴区段 ID 相同，导致列车已经离开计轴区段却被错误认为未清出，进路提前释放或 MA/信号状态错误 | 原始工作簿的多类区段各自独立编号；运行时曾把逻辑区段和计轴区段存入同一个裸 ID 缓存 | 计轴占压与逻辑区段缓存必须物理分离；前端诊断快照中的逻辑区段 ID 必须加 `LOGICAL:` 前缀。当前联锁、MA、信号和拓扑诊断只能显式调用 `is_axle_occupied` / `axle_occupied_by` | `section_occupation.py`、`rule_engine.py`、`route_service.py`、`signal_resolver.py`、`movement_authority.py` | `test_logical_section_id_does_not_overwrite_same_numbered_axle_section` |
| F23 | 列车进入同号但无关的 JZ18 时，进路被错误转入接近锁闭；或真实接近区段未被识别 | `pointApproachSectionIds` 保存的是点式接近区段表 ID，不是计轴区段 ID | 接近锁闭必须按 `进路 -> pointApproachSections -> axleSectionIds -> 计轴占压` 解析；接近区段 18 映射 JZ61 时，JZ18 不得触发，JZ61 必须触发 | `RouteCatalog.point_approach_axle_section_ids`、`RouteService.update` | `test_approach_lock_uses_mapped_axle_section_not_same_numbered_id` |

```text
区间运行
  -> 距离 <= 10m 且速度 <= 0.2m/s
  -> 到站，清理本区间 PathPlan，处理客流
  -> DWELLING（通常至少 30 个仿真秒）
      -> 期间预办理下一进路、计算下一段 MA、开放出发信号
  -> 倒计时结束
      -> 出站进路已锁闭且 MA 可用：DEPARTING
      -> 否则：WAITING_ROUTE（记录原因，每 2 个仿真秒重试）
```

## 信号、进路与 MA 的方向

```text
进路表 + 道岔状态 + 区段占压
        -> 联锁办理并锁闭进路
        -> 信号机黄/绿/红
        -> MovementAuthorityService 计算 MA
        -> ATO 以 MA 作为速度与制动边界
```

不要把 MA 作为实体信号机的输入再反向计算信号。这样会形成循环依赖。若前端需要展示“本车获授权”，应在信号灯之外显示该车的 MA，而不是伪造每辆车不同颜色的同一架实体信号机。

## 修改前检查

1. 先运行 `python -m pytest tests/test_member_c_phase2.py tests/test_engine_api.py tests/test_route_chain_planner.py tests/test_movement_authority.py -q`。
2. 修改进路/占压判断时，至少保留“自车独占可通过”和“外来车占压必须拒绝”两类测试。
3. 修改拓扑刷新或日志时，手工验证顶栏启动、拓扑页加车、切换页面回拓扑三种入口。
4. 修改 Tick 控件时，确认它不改变 `SimulationClock.tick_seconds`。

## 本次 main 合并增补记录

| 编号 | 用户可见现象 | 已确认根因 | 当前修复与不可破坏的约束 | 关键位置 | 验证方式 |
|---|---|---|---|---|---|
| F24 | FSP -> GGZ、KYL -> FSP 等下行相邻站运行到站后不再发车，拓扑上停在非站台 Seg 或一直等待 `NO_MAINLINE_ROUTE_MAPPING` | `line9-mainline-v1` 的 77 Seg 白名单来自旧最短路径，不覆盖完整上下行进路表链；联锁 Runtime 曾用该白名单筛掉合法进路和计轴区段 | 77 Seg 只作为普通演示/显示范围，不能作为 CI/MA 的规则过滤。`InterlockingRuntimeCoordinator` 的 eligible routes 和 `_sections_for_path()` 必须基于完整 319 Seg 线路数据 | `app/domain/interlocking/runtime.py`、`tests/test_mainline_scope.py` | `test_terminal_turnback_reverses_on_same_platform`、`test_mainline_scope.py` |
| F25 | 列车到站或终点折返后 `currentSegmentId` 变成 `None`，或锚到上一段路径的非站台 Seg，导致拓扑车消失/下一段 MA 从错误位置计算 | 到站清理 PathPlan 时清空了车头 Seg；下一段 PathPlan 建立前没有按当前运行方向重新锚定站台段 | 到站、终点折返后必须调用方向敏感的站台锚定：UP 使用平台表 `0x55` 对应 Seg，DOWN 使用 `0xaa` 对应 Seg。折返后先切方向，再锚定反向站台 | `SimulationEngine._anchor_train_at_current_platform()`、`_complete_path_arrival()`、`_turn_train_at_terminal()` | S55 下行到 GGZ 后折返为 UP 且停在 S13 |
| F26 | 折返实际发生，但拓扑/调度日志看不到 `TURNBACK` | 折返函数只改车辆方向，没有把生命周期事件合并到当前 tick 的调度决策快照 | 折返时生成 `DispatchDecision(action="TURNBACK")`，通过 `_pending_dispatch_decisions` 合并进本 tick `dispatch_decisions`，不能被常规调度覆盖 | `SimulationEngine._pending_dispatch_decisions`、`_turn_train_at_terminal()` | `test_terminal_turnback_reverses_on_same_platform` |
| F27 | 拓扑页只能看到车的 Seg，缺少当前站、下一站、进路链和中文调度事件，日志上下文不足 | 主引擎拓扑投影只返回旧 demo 的最小字段，并把 `events` 固定为空 | `/api/sim/topology-state` 必须继续保持成员 C shape，同时补充 `routeIds/currentStation/nextStation/directionCode`，并把 `TURNBACK/HOLD` 等调度决策投成中文 events | `ApiHandler._main_engine_topology_state()` | `tests/test_api_server.py`、前端 `npm run build` |
| F28 | `MemberCDemoRunner` 预锁进路测试误报 route 9 未锁闭 | route 9 已被列车接近推进到 `APPROACH_LOCKED`，它仍是锁闭状态的一种，不应只按 `LOCKED` 判断 | 所有“锁闭进路”统计和测试应同时包含 `LOCKED` 与 `APPROACH_LOCKED` | `tests/test_api_server.py`、拓扑锁闭计数 | `test_member_c_demo_prelocks_routes_for_signal_progression` |
