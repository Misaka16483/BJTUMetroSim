import { useState, useMemo, useEffect, useRef } from 'react';
import { useSimStore, type SpeedRunRecord } from '../store/useSimStore';
import { lineColor } from './LineSelector';
import MasterController from './MasterController';
import type { MetroLineData } from '../data/amapMetroApi';

/* ═══════════════════════ CBTC 驾驶台 · 北京地铁 9 号线 ═══════════════════════ */

export default function DriverConsole({ fullPage }: { fullPage?: boolean }) {
  if (!fullPage) return null;
  return <FullDriverView />;
}

/* ═══ root ═══ */
function FullDriverView() {
  const metroLines = useSimStore((s) => s.metroLines);
  const [activeLineId, setActiveLineId] = useState('9');
  const hasEngine = activeLineId === '9';
  const line = useMemo(() => metroLines.find(l => l.id === activeLineId), [metroLines, activeLineId]);
  const color = line ? lineColor(line.id) : '#5f7088';

  return (
    <div className="h-full flex flex-col"
      style={{
        background: '#111827',
        borderRadius: 6,
        overflow: 'hidden',
        fontFamily: "'PingFang SC','Microsoft YaHei','Noto Sans SC',system-ui,sans-serif",
      }}>
      <TopBar lines={metroLines} activeLineId={activeLineId} onSelect={setActiveLineId} color={color} />
      {hasEngine ? <ActiveCab line9={line!} /> : <PendingCab line={line} color={color} />}
    </div>
  );
}

/* ═══ 顶部栏 ═══ */
function TopBar({ lines, activeLineId, onSelect, color }: {
  lines: MetroLineData[]; activeLineId: string; onSelect: (id: string) => void; color: string;
}) {
  const { simTime, engineClockState, backendStatus } = useSimStore();
  const isBackend = backendStatus === 'connected';
  const clockColor = engineClockState === 'RUNNING' ? 'var(--green)'
    : engineClockState === 'PAUSED' ? 'var(--amber)' : '#4a5568';

  return (
    <div className="shrink-0 flex items-center gap-6 px-5 h-11"
      style={{
        borderBottom: '1px solid rgba(255,255,255,0.08)',
        background: 'rgba(255,255,255,0.03)',
        boxShadow: `inset 0 -1px 0 rgba(${hexToRgb(color)},0.10)`,
      }}>
      <div className="flex items-center gap-2">
        <span className="text-[9px] font-medium uppercase tracking-[0.15em] text-[#6b7280] shrink-0 select-none">LINE</span>
        <div className="flex flex-wrap gap-1">
          {lines.map((l) => {
            const lc = lineColor(l.id);
            const active = l.id === activeLineId;
            const hasData = l.id === '9';
            const label = l.name.length <= 3 ? l.name
              : (l.name.match(/地铁(\d+)号线/)?.[1] ? `${l.name.match(/地铁(\d+)号线/)![1]}号线` : l.name.slice(0, 6));
            return (
              <button key={l.id} type="button" onClick={() => onSelect(l.id)}
                className="flex items-center gap-1 px-2 py-1 rounded text-[9px] font-medium cursor-pointer select-none transition-colors duration-150"
                style={{
                  color: active ? '#e2e8f0' : '#6b7280',
                  background: active ? `rgba(${hexToRgb(lc)},0.10)` : 'transparent',
                  border: active ? `1px solid rgba(${hexToRgb(lc)},0.18)` : '1px solid transparent',
                }}>
                <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ backgroundColor: lc }} />
                {label}
                {hasData && <span className="w-1 h-1 rounded-full shrink-0 opacity-70" style={{ backgroundColor: lc }} />}
              </button>
            );
          })}
        </div>
      </div>
      <div className="flex-1" />
      {isBackend && (
        <div className="flex items-center gap-3 select-none">
          <span className="w-1.5 h-1.5 rounded-full" style={{
            backgroundColor: clockColor,
            boxShadow: engineClockState === 'RUNNING' ? `0 0 6px ${clockColor}` : 'none',
          }} />
          <span className="text-[9px] font-medium uppercase tracking-[0.14em] text-[#6b7280]">SIM</span>
          <span className="font-mono text-[15px] font-semibold tracking-[0.03em]" style={{ color: '#e2e8f0' }}>{simTime}</span>
        </div>
      )}
    </div>
  );
}

