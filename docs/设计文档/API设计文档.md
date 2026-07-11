# 轨道交通仿真系统 API 设计文档

版本：v0.1  
日期：2026-07-08  
适用范围：Phase 0、Phase 1、Phase 2 详细设计；Phase 3 实验 API 概要设计  
依据文档：`轨道交通仿真系统软件设计文档.md`、`数据库设计文档.md`、`前端接入与双层界面设计.md`

## 1. 设计目标

API 层负责连接仿真内核、数据库、平台接口适配器和前端双层界面。它不直接实现车辆、联锁、客流或供电算法，而是提供稳定的数据访问、控制命令、状态推送和回放查询接口。

API 设计目标：

1. 支持 Phase 0/1 快速接入前端，优先跑通 9 号线静态数据和单车仿真状态。
2. 支持 Phase 2 多车、联锁、客流、调度、自研供电、扰动和平台接口健康展示。
3. 支持 Phase 3 批量优化实验、指标查询、策略对比和回放。
4. 明确数据来源、数据质量和操作来源，避免把“自研/平台/Mock”和“降级/估算”等语义混用。
5. 保持前端字段稳定，减少后续模块实现变化对 UI 的影响。

## 2. 总体约定

## 2.1 基础路径

开发环境：

```text
http://127.0.0.1:8000
ws://127.0.0.1:8000
```

所有 HTTP API 使用 `/api` 前缀。

## 2.2 协议与格式

| 项 | 约定 |
|---|---|
| HTTP 方法 | 查询用 `GET`，状态改变用 `POST`，删除/清理用 `DELETE` |
| 数据格式 | JSON |
| 编码 | UTF-8 |
| 时间 | 仿真时间用 `simTimeMs`，墙钟时间用 ISO 8601 字符串 |
| 单位 | 字段名必须带单位，例如 `speedMps`、`powerKw`、`energyKwh` |
| 坐标 | 地图经纬度使用 `lat`、`lng` |
| 枚举 | 使用大写字符串，例如 `RUNNING`、`LOCKED`、`ONLINE` |
| 分页 | 列表接口使用 `limit`、`offset` |

## 2.3 响应格式

为避免前后端按不同契约实现，响应格式按阶段定稿如下：

1. Phase 0 已实现的轻量 API 作为 legacy 接口，可继续直接返回业务对象。
2. Phase 1 及之后新增接口必须统一使用 `{ ok, data, meta }` 响应包裹。
3. 本文后续接口示例若直接展示业务对象，默认表示包裹后的 `data` 内容；只有明确标注 legacy 的 Phase 0 接口可以直出。
4. 错误响应在所有阶段统一使用 `{ ok: false, error }`。

```json
{
  "ok": true,
  "data": {},
  "meta": {
    "simTimeMs": 120000,
    "source": "SELF_SIM",
    "generatedAt": "2026-07-08T10:00:00Z"
  }
}
```

错误响应：

```json
{
  "ok": false,
  "error": {
    "code": "ROUTE_NOT_LOCKABLE",
    "message": "Route cannot be locked because one or more interlocking constraints failed.",
    "detail": {
      "routeId": "R-001",
      "failedChecks": ["CONFLICT_ROUTE_LOCKED"]
    }
  }
}
```

## 2.4 通用查询参数

| 参数 | 类型 | 说明 |
|---|---|---|
| `runId` | integer/string | 运行批次 ID；不传时查询当前运行 |
| `simTimeMs` | integer | 指定仿真时间点，用于回放 |
| `fromMs` | integer | 时间范围起点 |
| `toMs` | integer | 时间范围终点 |
| `limit` | integer | 最大返回条数，默认 100 |
| `offset` | integer | 分页偏移，默认 0 |
| `source` | string | 数据来源过滤 |
| `quality` | string | 数据质量过滤 |

## 2.5 来源、质量与操作枚举

### 2.5.1 `source`：数据来源

| 值 | 含义 |
|---|---|
| `SELF_SIM` | 自研仿真模型 |
| `MOCK` | Mock 平台或测试数据 |
| `PLATFORM` | 老师协议中明确存在的平台接口 |
| `SCENARIO_FORCED` | 场景或扰动强制设置 |

### 2.5.2 `quality`：数据质量

| 值 | 含义 |
|---|---|
| `MEASURED` | 来自实测或平台有效帧 |
| `ESTIMATED` | 自研模型估算或推导 |
| `DEGRADED` | 接口异常、模型缺项或数据不完整时的降级结果 |
| `STALE` | 超过有效时间窗的旧数据 |

### 2.5.3 `mode`：运行模式

| 值 | 含义 |
|---|---|
| `PURE_SIM` | 纯自研仿真 |
| `PLATFORM_IN_LOOP` | 引入老师平台接口闭环 |
| `MOCK` | Mock 数据或测试模式 |

### 2.5.4 `operatorSource`：命令或决策来源

| 值 | 含义 |
|---|---|
| `DISPATCHER` | 人工调度员输入 |
| `AUTO_RULE` | 规则调度或算法自动生成 |
| `DRIVER` | 司机台或人工驾驶输入 |
| `ATO` | 自动驾驶控制输出 |
| `ATP` | 安全防护覆盖输出 |
| `POWER_LIMIT` | 供电限牵修正 |
| `SCENARIO` | 场景脚本触发 |
| `SYSTEM` | 系统保护或状态机触发 |

重要约束：当前老师协议无独立供电仿真接口，因此供电 API 返回的 `source` 不得为 `PLATFORM`，只能是 `SELF_SIM` 或 `SCENARIO_FORCED`；供电结果若因缺少实测数据而降级，应使用 `quality: "ESTIMATED"` 或 `quality: "DEGRADED"` 表达。

## 2.6 ID 类型与命名

为避免同一字段有时表示原始整数、有时表示业务字符串，API 统一采用以下规则：

| 类型 | API 字段 | 示例 | 说明 |
|---|---|---|---|
| 原始导入整数 ID | `id` 或具体对象 ID，如 `segmentId`、`platformId`、`signalId` | `13` | 来自线路表格或 `line_map.json` 的原始编号，保持整数语义 |
| 车站业务 ID | `stationBusinessId` | `S-GGZ` | 面向客流、调度、停站等业务记录的稳定字符串 |
| 车站代码 | `stationCode` | `GGZ` | 线路内车站代码，可用于展示和外部资料对照 |
| 车站原始 ID | `stationId` | `1` | 仅用于 Phase 0 legacy 静态站点映射；Phase 1+ 新接口不得用它承载 `S-GGZ` |
| 运行批次 ID | `runId` | `1` 或 `RUN-20260708-001` | 查询参数兼容整数主键和 UUID，响应中应同时给出 `id` 与 `runUuid` |
| 列车/进路/供电分区业务 ID | `trainId`、`routeId`、`powerSectionId` | `T0901`、`R-001`、`PWR-09-01` | 业务对象稳定标识，使用字符串 |

若接口需要同时返回原始 ID 与业务 ID，必须分别给出，例如：

```json
{
  "stationId": 1,
  "stationCode": "GGZ",
  "stationBusinessId": "S-GGZ",
  "stationName": "郭公庄"
}
```

## 2.7 字段映射表

