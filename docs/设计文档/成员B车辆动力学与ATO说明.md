# 成员 B 车辆动力学与 ATO 说明

版本：v0.1
日期：2026-07-08
对应分支：`feature/dynamics`
对应提交：`0e2892f feat(vehicle): add ATO dynamics demo`

## 1. 模块目标

成员 B 当前负责车辆动力学、ATO 自动停车、司机台控制命令合成，以及后续车辆平台/RT-LAB 对照适配。

本阶段先完成可独立运行的最小闭环：

```text
ATOController
  -> ControlCommand
  -> SimpleVehicleModel
  -> TrainState
  -> 停车误差/速度/加速度/能耗指标
```

该闭环不依赖实验室 RT-LAB 设备。RT-LAB 后续作为平台车辆模型对照或 Mock 适配器接入，不作为本阶段必需条件。

## 2. 代码位置

| 文件 | 作用 |
|---|---|
| `app/domain/vehicle/models.py` | 车辆参数、列车状态、控制命令等基础数据结构 |
| `app/domain/vehicle/services.py` | `SimpleVehicleModel` 一维车辆动力学模型 |
| `app/domain/control/models.py` | ATO 参数、目标停车点、运行模式 |
| `app/domain/control/services.py` | `ATOController` 与 `CabControlService` |
| `app/domain/control/scenarios.py` | 单车 ATO 自动停车演示场景 |
| `app/main.py` | `vehicle-demo` CLI 入口 |
| `tests/test_vehicle_models.py` | 车辆模型单元测试 |
| `tests/test_control_services.py` | ATO、司机台命令合成、自动停车闭环测试 |

## 3. 核心数据模型

### 3.1 VehicleConfig

`VehicleConfig` 表示车辆物理参数和控制能力：

| 字段 | 默认值 | 单位/含义 |
|---|---:|---|
| `train_id` | `T001` | 列车编号 |
| `mass_kg` | `180000.0` | 列车质量，kg |
| `max_speed_mps` | `22.22` | 最大速度，m/s，约 80 km/h |
| `max_traction_level` | `5` | 最大牵引级位 |
| `max_brake_level` | `5` | 最大常用制动级位 |
| `traction_force_per_level_n` | `20000.0` | 每级牵引力，N |
| `brake_force_per_level_n` | `25000.0` | 每级常用制动力，N |
| `emergency_brake_force_n` | `180000.0` | 紧急制动力，N |
| `basic_resistance_n` | `3000.0` | 基础运行阻力，N |
| `stop_speed_threshold_mps` | `0.05` | 判定停车的速度阈值，m/s |

这些参数用于第一版简化模型，不代表某一真实车型的精确标定值。后续可使用车辆平台或 RT-LAB 输出对牵引、制动、阻力参数进行校准。

### 3.2 TrainState

`TrainState` 表示某一 tick 的列车状态：

| 字段 | 含义 |
|---|---|
| `train_id` | 列车编号 |
| `position_m` | 一维线路位置，m |
| `speed_mps` | 当前速度，m/s |
| `acceleration_mps2` | 当前加速度，m/s² |
| `sim_time_s` | 仿真时间，s |
| `segment_id` | 可选轨道 Seg ID |
| `net_energy_kwh` | 当前累计牵引能耗，kWh |

### 3.3 ControlCommand

`ControlCommand` 表示当前 tick 的车辆控制命令：

| 字段 | 含义 |
|---|---|
| `train_id` | 列车编号 |
| `traction_level` | 牵引级位 |
| `brake_level` | 制动级位 |
| `emergency_brake` | 是否紧急制动 |
| `source` | 命令来源：`MANUAL`、`ATO`、`ATP_OVERRIDE`、`PLATFORM`、`MOCK` |

约束：

- 牵引和制动不能同时激活。
- 紧急制动时牵引级位必须为 0。
- 速度、位置、时间、能耗等非负字段会做基础校验。

## 4. 车辆动力学模型

当前实现为一维纵向动力学模型：

```text
traction_force = traction_level * traction_force_per_level_n * traction_limit_ratio
brake_force = brake_level * brake_force_per_level_n

if emergency_brake:
    traction_force = 0
    brake_force = emergency_brake_force_n

net_force = traction_force - brake_force - resistance_force - gradient_force
acceleration = net_force / mass_kg
speed_next = clamp(speed + acceleration * dt, 0, max_speed_mps)
position_next = position + average_speed * dt
```

当前阻力模型使用 `basic_resistance_n`，坡度力通过 `gradient_force_n` 参数预留，供后续接线路坡度或更高保真模型。

牵引限功率/限牵通过 `traction_limit_ratio` 表示，取值范围为 0 到 1。供电模块可在后续将欠压或限牵结果转成该参数传入车辆模型。

## 5. ATO 自动停车逻辑

`ATOController` 输入当前 `TrainState` 和目标 `AtoTarget`，输出 `ControlCommand`。

第一版规则：

```text
distance = target_position - current_position
brake_distance = speed^2 / (2 * expected_deceleration)

if emergency_brake_required:
    emergency_brake = true
elif distance <= stop_tolerance and speed <= stop_speed_threshold:
    hold brake
elif distance <= brake_distance + brake_margin:
    brake
elif speed < min(target_cruise_speed, permitted_speed):
    traction
else:
    coast
```

