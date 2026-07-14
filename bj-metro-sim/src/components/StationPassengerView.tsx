import { useEffect, useMemo, useRef, useState } from 'react';
import { useSimStore } from '../store/useSimStore';
import {
  addPlatformPassengers,
  fetchPassengerFlowMode,
  fetchStationPassengerHistory,
  setPassengerFlowMode,
  type SimStationInfo,
  type SimTrainState,
} from '../data/backendApi';

type Point = { t: number; up: number; down: number };

const UP = '#30d158';
const DOWN = '#64d2ff';
const SIM_START_SECONDS = 6 * 3600;
const HISTORY_SECONDS = 6 * 3600;

function formatTime(seconds: number) {
  const h = Math.floor(seconds / 3600) % 24;
  const m = Math.floor(seconds / 60) % 60;
  const s = Math.floor(seconds) % 60;
  return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}

function periodColor(seconds: number) {
  const hour = Math.floor(seconds / 3600) % 24;
  if (hour >= 6 && hour < 7) return { color: '#0ea5e9', label: '早间起步' };
  if (hour >= 7 && hour < 9) return { color: '#ef4444', label: '早高峰' };
  if (hour >= 17 && hour < 19) return { color: '#f59e0b', label: '晚高峰' };
  if (hour >= 9 && hour < 17) return { color: '#22c55e', label: '平峰' };
  return { color: '#64748b', label: '非高峰' };
}

function stationKey(code: string, direction: 'UP' | 'DOWN') {
  return `${code}:${direction}`;
}

function mergeHistory(previous: Point[], incoming: Point[]) {
  const byTime = new Map(previous.map((point) => [point.t, point]));
  for (const point of incoming) byTime.set(point.t, point);
  return [...byTime.values()]
    .filter((point) => point.t >= SIM_START_SECONDS)
    .sort((a, b) => a.t - b.t)
    .slice(-(HISTORY_SECONDS + 1));
}

function Chart({ title, points, now, unit }: { title: string; points: Point[]; now: number; unit: string }) {
  const width = 700;
  const height = 168;
  const pad = { left: 42, right: 8, top: 10, bottom: 22 };
  const start = Math.max(SIM_START_SECONDS, now - HISTORY_SECONDS);
  const end = start + HISTORY_SECONDS;
  const desiredMax = Math.max(1, ...points.flatMap((point) => [point.up, point.down])) * 1.12;
  const maxRef = useRef(desiredMax);
  // New peaks expand immediately; decay slowly to keep the curve and current
  // markers from jumping when a short-term queue changes the scale.
  maxRef.current = desiredMax > maxRef.current ? desiredMax : Math.max(desiredMax, maxRef.current * .995);
  const max = maxRef.current;
  const usableWidth = width - pad.left - pad.right;
  const usableHeight = height - pad.top - pad.bottom;
  const x = (time: number) => pad.left + ((time - start) / HISTORY_SECONDS) * usableWidth;
  const y = (value: number) => pad.top + (1 - value / max) * usableHeight;
  const path = (direction: 'up' | 'down') => points
    .filter((point) => point.t >= start && point.t <= end)
    .map((point, index) => `${index === 0 ? 'M' : 'L'}${x(point.t).toFixed(2)},${y(point[direction]).toFixed(2)}`)
    .join('');
  const bands: Array<{ x: number; width: number; color: string }> = [];
  for (let time = Math.floor(start / 3600) * 3600; time < end; time += 3600) {
    const bandStart = Math.max(time, start);
    const bandEnd = Math.min(time + 3600, end);
    if (bandEnd > bandStart) bands.push({ x: x(bandStart), width: x(bandEnd) - x(bandStart), color: periodColor(time).color });
  }
  const labels: number[] = [];
  for (let time = Math.ceil(start / 1800) * 1800; time <= end; time += 1800) labels.push(time);
  const last = points.at(-1);
  return <section className="min-h-0 flex-1 rounded-lg p-2" style={{ border: '1px solid rgba(255,255,255,.06)', background: 'rgba(255,255,255,.015)' }}>
    <div className="mb-1 flex justify-between text-[10px]">
      <b style={{ color: '#b7c5d8' }}>{title}</b>
      <span style={{ color: '#718096' }}><i style={{ color: UP }}>●</i> 上行　<i style={{ color: DOWN }}>●</i> 下行　{unit}</span>
    </div>
    <svg viewBox={`0 0 ${width} ${height}`} className="h-[calc(100%-20px)] w-full" preserveAspectRatio="none">
      {bands.map((band, index) => <rect key={index} x={band.x} y={pad.top} width={Math.max(.5, band.width)} height={usableHeight} fill={band.color} opacity=".07" />)}
      {[0, .25, .5, .75, 1].map((ratio) => <line key={ratio} x1={pad.left} x2={width - pad.right} y1={pad.top + ratio * usableHeight} y2={pad.top + ratio * usableHeight} stroke="rgba(255,255,255,.08)" strokeDasharray="4 5" />)}
      <text x={pad.left - 5} y={pad.top + 4} textAnchor="end" fontSize="7" fill="#94a3b8">{max.toFixed(0)}</text>
      <text x={pad.left - 5} y={pad.top + usableHeight + 3} textAnchor="end" fontSize="7" fill="#64748b">0</text>
      {points.length > 1 ? <path d={path('up')} fill="none" stroke={UP} strokeWidth="1.35" /> : null}
      {points.length > 1 ? <path d={path('down')} fill="none" stroke={DOWN} strokeWidth="1.35" /> : null}
      {last ? <><circle cx={x(last.t)} cy={y(last.up)} r="2.7" fill={UP} /><circle cx={x(last.t)} cy={y(last.down)} r="2.7" fill={DOWN} /></> : null}
      {labels.map((time) => <text key={time} x={x(time)} y={height - 3} textAnchor="middle" fontSize="7" fill="#64748b">{formatTime(time).slice(0, 5)}</text>)}
    </svg>
  </section>;
}

