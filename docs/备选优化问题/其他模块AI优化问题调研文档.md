# 其他模块 AI 优化问题调研文档

调研对象：除车辆动力学仿真模块、牵引供电仿真模块以外的其余模块。

覆盖模块：

1. 线路与轨道仿真模块
4. 信号与列控仿真模块
5. 司机台/驾驶行为仿真模块
6. 车站与站台仿真模块
7. 调度与运行图仿真模块
8. 环境与扰动仿真模块
9. 通信接口与数据交换模块
10. 仿真时钟与场景管理模块
11. 数据记录、评估与回放模块
12. 可视化与人机交互模块

撰写日期：2026-07-07

## 1. 文档目标

本文档用于回答两个问题：

1. 除车辆与供电模块外，其他模块中有哪些有深度、有真实需求的优化问题？
2. 这些问题如何引入人工智能方法，形成可仿真、可评估、可答辩的方案？

本文不是简单罗列“可以用 AI”，而是把每个候选问题整理为：

```text
真实业务背景
优化目标
决策变量
约束条件
可用 AI 方法
仿真实验设计
落地难度
推荐程度
```

## 2. 调研依据摘要

本次调研参考了轨道交通数据模型、自动列控、仿真接口、动态追踪间隔、列车重排、客流预测、司机辅助系统等方向的公开资料。

关键发现：

1. RailTopoModel 和 railML 说明，线路拓扑、基础设施、联锁、运行图、车辆等对象需要标准化表达，这为“线路数据质量优化、拓扑一致性检查、场景生成”提供了真实需求。
2. ERA/ERTMS/ATO 资料说明，ATO 自动驾驶需要任务数据、时刻目标、停车点、路线约束和 ETCS 安全监督，这为“信号约束下的动态运行优化”和“司机辅助建议”提供了真实依据。
3. SUMO/TraCI 说明，仿真系统可以通过接口实时读取状态并下发控制，这支持把 AI 控制器、调度器、客流控制器作为外部智能体接入仿真闭环。
4. 城市轨道客流预测研究显示，短时进站客流预测对站内拥挤管理和线路重调度有实际意义。
5. 列车运行图重排研究显示，扰动下的重排需要同时减少列车延误、乘客影响、换乘失败和运行图偏离，强化学习、图神经网络、进化算法等方法已有相关应用。
6. 动态追踪间隔和移动闭塞研究说明，数字化信号系统使线路能力从静态约束变成可实时优化的系统变量。

## 3. 资料来源登记表

