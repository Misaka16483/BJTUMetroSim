import { useEffect, useMemo, useRef, useState } from 'react';
import { useSimStore } from '../store/useSimStore';

type Point = { t: number; up: number; down: number };
type PassengerTrain = {
  id: string; direction: 'UP' | 'DOWN'; stationIndex: number; phase: 'DWELL' | 'RUN'; remainingSec: number;
  loadPax: number; lastBoarding: number; lastAlighting: number;
};

const STATIONS = [
  ['GGZ', '郭公庄'], ['FSP', '丰台科技园'], ['KYL', '科怡路'], ['FTN', '丰台南路'], ['FTD', '丰台东大街'], ['QLZ', '七里庄'], ['LLQ', '六里桥'], ['LLE', '六里桥东'], ['BWR', '北京西站'], ['JBG', '军事博物馆'], ['BDZ', '白堆子'], ['BQS', '白石桥南'], ['GTG', '国家图书馆'],
] as const;
const BASE_UP: Record<string, number> = { GGZ: 120, FSP: 60, KYL: 40, FTN: 35, FTD: 45, QLZ: 55, LLQ: 90, LLE: 50, BWR: 150, JBG: 80, BDZ: 40, BQS: 60, GTG: 70 };
const BASE_DOWN: Record<string, number> = { GGZ: 90, FSP: 45, KYL: 30, FTN: 25, FTD: 35, QLZ: 40, LLQ: 70, LLE: 35, BWR: 120, JBG: 60, BDZ: 30, BQS: 45, GTG: 55 };
const WINDOW_SEC = 6 * 3600;
const INITIAL_SIM_SEC = 6 * 3600;
const UP = '#30d158';
const DOWN = '#64d2ff';
const CAPACITY_PAX = 600;
const TRAVEL_SEC = [95, 72, 65, 78, 94, 82, 106, 80, 86, 82, 68, 75];
const ALIGHTING_RATIO = [0.06, 0.13, 0.15, 0.14, 0.12, 0.16, 0.18, 0.14, 0.10, 0.12, 0.14, 0.16, 0.20];
const DAY_SEQUENCE = [
  { label: '周一', factor: 1.00 }, { label: '周二', factor: 1.00 }, { label: '周三', factor: 1.00 }, { label: '周四', factor: 1.00 },
  { label: '周五', factor: 1.08 }, { label: '周六', factor: 0.67 }, { label: '周日', factor: 0.60 },
];

function dayFor(sec: number) { return DAY_SEQUENCE[Math.floor(sec / 86400) % DAY_SEQUENCE.length]; }

function period(sec: number) {
  const h = Math.floor(sec / 3600) % 24;
  if (h >= 7 && h < 9) return { factor: 1.45, color: '#ef4444', label: '早高峰' };
  if (h >= 17 && h < 19) return { factor: 1.15, color: '#f59e0b', label: '晚高峰' };
  if (h >= 6 && h < 7) return { factor: .2, color: '#0ea5e9', label: '早间' };
  if (h >= 9 && h < 17) return { factor: .55, color: '#22c55e', label: '平峰' };
  if (h >= 19 && h < 22) return { factor: .35, color: '#8b5cf6', label: '晚间' };
  return { factor: .08, color: '#6366f1', label: '夜间' };
}

function poisson(lambda: number) {
  if (lambda <= 0) return 0;
  if (lambda > 30) {
    const u = Math.max(Math.random(), Number.MIN_VALUE); const v = Math.random();
    return Math.max(0, Math.round(lambda + Math.sqrt(lambda) * Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v)));
  }
  const limit = Math.exp(-lambda); let product = 1; let count = 0;
  do { count++; product *= Math.random(); } while (product > limit);
  return count - 1;
}

function fmt(sec: number) {
  const h = Math.floor(sec / 3600) % 24; const m = Math.floor(sec / 60) % 60; const s = Math.floor(sec) % 60;
  return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}

function initialTrains(): PassengerTrain[] {
  return [
    { id: 'PAX-UP-01', direction: 'UP', stationIndex: 0, phase: 'DWELL', remainingSec: 30, loadPax: 280, lastBoarding: 0, lastAlighting: 0 },
    { id: 'PAX-DOWN-01', direction: 'DOWN', stationIndex: STATIONS.length - 1, phase: 'DWELL', remainingSec: 30, loadPax: 240, lastBoarding: 0, lastAlighting: 0 },
  ];
}