| 数据库字段 | API 字段 | `line_map.json` 字段 | 单位 | 可空 | 说明 |
|---|---|---|---|---|---|
| `segment_id` | `segmentId` | `segments[].id` | 无 | 否 | Seg 原始导入编号，API 使用整数 |
| `platform_id` | `platformId` | `platforms[].id` | 无 | 否 | 站台原始导入编号 |
| `signal_id` | `signalId` | `signals[].id` | 无 | 否 | 信号机原始导入编号 |
| `station_id` | `stationId` | `stations[].id` 或导入顺序 | 无 | 否 | Phase 0 静态站点原始 ID |
| `station_code` | `stationCode` | `stations[].code` | 无 | 否 | 车站代码 |
| `station_business_id` | `stationBusinessId` | 派生：`S-` + `code` | 无 | 否 | 客流、调度、停站等业务 ID |
| `offset_m` | `offsetM` | `offsetM` | m | 视对象而定 | Seg 内偏移 |
| `position_m` | `positionM` | 派生累计里程 | m | 是 | 沿当前路径累计位置 |
| `length_m` | `lengthM` | `lengthM` | m | 否 | Seg、站台或区段长度 |
| `speed_limit_mps` | `speedLimitMps` | `speedLimitMps` | m/s | 否 | 限速；展示 km/h 时由前端换算 |
| `gradient_permille` | `slopePermille` | `slopePermille` | ‰ | 是 | API 新字段使用 `slopePermille`；`gradientPermille` 为旧别名 |
| `sim_time_ms` | `simTimeMs` | 无 | ms | 运行表否 | 仿真时钟 |
| `load_factor` | `loadFactor` | 无 | 比例 | 是 | 0 到 1 以上可表示超载 |
| `source` | `source` | `source` | 无 | 否 | 数据来源 |
| `quality` | `quality` | 无 | 无 | 是 | 数据质量 |
| `derivation_method` | `derivationMethod` | 无 | 无 | 是 | 推导方法，如 `FROM_TRAIN_POSITION` |

## 2.8 轨道位置、范围与列车头尾语义

API 中凡涉及列车、区段占用、进路释放、扰动范围、移动授权或调度间隔的位置字段，必须区分“轨道上的一个点”和“列车/事件占用的一段范围”。不得只给一个 `segmentId + offsetM` 而让调用方猜测它代表车头、车尾还是中心点。

### 2.8.1 TrackPoint：轨道上的单点

`TrackPoint` 表示线路拓扑上的一个点：

```json
{
  "segmentId": 13,
  "offsetM": 30.0,
  "positionM": 343.0
}
```

字段说明：

| 字段 | 含义 |
|---|---|
| `segmentId` | 点所在的 Seg 编号 |
| `offsetM` | 点在该 Seg 内的偏移，单位 m |
| `positionM` | 点沿当前线路拓扑投影后的累计里程，单位 m |

约束：
1. `positionM` 用于排序、速度曲线、间隔计算和前端宏观展示。
2. `segmentId + offsetM` 用于查询限速、坡度、信号机、站台、计轴区段等轨道对象。
3. 若列车通过道岔或非单线线性拓扑，`positionM` 只能在同一 `pathId` 或同一运行方向路径内比较；跨路径比较必须先经过 `TrackNavigator` 映射。

### 2.8.2 TrainPosition：列车位置必须包含头尾

列车位置统一使用 `TrainPosition`：

```json
{
  "referencePoint": "HEAD",
  "direction": "UP",
  "pathId": "LINE9-UP-MAIN",
  "trainLengthM": 118.0,
  "head": {
    "segmentId": 13,
    "offsetM": 30.0,
    "positionM": 343.0
  },
  "tail": {
    "segmentId": 12,
    "offsetM": 86.0,
    "positionM": 225.0
  },
  "center": {
    "segmentId": 12,
    "offsetM": 145.0,
    "positionM": 284.0
  },
  "spans": [
    {"segmentId": 12, "fromOffsetM": 86.0, "toOffsetM": 180.0},
    {"segmentId": 13, "fromOffsetM": 0.0, "toOffsetM": 30.0}
  ]
}
```

语义约束：

| 字段 | 含义 |
|---|---|
| `referencePoint` | 当前主位置参考点，列车状态默认 `HEAD` |
| `direction` | 列车运行方向，决定车头/车尾关系 |
| `trainLengthM` | 列车物理长度，区段占用和进路释放必须使用 |
| `head` | 当前运行方向上的列车前端点 |
| `tail` | 当前运行方向上的列车后端点 |
| `center` | 可选，主要用于宏观地图图标或摄像机跟随，不得用于安全占用判断 |
| `spans` | 列车实际覆盖的 Seg 片段，必须支持跨多个 Seg |

若后端暂时只存储车头点，则车尾必须由后端通过 `TrackNavigator` 沿运行反方向回退 `trainLengthM` 得出，而不是由前端自行猜测：

```text
tail = TrackNavigator.move(head, distanceM = -trainLengthM, direction = train.direction)
spans = TrackNavigator.coveredSegments(tail, head, pathId)
```

注意：不能简单用 `tailPositionM = headPositionM - trainLengthM` 作为通用算法，因为 `UP/DOWN` 不一定总是对应累计里程增加/减少，且列车可能跨越多个 Seg、站场道岔或折返路径。可以在单一路径线性里程中用 `positionM` 做快速排序，但最终仍应映射回 `segmentId + offsetM`。

### 2.8.3 TrackRange：扰动、授权、进路和区段范围

涉及范围时统一使用 `TrackRange`：

```json
{
  "pathId": "LINE9-UP-MAIN",
  "direction": "UP",
  "start": {"segmentId": 13, "offsetM": 0.0, "positionM": 313.0},
  "end": {"segmentId": 24, "offsetM": 80.0, "positionM": 1280.0},
  "includeStart": true,
  "includeEnd": false
}
```

语义约束：
1. `start` 和 `end` 均为 `TrackPoint`，不得只写 `fromSegId/toSegId`。
2. `includeStart/includeEnd` 用于明确边界是否生效，默认建议 `[start, end)`。
3. 扰动、临时限速、移动授权和进路保护区段必须明确作用方向；双向生效时 `direction` 使用 `BOTH`。
4. 如果范围跨越道岔，必须提供 `pathId` 或 `routeId`，否则前端和安全模块无法判断经过哪条分支。

## 3. Phase 0/1 基础 API

## 3.1 健康检查

```http
GET /api/health
```

用途：检查 API 服务、线路缓存、仿真内核和数据库状态。

响应示例：

```json
{
  "ok": true,
  "service": "BJTUMetroSim API",
  "version": "0.1.0",
  "phase": "PHASE_1",
  "lineId": "9",
  "cacheExists": true,
  "validationOk": true,
  "simState": "LOADED",
  "database": {
    "connected": true,
    "path": "outputs/runs/current/run.sqlite"
  },
  "generatedAt": "2026-07-08T10:00:00Z"
}
```

## 3.2 9 号线宏观地图

```http
GET /api/lines/9/macro
```

用途：给宏观线路图提供 9 号线经纬度、车站、颜色和基础站点映射。

响应核心字段：

```json
{
  "id": "9",
  "name": "9号线",
  "color": "#8FC31F",
  "coordinates": [[[39.814322, 116.301889]]],
  "stations": [
    {
      "code": "GGZ",
      "name": "郭公庄",
      "lat": 39.814322,
      "lng": 116.301889,
      "mileageM": 313.0,
      "platformIds": [1, 2],
      "platformSegmentIds": [13, 39]
    }
  ],
  "source": "phase0-backend"
}
```

兼容说明：该接口当前已有实现，可保持现有字段名。

## 3.3 站点映射

```http
GET /api/lines/9/stations
```

用途：返回 9 号线中文站名、站码、里程、站台和 Seg 映射。

响应示例：