| Source ID | 方向 | 标题 | URL | 类型 | 可支撑内容 |
|---|---|---|---|---|---|
| SRC-OTH-001 | 线路/数据模型 | RailTopoModel | https://www.railtopomodel.org/ | 官方模型 | 线路拓扑、基础设施对象、拓扑标准化 |
| SRC-OTH-002 | 数据模型 | railML Working Groups | https://www.railml.org/en/working-groups | 官方资料 | railML 包含 Timetable、Infrastructure、Interlocking、Ontology 工作组 |
| SRC-OTH-003 | 数据模型 | Use Case-Driven railML Development | https://www.railml.org/en/news/use-case-driven-railml-development | 官方资料 | railML 子模式包括 Timetable、Infrastructure、Interlocking、Rolling Stock，用例驱动数据建模 |
| SRC-OTH-004 | ATO/列控 | ERA ERTMS/ATO Operational Principles | https://www.era.europa.eu/sites/default/files/2024-12/index64_12e108_2_ato_operational_principles.pdf | 官方规范 | ATO 任务、停车点、时刻目标、运行约束、安全监督 |
| SRC-OTH-005 | ATO/列控 | ERTMS Automatic Train Operation | https://ertms.be/activities/automatic-train-operation | 官方/行业资料 | ATO 在 ETCS 保护下自动驾驶，基于时刻表尽量节能运行 |
| SRC-OTH-006 | 仿真接口 | SUMO TraCI Documentation | https://sumo.dlr.de/docs/TraCI/index.html | 官方文档 | 仿真状态读取、外部控制器接入、实时控制接口 |
| SRC-OTH-007 | 仿真平台 | Eclipse SUMO | https://eclipse.dev/sumo/ | 官方文档 | 交通仿真、公共交通、行人、API 控制 |
| SRC-OTH-008 | 信号/能力 | Dynamic headway control for capacity optimization | https://www.frontiersin.org/journals/future-transportation/articles/10.3389/ffutr.2026.1826998/full | 综述论文 | 动态追踪间隔、能力优化、闭环运营 |
| SRC-OTH-009 | CBTC/轨迹预测 | Communication-Based Train Control with Dynamic Headway Optimization | https://www.mdpi.com/2076-0825/11/8/237 | 论文 | 基于轨迹预测的动态追踪间隔，LSTM + Kalman Filter |
| SRC-OTH-010 | 客流预测 | Inbound passenger flow Wave-LSTM | https://www.sciencedirect.com/science/article/abs/pii/S002002552100178X | 论文 | 短时进站客流预测用于拥挤管理和线路重调度 |
| SRC-OTH-011 | 客流预测 | IPSO-SVR passenger flow prediction | https://www.maxapress.com/article/doi/10.48130/dts-0025-0005 | 论文 | 粒子群优化 SVR 预测城市轨道客流 |
| SRC-OTH-012 | 调度重排 | Train rescheduling under disruptions with passenger transfer | https://www.sciencedirect.com/science/article/pii/S0360835225006059 | 论文 | 扰动下重排，兼顾运行图偏离和换乘旅客影响 |
| SRC-OTH-013 | 调度重排 | RL for timetable rescheduling under disruptions | https://www.sciencedirect.com/science/article/pii/S0360835225004759 | 论文 | 强化学习优化多扰动场景下运行图重排 |
| SRC-OTH-014 | 调度重排 | GNN for railway timetable rescheduling | https://link.springer.com/article/10.1007/s40534-025-00383-7 | 论文 | 用图神经网络建模运行图演化图，提高重排可解释性 |
| SRC-OTH-015 | 司机辅助 | Driver Advisory System LEADER Flow | https://rail.knorr-bremse.com/en/us/portfolio/products-and-systems/digital-solutions/driver-advisory-systems/ | 产品资料 | 司机辅助系统用于节能、准点和运行建议 |
| SRC-OTH-016 | 司机辅助 | DAS impact on driver workload and safety | https://www.worldtransitresearch.info/research/8946/ | 研究资料 | 司机辅助信息对安全表现和注意力分配的影响 |
| SRC-OTH-017 | 扰动/重排 | Integrated RL and optimization for railway rescheduling | https://www.bartdeschutter.org/publications/24-009-integrated-reinforcement-learning-optimization/24-009-integrated-reinforcement-learning-optimization.pdf | 论文 | 扰动下实时重排、强化学习与优化结合 |
| SRC-OTH-018 | 客流控制 | Joint Optimization of Passenger Flow Control and Train Skip-Stop | https://link.springer.com/article/10.1007/s40864-025-00265-5 | 论文 | 客流控制与跳停策略联合优化 |

## 4. 候选问题总览

| 编号 | 模块 | 优化问题 | AI 方法 | 推荐程度 |
|---|---|---|---|---|
| OAI-01 | 线路与轨道 | 线路拓扑数据质量检查与自动修复 | 图算法、异常检测、GNN | 高 |
| OAI-02 | 线路与轨道 | 场景线路生成与测试覆盖优化 | 生成式算法、遗传算法、约束求解 | 中高 |
| OAI-03 | 信号与列控 | 动态追踪间隔与线路能力优化 | 轨迹预测、强化学习、MPC | 很高 |
| OAI-04 | 信号与列控 | 信号约束下的安全运行策略优化 | 强化学习、规则约束学习、监督学习 | 高 |
| OAI-05 | 司机台/驾驶行为 | 个性化司机辅助建议优化 | 模仿学习、推荐系统、行为分类 | 高 |
| OAI-06 | 司机台/驾驶行为 | 异常驾驶行为识别与接管提醒 | 异常检测、时序分类、随机森林 | 高 |
| OAI-07 | 车站与站台 | 站台客流拥挤预测与限流优化 | LSTM、图神经网络、强化学习 | 很高 |
| OAI-08 | 车站与站台 | 停站时间预测与开关门策略优化 | 回归模型、强化学习、贝叶斯优化 | 高 |
| OAI-09 | 调度与运行图 | 扰动下运行图重排优化 | 强化学习、进化算法、GNN | 很高 |
| OAI-10 | 调度与运行图 | 客流感知发车间隔与跳停优化 | 多智能体强化学习、遗传算法 | 高 |
| OAI-11 | 环境与扰动 | 故障场景自动生成与鲁棒性测试 | 对抗生成、蒙特卡洛、主动学习 | 高 |
| OAI-12 | 环境与扰动 | 扰动影响预测与恢复策略选择 | 分类/回归、强化学习 | 高 |
| OAI-13 | 通信接口 | 通信异常检测与数据质量修复 | 异常检测、时序预测、自动编码器 | 中高 |
| OAI-14 | 仿真时钟/场景 | 自适应仿真步长与实验调度优化 | 贝叶斯优化、主动学习 | 中 |
| OAI-15 | 数据记录评估 | 自动指标挖掘与失败原因归因 | 决策树、SHAP、聚类 | 中高 |
| OAI-16 | 可视化交互 | 智能告警排序与解释型驾驶/调度看板 | 排序学习、可解释 AI | 中 |

