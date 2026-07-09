from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.domain.control import run_ato_stop_demo  # noqa: E402


DEFAULT_OUTPUT = ROOT / "outputs" / "ato_speed_curve.html"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render an interactive ATO target/actual speed comparison page.")
    parser.add_argument("--target-position", type=float, default=200.0, help="ATO stopping target position in meters.")
    parser.add_argument("--permitted-speed", type=float, default=12.0, help="Permitted speed in m/s.")
    parser.add_argument("--dt", type=float, default=1.0, help="Simulation step in seconds.")
    parser.add_argument("--max-ticks", type=int, default=120, help="Maximum simulation ticks.")
    parser.add_argument("--expected-deceleration", type=float, default=0.6, help="Expected deceleration in m/s^2.")
    parser.add_argument("--stop-tolerance", type=float, default=1.0, help="Allowed stop error in meters.")
    parser.add_argument("--train-id", default="T001", help="Train id used in the report.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output HTML file.")
    return parser.parse_args()


def build_report(payload: dict[str, Any]) -> str:
    data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return HTML_TEMPLATE.replace("__REPORT_DATA__", data_json)


def main() -> None:
    args = parse_args()
    result = run_ato_stop_demo(
        target_position_m=args.target_position,
        permitted_speed_mps=args.permitted_speed,
        dt_s=args.dt,
        max_ticks=args.max_ticks,
        expected_deceleration_mps2=args.expected_deceleration,
        stop_tolerance_m=args.stop_tolerance,
        train_id=args.train_id,
    )
    payload = result.to_dict(include_history=True)
    payload["scenario"] = {
        "targetPositionM": args.target_position,
        "permittedSpeedMps": args.permitted_speed,
        "dtS": args.dt,
        "maxTicks": args.max_ticks,
        "expectedDecelerationMps2": args.expected_deceleration,
        "stopToleranceM": args.stop_tolerance,
        "trainId": args.train_id,
    }
    payload["generatedAt"] = datetime.now().isoformat(timespec="seconds")

    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_report(payload), encoding="utf-8")
    print(output)


HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ATO 速度曲线对比</title>
  <style>
    :root {
      --bg: #f4f5f2;
      --panel: #ffffff;
      --ink: #1f2523;
      --muted: #66716c;
      --grid: #d9ded9;
      --axis: #505a55;
      --target: #087f7b;
      --actual: #c2512a;
      --traction: #2f8c57;
      --brake: #c56a20;
      --pid: #6b5fb5;
      --danger: #b91c1c;
      --ok: #23744b;
      --border: #d7ddd7;
      --shadow: 0 14px 30px rgba(36, 45, 42, 0.08);
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }

    .shell {
      max-width: 1280px;
      margin: 0 auto;
      padding: 22px;
    }

    header {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: end;
      gap: 16px;
      margin-bottom: 16px;
    }

    h1 {
      margin: 0;
      font-size: 26px;
      line-height: 1.16;
      font-weight: 760;
    }

    .subtitle {
      margin: 7px 0 0;
      color: var(--muted);
      font-size: 13px;
    }

    .status {
      display: flex;
      align-items: center;
      gap: 8px;
      border: 1px solid var(--border);
      background: var(--panel);
      padding: 9px 12px;
      border-radius: 8px;
      box-shadow: var(--shadow);
      font-size: 13px;
      color: var(--muted);
      white-space: nowrap;
    }

    .status strong {
      color: var(--ok);
      font-size: 14px;
    }

    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 330px;
      gap: 16px;
      align-items: start;
    }

    .panel {
      border: 1px solid var(--border);
      background: var(--panel);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }

    .chart-panel {
      padding: 14px;
    }

    .toolbar {
      display: grid;
      grid-template-columns: auto auto minmax(180px, 1fr) auto;
      gap: 10px;
      align-items: center;
      margin-bottom: 12px;
    }

    button {
      appearance: none;
      border: 1px solid #bdc7bf;
      background: #f9faf7;
      color: var(--ink);
      min-height: 34px;
      padding: 0 12px;
      border-radius: 7px;
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
    }

    button:hover {
      background: #eef3ee;
    }

    .speed-control {
      display: flex;
      align-items: center;
      gap: 7px;
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }

    input[type="range"] {
      width: 100%;
      accent-color: var(--target);
    }

    .time-readout {
      color: var(--ink);
      font-variant-numeric: tabular-nums;
      font-weight: 760;
      min-width: 92px;
      text-align: right;
    }

    .legend {
      display: flex;
      flex-wrap: wrap;
      gap: 10px 18px;
      margin: 8px 0 12px;
      color: var(--muted);
      font-size: 12px;
    }

    .legend-item {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      white-space: nowrap;
    }

    .swatch {
      width: 20px;
      height: 3px;
      border-radius: 99px;
      background: var(--ink);
    }

    .swatch.target {
      background: var(--target);
    }

    .swatch.actual {
      background: var(--actual);
    }

    .swatch.traction {
      background: var(--traction);
    }

    .swatch.brake {
      background: var(--brake);
    }

    .chart-stack {
      display: grid;
      gap: 12px;
    }

    .chart-title {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 12px;
      margin: 2px 0 5px;
      font-size: 13px;
      font-weight: 760;
    }

    .chart-title span {
      color: var(--muted);
      font-size: 12px;
      font-weight: 500;
    }

    svg {
      display: block;
      width: 100%;
      height: auto;
      overflow: visible;
    }

    .plot {
      background: #fbfcfa;
      border: 1px solid #e1e6e0;
      border-radius: 6px;
    }

    .grid-line {
      stroke: var(--grid);
      stroke-width: 1;
    }

    .axis-line {
      stroke: var(--axis);
      stroke-width: 1.2;
    }

    .axis-label {
      fill: var(--muted);
      font-size: 11px;
      font-variant-numeric: tabular-nums;
    }

    .target-path {
      fill: none;
      stroke: var(--target);
      stroke-width: 3;
      stroke-linejoin: round;
      stroke-linecap: round;
    }

    .actual-path {
      fill: none;
      stroke: var(--actual);
      stroke-width: 3;
      stroke-linejoin: round;
      stroke-linecap: round;
    }

    .permitted-line {
      stroke: #747d77;
      stroke-width: 1.5;
      stroke-dasharray: 7 6;
    }

    .cursor {
      stroke: #252a27;
      stroke-width: 1.2;
      stroke-dasharray: 4 5;
    }

    .dot-target {
      fill: var(--target);
      stroke: #fff;
      stroke-width: 2;
    }

    .dot-actual {
      fill: var(--actual);
      stroke: #fff;
      stroke-width: 2;
    }

    .command-svg {
      background: #fbfcfa;
      border: 1px solid #e1e6e0;
      border-radius: 6px;
      height: 104px;
    }

    .side {
      display: grid;
      gap: 12px;
    }

    .metrics {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      padding: 12px;
    }

    .metric {
      border: 1px solid #dbe2db;
      border-radius: 7px;
      padding: 10px;
      min-height: 74px;
      background: #fbfcfa;
    }

    .metric .label {
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 6px;
    }

    .metric .value {
      font-size: 22px;
      line-height: 1.05;
      font-weight: 790;
      font-variant-numeric: tabular-nums;
      overflow-wrap: anywhere;
    }

    .metric .unit {
      color: var(--muted);
      font-size: 12px;
      margin-left: 3px;
      font-weight: 600;
    }

    .inspector {
      padding: 12px;
    }

    .inspector h2 {
      margin: 0 0 10px;
      font-size: 15px;
      line-height: 1.2;
    }

    .row {
      display: grid;
      grid-template-columns: 110px minmax(0, 1fr);
      gap: 8px;
      padding: 7px 0;
      border-top: 1px solid #edf0ed;
      font-size: 13px;
    }

    .row:first-of-type {
      border-top: 0;
    }

    .row span:first-child {
      color: var(--muted);
    }

    .row span:last-child {
      text-align: right;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
      overflow-wrap: anywhere;
    }

    .mode-pill {
      justify-self: end;
      display: inline-flex;
      min-width: 64px;
      justify-content: center;
      border-radius: 999px;
      padding: 3px 9px;
      color: #fff;
      background: var(--axis);
      font-size: 12px;
      font-weight: 760;
    }

    .mode-TRACTION {
      background: var(--traction);
    }

    .mode-BRAKE {
      background: var(--brake);
    }

    .mode-EMERGENCY_BRAKE {
      background: var(--danger);
    }

    .mode-COAST {
      background: #6f766f;
    }

    .note {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.55;
      padding: 12px;
    }

    @media (max-width: 980px) {
      .shell {
        padding: 14px;
      }

      header,
      .layout {
        grid-template-columns: 1fr;
      }

      .status {
        justify-content: space-between;
      }

      .toolbar {
        grid-template-columns: auto auto 1fr;
      }

      .time-readout {
        grid-column: 1 / -1;
        text-align: left;
      }
    }

    @media (max-width: 640px) {
      h1 {
        font-size: 21px;
      }

      .toolbar {
        grid-template-columns: 1fr 1fr;
      }

      .speed-control {
        grid-column: 1 / -1;
      }

      .metrics {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div>
        <h1>ATO 目标速度 / 实际速度曲线对比</h1>
        <p class="subtitle" id="subtitle"></p>
      </div>
      <div class="status">
        <span>运行状态</span>
        <strong id="statusText"></strong>
      </div>
    </header>

    <main class="layout">
      <section class="panel chart-panel">
        <div class="toolbar">
          <button id="playButton" type="button">播放</button>
          <button id="replayButton" type="button">重放</button>
          <div class="speed-control">
            <span>速度</span>
            <input id="rateSlider" type="range" min="0.25" max="4" step="0.25" value="1">
            <span id="rateLabel">1.00x</span>
          </div>
          <div class="time-readout" id="timeReadout"></div>
        </div>

        <input id="scrubber" type="range" min="0" max="0" step="1" value="0" aria-label="仿真时间轴">

        <div class="legend">
          <span class="legend-item"><span class="swatch target"></span>生成目标速度</span>
          <span class="legend-item"><span class="swatch actual"></span>ATO 实际速度</span>
          <span class="legend-item"><span class="swatch traction"></span>牵引百分比</span>
          <span class="legend-item"><span class="swatch brake"></span>制动百分比</span>
        </div>

        <div class="chart-stack">
          <div>
            <div class="chart-title">速度-时间曲线 <span>检查 PID 跟踪与制动切换时刻</span></div>
            <svg id="timeChart" class="plot" viewBox="0 0 900 300" role="img" aria-label="速度随时间变化曲线"></svg>
          </div>
          <div>
            <div class="chart-title">速度-位置曲线 <span>检查停车点附近目标曲线收敛</span></div>
            <svg id="positionChart" class="plot" viewBox="0 0 900 300" role="img" aria-label="速度随位置变化曲线"></svg>
          </div>
          <div>
            <div class="chart-title">控制输出百分比 <span>牵引和制动按接口文档的百分比显示</span></div>
            <svg id="commandChart" class="command-svg" viewBox="0 0 900 104" role="img" aria-label="牵引制动百分比"></svg>
          </div>
        </div>
      </section>

      <aside class="side">
        <section class="panel metrics" id="metrics"></section>
        <section class="panel inspector">
          <h2>当前采样点</h2>
          <div class="row"><span>时间</span><span id="curTime"></span></div>
          <div class="row"><span>位置</span><span id="curPos"></span></div>
          <div class="row"><span>目标速度</span><span id="curTarget"></span></div>
          <div class="row"><span>实际速度</span><span id="curActual"></span></div>
          <div class="row"><span>速度误差</span><span id="curError"></span></div>
          <div class="row"><span>目标工况</span><span id="curTargetMode"></span></div>
          <div class="row"><span>PID 输出</span><span id="curPid"></span></div>
          <div class="row"><span>牵引</span><span id="curTraction"></span></div>
          <div class="row"><span>制动</span><span id="curBrake"></span></div>
          <div class="row"><span>模式</span><span id="curMode" class="mode-pill"></span></div>
        </section>
        <section class="panel note" id="note"></section>
      </aside>
    </main>
  </div>

  <script>
    const report = __REPORT_DATA__;
    const history = report.history || [];
    const scenario = report.scenario || {};
    const chartBox = { width: 900, height: 300, left: 58, right: 22, top: 18, bottom: 42 };
    const commandBox = { width: 900, height: 104, left: 58, right: 22, top: 14, bottom: 24 };
    const state = { index: 0, playing: false, rate: 1, lastFrame: 0, carry: 0 };

    const fmt = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 3 });
    const fmt1 = new Intl.NumberFormat("zh-CN", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
    const svgNS = "http://www.w3.org/2000/svg";

    const maxTime = Math.max(1, ...history.map((d) => d.simTimeS || 0));
    const maxPosition = Math.max(scenario.targetPositionM || report.target_position_m || 1, ...history.map((d) => d.positionM || 0));
    const permittedSpeed = scenario.permittedSpeedMps || 0;
    const maxSpeed = Math.max(1, permittedSpeed, ...history.flatMap((d) => [d.speedMps || 0, d.targetSpeedMps || 0])) * 1.12;
    const maxCommand = 100;

    document.getElementById("subtitle").textContent =
      `${report.train_id || scenario.trainId || "T001"} · 目标 ${fmt.format(report.target_position_m ?? scenario.targetPositionM ?? 0)} m · 许可速度 ${fmt.format(permittedSpeed)} m/s · 生成 ${history.length} 个采样点`;
    document.getElementById("statusText").textContent = report.status || "UNKNOWN";
    document.getElementById("note").textContent =
      `页面数据来自当前 Python ATO 仿真历史。目标速度是速度曲线生成器输出，实际速度是车辆动力学模型在 ATO 百分比牵引/制动命令下的响应。生成时间：${report.generatedAt || ""}`;

    const scrubber = document.getElementById("scrubber");
    scrubber.max = Math.max(0, history.length - 1);
    scrubber.addEventListener("input", () => {
      state.index = Number(scrubber.value);
      state.carry = 0;
      updateDynamic();
    });

    const playButton = document.getElementById("playButton");
    playButton.addEventListener("click", () => {
      state.playing = !state.playing;
      playButton.textContent = state.playing ? "暂停" : "播放";
      state.lastFrame = performance.now();
      requestAnimationFrame(tick);
    });

    document.getElementById("replayButton").addEventListener("click", () => {
      state.index = 0;
      state.carry = 0;
      scrubber.value = "0";
      updateDynamic();
      state.playing = true;
      playButton.textContent = "暂停";
      state.lastFrame = performance.now();
      requestAnimationFrame(tick);
    });

    const rateSlider = document.getElementById("rateSlider");
    rateSlider.addEventListener("input", () => {
      state.rate = Number(rateSlider.value);
      document.getElementById("rateLabel").textContent = `${state.rate.toFixed(2)}x`;
    });

    function tick(now) {
      if (!state.playing || history.length === 0) {
        return;
      }
      const elapsedMs = Math.max(0, now - state.lastFrame);
      state.lastFrame = now;
      state.carry += (elapsedMs / 1000) * state.rate;
      const dt = scenario.dtS || 1;
      while (state.carry >= dt) {
        state.index += 1;
        state.carry -= dt;
      }
      if (state.index >= history.length - 1) {
        state.index = history.length - 1;
        state.playing = false;
        playButton.textContent = "播放";
      }
      scrubber.value = String(state.index);
      updateDynamic();
      if (state.playing) {
        requestAnimationFrame(tick);
      }
    }

    function xScale(value, max, box) {
      const w = box.width - box.left - box.right;
      return box.left + (max <= 0 ? 0 : value / max) * w;
    }

    function ySpeed(value, box) {
      const h = box.height - box.top - box.bottom;
      return box.top + (1 - value / maxSpeed) * h;
    }

    function yCommand(value, box) {
      const h = box.height - box.top - box.bottom;
      return box.top + (1 - value / maxCommand) * h;
    }

    function make(tag, attrs = {}, text = "") {
      const node = document.createElementNS(svgNS, tag);
      Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
      if (text) {
        node.textContent = text;
      }
      return node;
    }

    function pathFor(points, getX, getY) {
      return points.map((d, i) => `${i === 0 ? "M" : "L"} ${getX(d).toFixed(2)} ${getY(d).toFixed(2)}`).join(" ");
    }

    function drawGrid(svg, box, xMax, xLabel, yMax, yLabel) {
      const plotW = box.width - box.left - box.right;
      const plotH = box.height - box.top - box.bottom;
      svg.innerHTML = "";
      for (let i = 0; i <= 5; i += 1) {
        const x = box.left + (plotW * i) / 5;
        const value = (xMax * i) / 5;
        svg.appendChild(make("line", { x1: x, y1: box.top, x2: x, y2: box.top + plotH, class: "grid-line" }));
        svg.appendChild(make("text", { x, y: box.height - 16, class: "axis-label", "text-anchor": "middle" }, fmt1.format(value)));
      }
      for (let i = 0; i <= 4; i += 1) {
        const y = box.top + (plotH * i) / 4;
        const value = yMax - (yMax * i) / 4;
        svg.appendChild(make("line", { x1: box.left, y1: y, x2: box.left + plotW, y2: y, class: "grid-line" }));
        svg.appendChild(make("text", { x: box.left - 10, y: y + 4, class: "axis-label", "text-anchor": "end" }, fmt1.format(value)));
      }
      svg.appendChild(make("line", { x1: box.left, y1: box.top + plotH, x2: box.left + plotW, y2: box.top + plotH, class: "axis-line" }));
      svg.appendChild(make("line", { x1: box.left, y1: box.top, x2: box.left, y2: box.top + plotH, class: "axis-line" }));
      svg.appendChild(make("text", { x: box.left + plotW / 2, y: box.height - 4, class: "axis-label", "text-anchor": "middle" }, xLabel));
      svg.appendChild(make("text", { x: 14, y: box.top + plotH / 2, class: "axis-label", "text-anchor": "middle", transform: `rotate(-90 14 ${box.top + plotH / 2})` }, yLabel));
    }

    function drawSpeedChart(svgId, xMax, xLabel, getXValue) {
      const svg = document.getElementById(svgId);
      const box = chartBox;
      drawGrid(svg, box, xMax, xLabel, maxSpeed, "速度 m/s");
      const plotW = box.width - box.left - box.right;
      const permittedY = ySpeed(permittedSpeed, box);
      svg.appendChild(make("line", {
        x1: box.left,
        y1: permittedY,
        x2: box.left + plotW,
        y2: permittedY,
        class: "permitted-line"
      }));
      svg.appendChild(make("path", {
        d: pathFor(history, (d) => xScale(getXValue(d), xMax, box), (d) => ySpeed(d.targetSpeedMps || 0, box)),
        class: "target-path"
      }));
      svg.appendChild(make("path", {
        d: pathFor(history, (d) => xScale(getXValue(d), xMax, box), (d) => ySpeed(d.speedMps || 0, box)),
        class: "actual-path"
      }));
      svg.appendChild(make("line", { id: `${svgId}Cursor`, x1: 0, y1: box.top, x2: 0, y2: box.height - box.bottom, class: "cursor" }));
      svg.appendChild(make("circle", { id: `${svgId}TargetDot`, r: 5, cx: 0, cy: 0, class: "dot-target" }));
      svg.appendChild(make("circle", { id: `${svgId}ActualDot`, r: 5, cx: 0, cy: 0, class: "dot-actual" }));
    }

    function drawCommandChart() {
      const svg = document.getElementById("commandChart");
      const box = commandBox;
      drawGrid(svg, box, maxTime, "时间 s", 100, "百分比");
      const usableW = box.width - box.left - box.right;
      const barW = Math.max(2, usableW / Math.max(1, history.length) - 1);
      history.forEach((d) => {
        const x = xScale(d.simTimeS || 0, maxTime, box);
        const tractionY = yCommand(d.tractionPercent || 0, box);
        const brakeY = yCommand(d.brakePercent || 0, box);
        svg.appendChild(make("rect", {
          x: x - barW / 2,
          y: tractionY,
          width: barW,
          height: box.height - box.bottom - tractionY,
          fill: "var(--traction)",
          opacity: 0.72
        }));
        svg.appendChild(make("rect", {
          x: x - barW / 2,
          y: brakeY,
          width: barW,
          height: box.height - box.bottom - brakeY,
          fill: "var(--brake)",
          opacity: 0.58
        }));
      });
      svg.appendChild(make("line", { id: "commandCursor", x1: 0, y1: box.top, x2: 0, y2: box.height - box.bottom, class: "cursor" }));
    }

    function renderMetrics() {
      const metrics = [
        ["停车误差", report.stop_error_m, "m"],
        ["最终速度", report.final_speed_mps, "m/s"],
        ["最大速度", report.max_speed_mps, "m/s"],
        ["运行时间", report.run_time_s, "s"],
        ["命令切换", report.command_switches, "次"],
        ["净能耗", report.net_energy_kwh, "kWh"]
      ];
      document.getElementById("metrics").innerHTML = metrics.map(([label, value, unit]) => `
        <div class="metric">
          <div class="label">${label}</div>
          <div class="value">${fmt.format(value ?? 0)}<span class="unit">${unit}</span></div>
        </div>
      `).join("");
    }

    function setText(id, value) {
      document.getElementById(id).textContent = value;
    }

    function updateDot(chartId, x, targetY, actualY) {
      const cursor = document.getElementById(`${chartId}Cursor`);
      const targetDot = document.getElementById(`${chartId}TargetDot`);
      const actualDot = document.getElementById(`${chartId}ActualDot`);
      cursor.setAttribute("x1", x);
      cursor.setAttribute("x2", x);
      targetDot.setAttribute("cx", x);
      targetDot.setAttribute("cy", targetY);
      actualDot.setAttribute("cx", x);
      actualDot.setAttribute("cy", actualY);
    }

    function updateDynamic() {
      if (history.length === 0) {
        return;
      }
      const d = history[Math.min(history.length - 1, Math.max(0, state.index))];
      const timeX = xScale(d.simTimeS || 0, maxTime, chartBox);
      const positionX = xScale(d.positionM || 0, maxPosition, chartBox);
      updateDot("timeChart", timeX, ySpeed(d.targetSpeedMps || 0, chartBox), ySpeed(d.speedMps || 0, chartBox));
      updateDot("positionChart", positionX, ySpeed(d.targetSpeedMps || 0, chartBox), ySpeed(d.speedMps || 0, chartBox));

      const commandX = xScale(d.simTimeS || 0, maxTime, commandBox);
      const commandCursor = document.getElementById("commandCursor");
      commandCursor.setAttribute("x1", commandX);
      commandCursor.setAttribute("x2", commandX);

      setText("timeReadout", `t = ${fmt.format(d.simTimeS || 0)} s`);
      setText("curTime", `${fmt.format(d.simTimeS || 0)} s`);
      setText("curPos", `${fmt.format(d.positionM || 0)} m`);
      setText("curTarget", `${fmt.format(d.targetSpeedMps || 0)} m/s`);
      setText("curActual", `${fmt.format(d.speedMps || 0)} m/s`);
      setText("curError", `${fmt.format(d.speedErrorMps || 0)} m/s`);
      setText("curTargetMode", d.targetProfileMode || "-");
      setText("curPid", `${fmt.format(d.pidOutputPercent || 0)} %`);
      setText("curTraction", `${fmt.format(d.tractionPercent || 0)} %`);
      setText("curBrake", `${fmt.format(d.brakePercent || 0)} %`);

      const mode = document.getElementById("curMode");
      mode.textContent = d.mode || "-";
      mode.className = `mode-pill mode-${d.mode || "COAST"}`;
    }

    renderMetrics();
    drawSpeedChart("timeChart", maxTime, "时间 s", (d) => d.simTimeS || 0);
    drawSpeedChart("positionChart", maxPosition, "位置 m", (d) => d.positionM || 0);
    drawCommandChart();
    updateDynamic();
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
