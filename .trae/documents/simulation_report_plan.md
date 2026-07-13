# 仿真报告生成功能实现计划

## 1. 需求分析

用户要求在每次仿真结束时自动生成一份报告，覆盖以下维度，**必须包含图表**：
- **动力性能**：列车能耗、牵引力、制动力、运行速度等
- **客流统计**：乘客上下车、等待时间、满载率、滞留情况等
- **供电性能**：功率消耗、再生电能回收、电压状态、变电站负载等

## 2. 现有系统分析

### 2.1 数据记录机制
系统已有的 `RunRecorder` 类（[recorder.py](file:///d:/all_projects/BJTUMetroSim/app/infra/recorder.py)）已记录以下数据：
- `events` - 列车状态、调度决策等事件
- `metrics` - 性能指标
- `station_passenger_records` - 站点客流记录
- `train_load_records` - 列车负载记录
- `power_records` - 功率记录
- `substation_power_records` - 变电站功率记录
- `regen_energy_records` - 再生电能记录
- `supercapacitor_power_records` - 超级电容记录

### 2.2 KPI跟踪
`DispatchKpiTracker` 类（[kpi.py](file:///d:/all_projects/BJTUMetroSim/app/domain/dispatch/kpi.py)）已追踪：
- 准点率
- 平均等待时间
- 满载率
- 延误恢复时间
- 追踪间隔违规

### 2.3 前端技术栈
前端项目（[bj-metro-sim](file:///d:/all_projects/BJTUMetroSim/bj-metro-sim)）使用：
- React + TypeScript + Vite
- Ant Design 组件库
- 自定义SVG图表（无专业图表库）

## 3. 实现方案

### 3.1 方案概述
报告生成采用**前后端协作**方式：
- **后端**：生成结构化报告数据（包含统计指标 + 图表数据），保存到数据库
- **前端**：使用 Recharts 图表库渲染报告图表，展示完整报告

### 3.2 新增依赖
| 位置 | 依赖 | 说明 |
|------|------|------|
| 前端 | `recharts` | React图表库，用于渲染折线图、柱状图、面积图等 |

### 3.3 新增报告生成器模块

创建 `app/core/report_generator.py`，包含：

**ReportGenerator 类**：
- 从 `RunRecorder` 提取仿真数据
- 计算各维度统计指标
- 生成图表所需的时间序列数据
- 生成结构化报告

### 3.4 报告结构设计

```json
{
  "runId": 1,
  "scenarioName": "line9_5train_power",
  "durationMs": 3600000,
  "durationStr": "01:00:00",
  "trainCount": 5,
  "generatedAt": "2026-07-13T14:30:00Z",
  "summary": { ... },
  "dynamics": { ... },      // 动力性能
  "passenger": { ... },     // 客流统计
  "power": { ... },         // 供电性能
  "kpi": { ... },           // KPI指标
  "charts": {               // 图表数据
    "dynamics": { ... },
    "passenger": { ... },
    "power": { ... }
  }
}
```

### 3.5 报告维度详细设计

#### 3.5.1 仿真概览 (summary)
| 字段 | 说明 | 来源 |
|------|------|------|
| runId | 运行ID | runs表 |
| scenarioName | 场景名称 | runs表 |
| startTime | 仿真开始时间 | runs表 |
| endTime | 仿真结束时间 | 最后tick时间 |
| durationMs | 仿真持续时间(毫秒) | 计算 |
| durationStr | 仿真持续时间(格式化) | 计算 |
| trainCount | 参与仿真的列车数 | 计算 |
| stationCount | 站点数量 | 配置 |
| totalEvents | 总事件数 | events表 |
| totalTicks | 总tick数 | 计算 |

#### 3.5.2 动力性能 (dynamics)
| 字段 | 说明 | 来源 |
|------|------|------|
| totalEnergyKwh | 总能耗(kWh) | train.state事件 |
| tractionEnergyKwh | 牵引能耗(kWh) | train.state事件 |
| auxiliaryEnergyKwh | 辅助能耗(kWh) | train.state事件 |
| regenGeneratedKwh | 再生电能(kWh) | train.state事件 |
| regenAcceptedKwh | 被接受的再生电能(kWh) | train.state事件 |
| regenWastedKwh | 浪费的再生电能(kWh) | train.state事件 |
| regenUtilizationRate | 再生利用率 | 计算 |
| maxSpeedKmh | 最大速度(km/h) | train.state事件 |
| avgSpeedKmh | 平均速度(km/h) | train.state事件 |
| totalDistanceKm | 总运行里程(km) | train.state事件 |
| maxTractionForceN | 最大牵引力(N) | train.state事件 |
| maxBrakeForceN | 最大制动力(N) | train.state事件 |

#### 3.5.3 客流统计 (passenger)
| 字段 | 说明 | 来源 |
|------|------|------|
| totalArrivals | 总进站人数 | station_passenger_records |
| totalBoardings | 总上车人数 | station_passenger_records |
| totalAlightings | 总下车人数 | station_passenger_records |
| totalLeftBehind | 总滞留人数 | station_passenger_records |
| avgWaitingSec | 平均等待时间(秒) | station_passenger_records |
| maxWaitingSec | 最大等待时间(秒) | station_passenger_records |
| peakCrowdingStation | 最拥挤站点 | station_passenger_records |
| peakCrowdingLevel | 最拥挤等级 | station_passenger_records |

#### 3.5.4 供电性能 (power)
| 字段 | 说明 | 来源 |
|------|------|------|
| totalPowerConsumedKwh | 总消耗电能(kWh) | power_records |
| totalRegenGeneratedKwh | 总再生电能(kWh) | regen_energy_records |
| totalRegenAbsorbedKwh | 总吸收再生电能(kWh) | regen_energy_records |
| totalRegenWastedKwh | 总浪费再生电能(kWh) | regen_energy_records |
| totalLossesKwh | 总损耗(kWh) | power_records |
| avgVoltageV | 平均电压(V) | train_voltage_records |
| minVoltageV | 最低电压(V) | train_voltage_records |
| maxVoltageV | 最高电压(V) | train_voltage_records |
| overloadEvents | 过载事件次数 | 计算 |

#### 3.5.5 KPI指标 (kpi)
| 字段 | 说明 | 来源 |
|------|------|------|
| onTimeRate | 准点率 | kpi_tracker |
| avgWaitSec | 平均等待时间 | kpi_tracker |
| avgLoadFactor | 平均满载率 | kpi_tracker |
| maxLoadFactor | 最大满载率 | kpi_tracker |
| overloadEvents | 超载事件次数 | kpi_tracker |
| headwayViolations | 追踪间隔违规次数 | kpi_tracker |
| recoveryTimeSec | 延误恢复时间 | kpi_tracker |

### 3.6 图表数据设计

#### 3.6.1 动力性能图表
| 图表类型 | 图表名称 | 数据内容 |
|----------|----------|----------|
| 折线图 | 速度-时间曲线 | 各列车速度随时间变化 |
| 面积图 | 能耗累积曲线 | 牵引能耗、再生电能累积 |
| 柱状图 | 列车能耗对比 | 各列车总能耗对比 |

#### 3.6.2 客流统计图表
| 图表类型 | 图表名称 | 数据内容 |
|----------|----------|----------|
| 折线图 | 进站人数趋势 | 各站点进站人数随时间变化 |
| 柱状图 | 站点客流排名 | 各站点总客流量排名 |
| 堆叠柱状图 | 上下车对比 | 各站点上车/下车人数对比 |

#### 3.6.3 供电性能图表
| 图表类型 | 图表名称 | 数据内容 |
|----------|----------|----------|
| 折线图 | 电压趋势 | 列车电压随时间变化 |
| 面积图 | 功率消耗趋势 | 牵引功率、再生功率随时间变化 |
| 柱状图 | 变电站负载 | 各变电站平均负载 |

### 3.7 修改仿真引擎

在 `SimulationEngine.stop()` 方法中添加报告生成逻辑：
1. 停止前调用 `generate_report()` 方法
2. 将报告保存到记录器

### 3.8 修改API服务

新增API端点：
- `GET /api/sim/report` - 获取当前仿真报告
- `GET /api/sim/report/{runId}` - 获取指定运行的报告

### 3.9 前端报告页面

创建报告展示页面，包含：
- 仿真概览卡片
- 动力性能图表区域
- 客流统计图表区域
- 供电性能图表区域
- KPI指标展示

## 4. 文件修改清单

### 4.1 后端文件
| 文件 | 操作 | 说明 |
|------|------|------|
| `app/core/report_generator.py` | 新建 | 报告生成器模块 |
| `app/core/engine.py` | 修改 | 添加报告生成和保存方法 |
| `app/api_server.py` | 修改 | 新增报告API端点 |
| `app/infra/recorder.py` | 修改 | 添加报告保存和读取方法 |

### 4.2 前端文件
| 文件 | 操作 | 说明 |
|------|------|------|
| `bj-metro-sim/package.json` | 修改 | 添加 `recharts` 依赖 |
| `bj-metro-sim/src/data/backendApi.ts` | 修改 | 添加报告API类型定义和调用 |
| `bj-metro-sim/src/components/SimulationReport.tsx` | 新建 | 报告展示组件 |
| `bj-metro-sim/src/components/ReportCharts.tsx` | 新建 | 报告图表组件 |
| `bj-metro-sim/src/App.tsx` | 修改 | 添加报告页面路由 |

## 5. 实施步骤

### 步骤1：安装前端图表依赖
```bash
cd bj-metro-sim
npm install recharts
```

### 步骤2：创建报告生成器模块
```bash
新建 app/core/report_generator.py
```
实现 `ReportGenerator` 类，包含：
- `__init__(recorder)` - 初始化
- `generate(run_id)` - 生成报告（含图表数据）
- `_extract_summary()` - 提取概览数据
- `_extract_dynamics()` - 提取动力数据（含图表数据）
- `_extract_passenger()` - 提取客流数据（含图表数据）
- `_extract_power()` - 提取供电数据（含图表数据）
- `_extract_kpi()` - 提取KPI数据

### 步骤3：修改记录器
```bash
修改 app/infra/recorder.py
```
添加：
- `save_report(run_id, report)` - 保存报告到数据库
- `get_report(run_id)` - 读取报告

### 步骤4：修改仿真引擎
```bash
修改 app/core/engine.py
```
在 `stop()` 方法中添加报告生成逻辑：
- 创建 `_report_generator` 属性
- 在停止时调用 `generate_report()` 并保存

### 步骤5：修改API服务
```bash
修改 app/api_server.py
```
添加：
- `GET /api/sim/report` - 获取当前报告
- `GET /api/sim/report/{runId}` - 获取历史报告

### 步骤6：前端类型定义
```bash
修改 bj-metro-sim/src/data/backendApi.ts
```
添加报告相关的类型定义和API调用函数

### 步骤7：创建报告图表组件
```bash
新建 bj-metro-sim/src/components/ReportCharts.tsx
```
实现图表组件：
- `DynamicsCharts` - 动力性能图表
- `PassengerCharts` - 客流统计图表
- `PowerCharts` - 供电性能图表

### 步骤8：创建报告展示页面
```bash
新建 bj-metro-sim/src/components/SimulationReport.tsx
```
实现报告页面，包含概览、图表、KPI展示

### 步骤9：集成到主应用
```bash
修改 bj-metro-sim/src/App.tsx
```
添加报告页面的路由或入口

## 6. 潜在风险与注意事项

### 6.1 性能风险
- 大规模仿真数据量较大，报告生成可能耗时较长
- **缓解措施**：报告生成在仿真停止时异步执行，不阻塞主线程

### 6.2 数据完整性
- 如果仿真中途异常终止，可能缺少部分数据
- **缓解措施**：在 `stop()` 方法中确保报告生成逻辑被执行

### 6.3 图表数据采样
- 直接返回所有tick数据可能导致图表渲染性能问题
- **缓解措施**：对时间序列数据进行采样，保留关键数据点

### 6.4 兼容性
- 现有数据库没有报告表，需要新增表结构
- **缓解措施**：在 `_init_schema()` 中添加报告表

## 7. 测试计划

### 7.1 功能测试
1. 启动仿真 → 运行一段时间 → 停止仿真 → 检查报告生成
2. 调用 `/api/sim/report` 检查返回数据完整性（含图表数据）
3. 前端报告页面检查图表渲染效果

### 7.2 边界测试
1. 空仿真（未运行直接停止）
2. 长时间仿真（30分钟以上）
3. 多列车仿真（5列车场景）

### 7.3 性能测试
1. 检查报告生成时间是否在可接受范围内（<5秒）
2. 检查前端图表渲染性能

## 8. 输出格式示例

```json
{
  "runId": 1,
  "scenarioName": "line9_5train_power",
  "durationMs": 3600000,
  "durationStr": "01:00:00",
  "trainCount": 5,
  "generatedAt": "2026-07-13T14:30:00Z",
  "summary": {
    "trainCount": 5,
    "stationCount": 13,
    "totalEvents": 12000,
    "totalTicks": 72000
  },
  "dynamics": {
    "totalEnergyKwh": 1250.5,
    "tractionEnergyKwh": 980.3,
    "auxiliaryEnergyKwh": 270.2,
    "regenGeneratedKwh": 340.8,
    "regenAcceptedKwh": 280.5,
    "regenWastedKwh": 60.3,
    "regenUtilizationRate": 0.823,
    "maxSpeedKmh": 79.8,
    "avgSpeedKmh": 35.2,
    "totalDistanceKm": 156.8
  },
  "passenger": {
    "totalArrivals": 12500,
    "totalBoardings": 11800,
    "totalAlightings": 11500,
    "totalLeftBehind": 120,
    "avgWaitingSec": 125.5,
    "maxWaitingSec": 450.2,
    "peakCrowdingStation": "LLQ"
  },
  "power": {
    "totalPowerConsumedKwh": 970.2,
    "totalRegenGeneratedKwh": 340.8,
    "totalRegenAbsorbedKwh": 280.5,
    "totalRegenWastedKwh": 60.3,
    "totalLossesKwh": 45.6,
    "avgVoltageV": 745.2,
    "minVoltageV": 680.5,
    "maxVoltageV": 765.8,
    "overloadEvents": 3
  },
  "kpi": {
    "onTimeRate": 0.965,
    "avgWaitSec": 125.5,
    "avgLoadFactor": 0.85,
    "maxLoadFactor": 1.35,
    "overloadEvents": 8,
    "headwayViolations": 2
  },
  "charts": {
    "dynamics": {
      "speedTimeSeries": [
        {"time": "06:00:00", "T0901": 0, "T0902": 0},
        {"time": "06:01:00", "T0901": 15, "T0902": 0},
        {"time": "06:02:00", "T0901": 35, "T0902": 12}
      ],
      "energyCumulative": [
        {"time": "06:00:00", "traction": 0, "auxiliary": 0, "regen": 0},
        {"time": "06:30:00", "traction": 490, "auxiliary": 135, "regen": 170},
        {"time": "07:00:00", "traction": 980, "auxiliary": 270, "regen": 340}
      ],
      "trainEnergyComparison": [
        {"trainId": "T0901", "energyKwh": 250},
        {"trainId": "T0902", "energyKwh": 245},
        {"trainId": "T0903", "energyKwh": 255},
        {"trainId": "T0904", "energyKwh": 248},
        {"trainId": "T0905", "energyKwh": 252}
      ]
    },
    "passenger": {
      "arrivalTimeSeries": [
        {"time": "06:00:00", "GGZ": 10, "FSP": 8},
        {"time": "07:00:00", "GGZ": 120, "FSP": 95},
        {"time": "08:00:00", "GGZ": 85, "FSP": 70}
      ],
      "stationPassengerRanking": [
        {"station": "LLQ", "total": 2500},
        {"station": "BDZ", "total": 2200},
        {"station": "BWR", "total": 1800}
      ],
      "boardingAlightingComparison": [
        {"station": "GGZ", "boarding": 1200, "alighting": 300},
        {"station": "FSP", "boarding": 900, "alighting": 600},
        {"station": "LLQ", "boarding": 1500, "alighting": 1800}
      ]
    },
    "power": {
      "voltageTimeSeries": [
        {"time": "06:00:00", "min": 750, "avg": 750, "max": 750},
        {"time": "07:00:00", "min": 720, "avg": 740, "max": 755},
        {"time": "08:00:00", "min": 700, "avg": 735, "max": 752}
      ],
      "powerTimeSeries": [
        {"time": "06:00:00", "traction": 0, "regen": 0, "losses": 0},
        {"time": "07:00:00", "traction": 2500, "regen": 800, "losses": 120},
        {"time": "08:00:00", "traction": 2800, "regen": 900, "losses": 135}
      ],
      "substationLoad": [
        {"substation": "SS01", "avgLoad": 0.65},
        {"substation": "SS02", "avgLoad": 0.72},
        {"substation": "SS03", "avgLoad": 0.58}
      ]
    }
  }
}
```

## 9. 完成标准

- ✅ 新增报告生成器模块
- ✅ 仿真停止时自动生成报告（含图表数据）
- ✅ 报告包含动力、客流、供电三个维度的统计指标
- ✅ 报告包含图表数据（时间序列、对比数据）
- ✅ 新增API端点 `/api/sim/report`
- ✅ 前端安装 recharts 依赖
- ✅ 前端创建报告展示页面，包含图表渲染
- ✅ 报告数据完整准确
- ✅ 测试通过