```json
{
  "lineId": "9",
  "stations": [
    {
      "stationId": 1,
      "stationCode": "GGZ",
      "stationBusinessId": "S-GGZ",
      "stationName": "郭公庄",
      "mileageM": 313.0,
      "lat": 39.814322,
      "lng": 116.301889,
      "platformIds": [1, 2],
      "platformSegmentIds": [13, 39]
    }
  ]
}
```

## 3.4 轨道级静态拓扑

```http
GET /api/lines/9/track-map
```

用途：返回微观轨道级视图所需的 Seg、信号机、站台、限速、坡度、区段、进路等静态对象。

响应核心字段：

```json
{
  "lineId": "9",
  "name": "9号线轨道级视图",
  "counts": {
    "segments": 319,
    "signals": 157,
    "platforms": 56,
    "routes": 249
  },
  "segments": [],
  "signals": [],
  "platforms": [],
  "speedRestrictions": [],
  "gradients": [],
  "routes": [],
  "logicalSections": [],
  "axleSections": []
}
```

实现要求：

1. Phase 0 可只返回 `segments`、`signals`、`platforms`、`speedRestrictions`、`gradients`。
2. Phase 2 必须补齐 `routes`、`logicalSections`、`axleSections`、`switches`、`powerSections`。

## 3.5 单个 Seg 上下文

```http
GET /api/track/segments/{segId}/context
```

用途：点击轨道 Seg 后查询周边信号、站台、限速、坡度和相邻 Seg。

响应示例：

```json
{
  "segment": {"id": 13, "lengthM": 120.0},
  "nextSegments": [14],
  "speedLimit": {"speedLimitMps": 16.67},
  "gradient": {"slopePermille": 0.0, "gradientPermille": 0.0},
  "nearestPlatform": {"platformId": 1, "stationName": "郭公庄"},
  "nextSignal": {"signalId": 8, "aspect": "GREEN"}
}
```

## 3.6 仿真状态快照

```http
GET /api/sim/state
```

查询参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `runId` | 否 | 不传时返回当前运行 |
| `simTimeMs` | 否 | 指定时间点，用于回放 |

响应示例：

```json
{
  "clock": {
    "state": "RUNNING",
    "simTimeMs": 120000,
    "tick": 1200,
    "speed": 1.0
  },
  "mode": "PURE_SIM",
  "trains": [
    {
      "trainId": "T0901",
      "lineId": "9",
      "position": {
        "referencePoint": "HEAD",
        "direction": "UP",
        "pathId": "LINE9-UP-MAIN",
        "trainLengthM": 118.0,
        "head": {
          "segmentId": 13,
          "offsetM": 30.0,
          "positionM": 343.0
        },
        "tail": {
          "segmentId": 12,
          "offsetM": 86.0,
          "positionM": 225.0
        },
        "spans": [
          {"segmentId": 12, "fromOffsetM": 86.0, "toOffsetM": 180.0},
          {"segmentId": 13, "fromOffsetM": 0.0, "toOffsetM": 30.0}
        ]
      },
      "speedMps": 8.2,
      "accelerationMps2": 0.4,
      "nextStation": "丰台科技园",
      "loadFactor": 0.42,
      "source": "SELF_SIM"
    }
  ],
  "signals": [],
  "sectionOccupancies": [],
  "power": [],
  "adapters": []
}
```

兼容说明：Phase 0 已实现的轻量接口可能仍返回顶层 `segmentId`、`offsetM`、`positionM`、`direction`。自 Phase 1 起，新增接口和 WebSocket 推送应使用 `position.head/tail/spans`。若为了兼容保留旧字段，旧字段只能视为 `position.head` 的别名，不得用于区段占用、进路释放或安全判断。

## 3.7 仿真生命周期控制

### 启动仿真

```http
POST /api/sim/start
```

请求体：

```json
{
  "scenarioId": "SCN-P1-AUTO-STOP",
  "mode": "PURE_SIM",
  "stepMs": 100,
  "realtime": true,
  "seed": 20260708
}
```

响应：

```json
{
  "runId": 1,
  "runUuid": "RUN-20260708-0001",
  "state": "RUNNING",
  "scenarioId": "SCN-P1-AUTO-STOP"
}
```

### 暂停、恢复、停止、单步

```http
POST /api/sim/pause
POST /api/sim/resume
POST /api/sim/stop
POST /api/sim/step
```

`/api/sim/step` 请求体：

```json
{
  "steps": 1
}
```

状态非法时返回：

```json
{
  "ok": false,
  "error": {
    "code": "INVALID_SIM_STATE",
    "message": "Cannot resume from STOPPED.",
    "detail": {"state": "STOPPED"}
  }
}
```

## 4. Phase 1 车辆、ATO 与安全 API

## 4.1 查询列车状态

```http
GET /api/trains
GET /api/trains/{trainId}
```

响应示例：

```json
{
  "trains": [
    {
      "trainId": "T0901",
      "position": {
        "referencePoint": "HEAD",
        "direction": "UP",
        "pathId": "LINE9-UP-MAIN",
        "trainLengthM": 118.0,
        "head": {
          "segmentId": 13,
          "offsetM": 30.0,
          "positionM": 343.0
        },
        "tail": {
          "segmentId": 12,
          "offsetM": 86.0,
          "positionM": 225.0
        },
        "center": {
          "segmentId": 12,
          "offsetM": 145.0,
          "positionM": 284.0
        },
        "spans": [
          {"segmentId": 12, "fromOffsetM": 86.0, "toOffsetM": 180.0},
          {"segmentId": 13, "fromOffsetM": 0.0, "toOffsetM": 30.0}
        ]
      },
      "kinematics": {
        "speedMps": 8.2,
        "accelerationMps2": 0.4
      },
      "control": {
        "mode": "ATO",
        "tractionLevel": 2,
        "brakeLevel": 0,
        "emergencyBrake": false
      },
      "load": {
        "onboardPax": 520,
        "loadFactor": 0.36
      }
    }
  ]
}
```

位置字段说明：
1. `head` 是列车运行方向上的前端点，等价于旧版 `segmentId/offsetM/positionM` 的语义。
2. `tail` 是后端点，由 `trainLengthM` 和运行方向沿 Seg 拓扑反算。
3. `center` 仅用于宏观地图图标或 UI 居中展示，不参与安全判断。
4. `spans` 是列车实际覆盖的 Seg 范围，区段占用、计轴区段、进路释放和碰撞风险必须基于 `spans` 或头尾范围计算。

## 4.2 下发司机台/ATO 控制命令

```http
POST /api/trains/{trainId}/commands
```

请求体：

```json
{
  "operatorSource": "DRIVER",
  "mode": "MANUAL",
  "tractionLevel": 2,
  "brakeLevel": 0,
  "emergencyBrake": false,
  "reason": "manual test"
}
```

约束：

1. 若 ATP 或联锁要求紧急制动，人工命令不得覆盖安全制动。
2. 若供电限牵生效，牵引命令必须被 `PowerService` 限幅。
3. 所有命令必须写入 `train_commands`。

## 4.3 查询 MA 与 ATP 状态

```http
GET /api/trains/{trainId}/authority
```

响应示例：

```json
{
  "trainId": "T0901",
  "simTimeMs": 120000,
  "movementAuthority": {
    "appliesToReferencePoint": "HEAD",
    "end": {
      "segmentId": 28,
      "offsetM": 42.0,
      "positionM": 980.0
    },
    "protectionTailClearRequired": true
  },
  "permittedSpeedMps": 16.67,
  "targetSpeedMps": 0.0,
  "targetDistanceM": 430.0,
  "emergencyBrakeRequired": false,
  "reason": "ROUTE_LOCKED",
  "source": "SELF_SIM"
}
```