function serviceStop(train: PassengerTrain, waits: Record<string, Point>, time: number) {
  const code = STATIONS[train.stationIndex][0];
  const waiting = waits[code] ?? { t: time, up: 0, down: 0 };
  const alighting = Math.round(train.loadPax * ALIGHTING_RATIO[train.stationIndex]);
  const afterAlighting = Math.max(0, train.loadPax - alighting);
  const queue = train.direction === 'UP' ? waiting.up : waiting.down;
  const boarding = Math.min(queue, CAPACITY_PAX - afterAlighting);
  waits[code] = train.direction === 'UP'
    ? { t: time, up: queue - boarding, down: waiting.down }
    : { t: time, up: waiting.up, down: queue - boarding };
  return { ...train, loadPax: afterAlighting + boarding, lastBoarding: boarding, lastAlighting: alighting, phase: 'DWELL' as const, remainingSec: 30 };
}

function tickTrain(train: PassengerTrain, waits: Record<string, Point>, time: number) {
  let next = { ...train, lastBoarding: 0, lastAlighting: 0 };
  if (next.phase === 'DWELL') {
    next.remainingSec -= 1;
    if (next.remainingSec <= 0) {
      const atUpperTerminal = next.direction === 'UP' && next.stationIndex === STATIONS.length - 1;
      const atLowerTerminal = next.direction === 'DOWN' && next.stationIndex === 0;
      if (atUpperTerminal || atLowerTerminal) next.direction = next.direction === 'UP' ? 'DOWN' : 'UP';
      const segmentIndex = next.direction === 'UP' ? next.stationIndex : next.stationIndex - 1;
      next = { ...next, phase: 'RUN', remainingSec: TRAVEL_SEC[Math.max(0, segmentIndex)] };
    }
  } else {
    next.remainingSec -= 1;
    if (next.remainingSec <= 0) {
      next.stationIndex += next.direction === 'UP' ? 1 : -1;
      next = serviceStop(next, waits, time);
    }
  }
  return next;
}

