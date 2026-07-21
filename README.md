# Project RailSim

面向城市轨道交通运营场景的多专业协同仿真与优化平台。

项目以 **RailSim** 为公开代号，整合列车运行、信号联锁、运行图、车站客流、牵引供电与储能等模块，用于方案验证、状态可视化和算法实验。

## 数据与项目边界

- 仓库中的线路、设备、客流和供电数据均为经过变换的仿真场景数据，不对应任何真实运营线路的完整工程配置。
- 站名、线路编号和历史文件名仅作为既有数据接口与场景兼容标识，不表示项目隶属关系，也不构成对真实线路性能的声明。
- 模型输出属于仿真结果或工程估算，只用于技术研究、功能验证和相对方案比较，不用于真实系统控制、保护整定或运营承诺。
- 提交代码、截图和报告前，请勿加入个人信息、组织内部资料、本机绝对路径、访问凭据或未经授权的原始数据。

## 仓库结构

- `bj-metro-sim/`：React + TypeScript 前端，提供线路总览、轨道级视图、运行图、客流和供电监控界面。
- `app/`：Python 后端与仿真核心，包含数据导入、API、仿真时钟、消息总线及运行记录。
- `data/`：匿名场景的静态拓扑、运行图、客流与供电配置。
- `docs/`：需求、设计、测试与项目管理文档。

## 快速启动

启动后端 API：

```bash
python -m app.api_server --host 127.0.0.1 --port 8000
```

默认场景启用运行图自动发车。后端生成往返运营任务并按计划时刻上线，前端“自动发车”控制台可查看计划、暂停或继续仿真。

启动前端：

```bash
cd bj-metro-sim
npm install
npm run dev
```

浏览器访问 `http://localhost:5173/`。

### 其他场景

指定其他场景：

```bash
python -m app.api_server --scenario data/scenarios/<scenario-file>.json --host 127.0.0.1 --port 8000
```

无需运行图但需要随场景创建固定列车时，可将 `autoSpawnTrains` 设为 `true`。

### 供电模块量化验收

```bash
python tools/run_power_acceptance.py
```

运行记录可通过 `GET /api/sim/run/export` 导出为结构化 JSON。

## 主要文档

- `docs/设计文档/轨道交通仿真系统软件设计文档.md`
- `docs/设计文档/API设计文档.md`
- `docs/设计文档/数据库设计文档.md`
- `docs/设计文档/前端接入与双层界面设计.md`
- `docs/设计文档/成员B车辆动力学与ATO说明.md`
- `docs/测试与验收/供电仿真模块量化验收报告.md`
- `docs/项目管理/五人分工与执行规范.md`

## 技术栈

- 前端：React、TypeScript、Vite、MapLibre、Ant Design、Zustand
- 后端：Python、SQLite、HTTP API、消息总线
- 数据：变换后的匿名线路场景、站点映射和轨道级静态拓扑
