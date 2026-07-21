# Project RailSim Web Console

Project RailSim 的城市轨道交通综合仿真前端，提供线路总览、列车运行、信号联锁、运行图、客流和牵引供电等可视化与控制功能。

## 本地开发

```bash
npm install
npm run dev
```

默认开发地址为 `http://localhost:5173/`，后端 API 默认由 Vite 代理至 `http://127.0.0.1:8000`。

## 检查与构建

```bash
npm run lint
npm run build
```

## 数据边界

界面展示的是匿名、变换后的场景数据。线路编号、站点代号和历史文件名仅用于接口兼容，不代表真实线路或项目隶属关系，仿真结果也不应解释为真实运营指标。