## 5. OAI-01 线路拓扑数据质量检查与自动修复

### 5.1 真实需求

线路与轨道模块依赖拓扑数据。若线路连接、道岔、站点、停车点、限速区、闭塞分区等对象存在断连、重叠、方向错误或里程异常，后续车辆运行、信号判断和调度仿真都会出错。

RailTopoModel 强调铁路网络拓扑和对象之间的关系表达，railML 也将基础设施、联锁和运行图作为结构化数据子模式。因此，线路数据质量不是文档问题，而是仿真可信度的前置条件。

### 5.2 优化问题

在给定线路拓扑数据的情况下，识别并修复尽可能多的数据错误，使线路模型满足连通性、方向性、里程连续性和对象挂接规则。

### 5.3 优化目标

```text
maximize repaired_error_count
minimize false_repair_count
minimize topology_inconsistency_score
```

可定义综合目标：

```text
J = w1 * disconnected_count
  + w2 * overlap_count
  + w3 * invalid_mileage_count
  + w4 * object_orphan_count
  + w5 * repair_cost
```

### 5.4 AI 方法

可选方法：

```text
图异常检测
图神经网络 GNN
规则引擎 + 机器学习分类器
约束求解 + 启发式修复
```

实训建议：

```text
规则检测 + 异常评分 + 自动修复建议
```

第一版不必训练 GNN，可以先把线路建成图：

```text
节点：站点、道岔、端点、信号机
边：轨道区间
属性：长度、方向、限速、坡度、闭塞区
```

然后检测：

```text
孤立节点
重复边
负长度边
里程不连续
停车点不在任何轨道上
限速区间越界
信号机方向与轨道方向冲突
```

### 5.5 仿真实验

输入：

```text
正常线路数据
人工注入错误后的线路数据
```

输出：

```text
错误类型
错误位置
修复建议
修复前后拓扑一致性评分
```

评价指标：

```text
错误检出率
误报率
自动修复成功率
修复后能否正常运行仿真
```

## 6. OAI-02 场景线路生成与测试覆盖优化

### 6.1 真实需求

仿真系统需要大量测试场景验证控制策略。人工设计场景容易遗漏极端情况，例如短站间距、连续限速、坡度突变、信号限制与停车点接近等。

### 6.2 优化问题

自动生成一组线路/场景，使其覆盖尽可能多的运行边界条件，并尽可能暴露控制算法弱点。

### 6.3 优化目标

```text
maximize scenario_coverage
maximize failure_discovery_rate
minimize duplicate_scenarios
```

覆盖维度：

```text
站间距
限速变化
坡度变化
曲线区间
停车点位置
信号限制
供电扰动
客流扰动
```

### 6.4 AI 方法

```text
遗传算法生成场景
蒙特卡洛随机场景生成
主动学习选择最有价值场景
对抗测试生成
```

实训建议：

```text
随机生成 + 覆盖评分 + 保留高价值场景
```

## 7. OAI-03 动态追踪间隔与线路能力优化

### 7.1 真实需求

