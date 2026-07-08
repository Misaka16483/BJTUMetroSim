# MetroDynamicsJava

一个独立的 Java 轨道交通车辆动力学仿真示例。

## 输入数据

`data/stations.csv` 使用从线路表中抽取并简化后的字段：

- `id`: 车站编号
- `code`: 车站缩写
- `name`: 车站名称
- `mileage_m`: 站台中心公里标，单位 m
- `speed_limit_to_next_kmh`: 本站到下一站的简化限速，单位 km/h
- `dwell_s`: 到达该站后的停站时间，单位 s

`data/grades.csv` 是简化坡度输入：

- `start_m`: 坡段起点里程，单位 m
- `end_m`: 坡段终点里程，单位 m
- `grade_promille`: 坡度，单位 ‰。正值表示沿仿真方向上坡，负值表示下坡

## 车辆动力学模型

仿真采用一维纵向动力学。每个时间步根据受力计算加速度：

```text
F_net = F_traction - F_brake - F_resistance - F_grade
a = F_net / M_equivalent
v_next = v + a * dt
x_next = x + (v + v_next) / 2 * dt
```

其中：

```text
M_equivalent = train_mass * rotating_mass_factor
F_resistance = A + B*v + C*v^2
F_grade = train_mass * g * grade_promille / 1000
```

默认车辆参数在 `Main.java` 中设置：

- 最大速度：80 km/h
- 列车质量：240 t
- 回转质量系数：1.08
- 最大牵引力：280 kN
- 最大牵引功率：3.2 MW
- 最大常用制动力：260 kN
- 最大再生制动功率：2.4 MW
- 再生制动可回收效率：75%
- 辅助系统功率：120 kW
- 运行阻力：`4500 + 120v + 18v^2`
- 仿真步长：1 s

列车运行阶段：

- `ACCEL`: 牵引加速，牵引力受最大牵引力和最大功率共同限制
- `CRUISE`: 接近限速时维持巡航，按阻力和坡度补偿牵引/制动
- `BRAKE`: 到站前按制动力制动
- `ARRIVE`: 到站
- `DWELL`: 停站

## 运行

```bash
javac -encoding UTF-8 -d out src/com/bjtu/metro/*.java
java -cp out com.bjtu.metro.Main
```

也可以指定输入和输出：

```bash
java -cp out com.bjtu.metro.Main data/stations.csv output/simulation_result.csv data/grades.csv
```

## 输出

程序会生成：

`output/simulation_result.csv`

字段：

- `time_s`: 仿真时间
- `position_m`: 列车位置
- `speed_mps`: 速度
- `speed_kmh`: 速度，单位 km/h
- `acceleration_mps2`: 加速度
- `grade_promille`: 当前坡度
- `traction_force_n`: 牵引力
- `brake_force_n`: 制动力
- `resistance_force_n`: 运行阻力
- `traction_energy_kwh`: 累计牵引电能
- `regen_energy_kwh`: 累计再生制动回收电能
- `auxiliary_energy_kwh`: 累计辅助系统能耗
- `net_energy_kwh`: 净电能消耗，等于牵引电能 + 辅助能耗 - 再生回收
- `phase`: ACCEL / CRUISE / BRAKE / ARRIVE / DWELL
- `from_station`: 当前区间起点站
- `to_station`: 当前区间终点站

## 仍然保留的简化

- 只模拟一列车从郭公庄开往国家图书馆。
- 线路按一维坐标处理，没有几何曲线半径。
- 坡度数据使用简化 CSV，未直接解析原始 Excel 的全部坡度表。
- 暂不考虑信号机、道岔、计轴区段占用和多列车追踪。