兼容说明：若保留旧字段 `maEndM`，它只能表示 `movementAuthority.end.positionM`，且约束对象为列车车头参考点。进路释放、保护区段释放和占用清出仍必须使用列车车尾位置判断。`targetDistanceM` 表示从当前车头到目标点的距离；若 UI 需要显示整列车距目标的安全余量，应额外计算 `tailClearanceM` 或 `headToTargetM`，不得混用。

## 4.4 查询安全事件

```http
GET /api/safety/events?runId=1&fromMs=0&toMs=300000
```

响应示例：

```json
{
  "events": [
    {
      "simTimeMs": 180000,
      "trainId": "T0901",
      "eventType": "OVERSPEED",
      "severity": "WARN",
      "module": "ATP",
      "actionTaken": "BRAKE_COMMAND",
      "detail": {}
    }
  ]
}
```

## 5. Phase 2 联锁 API

## 5.1 查询进路

```http
GET /api/interlocking/routes
GET /api/interlocking/routes/{routeId}
```

查询参数：

| 参数 | 说明 |
|---|---|
| `state` | 按进路状态过滤 |
| `trainId` | 查询某列车关联进路 |
| `includeStatic` | 是否返回静态区段、道岔、敌对进路配置 |

响应示例：

```json
{
  "routes": [
    {
      "routeId": "R-GGZ-FSP-UP",
      "state": "LOCKED",
      "trainId": "T0901",
      "startSignalId": 901,
      "endSignalId": 910,
      "direction": "UP",
      "lockedSwitches": [
        {"switchId": 903, "position": "NORMAL"}
      ],
      "logicalSectionIds": ["L-0901"],
      "axleSectionIds": ["A-0901"],
      "protectionSectionIds": ["P-0901"],
      "failureReason": null,
      "source": "SELF_SIM"
    }
  ]
}
```

## 5.2 请求办理进路

```http
POST /api/interlocking/routes/{routeId}/request
```

请求体：

```json
{
  "trainId": "T0901",
  "operatorSource": "DISPATCHER",
  "force": false,
  "reason": "scheduled departure"
}
```

成功响应：

```json
{
  "accepted": true,
  "routeId": "R-GGZ-FSP-UP",
  "state": "LOCKED",
  "simTimeMs": 120000
}
```

失败响应：

```json
{
  "ok": false,
  "error": {
    "code": "SECTION_OCCUPIED",
    "message": "Route cannot be locked because section A-0901 is occupied.",
    "detail": {
      "routeId": "R-GGZ-FSP-UP",
      "sectionId": "A-0901",
      "trainIds": ["T0902"]
    }
  }
}
```

## 5.3 请求释放进路

```http
POST /api/interlocking/routes/{routeId}/release
```

请求体：

```json
{
  "operatorSource": "DISPATCHER",
  "releaseType": "CANCEL",
  "reason": "manual cancellation"
}
```

`releaseType` 取值：

| 值 | 含义 |
|---|---|
| `AUTO` | 自动释放 |
| `CANCEL` | 取消进路 |
| `APPROACH_RELEASE` | 接近锁闭延时释放 |
| `EMERGENCY` | 故障/人工强制释放 |

释放判据：
1. `AUTO` 自动释放必须以列车车尾 `position.tail` 清出进路最后一个释放区段为准，不能只看车头越过终点信号。
2. `CANCEL` 取消进路前必须确认无列车接近或占用该进路的接近区段；若列车已接近，应转为 `APPROACH_RELEASE`。
3. `APPROACH_RELEASE` 的延时起点应记录为 `signalClosedAtMs`，延时到期后仍需再次检查相关区段占用。
4. 释放响应建议返回 `releaseBoundary` 和 `tailCleared`，用于前端解释为什么进路仍未解锁。

`AUTO` 释放响应示例：

```json
{
  "accepted": true,
  "routeId": "R-GGZ-FSP-UP",
  "state": "RELEASING",
  "releaseType": "AUTO",
  "releaseBoundary": {
    "sectionId": "A-0904",
    "boundary": {
      "segmentId": 24,
      "offsetM": 80.0,
      "positionM": 1280.0
    }
  },
  "tailCleared": false,
  "blockingTrainId": "T0901",
  "blockingTrainTail": {
    "segmentId": 24,
    "offsetM": 62.0,
    "positionM": 1262.0
  }
}
```

## 5.4 查询道岔状态

```http
GET /api/interlocking/switches
GET /api/interlocking/switches/{switchId}
```

响应示例：

```json
{
  "switches": [
    {
      "switchId": "W0903",
      "requestedPosition": "NORMAL",
      "actualPosition": "NORMAL",
      "lockedByRouteId": "R-GGZ-FSP-UP",
      "health": "OK",
      "source": "SELF_SIM"
    }
  ]
}
```

## 5.5 查询区段占用

```http
GET /api/sections/occupation
```

查询参数：

| 参数 | 说明 |
|---|---|
| `sectionType` | `PHYSICAL_SEGMENT`、`LOGICAL`、`AXLE` |
| `occupied` | `true/false` |

响应示例：

```json
{
  "sections": [
    {
      "sectionId": "A-0901",
      "sectionType": "AXLE",
      "occupied": true,
      "trainIds": ["T0901"],
      "occupiedBy": [
        {
          "trainId": "T0901",
          "head": {"segmentId": 13, "offsetM": 30.0, "positionM": 343.0},
          "tail": {"segmentId": 12, "offsetM": 86.0, "positionM": 225.0},
          "overlap": [
            {"segmentId": 12, "fromOffsetM": 86.0, "toOffsetM": 180.0},
            {"segmentId": 13, "fromOffsetM": 0.0, "toOffsetM": 30.0}
          ]
        }
      ],
      "stale": false,
      "source": "SELF_SIM",
      "quality": "ESTIMATED",
      "derivationMethod": "FROM_TRAIN_POSITION"
    }
  ],
  "headways": [
    {
      "frontTrainId": "T0901",
      "rearTrainId": "T0902",
      "clearanceM": 850.0,
      "headToHeadDistanceM": 968.0,
      "timeHeadwaySec": 96.5,
      "riskLevel": "NORMAL"
    }
  ]
}
```

占用与间隔语义：
1. `occupiedBy[].head/tail/overlap` 必须来自列车头尾位置，不得只按车头所在 Seg 判断。
2. `overlap` 表示该列车与当前区段的实际重叠范围，可用于微观轨道级高亮。
3. `clearanceM` 表示后车车头到前车车尾之间的净空距离，是安全追踪和碰撞风险判断的主字段。
4. `headToHeadDistanceM` 仅用于运行图/调度统计，不得替代 `clearanceM` 做安全判断。
5. 若平台信号给出区段占用，API 可同时返回 `platformOccupied` 与 `selfOccupied`；当两者不一致时 `stale` 或 `quality` 必须提示前端。

## 6. Phase 2 车站、客流与列车负载 API

## 6.1 查询站台客流

```http
GET /api/stations/crowd
GET /api/stations/{stationBusinessId}/crowd
```

响应示例：

```json
{
  "stations": [
    {
      "stationBusinessId": "S-GGZ",
      "stationCode": "GGZ",
      "stationName": "郭公庄",
      "direction": "UP",
      "waitingPax": 286,
      "platformDensityPaxPerM2": 2.4,
      "leftBehindPax": 36,
      "crowdingLevel": "HIGH",
      "source": "SELF_SIM"
    }
  ]
}
```