信号与列控系统的核心目标是安全和能力。数字信号、移动闭塞、CBTC 等技术使追踪间隔可以更动态地依赖列车速度、制动能力、位置预测和运行状态。动态追踪间隔优化的真实目标是提高线路通过能力，同时不降低安全性。

### 7.2 优化问题

在多列车运行仿真中，动态调整列车目标速度、移动授权或发车间隔，使系统在保持安全制动距离的前提下提高通过能力并减少延误。

### 7.3 决策变量

```text
target_speed_i(t)
movement_authority_end_i(t)
departure_headway_i
safe_margin_i(t)
```

### 7.4 优化目标

```text
minimize average_headway
minimize total_delay
maximize throughput
minimize safety_margin_violation
```

约束：

```text
列车间距 >= 安全制动距离 + 安全裕量
v_i(t) <= permitted_speed_i(t)
不得越过移动授权终点
不得触发追尾风险
```

### 7.5 AI 方法

```text
LSTM/Kalman Filter 轨迹预测
强化学习动态调整追踪间隔
模型预测控制 MPC
图神经网络预测多车状态传播
```

实训建议：

```text
轨迹预测 + 动态安全裕量调整
```

第一版可以只做两列车：

```text
前车按既定曲线运行
后车根据预测前车位置调整目标速度
比较固定闭塞/固定追踪间隔/动态追踪间隔
```

### 7.6 评价指标

```text
最小列车间距
平均追踪间隔
单位时间通过列车数
总延误
安全裕量最小值
急制动次数
```

## 8. OAI-04 信号约束下的安全运行策略优化

### 8.1 真实需求

ATO 自动驾驶必须服从 ATP/ETCS 安全监督。真实自动驾驶不是单纯追求节能或准点，而是在安全包络内生成牵引和制动策略。

### 8.2 优化问题

在信号状态、限速曲线、移动授权和停车目标约束下，生成最优驾驶策略，使列车准点、节能、平稳，并且不违反信号安全约束。

### 8.3 AI 方法

```text
安全约束强化学习
规则约束 + 智能参数优化
模仿学习从专家规则中学习策略
监督学习预测下一步安全动作
```

实训建议：

```text
规则安全层 + AI 优化层
```

结构：

```text
AI 输出建议动作
  ↓
安全层检查限速、信号、移动授权
  ↓
不安全动作被裁剪或替换
  ↓
车辆执行安全动作
```

评价指标：

```text
信号违规次数
超速次数
紧急制动次数
准点偏差
能耗
停车误差
```

## 9. OAI-05 个性化司机辅助建议优化

### 9.1 真实需求

司机辅助系统 DAS/C-DAS 用于给司机提供节能、准点、速度和惰行建议。真实系统中，建议必须可理解、可执行，且不能增加司机负担。

### 9.2 优化问题

根据列车状态、线路条件、运行图和司机历史操作习惯，生成司机更可能接受且能改善能耗/准点性的建议。

### 9.3 决策变量

```text
advice_type: 加速/惰行/制动/保持
advice_timing: 建议时机
advice_strength: 建议级别
display_priority: 显示优先级
```

### 9.4 优化目标

```text
maximize advice_acceptance_rate
minimize energy
minimize timetable_deviation
minimize driver_workload
```

### 9.5 AI 方法

```text
模仿学习
行为聚类
推荐系统
上下文 bandit
监督学习预测司机是否接受建议
```

实训建议：

```text
基于仿真驾驶记录的建议接受率预测
```

数据来源：

```text
手动驾驶记录
AI 建议记录
司机是否按建议操作
操作后能耗/准点变化
```

评价指标：

```text
建议接受率
建议后能耗改善
准点改善
建议触发次数
误建议次数
```

## 10. OAI-06 异常驾驶行为识别与接管提醒

### 10.1 真实需求

司机或手动控制可能出现过晚制动、频繁切换级位、接近限速仍牵引、停车点前速度过高等风险行为。系统需要提前识别风险并提示接管或建议制动。

### 10.2 优化问题

从驾驶行为时间序列中识别可能导致超速、越站、急制动或高能耗的异常模式，并尽早发出提醒。

### 10.3 AI 方法

```text
随机森林/LightGBM 时窗分类
LSTM 时序分类
Isolation Forest 异常检测
自动编码器重构误差
```

特征：