/* ═══ 活跃驾驶舱 ═══ */
function ActiveCab({ line9 }: { line9: MetroLineData }) {
  const {
    currentStation, nextStation, distanceToNextStationM, stationIndex, line9Stations,
    runDirection, currentSpeedMps, simTime, avgLoadRate, totalPassengers,
    engineClockState, tractionPercent, brakePercent,
    energyKwh, tractionEnergyKwh, regenGeneratedKwh, regenAcceptedKwh, regenWastedKwh,
    targetSpeedMps, permittedSpeedMps, speedProfile, speedProfileMeta, speedHistory,
    speedTimeHistory, estimatedRunTimeS, pathPositionM, pathTotalLengthM,
    currentSegmentId, localSpeedLimitMps, gradeRatio,
    manualMode, setManualMode,
    selectedTrainId, trains, selectTrain, trainColors,
    speedRunsByTrain, activeSpeedRunIdByTrain, viewedSpeedRunIdByTrain, selectSpeedRun,
    cabStatus, fetchCabStatus,
  } = useSimStore();
  const color = lineColor(line9.id);

  // 轮询司机台硬件状态
  const pollRef = useRef<number | null>(null);
  useEffect(() => {
    const poll = () => {
      fetchCabStatus();
      pollRef.current = window.setTimeout(poll, 1000);
    };
    void fetchCabStatus();
    pollRef.current = window.setTimeout(poll, 1000);
    return () => {
      if (pollRef.current !== null) window.clearTimeout(pollRef.current);
    };
  }, [fetchCabStatus]);

  const plcActive = cabStatus?.state === 'CONNECTED' && cabStatus?.controlState === 'ACTIVE';
  const plcInput = plcActive ? cabStatus?.lastInput : null;

  // PLC 控制时使用 PLC 回传的牵引/制动百分比
  const displayTraction = plcInput ? plcInput.tractionPercent : tractionPercent;
  const displayBrake = plcInput ? plcInput.brakePercent : brakePercent;

  const trainRuns = selectedTrainId ? (speedRunsByTrain[selectedTrainId] ?? []) : [];
  const activeRunId = selectedTrainId ? activeSpeedRunIdByTrain[selectedTrainId] : undefined;
  const viewedRunId = selectedTrainId ? viewedSpeedRunIdByTrain[selectedTrainId] : null;
  const activeRun = activeRunId ? trainRuns.find((run) => run.id === activeRunId) : trainRuns[trainRuns.length - 1];
  const chartRun = (viewedRunId ? trainRuns.find((run) => run.id === viewedRunId) : activeRun) ?? activeRun;
  const chartPositionHistory = chartRun?.positionHistory ?? speedHistory;
  const chartTimeHistory = chartRun?.timeHistory ?? speedTimeHistory;
  const chartProfile = chartRun?.profile ?? speedProfile;
  const chartLastPosition = chartPositionHistory[chartPositionHistory.length - 1];
  const chartLastTime = chartTimeHistory[chartTimeHistory.length - 1];
  const chartIsLive = !viewedRunId || chartRun?.id === activeRunId;

  const speedKmh = currentSpeedMps * 3.6;
  const eta = distanceToNextStationM > 0 && currentSpeedMps > 0
    ? Math.ceil(distanceToNextStationM / currentSpeedMps) : 0;

  return (
    <div className="flex-1 flex flex-col min-h-0" style={{ background: '#111827' }}>

      {/* ── 列车选择条 ── */}
      {trains.length > 1 && (
        <div className="shrink-0 flex items-center gap-2 px-8 pt-3 pb-1" style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
          <span className="text-[9px] font-medium uppercase tracking-[0.12em] text-[#6b7280]">SELECT TRAIN</span>
          <div className="flex gap-1.5">
            {trains.map((t) => {
              const sel = t.trainId === selectedTrainId;
              const tc = trainColors[t.trainId] || '#58a6ff';
              return (
                <button
                  key={t.trainId}
                  onClick={() => selectTrain(t.trainId)}
                  className="px-2.5 py-1 rounded text-[10px] font-medium cursor-pointer transition-colors duration-150"
                  style={{
                    color: sel ? '#e2e8f0' : tc,
                    background: sel ? `${tc}22` : 'rgba(255,255,255,0.03)',
                    border: sel ? `1px solid ${tc}40` : '1px solid rgba(255,255,255,0.04)',
                  }}
                >
                  <span className="w-1.5 h-1.5 rounded-full inline-block mr-1" style={{ backgroundColor: tc }} />
                  {t.trainId}
                  <span className="text-[8px] ml-1 opacity-50">{t.operationMode === 'MANUAL' ? 'RM' : 'ATO'}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* ── 中部顶部：站台信息 ── */}
      <div className="shrink-0 px-8 pt-6 pb-4">
        <StationRouteCard
          current={currentStation || '--'}
          next={nextStation || '--'}
          distanceKm={distanceToNextStationM / 1000}
          eta={eta}
          direction={runDirection}
        />
      </div>

      {/* ── 主体：左仪表盘 + 中PIS + 右控制 ── */}
      <div className="flex-1 flex min-h-0" style={{ gap: 0 }}>

        {/* 左列：速度仪表盘 + 底部指标 */}
        <div className="shrink-0 flex flex-col justify-center p-4"
          style={{ width: 340, borderRight: '1px solid rgba(255,255,255,0.08)', gap: 12 }}>
          <div style={{ width: 300, height: 200 }}>
            <CabGauge speedKmh={speedKmh} color={color} />
          </div>
          <div className="flex items-center justify-center gap-3 flex-wrap">
            <MetricBadge label="TARGET" value={String(Math.round(targetSpeedMps * 3.6))} unit="km/h" accent="#00a8ff" />
            <MetricBadge label="LOAD" value={`${avgLoadRate}%`} unit="" accent="#8FC31F" />
            <MetricBadge label="PAX" value={String(totalPassengers)} unit="" accent="#94a3b8" />
            <MetricBadge label="ENE" value={energyKwh.toFixed(1)} unit="kWh" accent="#f59e0b" />
            <MetricBadge label="TRAC" value={tractionEnergyKwh.toFixed(1)} unit="kWh" accent="#58a6ff" />
            <MetricBadge label="REG" value={`${regenAcceptedKwh.toFixed(1)}/${regenGeneratedKwh.toFixed(1)}`} unit="kWh" accent="#22c55e" />
            <MetricBadge label="WASTE" value={regenWastedKwh.toFixed(1)} unit="kWh" accent="#ef4444" />
            <MetricBadge label="ETIME" value={estimatedRunTimeS > 0 ? `${estimatedRunTimeS|0}` : '--'} unit="s" accent="#6366f1" />
            <MetricBadge label="MODE" value="AM-CBTC" unit="" accent="#8FC31F" />
          </div>
          {/* ── 速度-位点曲线 ── */}
          {selectedTrainId && trainRuns.length > 0 && (
            <SpeedRunSelector
              runs={trainRuns}
              activeRunId={activeRunId}
              viewedRunId={viewedRunId ?? null}
              onSelect={(runId) => selectSpeedRun(selectedTrainId, runId)}
            />
          )}
          <SpeedCurveChart
            profile={chartProfile}
            history={chartPositionHistory}
            currentSpeedMps={chartIsLive ? currentSpeedMps : (chartLastPosition?.speedMps ?? 0)}
            currentPositionM={chartLastPosition?.positionM ?? 0}
            pathTotalLengthM={chartRun?.pathTotalLengthM ?? pathTotalLengthM}
            profileSource={chartRun?.profileMeta?.source ?? speedProfileMeta?.source ?? ''}
            startStation={chartRun?.startStation ?? currentStation ?? '--'}
            endStation={chartRun?.endStation ?? nextStation ?? '--'}
          />
          {/* ── 速度-时间曲线 ── */}
          <SpeedTimeCurveChart
            history={chartTimeHistory}
            currentSpeedMps={chartIsLive ? currentSpeedMps : (chartLastTime?.speedMps ?? 0)}
            elapsedS={chartLastTime?.elapsedS ?? 0}
          />
        </div>

        {/* 中列：PIS */}
        <div className="flex-1 flex flex-col min-w-0 justify-center">
          {/* PIS 站序条 */}
          <div className="px-4 py-3">
            <CabPIS stations={line9Stations} currentIdx={stationIndex} direction={runDirection} color={color} />
          </div>
        </div>

        {/* 右列：控制面板 */}
        <div className="flex flex-col justify-between py-4 px-4"
          style={{ width: 200, borderLeft: '1px solid rgba(255,255,255,0.08)', gap: 20 }}>
          <div className="flex flex-col items-center py-3 px-4 rounded"
            style={{ border: '1px solid rgba(255,255,255,0.04)', background: 'rgba(255,255,255,0.015)' }}>
            <span className="text-[7px] font-semibold uppercase tracking-[0.18em] text-[#6b7280] mb-1.5">SIM TIME</span>
            <span className="font-mono text-[20px] font-bold tracking-[0.04em]" style={{ color: '#e2e8f0' }}>{simTime}</span>
          </div>


          {/* ── ATO / 手动 切换 ── */}
          {plcActive && (
            <div className="flex items-center justify-center gap-2 py-1.5 rounded select-none"
              style={{ border: '1px solid rgba(100,210,255,0.15)', background: 'rgba(100,210,255,0.04)' }}>
              <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: '#64d2ff', boxShadow: '0 0 5px rgba(100,210,255,0.5)' }} />
              <span className="text-[8px] font-semibold uppercase tracking-[0.1em]" style={{ color: '#64d2ff' }}>PLC 硬线控制</span>
            </div>
          )}
          <div
            className={`flex items-center rounded ${plcActive ? 'opacity-40 pointer-events-none' : 'cursor-pointer'}`}
            style={{ border: '1px solid rgba(255,255,255,0.06)', background: 'rgba(255,255,255,0.015)' }}
            onClick={() => { if (!plcActive) setManualMode(!manualMode, selectedTrainId ?? undefined); }}
          >
            <div
              className="flex-1 text-center py-1.5 rounded text-[9px] font-bold uppercase tracking-[0.1em] transition-colors duration-150"
              style={{
                color: !manualMode ? '#e2e8f0' : '#6b7280',
                background: !manualMode ? 'rgba(100,210,255,0.12)' : 'transparent',
              }}
            >
              ATO
            </div>
            <div
              className="flex-1 text-center py-1.5 rounded text-[9px] font-bold uppercase tracking-[0.1em] transition-colors duration-150"
              style={{
                color: manualMode ? '#fbbf24' : '#6b7280',
                background: manualMode ? 'rgba(251,191,36,0.12)' : 'transparent',
              }}
            >
              CM
            </div>
          </div>

          {/* ── 牵引/制动条 (统一使用 display 值) ── */}
          <div className="flex flex-col" style={{ gap: 8 }}>
            <DriveBar label="TRACTION" value={displayTraction} color="#22c55e" />
            <DriveBar label="BRAKE" value={displayBrake} color="#ef4444" />
          </div>

          {/* ── PLC 控制时显示司机台输入详情 ── */}
          {plcActive && plcInput && (
            <div className="flex flex-col rounded py-2 px-3" style={{ gap: 4, border: '1px solid rgba(100,210,255,0.08)', background: 'rgba(100,210,255,0.02)' }}>
              <span className="text-[7px] font-semibold uppercase tracking-[0.12em] text-[#64748b] mb-1">司机台输入</span>
              <InfoRow label="DIR" value={plcInput.direction} color="#e2e8f0" />
              <InfoRow label="TRACTION" value={`${plcInput.tractionPercent.toFixed(0)}%`} color="#22c55e" />
              <InfoRow label="BRAKE" value={`${plcInput.brakePercent.toFixed(0)}%`} color="#ef4444" />
              <InfoRow label="SPEED" value={`${(plcInput.speedMps * 3.6).toFixed(1)} km/h`} color="#00a8ff" />
              <InfoRow label="KEY" value={plcInput.keyActive ? 'ON' : 'OFF'} color={plcInput.keyActive ? '#22c55e' : '#6b7280'} />
              <InfoRow label="EB" value={plcInput.emergencyBrake ? '●' : '○'} color={plcInput.emergencyBrake ? '#ef4444' : '#6b7280'} />
            </div>
          )}

          {/* ── 手动驾驶：操纵杆 (PLC 控制时隐藏) ── */}
          {manualMode && !plcActive && (
            <div className="flex justify-center py-2"
              style={{ border: '1px solid rgba(251,191,36,0.08)', borderRadius: 8, background: 'rgba(251,191,36,0.02)' }}>
              <MasterController />
            </div>
          )}

          {/* ── 运行信息 ── */}
          <div className="flex flex-col" style={{ gap: 3 }}>
            <InfoRow label="PERMITTED" value={`${Math.round(permittedSpeedMps * 3.6)} km/h`} color="#00a8ff" />
            <InfoRow label="LOCAL LIMIT" value={`${Math.round(localSpeedLimitMps * 3.6)} km/h`} color="#f59e0b" />
            <InfoRow label="TARGET" value={`${Math.round(targetSpeedMps * 3.6)} km/h`} color="#8FC31F" />
            <InfoRow label="PATH" value={`${pathPositionM.toFixed(0)}/${pathTotalLengthM.toFixed(0)} m`} color="#94a3b8" />
            <InfoRow label="SEG" value={currentSegmentId === null ? '--' : String(currentSegmentId)} color="#cbd5e1" />
            <InfoRow label="GRADE" value={`${(gradeRatio * 10000).toFixed(1)}/10000`} color="#c084fc" />
          </div>

          <div className="flex flex-col" style={{ gap: 6 }}>
            <StateIndicator state={engineClockState} />
            <div className="flex justify-center gap-4 pt-2">
              <span className="text-[8px] font-medium uppercase tracking-[0.12em] text-[#6b7280] flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: manualMode ? '#f59e0b' : '#8FC31F', opacity: 0.6 }} />ATP
              </span>
              <span className="text-[8px] font-medium uppercase tracking-[0.12em] text-[#6b7280] flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: manualMode ? '#6b7280' : '#8FC31F', opacity: 0.6 }} />{manualMode ? 'CM' : 'ATO'}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ═══ 车站路线卡片（中部顶部） ═══ */
function StationRouteCard({ current, next, distanceKm, eta, direction }: {
  current: string; next: string; distanceKm: number; eta: number; direction: string;
}) {
  return (
    <div>
      <div className="flex items-center gap-4">
        <span className="text-[26px] font-bold tracking-[0.02em]"
          style={{ color: next ? 'rgba(255,255,255,0.18)' : '#e2e8f0' }}>
          {current}
        </span>
        <svg width="32" height="14" viewBox="0 0 32 14" className="shrink-0">
          <line x1="4" y1="7" x2="24" y2="7" stroke="rgba(0,168,255,0.5)" strokeWidth="1.5" strokeLinecap="round" />
          <polyline points="20,3 26,7 20,11" fill="none" stroke="rgba(0,168,255,0.7)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <span className="text-[26px] font-bold tracking-[0.02em]" style={{ color: '#e2e8f0' }}>{next}</span>
        <span className="text-[10px] font-medium px-2 py-0.5 rounded select-none" style={{
          color: direction === 'UP' ? '#8FC31F' : '#f59e0b',
          border: '1px solid rgba(255,255,255,0.05)',
          background: 'rgba(255,255,255,0.015)',
        }}>
          {direction === 'UP' ? '↑ 上行' : '↓ 下行'}
        </span>
      </div>
      <div className="flex items-center gap-8 mt-2">
        <div className="flex items-baseline gap-1.5">
          <span className="text-[28px] font-bold font-mono tracking-tight" style={{ color: '#00a8ff' }}>
            {distanceKm.toFixed(3)}
          </span>
          <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-[#6b7280]">km</span>
        </div>
        {eta > 0 && (
          <div className="flex items-baseline gap-1.5">
            <span className="text-[22px] font-bold font-mono tracking-tight" style={{ color: '#9ca3af' }}>
              {eta}
            </span>
            <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-[#6b7280]">s</span>
          </div>
        )}
      </div>
    </div>
  );
}

/* ═══ 速度仪表盘（圆形） ═══ */
function CabGauge({ speedKmh, color: _color }: { speedKmh: number; color: string }) {
  const CX = 170, CY = 140, R = 108;
  const MAX = 80, START = 180, SWEEP = 180;
  const toRad = (d: number) => d * Math.PI / 180;
  const ap = (d: number, r: number) => ({ x: CX + r * Math.cos(toRad(d)), y: CY + r * Math.sin(toRad(d)) });

  const clamped = Math.min(speedKmh, MAX);
  const needleAngle = START + (clamped / MAX) * SWEEP;
  const arcPath = (a: number, b: number, r: number) => {
    const s = ap(a, r), e = ap(b, r);
    return `M${s.x} ${s.y} A${r} ${r} 0 0 1 ${e.x} ${e.y}`;
  };

  return (
    <svg viewBox="0 0 340 240" className="w-full h-full">
      <defs>
        <filter id="gaugeGlow"><feGaussianBlur stdDeviation="4" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
      </defs>

      {/* 背景弧 */}
      <path d={arcPath(START, START + SWEEP, R)} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="12" strokeLinecap="round" />

      {/* 刻度 — 统一颜色，不随速度变色 */}
      {[...Array(17)].map((_, i) => {
        const v = i * 5, a = START + (v / MAX) * SWEEP;
        const major = v % 10 === 0;
        const inn = ap(a, major ? R - 24 : R - 14), out = ap(a, R - 2);
        return <line key={`t-${i}`} x1={inn.x} y1={inn.y} x2={out.x} y2={out.y}
          stroke="rgba(255,255,255,0.14)" strokeWidth={major ? 1.4 : 0.6} />;
      })}

      {/* 数字标签 — 0 和 80 水平对齐 */}
      {[0, 20, 40, 60, 80].map(v => {
        const a = START + (v / MAX) * SWEEP, p = ap(a, R - 46);
        return <text key={`n-${v}`} x={p.x} y={p.y} textAnchor="middle" dominantBaseline="middle"
          fontSize="11" fontWeight="500" fill="rgba(255,255,255,0.22)" fontFamily="'JetBrains Mono','SF Mono'">{v}</text>;
      })}

      {/* 指针 — 只有它随速度动 */}
      <g transform={`rotate(${needleAngle},${CX},${CY})`}>
        <polygon points={`${CX+3},${CY-2.5} ${CX+3},${CY+2.5} ${CX+R-20},${CY+1} ${CX+R-20},${CY-1}`}
          fill="#ef4444" filter="url(#gaugeGlow)" style={{ transition: 'all 320ms cubic-bezier(0.22, 1, 0.36, 1)' }} />
        <polygon points={`${CX-3},${CY-3.5} ${CX-3},${CY+3.5} ${CX-20},${CY+1.5} ${CX-20},${CY-1.5}`}
          fill="rgba(255,255,255,0.08)" opacity="0.25" />
      </g>
      <circle cx={CX} cy={CY} r="9" fill="#111827" stroke="rgba(255,255,255,0.12)" strokeWidth="2" />

      {/* 速度数字 */}
      <text x={CX} y={CY + 48} textAnchor="middle" fontSize="50" fontWeight="800" fill="#e2e8f0"
        fontFamily="'JetBrains Mono','SF Mono','Consolas',monospace" letterSpacing="1">
        {String(Math.round(clamped)).padStart(2, '0')}
      </text>
      <text x={CX} y={CY + 72} textAnchor="middle" fontSize="11" fill="#6b7280"
        fontFamily="system-ui,sans-serif">km/h</text>
    </svg>
  );
}

/* ═══ PIS 站序条 ═══ */
function CabPIS({ stations, currentIdx, direction, color }: {
  stations: string[]; currentIdx: number; direction: string; color: string;
}) {
  const SP = 64, DOT_R = 4, ACTIVE_R = 7, H = 42;
  const totalW = stations.length * SP + 40;

  return (
    <div className="overflow-x-auto w-full" style={{ scrollbarWidth: 'none' }}>
      <svg width={totalW} height={H} viewBox={`0 0 ${totalW} ${H}`}>
        {stations.map((_, i) => {
          if (i === stations.length - 1) return null;
          const x1 = 32 + i * SP + ACTIVE_R, x2 = 32 + (i + 1) * SP - ACTIVE_R;
          const past = direction === 'UP' ? i > currentIdx : i < currentIdx;
          return <line key={`l-${i}`} x1={x1} y1="20" x2={x2} y2="20"
            stroke={past ? 'rgba(255,255,255,0.06)' : 'rgba(255,255,255,0.12)'} strokeWidth="1.5" />;
        })}
        {stations.map((name, i) => {
          const cx = 32 + i * SP, cur = i === currentIdx;
          const past = direction === 'UP' ? i > currentIdx : i < currentIdx;
          return (
            <g key={`d-${i}`}>
              {cur && <circle cx={cx} cy="20" r={ACTIVE_R + 4} fill="none" stroke={color} strokeWidth="1.2" opacity="0.12" />}
              <circle cx={cx} cy="20" r={cur ? ACTIVE_R : DOT_R}
                fill={cur ? color : past ? 'rgba(255,255,255,0.2)' : 'rgba(255,255,255,0.35)'}
                style={cur ? { filter: `drop-shadow(0 0 5px ${color}80)` } : undefined} />
              <text x={cx} y={cur ? 36 : 32} textAnchor="middle"
                fontSize={cur ? "10" : "8"} fontWeight={cur ? 600 : 400}
                fill={cur ? '#e2e8f0' : past ? '#6b7280' : '#9ca3af'}
                fontFamily="'PingFang SC','Microsoft YaHei','Noto Sans SC',sans-serif">{name}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/* ═══ 指标徽章 ═══ */
function MetricBadge({ label, value, unit, accent }: { label: string; value: string; unit: string; accent: string }) {
  return (
    <div className="flex items-center gap-2 px-3 py-1.5 rounded"
      style={{ border: '1px solid rgba(255,255,255,0.03)', background: 'rgba(255,255,255,0.01)' }}>
      <span className="text-[8px] font-semibold uppercase tracking-[0.12em] text-[#6b7280]">{label}</span>
      <span className="text-[13px] font-bold font-mono" style={{ color: accent }}>{value}</span>
      {unit && <span className="text-[8px] font-medium text-[#6b7280]">{unit}</span>}
    </div>
  );
}

/* ═══ 状态指示 ═══ */
function StateIndicator({ state }: { state: string }) {
  const c = state === 'RUNNING' ? '#22c55e' : state === 'PAUSED' ? '#eab308' : '#475569';
  return (
    <div className="flex items-center justify-center gap-2 py-1.5 rounded select-none"
      style={{ border: '1px solid rgba(255,255,255,0.03)' }}>
      <span className="w-1.5 h-1.5 rounded-full" style={{
        backgroundColor: c,
        boxShadow: state === 'RUNNING' ? `0 0 4px ${c}` : 'none',
      }} />
      <span className="text-[9px] font-medium uppercase tracking-[0.1em] text-[#9ca3af]">{state || 'IDLE'}</span>
    </div>
  );
}

/* ═══ 牵引/制动进度条 ═══ */
function DriveBar({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="select-none">
      <div className="flex justify-between items-center mb-1">
        <span className="text-[7px] font-semibold uppercase tracking-[0.14em] text-[#6b7280]">{label}</span>
        <span className="text-[10px] font-bold font-mono" style={{ color }}>{value.toFixed(1)}%</span>
      </div>
      <div className="h-1.5 rounded-full w-full" style={{ background: 'rgba(255,255,255,0.06)' }}>
        <div className="h-full rounded-full transition-all duration-200" style={{
          width: `${Math.min(value, 100)}%`,
          background: color,
          boxShadow: value > 5 ? `0 0 6px ${color}40` : 'none',
        }} />
      </div>
    </div>
  );
}

/* ═══ 运行信息行 ═══ */
function InfoRow({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="flex justify-between items-center py-0.5 select-none">
      <span className="text-[7px] font-semibold uppercase tracking-[0.12em] text-[#6b7280]">{label}</span>
      <span className="text-[11px] font-bold font-mono" style={{ color }}>{value}</span>
    </div>
  );
}

/* ═══ 待定驾驶舱 ═══ */
function PendingCab({ line, color }: { line: MetroLineData | undefined; color: string }) {
  const name = line ? line.name : '未知线路';
  return (
    <div className="flex-1 flex flex-col items-center justify-center select-none" style={{ gap: 28 }}>
      <svg viewBox="0 0 340 240" className="w-[260px]">
        <path d="M 62 140 A 108 108 0 0 1 278 140" fill="none" stroke="rgba(255,255,255,0.02)" strokeWidth="12" strokeLinecap="round" />
        <text x="170" y="188" textAnchor="middle" fontSize="44" fontWeight="700" fill="rgba(255,255,255,0.03)" fontFamily="'JetBrains Mono'">--</text>
        <text x="170" y="212" textAnchor="middle" fontSize="11" fill="rgba(255,255,255,0.02)">km/h</text>
      </svg>
      <div className="flex flex-col items-center" style={{ gap: 12 }}>
        <div className="flex items-center gap-2">
          <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: color }} />
          <span className="text-[13px] font-semibold text-[#334155]">{name}</span>
        </div>
        <span className="text-[10px] font-medium px-3 py-1 rounded-full font-mono select-none"
          style={{ color: 'rgba(245,158,11,0.45)', border: '1px solid rgba(245,158,11,0.1)', background: 'rgba(245,158,11,0.02)' }}>
          仿真引擎待定
        </span>
        <p className="text-[9px] text-[#1e293b] text-center leading-relaxed max-w-[280px]">
          该线路轨旁设备数据与仿真引擎尚未接入。<br />切换至 <span style={{ color }}>9 号线</span> 以查看实时驾驶数据。
        </p>
      </div>
    </div>
  );
}

function hexToRgb(hex: string): string {
  if (hex.startsWith('#')) hex = hex.slice(1);
  if (hex.length === 3) hex = hex.split('').map(c => c + c).join('');
  const n = parseInt(hex, 16);
  return `${(n >> 16) & 255},${(n >> 8) & 255},${n & 255}`;
}

function SpeedRunSelector({ runs, activeRunId, viewedRunId, onSelect }: {
  runs: SpeedRunRecord[];
  activeRunId?: string;
  viewedRunId: string | null;
  onSelect: (runId: string | null) => void;
}) {
  const completedRuns = runs.filter((run) => run.completed).reverse();
  return (
    <div className="flex items-center gap-2 px-8 py-1 rounded" style={{ background: 'rgba(255,255,255,0.02)' }}>
      <span className="text-[7px] font-semibold uppercase tracking-[0.12em] text-[#6b7280] shrink-0">区间记录</span>
      <select
        aria-label="选择速度曲线区间"
        value={viewedRunId ?? '__live__'}
        onChange={(event) => onSelect(event.target.value === '__live__' ? null : event.target.value)}
        className="min-w-0 flex-1 text-[8px] font-mono rounded px-1.5 py-1 cursor-pointer"
        style={{ color: '#cbd5e1', background: '#172033', border: '1px solid rgba(255,255,255,0.08)' }}
      >
        <option value="__live__">
          实时 · {runs.find((run) => run.id === activeRunId)?.startStation ?? '--'} → {runs.find((run) => run.id === activeRunId)?.endStation ?? '--'}
        </option>
        {completedRuns.map((run) => (
          <option key={run.id} value={run.id}>
            {run.startedAtSimTime} · {run.startStation} → {run.endStation}
          </option>
        ))}
      </select>
      <span className="text-[7px] text-[#475569] shrink-0">{completedRuns.length} 历史</span>
    </div>
  );
}

/* ═══ 速度曲线对比图 ═══ */
function SpeedCurveChart({
  profile,
  history,
  currentSpeedMps,
  currentPositionM,
  pathTotalLengthM,
  profileSource,
  startStation,
  endStation,
}: {
  profile: Array<{ positionM: number; speedMps: number; localSpeedLimitMps?: number }>;
  history: Array<{ positionM: number; speedMps: number; targetSpeedMps?: number; localSpeedLimitMps?: number }>;
  currentSpeedMps: number;
  currentPositionM: number;
  pathTotalLengthM: number;
  profileSource: string;
  startStation: string;
  endStation: string;
}) {
  const W = 320; const H = 90; const PAD = { t: 8, r: 12, b: 22, l: 32 };
  const iw = W - PAD.l - PAD.r; const ih = H - PAD.t - PAD.b;
  const limitSeries = profile.some(p => p.localSpeedLimitMps !== undefined)
    ? profile
        .filter(p => p.localSpeedLimitMps !== undefined)
        .map(p => ({ positionM: p.positionM, speedMps: p.localSpeedLimitMps ?? 0 }))
    : history
        .filter(p => p.localSpeedLimitMps !== undefined)
        .map(p => ({ positionM: p.positionM, speedMps: p.localSpeedLimitMps ?? 0 }));
  const allSpeeds = [
    ...profile.map(p => p.speedMps),
    ...history.map(p => p.speedMps),
    ...limitSeries.map(p => p.speedMps),
    currentSpeedMps,
  ];
  const maxSpeed = Math.max(25, Math.ceil(Math.max(...allSpeeds, 0) * 1.1));

  const toX = (pos: number, minPos: number, maxPos: number) =>
    PAD.l + ((pos - minPos) / (maxPos - minPos || 1)) * iw;
  const toY = (v: number) => PAD.t + (1 - v / maxSpeed) * ih;

  // 计算 X 轴范围
  const allPositions = [...profile.map(p => p.positionM), ...history.map(p => p.positionM)];
  const profileEndM = profile.length > 0 ? profile[profile.length - 1].positionM : 0;
  const stableEndM = Math.max(pathTotalLengthM, profileEndM, 1);
  const minPos = 0;
  const maxPos = stableEndM > 1 ? stableEndM : (allPositions.length ? Math.max(...allPositions, 1500) : 1500);

  const profilePath = profile.length > 1
    ? `M${toX(profile[0].positionM, minPos, maxPos)},${toY(profile[0].speedMps)}` +
      profile.slice(1).map(p => `L${toX(p.positionM, minPos, maxPos)},${toY(p.speedMps)}`).join('')
    : '';
  const histPath = history.length > 1
    ? `M${toX(history[0].positionM, minPos, maxPos)},${toY(history[0].speedMps)}` +
      history.slice(1).map(p => `L${toX(p.positionM, minPos, maxPos)},${toY(p.speedMps)}`).join('')
    : '';
  const limitPath = limitSeries.length > 1
    ? `M${toX(limitSeries[0].positionM, minPos, maxPos)},${toY(limitSeries[0].speedMps)}` +
      limitSeries.slice(1).map(p => `L${toX(p.positionM, minPos, maxPos)},${toY(p.speedMps)}`).join('')
    : '';
  const planLabel = profileSource === 'DCDP_STRICT'
    ? 'DCDP'
    : 'PLAN';

  return (
    <div className="select-none" style={{ position: 'relative' }}>
      <div className="flex items-center justify-between mb-1" style={{ paddingLeft: 32, paddingRight: 12 }}>
        <span className="text-[7px] font-semibold uppercase tracking-[0.12em] text-[#6b7280]">Speed-Position</span>
        <div className="flex items-center gap-2" style={{ fontSize: 7 }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
            <span style={{ width: 8, height: 1, background: '#3b82f6', display: 'inline-block', borderRadius: 1 }} />
            <span className="text-[#64748b]">{planLabel}</span>
          </span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
            <span style={{ width: 8, height: 1, background: '#22c55e', display: 'inline-block', borderRadius: 1 }} />
            <span className="text-[#64748b]">ACT</span>
          </span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
            <span style={{ width: 8, height: 1, background: '#f59e0b', display: 'inline-block', borderRadius: 1 }} />
            <span className="text-[#64748b]">LIMIT</span>
          </span>
        </div>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} style={{ display: 'block' }}>
        {/* 网格线 */}
        {[0, 0.25, 0.5, 0.75, 1].map(r => {
          const y = PAD.t + r * ih;
          return <line key={`g${r}`} x1={PAD.l} y1={y} x2={W - PAD.r} y2={y} stroke="rgba(255,255,255,0.04)" strokeWidth={0.5} />;
        })}
        {/* Y轴标签 */}
        <text x={PAD.l - 4} y={PAD.t + 3} textAnchor="end" fill="#64748b" fontSize={6} fontFamily="monospace">{maxSpeed * 3.6 | 0}</text>
        <text x={PAD.l - 4} y={PAD.t + ih + 3} textAnchor="end" fill="#64748b" fontSize={6} fontFamily="monospace">0</text>
        <text x={PAD.l - 4} y={PAD.t + ih / 2 + 3} textAnchor="end" fill="#475569" fontSize={5.5} fontFamily="monospace">{(maxSpeed * 3.6 / 2) | 0}</text>
        {/* X轴标签 — 站名 */}
        <text x={PAD.l} y={H - 3} textAnchor="start" fill="#94a3b8" fontSize={6.5} fontFamily="monospace" fontWeight={600}>{startStation}</text>
        <text x={W - PAD.r} y={H - 3} textAnchor="end" fill="#94a3b8" fontSize={6.5} fontFamily="monospace" fontWeight={600}>{endStation}</text>
        {/* 局部限速线 */}
        {limitPath && <path d={limitPath} fill="none" stroke="#f59e0b" strokeWidth={1} opacity={0.45} />}
        {/* 规划曲线 */}
        {profilePath && <path d={profilePath} fill="none" stroke="#3b82f6" strokeWidth={1.2} strokeDasharray="3,2" opacity={0.7} />}
        {/* 实际曲线 */}
        {histPath && <path d={histPath} fill="none" stroke="#22c55e" strokeWidth={1.2} opacity={0.9} />}
        {/* 当前速度点 */}
        {currentPositionM > 0 && (
          <circle cx={toX(currentPositionM, minPos, maxPos)} cy={toY(currentSpeedMps)} r={2.5}
            fill="#22c55e" stroke="#111827" strokeWidth={1}
            style={{ filter: 'drop-shadow(0 0 4px rgba(34,197,94,0.6))' }} />
        )}
      </svg>
    </div>
  );
}

/* ── 速度-时间曲线 ── */
function SpeedTimeCurveChart({
  history,
  currentSpeedMps,
  elapsedS,
}: {
  history: Array<{ elapsedS: number; speedMps: number }>;
  currentSpeedMps: number;
  elapsedS: number;
}) {
  const W = 320; const H = 80; const PAD = { t: 8, r: 12, b: 18, l: 32 };
  const iw = W - PAD.l - PAD.r; const ih = H - PAD.t - PAD.b;
  const maxSpeed = 25;

  const maxT = history.length > 0 ? Math.max(history[history.length - 1].elapsedS + 10, 60) : 60;
  const toX = (t: number) => PAD.l + (t / maxT) * iw;
  const toY = (v: number) => PAD.t + (1 - v / maxSpeed) * ih;

  const histPath = history.length > 1
    ? `M${toX(history[0].elapsedS)},${toY(history[0].speedMps)}` +
      history.slice(1).map(p => `L${toX(p.elapsedS)},${toY(p.speedMps)}`).join('')
    : '';

  return (
    <div className="select-none" style={{ position: 'relative' }}>
      <div className="flex items-center justify-between mb-1" style={{ paddingLeft: 32, paddingRight: 12 }}>
        <span className="text-[7px] font-semibold uppercase tracking-[0.12em] text-[#6b7280]">Speed-Time</span>
        <span className="text-[7px] text-[#475569]">{elapsedS.toFixed(1)}s</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} style={{ display: 'block' }}>
        {[0, 0.25, 0.5, 0.75, 1].map(r => {
          const y = PAD.t + r * ih;
          return <line key={`gt${r}`} x1={PAD.l} y1={y} x2={W - PAD.r} y2={y} stroke="rgba(255,255,255,0.04)" strokeWidth={0.5} />;
        })}
        <text x={PAD.l - 4} y={PAD.t + 3} textAnchor="end" fill="#64748b" fontSize={6} fontFamily="monospace">{maxSpeed * 3.6 | 0}</text>
        <text x={PAD.l - 4} y={PAD.t + ih + 3} textAnchor="end" fill="#64748b" fontSize={6} fontFamily="monospace">0</text>
        <text x={PAD.l} y={H - 2} textAnchor="start" fill="#475569" fontSize={5.5} fontFamily="monospace">0s</text>
        <text x={W - PAD.r} y={H - 2} textAnchor="end" fill="#475569" fontSize={5.5} fontFamily="monospace">{maxT | 0}s</text>
        {[0.25, 0.5, 0.75].map(r => (
          <text key={`xt${r}`} x={PAD.l + r * iw} y={H - 2} textAnchor="middle" fill="#334155" fontSize={5} fontFamily="monospace">{(maxT * r) | 0}s</text>
        ))}
        {histPath && <path d={histPath} fill="none" stroke="#22c55e" strokeWidth={1.2} opacity={0.9} />}
        {elapsedS > 0 && (
          <circle cx={toX(elapsedS)} cy={toY(currentSpeedMps)} r={2.5}
            fill="#22c55e" stroke="#111827" strokeWidth={1}
            style={{ filter: 'drop-shadow(0 0 4px rgba(34,197,94,0.6))' }} />
        )}
      </svg>
    </div>
  );
}