## 6.2 设置客流场景

```http
POST /api/passenger/profiles
```

请求体：

```json
{
  "profiles": [
    {
      "stationBusinessId": "S-GGZ",
      "stationCode": "GGZ",
      "direction": "UP",
      "timeSliceSec": [0, 1800],
      "arrivalRatePaxPerMin": 38.5,
      "odDistribution": [
        {"destStationBusinessId": "S-FSP", "destStationCode": "FSP", "ratio": 0.35}
      ]
    }
  ],
  "replaceExisting": true
}
```

响应：

```json
{
  "accepted": true,
  "profileCount": 1
}
```

## 6.3 查询列车负载

```http
GET /api/trains/load
GET /api/trains/{trainId}/load
```

响应示例：

```json
{
  "loads": [
    {
      "trainId": "T0901",
      "onboardPax": 842,
      "capacityPax": 1460,
      "loadFactor": 0.58,
      "vehicleLoadKg": 54730,
      "source": "SELF_SIM"
    }
  ]
}
```

## 6.4 查询停站记录

```http
GET /api/stations/dwell-records?runId=1&trainId=T0901
```

响应示例：

```json
{
  "records": [
    {
      "trainId": "T0901",
      "stationBusinessId": "S-GGZ",
      "stationCode": "GGZ",
      "arrivalMs": 120000,
      "departMs": 168000,
      "plannedDwellSec": 35,
      "actualDwellSec": 48,
      "reason": "PASSENGER_BOARDING"
    }
  ]
}
```

## 7. Phase 2 调度 API

## 7.1 查询调度状态

```http
GET /api/dispatch/state
```

响应示例：

```json
{
  "simTimeMs": 120000,
  "mode": "RULE_BASED",
  "headways": [
    {
      "frontTrainId": "T0901",
      "rearTrainId": "T0902",
      "clearanceM": 850.0,
      "headToHeadDistanceM": 968.0,
      "timeHeadwaySec": 96.5,
      "basis": "REAR_HEAD_TO_FRONT_TAIL",
      "riskLevel": "NORMAL"
    }
  ],
  "activeDecisions": [
    {
      "decisionId": "DD-0001",
      "trainId": "T0902",
      "stationBusinessId": "S-GGZ",
      "stationCode": "GGZ",
      "action": "HOLD",
      "durationSec": 18,
      "reason": "HEADWAY_TOO_SHORT",
      "applied": true
    }
  ]
}
```

调度间隔字段说明：
1. `clearanceM` 是后车车头到前车车尾的净空距离，用于安全风险和追踪间隔判断。
2. `headToHeadDistanceM` 是两车车头累计位置差，主要用于运行图统计和可视化。
3. `basis` 必须说明间隔计算基准，默认 `REAR_HEAD_TO_FRONT_TAIL`。
4. 任何 `HOLD`、`RELEASE`、`STAGGER_DEPARTURE` 决策如果引用间隔，应在 `expectedImpact` 或 `detail` 中写明使用的是 `clearanceM` 还是 `timeHeadwaySec`。

## 7.2 下发调度命令

```http
POST /api/dispatch/decisions
```

请求体：

```json
{
  "trainId": "T0902",
  "stationBusinessId": "S-GGZ",
  "stationCode": "GGZ",
  "action": "HOLD",
  "durationSec": 18,
  "reason": "manual dispatch adjustment",
  "operatorSource": "DISPATCHER"
}
```

支持动作：

| 动作 | 说明 |
|---|---|
| `HOLD` | 扣车 |
| `RELEASE` | 放行 |
| `DWELL_EXTEND` | 延长停站 |
| `SPEED_LEVEL_ADJUST` | 调整运行等级 |
| `STAGGER_DEPARTURE` | 错峰发车 |
| `ADD_TRAIN_REQUEST` | 加车建议 |
| `SKIP_STOP_CANDIDATE` | 跳停候选 |

## 8. Phase 2 自研供电 API

## 8.1 查询供电状态

```http
GET /api/power/state
GET /api/power/sections/{powerSectionId}
```

响应示例：

```json
{
  "sections": [
    {
      "powerSectionId": "PWR-0901",
      "name": "郭公庄-六里桥",
      "requestedPowerKw": 9300,
      "availablePowerKw": 8500,
      "tractionLimitRatio": 0.91,
      "voltageLevel": "LIMITED",
      "energyKwh": 126.4,
      "regenEnergyKwh": 18.7,
      "absorbedRegenKw": 320.0,
      "wastedRegenKw": 45.0,
      "source": "SELF_SIM",
      "quality": "ESTIMATED"
    }
  ]
}
```

硬约束：

1. 当前不得返回 `source: "PLATFORM"`。
2. 若供电状态由扰动强制设置，返回 `source: "SCENARIO_FORCED"`。
3. 若未来新增供电协议，必须先更新协议能力边界核查和数据库设计。

## 8.2 更新供电配置

```http
POST /api/power/sections/{powerSectionId}/config
```

请求体：

```json
{
  "maxTractionPowerKw": 8500,
  "warningPowerKw": 7200,
  "regenAbsorbLimitKw": 2500,
  "assumption": "Derived from default Phase 2 scenario."
}
```

用途：用于实验参数调整。正式运行中必须记录到场景或实验参数，保证可复现。

## 9. Phase 2 扰动 API

## 9.1 查询扰动

```http
GET /api/disturbances
GET /api/disturbances/{disturbanceId}
```

响应示例：

```json
{
  "disturbances": [
    {
      "disturbanceId": "D001",
      "type": "TEMP_SPEED_RESTRICTION",
      "state": "ACTIVE",
      "startMs": 300000,
      "endMs": 900000,
      "scope": {
        "scopeType": "TRACK_RANGE",
        "trackRange": {
          "pathId": "LINE9-UP-MAIN",
          "direction": "UP",
          "start": {"segmentId": 13, "offsetM": 0.0, "positionM": 313.0},
          "end": {"segmentId": 24, "offsetM": 80.0, "positionM": 1280.0},
          "includeStart": true,
          "includeEnd": false
        }
      },
      "severity": 0.8
    }
  ]
}
```

## 9.2 注入扰动

```http
POST /api/disturbances
```

请求体：

```json
{
  "disturbanceId": "D001",
  "type": "TEMP_SPEED_RESTRICTION",
  "trigger": {"type": "TIME", "simTimeMs": 300000},
  "durationMs": 600000,
  "scope": {
    "scopeType": "TRACK_RANGE",
    "trackRange": {
      "pathId": "LINE9-UP-MAIN",
      "direction": "UP",
      "start": {"segmentId": 13, "offsetM": 0.0, "positionM": 313.0},
      "end": {"segmentId": 24, "offsetM": 80.0, "positionM": 1280.0},
      "includeStart": true,
      "includeEnd": false
    }
  },
  "severity": 0.8,
  "parameters": {
    "speedLimitMps": 8.33
  }
}
```

扰动范围约束：
1. 临时限速、供电欠压、区间封锁、信号故障等作用于轨道范围的扰动必须使用 `scopeType: "TRACK_RANGE"`。
2. 车门故障、司机台故障、列车通信丢失等作用于列车的扰动使用 `scopeType: "TRAIN"`，并提供 `trainId`。
3. 站台大客流、屏蔽门故障等作用于车站/站台的扰动使用 `scopeType: "STATION"` 或 `scopeType: "PLATFORM"`，并提供 `stationBusinessId`、`stationCode`、`platformId`、`direction`。
4. 旧版 `fromSegId/toSegId` 只能作为兼容输入，后端必须在入库前转换为完整 `TrackRange`，并明确边界偏移和方向。