```text
当前速度
距离停车点
制动级位
牵引级位
加速度
距离限速点
司机操作变化频率
```

标签：

```text
正常
过晚制动风险
超速风险
越站风险
高能耗操作
```

评价指标：

```text
提前预警时间
准确率
召回率
误报率
越站/超速减少率
```

## 11. OAI-07 站台客流拥挤预测与限流优化

### 11.1 真实需求

车站客流拥挤直接影响安全、停站时间、准点性和调度策略。短时进站客流预测被研究用于日常拥挤管理和线路重调度。

### 11.2 优化问题

预测未来若干分钟站台客流，并优化限流、开门组织、发车间隔或跳停策略，使站台拥挤度保持在安全范围内，同时减少乘客等待时间。

### 11.3 决策变量

```text
entry_control_rate
platform_dispatch_policy
dwell_time_adjustment
skip_stop_decision
train_headway_adjustment
```

### 11.4 优化目标

```text
minimize platform_overcrowding_time
minimize average_waiting_time
minimize passenger_stranding
minimize timetable_disruption
```

约束：

```text
platform_density <= safety_threshold
entry_control_rate 在允许范围内
列车载客容量不能超过上限
调度调整不违反最小追踪间隔
```

### 11.5 AI 方法

```text
LSTM/GRU 客流预测
图神经网络 GNN 多站客流传播预测
强化学习限流控制
粒子群/遗传算法优化限流参数
```

实训建议：

```text
客流预测 + 简化限流策略优化
```

若没有真实客流数据，可生成模拟客流：

```text
早高峰上升
突发大客流
站台容量受限
列车到发带走乘客
```

评价指标：

```text
客流预测 MAE/RMSE
站台最大人数
拥挤持续时间
平均等待时间
限流强度
滞留乘客数
```

## 12. OAI-08 停站时间预测与开关门策略优化

### 12.1 真实需求

停站时间受上下车人数、拥挤度、车门状态、乘客交换效率影响。停站时间过短会导致乘客滞留，过长会造成延误传播。

### 12.2 优化问题

预测每站所需停站时间，并动态调整开关门和发车策略，在保障乘降完成的前提下减少延误。

### 12.3 AI 方法

```text
回归模型预测 dwell time
LSTM 预测连续站点停站时间
贝叶斯优化调整停站时间缓冲
强化学习决策是否延长停站
```

特征：

```text
上车人数
下车人数
站台拥挤度
车厢拥挤度
门数量
当前晚点
后续运行裕量
```

目标：

```text
minimize passenger_left_behind
minimize delay
minimize unnecessary_dwell_extension
```

实训最小版本：

```text
用模拟客流生成停站时间数据
训练随机森林回归预测 dwell time
比较固定停站时间与预测停站时间策略
```

## 13. OAI-09 扰动下运行图重排优化

### 13.1 真实需求

扰动发生后，原运行图可能不可行。调度员需要调整到发时刻、停站时间、列车顺序、折返、越站或跳停，目标是减少延误传播和乘客影响。

### 13.2 优化问题

在设备故障、区间封锁、列车晚点、站台拥挤等扰动下，生成新的运行图，使总延误、运行图偏离、换乘失败和乘客等待最小。

### 13.3 决策变量

```text
arrival_time_i_s
departure_time_i_s
dwell_time_i_s
train_order
skip_stop_decision
turnback_decision
short_turn_route
```

### 13.4 优化目标

```text
minimize total_train_delay
minimize passenger_delay
minimize timetable_deviation
minimize missed_transfer_count
minimize cancellation_count
```

约束：

```text
最小追踪间隔
站台容量
车辆周转时间
区间通过能力
乘客换乘时间
信号安全约束
```

### 13.5 AI 方法

```text
强化学习
多智能体强化学习
遗传算法/进化算法
图神经网络预测延误传播
GNN + 优化器生成重排建议
```

实训建议：

```text
小规模运行图重排 + 遗传算法或强化学习
```

最小场景：

```text
3 个车站
2-4 列车
1 个区间临时限速或封锁
可调整停站时间和发车时间
```

评价指标：

```text
总延误
最大单车延误
运行图偏离
乘客等待时间
重排计算时间
冲突次数
```

## 14. OAI-10 客流感知发车间隔与跳停优化