function platformValue(byKey: Map<string, SimStationInfo>, code: string, direction: 'UP' | 'DOWN') {
  return byKey.get(stationKey(code, direction)) ?? { code, name: code, direction, waitingPax: 0, leftBehindPax: 0, arrivalsLastTick: 0, platformDensity: 0 };
}

function crowdingMeta(level: SimStationInfo['crowdingLevel']) {
  switch (level) {
    case 'CRITICAL': return { label: '严重拥挤', color: '#ef4444' };
    case 'HIGH': return { label: '拥挤', color: '#f97316' };
    case 'MEDIUM': return { label: '较拥挤', color: '#f59e0b' };
    default: return { label: '正常', color: '#30d158' };
  }
}

function trainSignalText(train: SimTrainState) {
  const side = train.doorSide === 'LEFT' ? '左门' : '右门';
  if (train.doorNotice === 'PREPARE_OPEN') return `准备开启${side}`;
  if (train.doorNotice === 'PREPARE_CLOSE') return '准备关门';
  if (train.doorState === 'OPEN') return `${side}开启，上下客中`;
  if (train.doorState === 'CLOSING') return '正在关门';
  if (train.phase === 'DWELLING') return '停站待开门';
  return '车门关闭';
}

function trainLocationText(train: SimTrainState) {
  const current = train.currentStation || train.currentStationCode;
  const next = train.nextStation || train.nextStationCode;
  if (train.phase === 'DWELLING') return `停靠：${current}`;
  if (train.phase === 'DEPARTING') return `驶离：${current}`;
  return `运行：${current} → ${next}`;
}

function PassengerExchangeMetrics({ train, compact = false }: { train: SimTrainState; compact?: boolean }) {
  const boarding = train.currentBoardingPax ?? train.lastBoarding ?? 0;
  const alighting = train.currentAlightingPax ?? train.lastAlighting ?? 0;
  const boardingRate = train.currentBoardingRatePaxPerSec ?? 0;
  const alightingRate = train.currentAlightingRatePaxPerSec ?? 0;

  return <div data-testid={`passenger-exchange-${train.trainId}`} className={compact ? 'mt-2' : 'mt-2 rounded-md p-2'} style={compact ? undefined : { background: 'rgba(255,255,255,.025)', border: '1px solid rgba(255,255,255,.05)' }}>
    <div className="mb-1 flex items-center justify-between text-[8px] uppercase tracking-[.12em]" style={{ color: '#64748b' }}>
      <span>本次停站累计</span>
      {train.phase === 'DWELLING' ? <span style={{ color: '#7dd3fc' }}>实时更新</span> : null}
    </div>
    <div className="grid grid-cols-2 gap-1.5">
      <div className="rounded px-2 py-1.5" style={{ background: 'rgba(48,209,88,.07)' }}>
        <div className="text-[8px]" style={{ color: '#86efac' }}>上车</div>
        <b className="font-mono text-sm" style={{ color: '#d9fbe3' }}>{boarding.toLocaleString()} <small className="text-[8px] font-normal">人</small></b>
      </div>
      <div className="rounded px-2 py-1.5" style={{ background: 'rgba(255,159,10,.07)' }}>
        <div className="text-[8px]" style={{ color: '#fbbf24' }}>下车</div>
        <b className="font-mono text-sm" style={{ color: '#fff1cf' }}>{alighting.toLocaleString()} <small className="text-[8px] font-normal">人</small></b>
      </div>
    </div>
    <div className="mt-1.5 flex justify-between font-mono text-[8px]" style={{ color: '#94a3b8' }}>
      <span>上车 {boardingRate.toFixed(1)} 人/s</span>
      <span>下车 {alightingRate.toFixed(1)} 人/s</span>
    </div>
  </div>;
}

