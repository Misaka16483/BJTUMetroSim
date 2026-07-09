import { useState, useMemo } from 'react';
import { useSimStore } from '../store/useSimStore';
import { lineColor } from './LineSelector';
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
      {hasEngine ? <ActiveCab color={color} /> : <PendingCab line={line} color={color} />}
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
function ActiveCab({ color }: { color: string }) {
  const {
    nextStation, distanceToNextStationM, stationIndex, line9Stations,
    runDirection, currentSpeedMps, simTime, avgLoadRate, totalPassengers,
    backendStatus, engineClockState,
    startBackendSim, pauseBackendSim, resumeBackendSim, stopBackendSim,
  } = useSimStore();

  const speedKmh = currentSpeedMps * 3.6;
  const isBackend = backendStatus === 'connected';
  const eta = distanceToNextStationM > 0 && currentSpeedMps > 0
    ? Math.ceil(distanceToNextStationM / currentSpeedMps) : 0;

  return (
    <div className="flex-1 flex flex-col min-h-0" style={{ background: '#111827' }}>

      {/* ── 中部顶部：站台信息 ── */}
      <div className="shrink-0 px-8 pt-6 pb-4">
        <StationRouteCard
          current={line9Stations[stationIndex] || '--'}
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
            <MetricBadge label="TARGET" value="80" unit="km/h" accent="#00a8ff" />
            <MetricBadge label="LOAD" value={`${avgLoadRate}%`} unit="" accent="#8FC31F" />
            <MetricBadge label="PAX" value={String(totalPassengers)} unit="" accent="#94a3b8" />
            <MetricBadge label="MODE" value="AM-CBTC" unit="" accent="#8FC31F" />
          </div>
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

          {isBackend && <ControlButtons state={engineClockState} onStart={startBackendSim} onPause={pauseBackendSim} onResume={resumeBackendSim} onStop={stopBackendSim} />}

          <div className="flex flex-col" style={{ gap: 6 }}>
            <StateIndicator state={engineClockState} />
            <div className="flex justify-center gap-4 pt-2">
              <span className="text-[8px] font-medium uppercase tracking-[0.12em] text-[#6b7280] flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: '#8FC31F', opacity: 0.6 }} />ATP
              </span>
              <span className="text-[8px] font-medium uppercase tracking-[0.12em] text-[#6b7280] flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: '#8FC31F', opacity: 0.6 }} />ATO
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

/* ═══ 控制按钮 ═══ */
function ControlButtons({ state, onStart, onPause, onResume, onStop }: {
  state: string; onStart: () => void; onPause: () => void; onResume: () => void; onStop: () => void;
}) {
  const isActive = state === 'RUNNING' || state === 'PAUSED';
  return (
    <div className="flex flex-col" style={{ gap: 6 }}>
      {!isActive ? (
        <CabButton onClick={onStart} color="#22c55e" label="START" icon="play" />
      ) : state === 'RUNNING' ? (
        <>
          <CabButton onClick={onPause} color="#eab308" label="PAUSE" icon="pause" />
          <CabButton onClick={onStop} color="#ef4444" label="STOP" icon="stop" />
        </>
      ) : (
        <>
          <CabButton onClick={onResume} color="#22c55e" label="RESUME" icon="play" />
          <CabButton onClick={onStop} color="#ef4444" label="STOP" icon="stop" />
        </>
      )}
    </div>
  );
}

function CabButton({ onClick, color, label, icon }: { onClick: () => void; color: string; label: string; icon: 'play' | 'pause' | 'stop' }) {
  return (
    <button onClick={onClick}
      className="flex items-center justify-center gap-2 cursor-pointer rounded py-2.5 transition-colors duration-150 w-full select-none"
      style={{ color, background: `${color}0d`, border: `1px solid ${color}26` }}>
      {icon === 'play' && <svg width="10" height="10" viewBox="0 0 10 10"><polygon points="2,1 9,5 2,9" fill="currentColor" /></svg>}
      {icon === 'pause' && <svg width="10" height="10" viewBox="0 0 10 10"><rect x="1" y="1" width="3" height="8" rx="0.5" fill="currentColor" /><rect x="6" y="1" width="3" height="8" rx="0.5" fill="currentColor" /></svg>}
      {icon === 'stop' && <svg width="10" height="10" viewBox="0 0 10 10"><rect x="1.5" y="1.5" width="7" height="7" rx="1.2" fill="currentColor" /></svg>}
      <span className="text-[10px] font-bold tracking-[0.12em]">{label}</span>
    </button>
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