### 14.1 真实需求

客流不均衡时，固定发车间隔可能造成部分车站拥挤、部分列车空载。跳停、短线折返、调整发车间隔可以提高运输效率，但会影响公平性和等待时间。

### 14.2 优化问题

根据各站客流预测，动态调整发车间隔、停站方案或跳停策略，使乘客等待、车厢拥挤和运行图扰动综合最小。

### 14.3 AI 方法

```text
遗传算法搜索跳停模式
强化学习调整发车间隔
多智能体强化学习控制多站策略
客流预测 + 优化调度
```

目标函数：

```text
J = w1 * average_waiting_time
  + w2 * overcrowding_penalty
  + w3 * skipped_passenger_penalty
  + w4 * timetable_deviation
```

实训最小版本：

```text
两种策略对比：
固定停站
高拥挤站优先服务/低客流站跳停
```

## 15. OAI-11 故障场景自动生成与鲁棒性测试

### 15.1 真实需求

只测试正常场景无法证明系统可靠。真实系统需要面对信号故障、供电故障、车门故障、通信延迟、低黏着、站台拥挤等扰动。

### 15.2 优化问题

自动生成最能暴露系统弱点的扰动场景，用更少的测试次数发现更多失败模式。

### 15.3 决策变量

```text
fault_type
fault_start_time
fault_position
fault_duration
fault_severity
affected_module
recovery_time
```

### 15.4 优化目标

```text
maximize failure_discovery_rate
maximize scenario_diversity
maximize worst_case_score
minimize test_count
```

### 15.5 AI 方法

```text
蒙特卡洛场景采样
遗传算法搜索高风险故障组合
主动学习选择下一批测试场景
对抗生成测试
```

实训建议：

```text
遗传算法生成扰动场景
```

个体编码：

```text
[fault_type, start_time, duration, severity, position]
```

适应度：

```text
failure_score =
  10 * overrun
  + 10 * overspeed
  + delay
  + stop_error
  + power_overload_count
```

## 16. OAI-12 扰动影响预测与恢复策略选择

### 16.1 真实需求

调度员或自动控制系统需要快速判断扰动影响：会不会晚点？会不会拥挤？是否需要跳停、扣车、限流或短线折返？

### 16.2 优化问题

根据扰动类型、位置、持续时间和当前系统状态，预测影响范围，并选择最合适的恢复策略。

### 16.3 AI 方法

```text
分类模型预测扰动等级
回归模型预测延误传播
强化学习选择恢复动作
案例推理推荐历史相似处置方案
```

输出：

```text
预计总延误
受影响列车数
受影响乘客数
建议恢复策略
```

评价指标：

```text
预测误差
策略成功率
恢复时间
延误减少率
乘客影响减少率
```

## 17. OAI-13 通信异常检测与数据质量修复

### 17.1 真实需求

仿真台和司机台通信中可能出现丢包、延迟、乱序、字段异常、状态跳变。若不检测，AI 控制器可能基于错误状态做出危险决策。

### 17.2 优化问题

实时识别通信数据异常，并尽可能修复或标记不可用数据，使控制系统在异常通信下仍然稳定。

### 17.3 AI 方法

```text
Isolation Forest
One-Class SVM
LSTM 预测下一状态
自动编码器检测异常
卡尔曼滤波修复状态
```

特征：

```text
报文间隔
序号跳变
速度变化率
位置变化率
字段缺失率
解析失败次数
```

评价指标：

```text
异常检出率
误报率
平均检测延迟
修复后状态误差
控制失败减少率
```

## 18. OAI-14 自适应仿真步长与实验调度优化

### 18.1 真实需求

批量优化和 AI 训练需要运行大量仿真。固定小步长精度高但慢，固定大步长快但可能漏掉关键事件。

### 18.2 优化问题

根据仿真状态自动调整步长和实验顺序，在保证关键事件精度的前提下减少总仿真时间。

### 18.3 AI 方法

```text
主动学习选择最有价值实验
贝叶斯优化实验参数
事件驱动自适应步长
强化学习调度仿真资源
```

实训建议：

```text
主动学习式实验选择
```

优先运行不确定性高、失败概率高或信息增益大的场景。

## 19. OAI-15 自动指标挖掘与失败原因归因