export default function StationPassengerView() {
  const simStations = useSimStore((state) => state.simStations);
  const trains = useSimStore((state) => state.trains);
  const simTimeMs = useSimStore((state) => state.simTimeMs);
  const simTime = useSimStore((state) => state.simTime);
  const engineClockState = useSimStore((state) => state.engineClockState);
  const speed = useSimStore((state) => state.speed);
  const selectedStationCode = useSimStore((state) => state.selectedStationCode);
  const setSelectedStationCode = useSimStore((state) => state.setSelectedStationCode);
  const setViewMode = useSimStore((state) => state.setViewMode);
  const stationByKey = useMemo(() => new Map(simStations.map((station) => [stationKey(station.code, station.direction === 'DOWN' ? 'DOWN' : 'UP'), station])), [simStations]);
  const stations = useMemo(() => {
    const unique = new Map<string, SimStationInfo>();
    for (const station of simStations) if (!unique.has(station.code)) unique.set(station.code, station);
    return [...unique.values()];
  }, [simStations]);
  const [focus, setFocus] = useState(selectedStationCode ?? 'GGZ');
  const [waitingHistory, setWaitingHistory] = useState<Point[]>(() => [{ t: SIM_START_SECONDS, up: 0, down: 0 }]);
  const [arrivalHistory, setArrivalHistory] = useState<Point[]>(() => [{ t: SIM_START_SECONDS, up: 0, down: 0 }]);
  const [poissonEnabled, setPoissonEnabled] = useState<boolean | null>(null);
  const [poissonPending, setPoissonPending] = useState(false);
  const [poissonError, setPoissonError] = useState<string | null>(null);
  const [manualPassengerInputs, setManualPassengerInputs] = useState<Record<'UP' | 'DOWN', string>>({ UP: '50', DOWN: '50' });
  const [manualAddPending, setManualAddPending] = useState<'UP' | 'DOWN' | null>(null);
  const [manualAddFeedback, setManualAddFeedback] = useState<Record<'UP' | 'DOWN', string | null>>({ UP: null, DOWN: null });
  const historyCursor = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    void fetchPassengerFlowMode()
      .then((response) => {
        if (!cancelled) setPoissonEnabled(response.passengerFlow.usePoisson);
      })
      .catch(() => {
        if (!cancelled) setPoissonError('客流模式读取失败');
      });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (selectedStationCode && stations.some((station) => station.code === selectedStationCode)) setFocus(selectedStationCode);
  }, [selectedStationCode, stations]);

  useEffect(() => {
    historyCursor.current = null;
    setWaitingHistory([{ t: SIM_START_SECONDS, up: 0, down: 0 }]);
    setArrivalHistory([{ t: SIM_START_SECONDS, up: 0, down: 0 }]);
  }, [focus]);

  const simSeconds = Math.floor(simTimeMs / 1000);
  const up = platformValue(stationByKey, focus, 'UP');
  const down = platformValue(stationByKey, focus, 'DOWN');
  useEffect(() => {
    let cancelled = false;
    const loadHistory = async () => {
      try {
        const response = await fetchStationPassengerHistory(focus, historyCursor.current ?? undefined);
        if (cancelled) return;
        const upHistory = response.history.UP;
        const downHistory = response.history.DOWN;
        const byTime = new Map<number, Point>();
        for (const point of upHistory) byTime.set(point.simTimeMs, { t: point.simTimeMs / 1000, up: point.waitingPax, down: byTime.get(point.simTimeMs)?.down ?? 0 });
        for (const point of downHistory) byTime.set(point.simTimeMs, { t: point.simTimeMs / 1000, up: byTime.get(point.simTimeMs)?.up ?? 0, down: point.waitingPax });
        const arrivalByTime = new Map<number, Point>();
        for (const point of upHistory) arrivalByTime.set(point.simTimeMs, { t: point.simTimeMs / 1000, up: point.arrivals, down: arrivalByTime.get(point.simTimeMs)?.down ?? 0 });
        for (const point of downHistory) arrivalByTime.set(point.simTimeMs, { t: point.simTimeMs / 1000, up: arrivalByTime.get(point.simTimeMs)?.up ?? 0, down: point.arrivals });
        const waiting = [...byTime.values()].sort((a, b) => a.t - b.t);
        const arrivals = [...arrivalByTime.values()].sort((a, b) => a.t - b.t);
        if (waiting.length > 0 || arrivals.length > 0) {
          const newestMs = Math.max(...[...byTime.keys(), ...arrivalByTime.keys()]);
          historyCursor.current = newestMs;
          setWaitingHistory((previous) => mergeHistory(previous, waiting));
          setArrivalHistory((previous) => mergeHistory(previous, arrivals));
        }
      } catch {
        // The global engine may not be attached yet; retain the 06:00 origin.
      }
    };
    void loadHistory();
    const interval = window.setInterval(loadHistory, engineClockState === 'RUNNING' ? 500 : 2_000);
    return () => { cancelled = true; window.clearInterval(interval); };
  }, [engineClockState, focus]);

  const ratePoints = useMemo(() => arrivalHistory.map((point, index) => {
    const from = Math.max(0, index - 299);
    let upRate = 0;
    let downRate = 0;
    for (let cursor = from; cursor <= index; cursor += 1) {
      upRate += arrivalHistory[cursor].up;
      downRate += arrivalHistory[cursor].down;
    }
    const seconds = Math.max(1, point.t - arrivalHistory[from].t + 1);
    return { t: point.t, up: upRate * 60 / seconds, down: downRate * 60 / seconds };
  }), [arrivalHistory]);

  const focusedTrains = trains.filter((train) => train.currentStationCode === focus && train.phase === 'DWELLING');
  const trainOverview = useMemo(
    () => [...trains].sort((a, b) => b.loadFactor - a.loadFactor),
    [trains],
  );
  const period = periodColor(simSeconds);
  const name = stations.find((station) => station.code === focus)?.name ?? focus;
  const selectStation = (code: string) => {
    setFocus(code);
    setSelectedStationCode(code);
  };
  const togglePoisson = async () => {
    if (poissonEnabled === null || poissonPending) return;
    setPoissonPending(true);
    setPoissonError(null);
    try {
      const response = await setPassengerFlowMode(!poissonEnabled);
      setPoissonEnabled(response.passengerFlow.usePoisson);
    } catch {
      setPoissonError('客流模式切换失败');
    } finally {
      setPoissonPending(false);
    }
  };
  const addPassengers = async (direction: 'UP' | 'DOWN') => {
    const passengers = Number.parseInt(manualPassengerInputs[direction], 10);
    if (!Number.isInteger(passengers) || passengers <= 0) {
      setManualAddFeedback((previous) => ({ ...previous, [direction]: '请输入大于 0 的整数' }));
      return;
    }
    setManualAddPending(direction);
    setManualAddFeedback((previous) => ({ ...previous, [direction]: null }));
    try {
      const response = await addPlatformPassengers(focus, direction, passengers);
      const currentUp = direction === 'UP' ? response.projectedWaitingPax : (up.waitingPax ?? 0);
      const currentDown = direction === 'DOWN' ? response.projectedWaitingPax : (down.waitingPax ?? 0);
      setWaitingHistory((previous) => mergeHistory(previous, [{ t: simSeconds, up: currentUp, down: currentDown }]));
      setManualAddFeedback((previous) => ({
        ...previous,
        [direction]: response.status === 'QUEUED' ? `已提交 +${passengers}，下个 tick 生效` : `已增加 ${passengers} 人`,
      }));
    } catch {
      setManualAddFeedback((previous) => ({ ...previous, [direction]: '增加人数失败' }));
    } finally {
      setManualAddPending(null);
    }
  };

  return <div className="flex h-full flex-col" style={{ background: '#070b11' }}>
    <header className="flex h-12 shrink-0 items-center justify-between px-5" style={{ borderBottom: '1px solid rgba(255,255,255,.06)' }}>
      <div className="flex items-center gap-3">
        <button type="button" onClick={() => setViewMode('macro')} className="rounded px-2 py-1 text-[10px]" style={{ color: '#94a3b8', border: '1px solid rgba(255,255,255,.12)' }}>← 地图</button>
        <b className="text-[11px]" style={{ color: '#cbd5e1' }}>全局客流监控</b>
        <span className="rounded px-1.5 py-0.5 text-[9px]" style={{ color: '#cbd5e1', background: 'rgba(100,210,255,.09)' }}>周一 D1 · 06:00 起算</span>
        <span className="text-[9px]" style={{ color: period.color }}>{period.label}</span>
      </div>
      <div className="flex items-center gap-3 text-[10px]">
        <span className="font-mono text-sm" style={{ color: '#e2e8f0' }}>{simTime}</span>
        <span style={{ color: engineClockState === 'RUNNING' ? UP : '#f59e0b' }}>{engineClockState}</span>
        <span style={{ color: '#94a3b8' }}>每现实秒 {speed === 60 ? '1分钟' : `${speed}秒`}</span>
      </div>
    </header>
    <div className="flex min-h-0 flex-1 gap-2 p-2">
      <aside className="w-52 shrink-0 overflow-y-auto rounded-lg" style={{ border: '1px solid rgba(255,255,255,.06)' }}>
        <div className="sticky top-0 z-10 px-3 py-2 text-[10px]" style={{ color: '#94a3b8', background: '#0a1019', borderBottom: '1px solid rgba(255,255,255,.06)' }}>全部站台状态（候车人数 / 密度）</div>
        {stations.map((station) => {
          const stationUp = platformValue(stationByKey, station.code, 'UP');
          const stationDown = platformValue(stationByKey, station.code, 'DOWN');
          const upCrowding = crowdingMeta(stationUp.crowdingLevel);
          const downCrowding = crowdingMeta(stationDown.crowdingLevel);
          return <button type="button" key={station.code} onClick={() => selectStation(station.code)} className="block w-full border-b px-3 py-2 text-left text-[11px]" style={{ contentVisibility: 'auto', containIntrinsicSize: '0 64px', borderColor: 'rgba(255,255,255,.04)', background: focus === station.code ? 'rgba(100,210,255,.09)' : 'transparent', color: '#d7e0eb' }}>
            <div className="mb-1 flex justify-between"><b>{station.name}</b><span className="font-mono text-[9px]" style={{ color: '#94a3b8' }}>{((stationUp.waitingPax ?? 0) + (stationDown.waitingPax ?? 0)).toLocaleString()} 人</span></div>
            <div className="grid grid-cols-2 gap-1 font-mono text-[9px]">
              <span style={{ color: UP }}>↑ {stationUp.waitingPax ?? 0} / {(stationUp.platformDensity ?? 0).toFixed(2)}</span>
              <span style={{ color: DOWN }}>↓ {stationDown.waitingPax ?? 0} / {(stationDown.platformDensity ?? 0).toFixed(2)}</span>
            </div>
            <div className="mt-1 flex gap-2 text-[8px]"><span style={{ color: upCrowding.color }}>↑{upCrowding.label}</span><span style={{ color: downCrowding.color }}>↓{downCrowding.label}</span></div>
          </button>;
        })}
      </aside>
      <main className="flex min-w-0 flex-1 flex-col gap-2">
        <Chart title={`${name} · 站台候车人数`} points={waitingHistory} now={simSeconds} unit="pax" />
        <Chart title={`${name} · 进站率（5分钟滚动）`} points={ratePoints} now={simSeconds} unit="pax/min" />
      </main>
      <aside className="flex w-[17rem] shrink-0 flex-col gap-2 overflow-y-auto">
        <section data-testid="passenger-flow-mode-control" className="rounded-lg p-3" style={{ border: '1px solid rgba(100,210,255,.16)', background: 'linear-gradient(135deg, rgba(100,210,255,.055), rgba(255,255,255,.015))' }}>
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="text-[10px] font-semibold" style={{ color: '#d7e0eb' }}>进站客流生成</div>
              <div className="mt-0.5 text-[8px]" style={{ color: '#718096' }}>{poissonEnabled ? '泊松随机采样 · 自动生成开启' : '自动生成已停止 · 可按站台加人'}</div>
            </div>
            <button
              type="button"
              role="switch"
              aria-label="切换泊松随机客流"
              aria-checked={poissonEnabled === true}
              disabled={poissonEnabled === null || poissonPending}
              onClick={() => { void togglePoisson(); }}
              className="relative h-6 w-11 shrink-0 rounded-full transition-colors disabled:cursor-wait disabled:opacity-50"
              style={{ background: poissonEnabled ? 'rgba(48,209,88,.42)' : 'rgba(100,116,139,.35)', border: `1px solid ${poissonEnabled ? 'rgba(48,209,88,.65)' : 'rgba(148,163,184,.25)'}` }}
              title={poissonError ?? '开启时自动生成泊松客流；关闭时停止生成并允许手动加人'}
            >
              <span className="absolute top-[3px] h-4 w-4 rounded-full transition-all" style={{ left: poissonEnabled ? 23 : 3, background: poissonEnabled ? '#b9f6ca' : '#cbd5e1', boxShadow: '0 1px 4px rgba(0,0,0,.45)' }} />
            </button>
          </div>
          <div className="mt-2 flex items-center justify-between border-t pt-2 text-[8px]" style={{ color: '#94a3b8', borderColor: 'rgba(255,255,255,.06)' }}>
            <span>模式</span>
            <span style={{ color: poissonEnabled ? '#7ee7a2' : '#cbd5e1' }}>{poissonPending ? '切换中…' : poissonEnabled ? '自动生成中' : poissonEnabled === false ? '手动输入' : '读取中…'}</span>
          </div>
          {poissonError ? <div className="mt-1 text-[8px]" role="alert" style={{ color: '#ef4444' }}>{poissonError}</div> : null}
        </section>
        <div className="rounded-lg p-3" style={{ border: '1px solid rgba(255,255,255,.09)' }}>
          <div className="mb-2 flex justify-between"><small style={{ color: '#94a3b8' }}>全部列车载客率</small><small style={{ color: '#64748b' }}>{trainOverview.length} 列</small></div>
          {trainOverview.length === 0 ? <p className="text-[10px]" style={{ color: '#64748b' }}>尚未配置列车</p> : trainOverview.map((train) => {
            const loadPercent = Math.round(train.loadFactor * 100);
            const loadColor = loadPercent >= 100 ? '#ef4444' : loadPercent >= 80 ? '#f59e0b' : UP;
            const isDwelling = train.phase === 'DWELLING';
            const signalColor = train.doorState === 'OPEN' ? UP : train.doorNotice === 'PREPARE_OPEN' || train.doorNotice === 'PREPARE_CLOSE' ? '#f59e0b' : '#94a3b8';
            return <div key={train.trainId} className="border-t py-2 text-[10px]" style={{ contentVisibility: 'auto', containIntrinsicSize: '0 104px', borderColor: 'rgba(255,255,255,.06)' }}>
              <div className="flex justify-between"><span style={{ color: '#d7e0eb' }}>{train.trainId} · {train.direction === 'UP' ? '上行' : '下行'}</span><b style={{ color: loadColor }}>{loadPercent}%</b></div>
              <div className="mt-1 h-1.5 overflow-hidden rounded" style={{ background: 'rgba(255,255,255,.08)' }}><div className="h-full rounded" style={{ width: `${Math.min(100, Math.max(0, loadPercent))}%`, background: loadColor }} /></div>
              <div className="mt-1 flex justify-between font-mono" style={{ color: '#94a3b8' }}><span>{train.onboardPax}/{train.capacityPax} 人</span><span>{isDwelling ? `停站 ${Math.ceil(train.dwellRemainingSec)}s` : train.phase}</span></div>
              <div className="mt-1 truncate" style={{ color: isDwelling ? '#d7e0eb' : '#94a3b8' }}>{trainLocationText(train)}</div>
              <div className="mt-1" style={{ color: signalColor }}>信号：{trainSignalText(train)}</div>
              <PassengerExchangeMetrics train={train} />
            </div>;
          })}
        </div>
        {(['UP', 'DOWN'] as const).map((direction) => {
          const platform = direction === 'UP' ? up : down;
          const color = direction === 'UP' ? UP : DOWN;
          return <div key={direction} className="rounded-lg p-3" style={{ border: `1px solid ${color}33` }}>
            <div className="flex items-center justify-between"><small style={{ color }}>{name} · {direction === 'UP' ? '上行站台' : '下行站台'}</small><span className="font-mono text-[8px]" style={{ color: '#64748b' }}>{focus}</span></div>
            <div className="mt-2 text-[10px]" style={{ color: '#94a3b8' }}>候车 / 滞留 / 密度</div>
            <div className="font-mono text-2xl" style={{ color: '#e2e8f0' }}>{(platform.waitingPax ?? 0).toLocaleString()} <span className="text-[10px]">pax</span></div>
            <div className="mt-2 text-[11px]" style={{ color: '#cbd5e1' }}>滞留 {platform.leftBehindPax ?? 0}　{(platform.platformDensity ?? 0).toFixed(2)} pax/m²</div>
            {poissonEnabled === false ? <div className="mt-3 border-t pt-2" style={{ borderColor: 'rgba(255,255,255,.07)' }}>
              <label htmlFor={`manual-passengers-${direction}`} className="text-[8px]" style={{ color: '#94a3b8' }}>向本站台增加候车人数</label>
              <div className="mt-1 flex gap-1.5">
                <input
                  id={`manual-passengers-${direction}`}
                  type="number"
                  min="1"
                  max="1000000"
                  step="1"
                  value={manualPassengerInputs[direction]}
                  onChange={(event) => setManualPassengerInputs((previous) => ({ ...previous, [direction]: event.target.value }))}
                  className="min-w-0 flex-1 rounded px-2 py-1 font-mono text-[10px] outline-none"
                  style={{ color: '#e2e8f0', border: '1px solid rgba(148,163,184,.2)', background: 'rgba(15,23,42,.72)' }}
                  aria-label={`${name}${direction === 'UP' ? '上行' : '下行'}站台增加人数`}
                />
                <button
                  type="button"
                  disabled={manualAddPending !== null}
                  onClick={() => { void addPassengers(direction); }}
                  className="rounded px-2 py-1 text-[9px] disabled:cursor-wait disabled:opacity-50"
                  style={{ color, border: `1px solid ${color}55`, background: `${color}12` }}
                >
                  {manualAddPending === direction ? '提交中…' : '增加'}
                </button>
              </div>
              {manualAddFeedback[direction] ? <div className="mt-1 text-[8px]" role="status" style={{ color: manualAddFeedback[direction]?.includes('失败') || manualAddFeedback[direction]?.includes('请输入') ? '#ef4444' : '#7ee7a2' }}>{manualAddFeedback[direction]}</div> : null}
            </div> : null}
          </div>;
        })}
        <div className="rounded-lg p-3" style={{ border: '1px solid rgba(255,255,255,.09)' }}>
          <small style={{ color: '#94a3b8' }}>本站停站列车</small>
          {focusedTrains.length === 0 ? <p className="mt-2 text-[10px]" style={{ color: '#64748b' }}>暂无列车停站；候车人数由主仿真持续累积。</p> : focusedTrains.map((train) => <div key={train.trainId} className="mt-2 border-t pt-2 text-[10px]" style={{ color: '#d7e0eb', borderColor: 'rgba(255,255,255,.06)' }}>
            <div>{train.trainId}　{Math.round(train.loadFactor * 100)}%　{train.onboardPax}/{train.capacityPax}</div>
            <div className="mt-1" style={{ color: train.doorState === 'OPEN' ? UP : train.doorNotice === 'PREPARE_OPEN' || train.doorNotice === 'PREPARE_CLOSE' ? '#f59e0b' : '#94a3b8' }}>
              {train.doorNotice === 'PREPARE_OPEN' ? `模拟信号：准备开启${train.doorSide === 'LEFT' ? '左门' : '右门'}` : train.doorNotice === 'PREPARE_CLOSE' ? '模拟信号：准备关门' : train.doorState === 'OPEN' ? `模拟信号：${train.doorSide === 'LEFT' ? '左门' : '右门'}开启` : train.doorState === 'CLOSING' ? '模拟信号：正在关门' : '车门关闭'}
            </div>
            <PassengerExchangeMetrics train={train} compact />
            <div className="mt-1 text-right font-mono text-[8px]" style={{ color: '#64748b' }}>剩余停站 {Math.ceil(train.dwellRemainingSec)} s</div>
          </div>)}
        </div>
      </aside>
    </div>
  </div>;
}