支持扰动类型：

```text
TEMP_SPEED_RESTRICTION
DOOR_FAULT
PSD_FAULT
PASSENGER_SURGE
SIGNAL_FAULT
SWITCH_FAULT
POWER_UNDERVOLTAGE
POWER_OUTAGE
COMMUNICATION_LOSS
```

## 9.3 清除扰动

```http
POST /api/disturbances/{disturbanceId}/clear
```

请求体：

```json
{
  "reason": "manual recovery"
}
```

## 10. 平台接口适配器 API

## 10.1 查询接口健康

```http
GET /api/adapters/health
GET /api/adapters/{adapterId}/health
```

响应示例：

```json
{
  "adapters": [
    {
      "adapterId": "signal_udp",
      "name": "SignalUdpAdapter",
      "status": "ONLINE",
      "mode": "PLATFORM",
      "cycleMs": 100,
      "lastFrameAtMs": 120000,
      "rxCount": 1200,
      "txCount": 1188,
      "dropCount": 2,
      "crcErrorCount": 0,
      "parseErrorCount": 0,
      "missedFrameCount": 0,
      "lastError": null
    },
    {
      "adapterId": "rtlab_api",
      "name": "RtLabApiAdapter",
      "status": "MOCK",
      "mode": "MOCK",
      "note": "Vehicle dynamics only; not a power adapter."
    }
  ]
}
```

## 10.2 连接/断开适配器

```http
POST /api/adapters/{adapterId}/connect
POST /api/adapters/{adapterId}/disconnect
```

请求体：

```json
{
  "mode": "MOCK",
  "config": {
    "host": "127.0.0.1",
    "port": 8302
  }
}
```

约束：真实平台连接参数必须来自配置文件或运行参数，不应写死在前端。

## 10.3 查询外部帧摘要

```http
GET /api/adapters/{adapterId}/frames?runId=1&limit=100
```

响应示例：

```json
{
  "frames": [
    {
      "simTimeMs": 120000,
      "adapterId": "signal_udp",
      "direction": "RX",
      "protocol": "UDP",
      "frameType": "SIGNAL_STATE",
      "rawLen": 128,
      "rawHash": "sha256:...",
      "parseOk": true,
      "mappedTopics": ["state.signal", "state.switch"]
    }
  ]
}
```

## 11. 运行记录与回放 API

## 11.1 查询运行批次

```http
GET /api/runs
GET /api/runs/{runId}
```

响应示例：

```json
{
  "runs": [
    {
      "runId": 1,
      "runUuid": "RUN-20260708-0001",
      "name": "phase1-auto-stop",
      "scenarioId": "SCN-P1-AUTO-STOP",
      "mode": "PURE_SIM",
      "startedAt": "2026-07-08T10:00:00Z",
      "endedAt": "2026-07-08T10:03:00Z",
      "status": "COMPLETED"
    }
  ]
}
```

## 11.2 查询指标

```http
GET /api/runs/{runId}/metrics
```

响应示例：

```json
{
  "runId": 1,
  "metrics": [
    {"name": "stopErrorM", "value": 0.32, "unit": "m", "pass": true},
    {"name": "energyKwh", "value": 126.4, "unit": "kWh", "pass": true}
  ]
}
```

## 11.3 查询回放帧

```http
GET /api/runs/{runId}/replay?fromMs=0&toMs=300000&stepMs=1000
```

响应示例：

```json
{
  "runId": 1,
  "frames": [
    {
      "simTimeMs": 0,
      "trains": [
        {
          "trainId": "T0901",
          "position": {
            "referencePoint": "HEAD",
            "direction": "UP",
            "trainLengthM": 118.0,
            "head": {"segmentId": 13, "offsetM": 30.0, "positionM": 343.0},
            "tail": {"segmentId": 12, "offsetM": 86.0, "positionM": 225.0},
            "spans": [
              {"segmentId": 12, "fromOffsetM": 86.0, "toOffsetM": 180.0},
              {"segmentId": 13, "fromOffsetM": 0.0, "toOffsetM": 30.0}
            ]
          },
          "speedMps": 8.2
        }
      ],
      "routes": [],
      "sections": [
        {
          "sectionId": "A-0901",
          "occupied": true,
          "trainIds": ["T0901"]
        }
      ],
      "stations": [],
      "power": [],
      "events": []
    }
  ]
}
```

回放约束：`frames[].trains[].position`、`frames[].sections`、`frames[].routes` 必须与实时 API 使用相同 schema。不得为了压缩回放数据而只保留车头点；如需压缩，应在服务端提供 `detailLevel=compact/full` 参数，并在 `compact` 模式中明确说明省略了 `tail/spans`，前端不得用 compact 帧做安全占用判断。

## 11.4 导出运行数据

```http
GET /api/runs/{runId}/export?format=zip
```

支持格式：

| format | 内容 |
|---|---|
| `zip` | SQLite、CSV、summary JSON |
| `csv` | 主要表 CSV |
| `json` | summary 和指标 JSON |

## 12. Phase 3 优化实验 API

## 12.1 查询实验

```http
GET /api/experiments
GET /api/experiments/{experimentId}
```

响应示例：

```json
{
  "experiments": [
    {
      "experimentId": "EXP-ATO-ENERGY-001",
      "name": "ATO energy saving",
      "problemType": "ATO_ENERGY",
      "trialCount": 120,
      "bestTrialId": "TRIAL-0088"
    }
  ]
}
```

## 12.2 创建实验

```http
POST /api/experiments
```

请求体：

```json
{
  "experimentId": "EXP-ATO-ENERGY-001",
  "name": "ATO energy saving",
  "problemType": "ATO_ENERGY",
  "scenarioId": "SCN-P1-AUTO-STOP",
  "algorithm": "GA",
  "objectives": [
    {"name": "stopErrorM", "direction": "MINIMIZE"},
    {"name": "energyKwh", "direction": "MINIMIZE"},
    {"name": "maxJerkMps3", "direction": "MINIMIZE"}
  ],
  "constraints": [
    {"name": "safetyViolationCount", "operator": "=", "value": 0}
  ]
}
```

## 12.3 查询实验结果摘要

```http
GET /api/experiments/{experimentId}/summary
```

响应示例：

```json
{
  "experimentId": "EXP-ATO-ENERGY-001",
  "baseline": {"trialId": "BASELINE", "energyKwh": 132.0},
  "best": {"trialId": "TRIAL-0088", "energyKwh": 118.5},
  "paretoPoints": [],
  "notes": [
    "Power metrics are computed by self-developed PowerService."
  ]
}
```

## 12.4 查询 trial

```http
GET /api/experiments/{experimentId}/trials
GET /api/experiments/{experimentId}/trials/{trialId}
```

## 13. WebSocket 设计

## 13.1 仿真主推送

```text
WS /api/sim/stream
```

订阅请求：

```json
{
  "action": "subscribe",
  "topics": [
    "state.train",
    "state.interlocking",
    "state.occupation",
    "state.station",
    "state.dispatch",
    "state.power",
    "state.disturbance",
    "event.safety"
  ],
  "minIntervalMs": 200
}
```

推送消息：