### 19.1 真实需求

仿真会产生大量数据。答辩和调试时，不仅要知道“失败了”，还要知道“为什么失败”：制动晚了、供电限制太强、站台拥挤、信号约束导致减速，还是调度冲突。

### 19.2 优化问题

从仿真记录中自动识别失败模式，解释关键影响因素，并生成改进建议。

### 19.3 AI 方法

```text
决策树分类失败原因
随机森林特征重要性
SHAP 可解释分析
聚类发现典型失败模式
规则挖掘
```

标签：

```text
停车失败
超速
供电过载
站台拥挤
调度冲突
通信异常
```

输出：

```text
失败类型
主要影响因素
相似失败案例
建议调整参数
```

## 20. OAI-16 智能告警排序与解释型驾驶/调度看板

### 20.1 真实需求

复杂仿真中告警很多，用户需要先看到最重要、最紧急、最可操作的信息。否则界面会变成日志堆积，反而影响决策。

### 20.2 优化问题

根据告警严重度、紧急程度、影响范围和可操作性，对告警进行排序并生成解释。

### 20.3 AI 方法

```text
排序学习
风险评分模型
规则 + 机器学习融合
大语言模型生成解释文本
```

评价指标：

```text
高风险告警置顶率
用户响应时间
误忽略率
解释可理解性
处置成功率
```

## 21. 最推荐的三条主线

## 21.1 主线 A：信号约束下的动态追踪间隔优化

对应模块：

```text
线路与轨道
信号与列控
调度运行图
车辆动力学
数据评估
```

核心问题：

```text
如何在保证安全制动距离的前提下缩短追踪间隔，提高线路通过能力？
```

AI 方法：

```text
轨迹预测 + 强化学习/MPC
```

最小 Demo：

```text
两列车同线运行
前车速度变化
后车根据预测前车轨迹调整目标速度
比较固定追踪间隔和动态追踪间隔
```

评价指标：

```text
平均追踪间隔
总延误
最小安全距离
急制动次数
通过能力
```

推荐程度：很高。

## 21.2 主线 B：客流预测驱动的车站限流与运行图调整

对应模块：

```text
车站与站台
调度运行图
环境扰动
数据记录
可视化
```

核心问题：

```text
如何预测站台拥挤并动态调整限流、停站时间或发车间隔？
```

AI 方法：

```text
LSTM/随机森林客流预测 + 遗传算法/强化学习控制策略
```

最小 Demo：

```text
构造 3 个车站客流
预测未来 5 分钟站台人数
当预测拥挤时启动限流或延长停站
比较固定策略和 AI 策略
```

评价指标：

```text
最大站台人数
拥挤持续时间
平均等待时间
滞留乘客数
运行图延误
```

推荐程度：很高。

## 21.3 主线 C：扰动下运行图智能重排

对应模块：

```text
调度运行图
信号列控
车站站台
环境扰动
数据记录
```

核心问题：

```text
故障或晚点发生后，如何快速生成新的运行方案，减少延误传播和乘客影响？
```

AI 方法：

```text
遗传算法/强化学习/GNN
```

最小 Demo：

```text
3 站 3 车
一个区间临时限速或封锁
AI 调整发车时间和停站时间
比较不重排、规则重排、AI 重排
```

评价指标：

```text
总延误
最大延误
运行图偏离
换乘失败数
冲突次数
重排计算时间
```

推荐程度：很高。

## 22. 适合实训的优先级排序

| 优先级 | 方向 | 原因 |
|---|---|---|
| 1 | 扰动下运行图智能重排 | 真实需求强、AI 特征明显、答辩故事完整 |
| 2 | 客流预测驱动的限流与调度 | 直观、易可视化、适合生成模拟数据 |
| 3 | 信号约束下动态追踪间隔 | 技术深度高，但需要多车与信号模型 |
| 4 | 异常驾驶行为识别 | 易实现、适合作为辅助功能 |
| 5 | 故障场景自动生成 | 对测试很有价值，适合增强鲁棒性展示 |
| 6 | 线路拓扑质量检查 | 工程基础好，但演示效果略弱 |

## 23. 推荐整合方案

如果要和已有“车辆 + 供电 + AI 停车优化”主线整合，建议选择一个扩展方向，不要全部做。

