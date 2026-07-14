# 供电仿真批量实验与AI优化设计

## 1. 目标与边界

本模块将9号线DC750V供电潮流作为候选方案的评价器，支持以下四类优化问题：

| 问题代码 | 决策变量 | 主要目标 | 主要约束 |
|---|---|---|---|
| `REGEN_MATCHING` | 发车错峰、制动相位偏移 | 降低再生浪费和线路损耗 | 收敛、功率平衡、最低电压 |
| `TRACTION_STAGGER` | 相邻列车发车错峰 | 降低整流峰值和限牵 | 运行图偏差、最低电压 |
| `EFS_CAPACITY` | TS-0901/0905/0909回馈容量 | 权衡再生利用、损耗和容量成本 | 收敛、功率平衡、最低电压 |
| `N1_ROBUST_TIMETABLE` | 发车错峰、牵引强度 | 降低N-1最坏限牵与欠压 | 正常及TS-0903/0905/0907停运场景均可行 |

当前算法为确定性种子的进化搜索或随机搜索。它属于可解释的AI/启发式优化基线；后续可在不修改仿真评价器的前提下接入贝叶斯优化、NSGA-II或强化学习。

## 2. 软件结构

```text
PowerExperimentRequest
  -> PowerExperimentRunner
      -> 候选生成/变异
      -> 逐时段构造多车牵引与制动负荷
      -> DCTractionPowerFlowSolver
      -> 目标、约束、可行性和加权得分
  -> PowerExperimentRegistry
      -> power_experiments
      -> power_experiment_trials
  -> REST API / CLI / JSON报告
```

实现文件：

- `app/domain/power/experiments.py`：请求校验、场景仿真、目标计算、进化搜索和SQLite存储。
- `tools/run_power_optimization.py`：单问题或四问题批量命令行入口。
- `app/api_server.py`：实验创建、批量创建、摘要和试验明细API。
- `tests/test_power_experiments.py`：四问题、确定性、持久化和HTTP闭环测试。

## 3. 统一试验输出

每个 `trial` 至少包含：

| 字段 | 说明 |
|---|---|
| candidate | 决策变量及数值 |
| objectives | 加权得分及各目标分量 |
| constraints | 收敛、平衡误差和最低电压约束 |
| feasible | 是否满足全部硬约束 |
| metrics | 再生能量、峰值功率、最低电压、限牵、损耗和失败步数 |
| generation | 候选所属代数 |
| trialId | 可追踪的试验编号 |

所有同请求、同算法、同种子的候选和结果应一致。实验编号只用于存储，不参与随机过程。

## 4. 目标函数解释

加权目标仅用于候选排序，不是现实节能率。报告中的 `improvementPercent` 表示相对当前加权目标基线的改善。

- 再生匹配：再生浪费为主，兼顾线路损耗和运行图偏差。
- 牵引错峰：整流峰值为主，重罚限牵，兼顾运行图偏差。
- 回馈容量：再生浪费、线路损耗和容量成本的综合权衡。
- N-1鲁棒运行图：最坏场景限牵和欠压为主，兼顾峰值和运行图偏差。

硬约束统一为：所有时段收敛、功率平衡误差小于1%、最低列车电压不低于500 V。

## 5. 执行方式

四问题批量运行：

```powershell
python tools/run_power_optimization.py --population 8 --generations 3
```

单问题运行：

```powershell
python tools/run_power_optimization.py --problem N1_ROBUST_TIMETABLE --population 12 --generations 5 --seed 20260711
```

默认输出：

- `outputs/power-optimization-report.json`
- `outputs/power_experiments.sqlite`

## 6. 结果使用限制

当前拓扑容量和部分设备位置为 `ENGINEERING_ESTIMATE`。在真实参数未校准前，优化结果用于算法比较、闭环演示和方案筛选，不得直接作为现场运行图、保护整定或设备投资决策。

## 7. 已选实验主题与 V1 实现

在上述备选问题基础上，V1 正式实验主题确定为“多车牵引/制动时序与超级电容控制联合优化”。该实验直接调用生产版直流潮流求解器，采用带约束 NSGA-II，同时最小化 SOC 修正取电能量、整流峰值功率和再生浪费，并以同预算随机搜索、仅时序和仅储能三组结果作为对照。

与早期四问题演示相比，V1 增加了运行时间与停车位置保持、速度跟踪、牵引所容量、储能期末 SOC 修正、重复随机种子、独立半步长复算、消融和敏感性分析，因此后续论文或答辩中的定量结论应优先引用 V1 实验。

- 实现：`app/domain/power/joint_optimization.py`
- 执行入口：`tools/run_joint_power_optimization.py`
- 冻结摘要：`data/contracts/joint_power_optimization_v1_summary.json`
- V2 代理轨迹报告：`docs/测试与验收/多车时序与超级电容联合优化实验报告.md`
- V3 主引擎长时域报告：`docs/测试与验收/多车时序与超级电容联合优化实验报告V3.md`