function Chart({ title, points, now, rate }: { title: string; points: Point[]; now: number; rate?: boolean }) {
  const W = 700; const H = 168; const pad = { l: 42, r: 8, t: 10, b: 22 }; const start = Math.max(INITIAL_SIM_SEC, now - WINDOW_SEC); const end = start + WINDOW_SEC;
  const desiredMax = Math.max(1, ...points.flatMap((p) => [p.up, p.down])) * 1.12;
  const maxRef = useRef(desiredMax);
  // 新峰值立即扩展；低值仅缓慢回落，避免曲线和圆点因坐标轴重标尺跳动。
  maxRef.current = desiredMax > maxRef.current ? desiredMax : Math.max(desiredMax, maxRef.current * .995);
  const max = maxRef.current;
  const iw = W - pad.l - pad.r; const ih = H - pad.t - pad.b;
  const x = (t: number) => pad.l + ((t - start) / WINDOW_SEC) * iw;
  const y = (v: number) => pad.t + (1 - v / max) * ih;
  const path = (key: 'up' | 'down') => points.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(2)},${y(p[key]).toFixed(2)}`).join('');
  const bands = [] as { x: number; w: number; c: string }[];
  for (let t = Math.floor(start / 3600) * 3600; t < end; t += 3600) { const a = Math.max(t, start); const b = Math.min(t + 3600, end); if (b > a) bands.push({ x: x(a), w: x(b) - x(a), c: period(t).color }); }
  const labels = [] as number[]; for (let t = Math.ceil(start / 1800) * 1800; t <= end; t += 1800) labels.push(t);
  const last = points.at(-1);
  return <section className="min-h-0 flex-1 rounded-lg p-2" style={{ border: '1px solid rgba(255,255,255,.06)', background: 'rgba(255,255,255,.015)' }}>
    <div className="mb-1 flex justify-between text-[10px]"><b style={{ color: '#b7c5d8' }}>{title}</b><span style={{ color: '#718096' }}><i style={{ color: UP }}>●</i> 上行　<i style={{ color: DOWN }}>●</i> 下行　{rate ? 'pax/min' : 'pax'}</span></div>
    <svg viewBox={`0 0 ${W} ${H}`} className="h-[calc(100%-20px)] w-full" preserveAspectRatio="none">
      {bands.map((band, i) => <rect key={i} x={band.x} y={pad.t} width={Math.max(.5, band.w)} height={ih} fill={band.c} opacity=".07" />)}
      {[0, .25, .5, .75, 1].map((r) => <line key={r} x1={pad.l} x2={W - pad.r} y1={pad.t + r * ih} y2={pad.t + r * ih} stroke="rgba(255,255,255,.08)" strokeDasharray="4 5" />)}
      <text x={pad.l - 5} y={pad.t + 4} textAnchor="end" fontSize="7" fill="#94a3b8">{max.toFixed(0)}</text>
      <text x={pad.l - 5} y={pad.t + ih + 3} textAnchor="end" fontSize="7" fill="#64748b">0</text>
      {points.length > 1 && <path d={path('up')} fill="none" stroke={UP} strokeWidth="1.35" />}
      {points.length > 1 && <path d={path('down')} fill="none" stroke={DOWN} strokeWidth="1.35" />}
      {last && <><circle cx={x(last.t)} cy={y(last.up)} r="2.7" fill={UP} /><circle cx={x(last.t)} cy={y(last.down)} r="2.7" fill={DOWN} /></>}
      {labels.map((t) => <text key={t} x={x(t)} y={H - 3} textAnchor="middle" fontSize="7" fill="#64748b">{fmt(t).slice(0, 5)}</text>)}
    </svg>
  </section>;
}

export default function StationPassengerView() {
  const selectedStationCode = useSimStore((s) => s.selectedStationCode);
  const setSelectedStationCode = useSimStore((s) => s.setSelectedStationCode);
  const setViewMode = useSimStore((s) => s.setViewMode);
  const [focus, setFocus] = useState(selectedStationCode ?? 'GGZ');
  const [running, setRunning] = useState(true); const [speed, setSpeed] = useState(60); const [simSec, setSimSec] = useState(INITIAL_SIM_SEC);
  const [waiting, setWaiting] = useState<Record<string, Point>>({}); const [history, setHistory] = useState<Point[]>([{ t: INITIAL_SIM_SEC, up: 0, down: 0 }]); const [arrivals, setArrivals] = useState<Point[]>([{ t: INITIAL_SIM_SEC, up: 0, down: 0 }]);
  const simRef = useRef(simSec); const speedRef = useRef(speed); const runningRef = useRef(running); const focusRef = useRef(focus);
  const waits = useRef<Record<string, Point>>({}); const historyRef = useRef<Point[]>([{ t: INITIAL_SIM_SEC, up: 0, down: 0 }]); const arrivalRef = useRef<Point[]>([{ t: INITIAL_SIM_SEC, up: 0, down: 0 }]); const trainsRef = useRef<PassengerTrain[]>(initialTrains());
  useEffect(() => { if (selectedStationCode && STATIONS.some(([code]) => code === selectedStationCode)) setFocus(selectedStationCode); }, [selectedStationCode]);
  useEffect(() => { focusRef.current = focus; historyRef.current = []; arrivalRef.current = []; setHistory([]); setArrivals([]); }, [focus]);
  useEffect(() => { speedRef.current = speed; }, [speed]); useEffect(() => { runningRef.current = running; }, [running]);
  useEffect(() => {
    let frame = 0; let last: number | undefined; let carry = 0;
    const loop = (ms: number) => {
      const delta = last == null ? 0 : Math.min(.1, (ms - last) / 1000); last = ms;
      if (runningRef.current) {
        carry += delta * speedRef.current;
        while (carry >= 1) {
          carry -= 1; simRef.current += 1; const p = period(simRef.current); const day = dayFor(simRef.current); const focusCode = focusRef.current;
          let focusUp = 0; let focusDown = 0;
          for (const [code] of STATIONS) { const up = poisson(BASE_UP[code] * p.factor * day.factor / 60); const down = poisson(BASE_DOWN[code] * p.factor * day.factor / 60); const prior = waits.current[code] ?? { t: simRef.current, up: 0, down: 0 }; waits.current[code] = { t: simRef.current, up: prior.up + up, down: prior.down + down }; if (code === focusCode) { focusUp = up; focusDown = down; } }
          trainsRef.current = trainsRef.current.map((train) => tickTrain(train, waits.current, simRef.current));
          // 记录真实到站上下客处理后的站台状态，圆点和曲线使用同一来源。
          const current = waits.current[focusCode] ?? { t: simRef.current, up: 0, down: 0 };
          historyRef.current.push(current); arrivalRef.current.push({ t: simRef.current, up: focusUp, down: focusDown });
          if (historyRef.current.length > WINDOW_SEC + 1) historyRef.current.shift(); if (arrivalRef.current.length > WINDOW_SEC + 1) arrivalRef.current.shift();
        }
        setSimSec(simRef.current); setWaiting({ ...waits.current }); setHistory([...historyRef.current]); setArrivals([...arrivalRef.current]);
      }
      frame = requestAnimationFrame(loop);
    }; frame = requestAnimationFrame(loop); return () => cancelAnimationFrame(frame);
  }, []);
  const ratePoints = useMemo(() => arrivals.map((point, index) => { const from = Math.max(0, index - 299); let up = 0; let down = 0; for (let i = from; i <= index; i++) { up += arrivals[i].up; down += arrivals[i].down; } const seconds = Math.max(1, point.t - arrivals[from].t + 1); return { t: point.t, up: up * 60 / seconds, down: down * 60 / seconds }; }), [arrivals]);
  const name = STATIONS.find(([code]) => code === focus)?.[1] ?? focus; const current = waiting[focus] ?? { t: simSec, up: 0, down: 0 }; const latestArrival = arrivals.at(-1) ?? { t: simSec, up: 0, down: 0 }; const p = period(simSec); const day = dayFor(simSec); const dayNo = Math.floor(simSec / 86400) + 1;
  return <div className="flex h-full flex-col" style={{ background: '#070b11' }}>
    <header className="flex h-12 shrink-0 items-center justify-between px-5" style={{ borderBottom: '1px solid rgba(255,255,255,.06)' }}><div className="flex items-center gap-3"><button type="button" onClick={() => setViewMode('macro')} className="rounded px-2 py-1 text-[10px]" style={{ color: '#94a3b8', border: '1px solid rgba(255,255,255,.12)' }}>← 地图</button><b className="text-[11px]" style={{ color: '#cbd5e1' }}>客流独立仿真</b><span className="rounded px-1.5 py-0.5 text-[9px]" style={{ color: '#cbd5e1', background: 'rgba(100,210,255,.09)' }}>{day.label} × {day.factor}</span><span className="text-[9px]" style={{ color: p.color }}>{p.label} × {p.factor}</span></div><div className="flex items-center gap-3"><span className="font-mono text-sm" style={{ color: '#e2e8f0' }}>D{dayNo} {fmt(simSec)}</span>{[[1, '1秒'], [60, '1分'], [3600, '1时'], [86400, '1天']].map(([value, label]) => <button type="button" key={value} onClick={() => setSpeed(value as number)} className="text-[10px]" style={{ color: speed === value ? '#fff' : '#64748b' }}>{label}</button>)}<button type="button" onClick={() => setRunning(!running)} style={{ color: running ? UP : '#94a3b8' }}>{running ? '❚❚' : '▶'}</button></div></header>
    <div className="flex min-h-0 flex-1 gap-2 p-2"><aside className="w-40 shrink-0 overflow-y-auto rounded-lg" style={{ border: '1px solid rgba(255,255,255,.06)' }}>{STATIONS.map(([code, station]) => { const value = waiting[code]; return <button type="button" key={code} onClick={() => { setFocus(code); setSelectedStationCode(code); }} className="block w-full border-b px-3 py-2 text-left text-[11px]" style={{ borderColor: 'rgba(255,255,255,.04)', background: focus === code ? 'rgba(100,210,255,.09)' : 'transparent', color: '#d7e0eb' }}>{station}<span className="float-right font-mono text-[9px]" style={{ color: '#94a3b8' }}>{((value?.up ?? 0) + (value?.down ?? 0)).toLocaleString()}</span></button>; })}</aside><main className="flex min-w-0 flex-1 flex-col gap-2"><Chart title={`${name} · 候车人数`} points={history} now={simSec} /><Chart title={`${name} · 进站率（5分钟滚动）`} points={ratePoints} now={simSec} rate /></main><aside className="flex w-52 shrink-0 flex-col gap-2"><div className="rounded-lg p-3" style={{ border: `1px solid ${UP}33` }}><small style={{ color: UP }}>上行站台</small><div className="mt-2 text-[10px]" style={{ color: '#94a3b8' }}>候车人数 / 本秒到达</div><div className="font-mono text-2xl" style={{ color: '#e2e8f0' }}>{current.up.toLocaleString()} <span className="text-[10px]">pax</span></div><div className="mt-2 text-[11px]" style={{ color: '#cbd5e1' }}>+{latestArrival.up} pax　密度 {(current.up / 120).toFixed(2)} pax/m²</div></div><div className="rounded-lg p-3" style={{ border: `1px solid ${DOWN}33` }}><small style={{ color: DOWN }}>下行站台</small><div className="mt-2 text-[10px]" style={{ color: '#94a3b8' }}>候车人数 / 本秒到达</div><div className="font-mono text-2xl" style={{ color: '#e2e8f0' }}>{current.down.toLocaleString()} <span className="text-[10px]">pax</span></div><div className="mt-2 text-[11px]" style={{ color: '#cbd5e1' }}>+{latestArrival.down} pax　密度 {(current.down / 120).toFixed(2)} pax/m²</div></div></aside></div>
  </div>;
}
