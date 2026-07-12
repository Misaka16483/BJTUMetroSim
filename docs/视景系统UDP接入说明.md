# 视景系统 UDP 接入说明

## 1. 当前实现边界

本分支实现《轨交多系统平台接口协议汇总》中的北京地铁 9 号线视景 Version 1.3 输出接口：仿真引擎状态经过协议映射后，每 100 ms 通过 UDP 发送到视景控制计算机。

已实现：

1. 77 架信号机和 29 组道岔的协议固定顺序。
2. 本车速度、发车倒计时、运行工况、牵引/制动百分比、EdgeID、区段内距离和方向。
3. 最多 128 列他车的位置、边号、方向和速度。
4. 显式小端编码，不依赖运行主机的字节序或 C 结构体对齐。
5. `compact` 变长数组布局和 `fixed` 固定 C 结构布局。
6. 100 ms 后台发布、状态统计、错误重试、CLI 单帧发送和 UDP 接收解码工具。
7. 后端 API 动态连接、断开、状态查询和协议 ID 到当前电子地图 ID 的覆盖配置。

尚需实验室实机确认：

1. 视景控制机最终使用 `compact` 还是 `fixed`。任务示例调用 `BuildCommunicationPacket`，对应 `compact`；协议附件的 `strTCMS2VIEW` 对应 `fixed`。
2. Vision 1.3 的 `RunState` 字节到底仍使用 `0x11/0x12/0x13` 工况，还是现场程序已将其完全改作头灯控制。当前按协议表中的工况编码发送。
3. 当前电子地图和旧视景协议使用不同的信号/道岔编号。程序通过公里标和方向自动匹配；当前数据可匹配 33/77 架信号和 9/29 组道岔。未匹配信号发送红灯，未匹配道岔发送定位，可在联调时通过 API 覆盖。

## 2. 默认网络参数

| 参数 | 默认值 | 说明 |
|---|---:|---|
| 视景控制机 | `18.32.115.28` | 协议给出的控制计算机 IP |
| 目标端口 | `8303` | 视景控制机接收端口 |
| 本地地址 | `0.0.0.0` | 由操作系统选择本机接口 |
| 本地源端口 | `8302` | 协议给出的 ATS 发送端口 |
| 周期 | `100 ms` | 协议规定交换间隔 |
| 默认布局 | `compact` | 128 字节基准帧；每列他车增加 9 字节 |
| 固定布局 | `fixed` | 1556 字节 `strTCMS2VIEW` 帧 |

实验室计算机若确实配置为 `18.32.115.27`，可把 `--vision-local-host` 设置为该地址；本机没有该地址时不要强制绑定，否则会进入 `RETRYING` 并在状态接口显示绑定错误。

## 3. 无硬件验证

生成一帧但不打开 UDP：

```bash
python -m app.main vision-send-demo --dry-run
python -m app.main vision-send-demo --layout fixed --dry-run
```

预期输出分别包含：

```text
vision-v1.3-compact bytes=128
vision-v1.3-fixed bytes=1556
```

在一个终端启动本地接收器：

```bash
python -m app.main vision-receive \
  --host 127.0.0.1 \
  --port 18303 \
  --max-frames 1
```

在另一个终端发送测试帧：

```bash
python -m app.main vision-send-demo \
  --host 127.0.0.1 \
  --port 18303 \
  --local-port 0 \
  --speed-mps 12.5 \
  --edge-id 21 \
  --section-distance-m 725.16
```

接收器会输出报文长度、计数、速度、EdgeID、区段距离、方向及他车数量，解析失败时直接报错，不会把错误报文当作成功。

## 4. 随后端自动启动

```bash
python app/api_server.py \
  --scenario data/scenarios/line9_single.json \
  --vision-enabled \
  --vision-host 18.32.115.28 \
  --vision-port 8303 \
  --vision-local-port 8302 \
  --vision-layout compact \
  --vision-train-id T0901
```

视景发布线程读取引擎的最新原子快照，不直接修改仿真状态。仿真暂停时仍周期发送最后一帧状态，保证视景端能够持续检测通信存活。

## 5. 运行时 API

查询状态：

```bash
curl http://127.0.0.1:8000/api/hardware/vision/status
```

动态连接：

```bash
curl -X POST http://127.0.0.1:8000/api/hardware/vision/connect \
  -H 'Content-Type: application/json' \
  -d '{
    "remoteHost": "18.32.115.28",
    "remotePort": 8303,
    "localHost": "0.0.0.0",
    "localPort": 8302,
    "intervalMs": 100,
    "layout": "compact",
    "primaryTrainId": "T0901"
  }'
```

断开：

```bash
curl -X POST http://127.0.0.1:8000/api/hardware/vision/disconnect
```

状态中的关键字段：

| 字段 | 含义 |
|---|---|
| `state` | `DISCONNECTED`、`STARTING`、`CONNECTED` 或 `RETRYING` |
| `framesSent` | 已成功发送的数据报数量 |
| `lastFrameSize` | 最近一帧长度 |
| `lastError` | 最近一次绑定、编码或发送错误 |
| `mapping.mappedSignalCount` | 已映射到当前电子地图的协议信号数量 |
| `mapping.mappedSwitchCount` | 已映射到当前电子地图的协议道岔数量 |

## 6. 现场映射校准

联调确定旧协议编号与当前电子地图 ID 的对应关系后，可在连接请求中覆盖自动映射。键是协议中的十六进制编号字符串，值是当前 `/api/sim/interlocking/state` 返回的 `signalId` 或 `switchId`：

```json
{
  "remoteHost": "18.32.115.28",
  "signalSourceMap": {
    "0121": 57,
    "0120": 58
  },
  "switchSourceMap": {
    "0101": 11,
    "0102": 12
  }
}
```

覆盖配置只影响协议输出映射，不改变引擎内部信号与联锁计算。完整联调时应依次核对：数据报计数、信号/道岔顺序、速度单位、EdgeID、区段内距离、方向、他车显示和 100 ms 周期。