### 方案 1：运行图扰动重排扩展

项目标题：

```text
供电与信号约束下的列车运行控制及扰动重排优化
```

新增 AI：

```text
遗传算法/强化学习重排运行图
```

优点：

```text
从单车控制扩展到多车调度
工程深度明显提升
```

### 方案 2：车站客流智能联动扩展

项目标题：

```text
客流感知的列车运行与车站协同仿真优化
```

新增 AI：

```text
LSTM/随机森林预测客流
限流与停站时间优化
```

优点：

```text
场景直观
图表丰富
乘客视角强
```

### 方案 3：信号动态追踪间隔扩展

项目标题：

```text
面向线路能力提升的动态追踪间隔智能优化
```

新增 AI：

```text
前车轨迹预测
后车目标速度优化
```

优点：

```text
技术含量最高
与信号列控模块结合紧密
```

风险：

```text
需要多车仿真和安全距离模型
实现难度高于客流和调度重排
```

## 24. 最终建议

如果团队希望在已有第 2、3 模块基础上增加一个“其他模块 AI 优化亮点”，推荐优先选择：

```text
扰动下运行图智能重排
```

原因：

1. 真实业务需求强，故障、晚点、限速、封锁都是实际运营问题。
2. AI 方法自然，遗传算法、强化学习、GNN 都能合理引入。
3. 可先做小规模版本，3 站 3 车即可演示。
4. 能与车辆控制、信号约束、车站停站、环境扰动、数据评估模块联动。
5. 答辩时容易讲清楚“优化前后总延误减少多少”。

推荐最小优化模型：

```text
决策变量：
  每列车发车时间调整量
  每站停站时间调整量
  是否跳停

目标函数：
  minimize total_delay
  + passenger_waiting_time
  + timetable_deviation
  + conflict_penalty

约束：
  最小追踪间隔
  停站时间上下限
  不违反信号安全
  不超过站台容量

AI 方法：
  遗传算法第一版
  强化学习作为后续扩展
```

## 25. 参考资料

1. RailTopoModel: https://www.railtopomodel.org/
2. railML Working Groups: https://www.railml.org/en/working-groups
3. Use Case-Driven railML Development: https://www.railml.org/en/news/use-case-driven-railml-development
4. ERA ERTMS/ATO Operational Principles: https://www.era.europa.eu/sites/default/files/2024-12/index64_12e108_2_ato_operational_principles.pdf
5. ERTMS Automatic Train Operation: https://ertms.be/activities/automatic-train-operation
6. SUMO TraCI Documentation: https://sumo.dlr.de/docs/TraCI/index.html
7. Eclipse SUMO: https://eclipse.dev/sumo/
8. Dynamic headway control for capacity optimization: https://www.frontiersin.org/journals/future-transportation/articles/10.3389/ffutr.2026.1826998/full
9. Communication-Based Train Control with Dynamic Headway Optimization: https://www.mdpi.com/2076-0825/11/8/237
10. Inbound passenger flow prediction with Wave-LSTM: https://www.sciencedirect.com/science/article/abs/pii/S002002552100178X
11. IPSO-SVR urban rail passenger flow prediction: https://www.maxapress.com/article/doi/10.48130/dts-0025-0005
12. High-speed train rescheduling under disruptions: https://www.sciencedirect.com/science/article/pii/S0360835225006059
13. Reinforcement learning for timetable rescheduling under disruptions: https://www.sciencedirect.com/science/article/pii/S0360835225004759
14. GNN for railway timetable rescheduling: https://link.springer.com/article/10.1007/s40534-025-00383-7
15. Driver Advisory System LEADER Flow: https://rail.knorr-bremse.com/en/us/portfolio/products-and-systems/digital-solutions/driver-advisory-systems/
16. DAS impact on driver workload and safety: https://www.worldtransitresearch.info/research/8946/
17. Integrated RL and optimization for railway rescheduling: https://www.bartdeschutter.org/publications/24-009-integrated-reinforcement-learning-optimization/24-009-integrated-reinforcement-learning-optimization.pdf
18. Joint Optimization of Passenger Flow Control and Train Skip-Stop: https://link.springer.com/article/10.1007/s40864-025-00265-5

