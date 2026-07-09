# BJTUMetroSim

北京交通大学软件工程实训轨道交通仿真系统。

本仓库整合三部分内容：

- `bj-metro-sim/`：React + TypeScript 前端，包含北京地铁 9 号线宏观线路图与微观轨道级界面。
- `app/`：Python 后端与仿真基础框架，包含线路数据导入、API 服务、仿真时钟、消息总线和 Recorder。
- `docs/`：需求调研、软件设计、数据库设计、API 设计、分工与执行规范等项目文档。

## 快速启动前端

```bash
cd bj-metro-sim
npm install
npm run dev
```

浏览器访问：

```text
http://localhost:5173/
```

## 快速启动后端 API

在仓库根目录运行：

```bash
python -m app.api_server --host 127.0.0.1 --port 8000
```

前端默认通过后端 API 获取 9 号线静态数据和仿真状态。

## 主要文档

- `docs/设计文档/轨道交通仿真系统软件设计文档.md`
- `docs/设计文档/API设计文档.md`
- `docs/设计文档/数据库设计文档.md`
- `docs/设计文档/前端接入与双层界面设计.md`
- `docs/设计文档/成员B车辆动力学与ATO说明.md`
- `docs/项目管理/五人分工与执行规范.md`

## 技术栈

- 前端：React、TypeScript、Vite、MapLibre、Ant Design、Zustand
- 后端：Python、SQLite、HTTP API、消息总线
- 数据：北京地铁 9 号线线路数据、站点映射、轨道级静态拓扑