默认 ATO 参数：

| 字段 | 默认值 | 含义 |
|---|---:|---|
| `target_cruise_speed_mps` | `12.0` | 目标巡航速度 |
| `expected_deceleration_mps2` | `0.8` | 预期制动减速度 |
| `brake_margin_m` | `20.0` | 制动提前量 |
| `stop_tolerance_m` | `1.0` | 允许停车误差 |
| `hold_brake_level` | `1` | 到位后的保持制动 |
| `max_traction_level` | `4` | ATO 最大牵引级位 |
| `max_brake_level` | `4` | ATO 最大制动级位 |

演示场景中将 `expected_deceleration_mps2` 设置为 `0.6`，用于匹配当前简化车辆模型的制动能力，使停车误差稳定控制在 1 m 范围内。

## 6. 司机台命令合成

`CabControlService` 用于合成人工命令、ATO 命令和 ATP 覆盖命令。

当前优先级：

```text
ATP emergency override
  > ATO command
  > manual command
  > coast
```

当 `atp_emergency_brake = true` 时，最终输出：

```text
traction_level = 0
brake_level = 0
emergency_brake = true
source = ATP_OVERRIDE
```

后续接入司机台 PLC 时，PLC 主手柄状态、牵引级位、制动级位和紧急制动按钮可先转换为 `ControlCommand` 或 `DriverInput`，再由该服务合成最终命令。

## 7. 演示命令

在仓库根目录运行：

```bash
python -m app.main vehicle-demo --target-position 200
```

当前输出示例：

```json
{
  "ok": true,
  "train_id": "T001",
  "target_position_m": 200.0,
  "final_position_m": 199.611,
  "stop_error_m": -0.389,
  "final_speed_mps": 0.0,
  "run_time_s": 45.0,
  "max_speed_mps": 8.328,
  "max_abs_acceleration_mps2": 0.572,
  "command_switches": 1,
  "net_energy_kwh": 1.842392,
  "ticks": 45,
  "status": "STOPPED_AT_TARGET"
}
```

如需查看每 tick 的位置、速度、加速度和命令级位：

```bash
python -m app.main vehicle-demo --target-position 200 --include-history
```

## 8. 测试覆盖

运行全部测试：

```bash
python -m unittest discover -s tests
```

当前验证点：

| 测试 | 覆盖内容 |
|---|---|
| `test_vehicle_config_defaults` | 车辆默认参数可用 |
| `test_train_state_rejects_negative_speed` | 非法负速度被拒绝 |
| `test_control_command_rejects_conflicting_levels` | 牵引/制动不能同时激活 |
| `test_vehicle_accelerates_with_traction_command` | 牵引命令能使车辆加速 |
| `test_vehicle_brakes_to_stop_without_negative_speed` | 制动不会产生负速度 |
| `test_emergency_brake_is_stronger_than_service_brake` | 紧急制动强于常用制动 |
| `test_power_limit_reduces_acceleration` | 限牵会降低加速度 |
| `test_ato_vehicle_loop_stops_near_target` | ATO + 车辆模型闭环能在目标点附近停车 |
| `test_cab_atp_emergency_overrides_ato` | ATP 紧急制动覆盖 ATO 命令 |
| `test_vehicle_demo_returns_reproducible_summary` | CLI demo 场景输出可复现摘要 |

最近一次测试结果：

```text
Ran 25 tests
OK
```

## 9. 与 RT-LAB/平台接口的关系

当前模块默认使用 `SimpleVehicleModel` 自研模型运行，不要求真实 RT-LAB 环境。

后续可扩展三类车辆模型：

| 模型 | 作用 |
|---|---|
| `SimpleVehicleModel` | 本阶段自研简化模型，适合开发、测试、演示 |
| `MockRtLabVehicleModel` | 不连接设备，只按协议模拟速度、加速度、累计里程 |
| `PlatformVehicleModel` | 通过车辆 UDP 或 RT-LAB API 与实验室平台交互 |

协议映射建议：

| 内部字段 | 平台/RT-LAB 字段 |
|---|---|
| `ControlCommand.traction_level > 0` | 指令 `1`，表示加速 |
| `ControlCommand.brake_level > 0` | 指令 `2`，表示减速 |
| 牵引/制动均为 0 | 指令 `0`，表示惰行 |
| 级位比例 | 加减速百分比 |
| 平台速度 | `TrainState.speed_mps` |
| 平台加速度 | `TrainState.acceleration_mps2` |
| 平台累计里程 | `TrainState.position_m` |

注意：协议中的 `PowerSystemAndTrainsV1/...` 变量路径不能直接解释为完整供电仿真接口。当前能明确对接的是车辆控制与车辆状态字段；牵引供电网络、电压、电流、潮流和再生能量回馈仍应由自研供电模块或后续补充协议处理。

## 10. 后续任务

建议按以下顺序继续：

1. 接入 `RunRecorder`，记录每 tick 的 `TrainState`、`ControlCommand` 和指标。
2. 增加 `DriverInput` 数据结构，为司机台 PLC 输入做准备。
3. 增加 RT-LAB/车辆 UDP Mock 编解码测试，先验证字段映射。
4. 将 `vehicle-demo` 结果接入后端 API 或前端曲线展示。
5. 使用真实车辆平台或 Java demo 输出标定车辆参数。