```json
{
  "topic": "state.train",
  "simTimeMs": 120000,
  "sequence": 1024,
  "source": "SELF_SIM",
  "payload": {
    "trains": [
      {
        "trainId": "T0901",
        "position": {
          "referencePoint": "HEAD",
          "direction": "UP",
          "trainLengthM": 118.0,
          "head": {"segmentId": 13, "offsetM": 30.0, "positionM": 343.0},
          "tail": {"segmentId": 12, "offsetM": 86.0, "positionM": 225.0},
          "spans": [
            {"segmentId": 12, "fromOffsetM": 86.0, "toOffsetM": 180.0},
            {"segmentId": 13, "fromOffsetM": 0.0, "toOffsetM": 30.0}
          ]
        },
        "speedMps": 8.2
      }
    ]
  }
}
```

主题清单：

| 主题 | 内容 |
|---|---|
| `state.clock` | 仿真时钟 |
| `state.train` | 列车位置、速度、负载 |
| `state.signal` | 信号显示 |
| `state.interlocking` | 进路、道岔、联锁状态 |
| `state.occupation` | 区段占用和追踪间隔 |
| `state.station` | 站台客流和停站 |
| `state.dispatch` | 调度决策 |
| `state.power` | 自研供电状态 |
| `state.disturbance` | 扰动状态 |
| `event.safety` | 安全事件 |
| `event.system` | 系统事件 |

## 13.2 接口健康推送

```text
WS /api/adapters/stream
```

推送消息：

```json
{
  "topic": "state.adapter",
  "simTimeMs": 120000,
  "payload": {
    "adapters": []
  }
}
```

## 13.3 WebSocket 约束

1. 高频消息必须限流，前端默认 200 ms 刷新即可。
2. 安全事件、扰动触发、接口掉线不应被限流丢弃。
3. 每条消息必须包含 `topic`、`simTimeMs`、`sequence`。
4. 前端断线重连后，应先调用 `GET /api/sim/state` 获取快照，再恢复订阅。
5. `state.train`、`state.occupation`、`state.interlocking` 推送必须复用 HTTP API 中的 `TrainPosition`、`TrackRange`、区段占用和进路释放 schema，不能另起一套简化字段。

## 14. 错误码

| 错误码 | HTTP 状态 | 说明 |
|---|---:|---|
| `NOT_FOUND` | 404 | 资源不存在 |
| `VALIDATION_ERROR` | 400 | 请求体或参数不合法 |
| `INVALID_SIM_STATE` | 409 | 仿真状态不允许当前操作 |
| `ROUTE_NOT_FOUND` | 404 | 进路不存在 |
| `ROUTE_NOT_LOCKABLE` | 409 | 进路因占用、敌对、道岔不可用或保护区段不满足等综合原因不可办理 |
| `SECTION_OCCUPIED` | 409 | 区段占用导致进路办理失败 |
| `CONFLICT_ROUTE_LOCKED` | 409 | 敌对进路已锁闭 |
| `SWITCH_UNAVAILABLE` | 409 | 道岔不可用 |
| `POWER_UNAVAILABLE` | 409 | 自研供电约束不允许执行 |
| `SAFETY_CONSTRAINT_VIOLATION` | 409 | 动作违反安全约束 |
| `ADAPTER_OFFLINE` | 503 | 平台适配器离线 |
| `ADAPTER_PROTOCOL_ERROR` | 502 | 平台协议解析错误 |
| `RECORDER_ERROR` | 500 | 记录写入失败 |

## 15. 实现优先级

## 15.1 Phase 0 必须实现

| API | 状态 |
|---|---|
| `GET /api/health` | 已有基础实现 |
| `GET /api/lines/9/macro` | 已有基础实现 |
| `GET /api/lines/9/stations` | 已有基础实现 |
| `GET /api/lines/9/track-map` | 已有基础实现 |
| `GET /api/track/segments/{segId}/context` | 已有基础实现 |
| `GET /api/sim/state` | 已有 Mock 状态 |

## 15.2 Phase 1 必须实现

```text
POST /api/sim/start
POST /api/sim/pause
POST /api/sim/resume
POST /api/sim/stop
POST /api/sim/step
GET  /api/trains
GET  /api/trains/{trainId}
GET  /api/trains/{trainId}/authority
GET  /api/safety/events
GET  /api/runs
GET  /api/runs/{runId}
GET  /api/runs/{runId}/metrics
WS   /api/sim/stream
```

## 15.3 Phase 2 必须实现

```text
GET  /api/interlocking/routes
POST /api/interlocking/routes/{routeId}/request
POST /api/interlocking/routes/{routeId}/release
GET  /api/interlocking/switches
GET  /api/sections/occupation
GET  /api/stations/crowd
GET  /api/trains/load
GET  /api/dispatch/state
POST /api/dispatch/decisions
GET  /api/power/state
GET  /api/disturbances
POST /api/disturbances
GET  /api/adapters/health
GET  /api/runs/{runId}/replay
GET  /api/runs/{runId}/export
WS   /api/adapters/stream
```

## 15.4 Phase 3 实现

```text
GET  /api/experiments
POST /api/experiments
GET  /api/experiments/{experimentId}/summary
GET  /api/experiments/{experimentId}/trials
```

## 16. API 与数据库映射

| API | 数据来源 |
|---|---|
| `/api/lines/9/track-map` | Phase 0 权威源为 `data/cache/line_map.json`；Phase 1/2 导入验收后可切换为线路静态库 |
| `/api/sim/state` | 内存状态；回放时查运行库 |
| `/api/trains` | `train_telemetry` 最新帧 |
| `/api/trains/{trainId}/authority` | `movement_authorities` |
| `/api/interlocking/routes` | `route_state_records` + 静态 `routes` |
| `/api/sections/occupation` | `section_occupation_records` |
| `/api/stations/crowd` | `station_passenger_records` |
| `/api/trains/load` | `train_load_records` |
| `/api/dispatch/state` | `dispatch_decisions` |
| `/api/power/state` | `power_records` |
| `/api/disturbances` | `disturbance_records` |
| `/api/adapters/health` | `adapter_health_records` |
| `/api/runs` | `runs` |
| `/api/runs/{runId}/metrics` | `metrics` |
| `/api/runs/{runId}/replay` | 运行库 typed records；Phase 2 起提供 |
| `/api/runs/{runId}/export` | 运行库 + 静态线路快照；Phase 2 起提供 |
| `/api/experiments/{experimentId}/summary` | `experiments`、`objective_values`、`pareto_points` |

## 17. 验收标准

| 编号 | 验收项 | 通过标准 |
|---|---|---|
| API-001 | 健康检查 | 能返回服务、线路缓存和仿真状态 |
| API-002 | 静态线路 | 前端可通过 API 显示 9 号线和轨道级拓扑 |
| API-003 | 仿真控制 | start/pause/resume/stop/step 状态转换正确 |
| API-004 | 状态快照 | `GET /api/sim/state` 字段稳定，可被前端消费 |
| API-005 | WebSocket | 前端能订阅列车、联锁、客流、供电、接口状态 |
| API-006 | 联锁接口 | 进路办理失败能返回明确错误码和原因 |
| API-007 | 客流调度 | 客流、负载、停站和调度状态可查询 |
| API-008 | 供电接口 | 返回自研 `PowerService` 状态，且不误标为平台供电 |
| API-009 | 扰动接口 | 可注入、查询、清除扰动 |
| API-010 | 回放接口 | 可按 `runId` 和时间范围查询历史帧 |
| API-011 | 实验接口 | 可查询优化实验摘要和 trial 指标 |

## 18. 后续实现建议

当前 `app/api_server.py` 使用 Python 标准库 `http.server`，适合 Phase 0 静态数据接入。Phase 1 之后建议迁移到 FastAPI：

1. 使用 Pydantic 定义请求/响应模型。
2. 使用 `APIRouter` 按 `lines`、`sim`、`trains`、`interlocking`、`power`、`adapters` 分组。
3. 使用 FastAPI WebSocket 支持 `state.*` 推送。
4. 保留现有 URL，避免前端返工。
5. API 响应模型先兼容现有 Phase 0 字段，再逐步切换到统一包裹格式。
## 供电网络接口补充

本节对应9号线牵引供电准静态仿真V1，数据来源为自研工程近似模型：

```text
source = SELF_SIM
quality = ENGINEERING_ESTIMATE
```

### GET /api/lines/9/power-topology

返回9号线DC750V接触轨牵引供电V1显式静态拓扑。正式配置启用严格校验，不允许在缺少馈线、接触轨、回流轨或开关时静默生成设备。

响应字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| lineId | string | 线路 ID |
| nominalVoltageV | number | 标称直流电压，V0 为 750V |
| quality | string | 数据质量 |
| modelVersion | string | 拓扑和参数模型版本，当前为 `LINE9-DC750-V1.0` |
| provenance | object | 来源目录、参数文档和已知局限 |
| substations | array | 牵引变电所列表 |
| feeders | array | 馈电臂列表 |
| contactRailSections | array | 接触轨分段 |
| returnRailSections | array | 回流轨分段 |
| switches | array | 联络/隔离/断路器开关 |

### GET /api/sim/power/state

返回当前仿真 tick 的牵引供电潮流状态。该接口与 `/api/sim/state.powerNetwork` 内容一致，便于前端或实验脚本单独轮询供电状态。

响应字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| simTimeMs | integer | 仿真绝对时刻 |
| substations | array | 各牵引所电压、有符号净电流/功率、整流功率、回馈功率和负载率 |
| feeders | array | 馈电臂有符号电流、功率和负载率；正值表示从所向线路供电，负值表示向所回送 |
| trainVoltages | array | 每列车受电电压、电流、限功率系数、等效受电里程、车头/车尾、全部受流点及跨越供电分区 |
| regen | object | 再生生成、邻车吸收、具体设备回馈、路径损耗、浪费和逐路径明细 |
| lossesKw | number | 线路损耗 |
| alerts | array | 欠压、过载、过压、再生浪费等告警 |

`regen.paths[]`字段如下：

| 字段 | 类型 | 说明 |
|---|---|---|
| sourceTrainId | string | 再生源列车 |
| sinkType | string | `TRAIN`、`SUBSTATION_FEEDBACK`或`WASTE` |
| sinkId | string | 受能列车、能馈牵引所或制动电阻标识 |
| viaSubstationId | string/null | 路径共同经过或回馈到的牵引所 |
| sourceFeederId/sinkFeederId | string/null | 再生源和受端馈线 |
| generatedKw | number | 源列车沿该路径提供的功率 |
| deliveredKw | number | 到达受端的功率 |
| lossesKw | number | 路径电阻损耗 |
| currentA | number | 该分配路径电流绝对值 |
| pathResistanceOhm | number | 正、回流及馈线的路径等效电阻 |

每个tick必须满足：`generatedKw = absorbedKw + feedbackKw + transferLossesKw + wastedKw`；每条非浪费路径必须满足：`generatedKw = deliveredKw + lossesKw`。

`trainVoltages[]`中的位置字段：

| 字段 | 说明 |
|---|---|
| mileageM | 全部有效受流点的等效中心里程 |
| headMileageM/tailMileageM | 按运行方向定义的车头、车尾线路里程 |
| pantographMileagesM | 车载受流点线路里程数组；V1默认两个等效受流点 |
| spannedPowerSectionIds | 118 m列车车体当前跨越的全部供电区段 |

供电路径由受流点而不是车头点求解。同一馈线上的多个受流点只保留最短有效路径；不同馈线上的受流点可以在跨分段时共同受流。

### POST /api/sim/power/faults

注入供电故障。当前 V0 支持单座牵引所退出，并按大双边越区供电逻辑闭合相关联络开关。

请求示例：

```json
{
  "faultType": "SUBSTATION_OUTAGE",
  "targetId": "TS-0905",
  "mode": "N_MINUS_1_BIG_BILATERAL"
}
```

响应示例：

```json
{
  "ok": true,
  "data": {
    "faultId": "PF-TS-0905",
    "affectedSubstationId": "TS-0905",
    "supplyMode": "BIG_BILATERAL",
    "openedSwitches": ["FD-0905-UP-LEFT"],
    "closedSwitches": ["SW-TIE-0905"]
  }
}
```

### POST /api/sim/power/switches/{switchId}/operate

远程操作供电网络开关。当前 V0 用于联络开关分合闸实验、牵引所 N-1 越区供电恢复过程展示。

请求示例：
```json
{
  "state": "CLOSED"
}
```

响应示例：
```json
{
  "ok": true,
  "data": {
    "switchId": "SW-TIE-0905",
    "switchType": "TIE",
    "mileageM": 6339.9,
    "fromNodeId": "TS-0905",
    "toNodeId": "TS-0904",
    "normalState": "OPEN",
    "currentState": "CLOSED",
    "remoteControllable": true
  }
}
```

### /api/sim/state 扩展字段

`GET /api/sim/state` 新增：

| 字段 | 类型 | 说明 |
|---|---|---|
| power | array | 兼容旧前端的上/下行聚合供电状态 |
| powerNetwork | object | 牵引供电网络潮流快照 |
| dispatchDecisions | array | 当前 tick 调度决策 |
| kpi.minTrainVoltageV | number | 当前最低列车受电电压 |
| kpi.totalAbsorbedRegenKw | number | 当前再生吸收功率 |
| kpi.totalWastedRegenKw | number | 当前再生浪费功率 |
| kpi.powerLossesKw | number | 当前线路损耗 |

## 供电命令与优化实验API（V2补充）

### 可回放供电命令

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/sim/power/commands` | 排队执行统一供电命令 |
| GET | `/api/sim/power/commands` | 查询最近命令及待执行命令 |
| GET | `/api/sim/power/commands/{commandId}` | 查询单条命令 |
| POST | `/api/sim/power/commands/replay` | 按相对仿真时刻重放导出命令 |

`commandType` 支持：`SUBSTATION_OUTAGE`、`SUBSTATION_RESTORE`、`OPERATE_SWITCH`、`SET_FEEDER_STATUS`、`SET_CONTACT_SECTION_STATUS`、`RESET_NETWORK`。命令可带 `applyAtSimTimeMs`，仅在对应tick边界生效。记录同时保留 `requestPayload` 和执行 `data/error`。

### 批量优化实验

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/power/experiments` | 同步创建并运行一个实验 |
| POST | `/api/power/experiments/batch` | 批量运行多个实验 |
| GET | `/api/power/experiments` | 列出实验摘要 |
| GET | `/api/power/experiments/{experimentId}` | 获取实验摘要和最佳候选 |
| GET | `/api/power/experiments/{experimentId}/trials` | 获取全部候选明细 |

请求示例：

```json
{
  "problem": "N1_ROBUST_TIMETABLE",
  "algorithm": "EVOLUTIONARY",
  "populationSize": 12,
  "generations": 5,
  "seed": 20260711,
  "trainCount": 12,
  "timeSlots": 16,
  "slotSeconds": 5
}
```

`problem` 支持 `REGEN_MATCHING`、`TRACTION_STAGGER`、`EFS_CAPACITY`、`N1_ROBUST_TIMETABLE`。响应包含基线、最佳试验、改善比例、逐代摘要、全部目标与硬约束。
