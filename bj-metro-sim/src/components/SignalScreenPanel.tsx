import { useSimStore } from '../store/useSimStore';

/* ═══════════════════════ SVG 速度仪表盘 ═══════════════════════ */
function SpeedGauge({ speedKmh, limitKmh, targetKmh }: { speedKmh: number; limitKmh: number; targetKmh: number }) {
  const CX = 150;
  const CY = 115;
  const R = 88;
  const MAX = 90;
  const START_ANGLE = 145;
  const SWEEP = 250;

  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const arcPoint = (deg: number, r: number) => ({
    x: CX + r * Math.cos(toRad(deg)),
    y: CY + r * Math.sin(toRad(deg)),
  });

  const needleAngle = START_ANGLE + (Math.min(speedKmh, MAX) / MAX) * SWEEP;

  const arcPath = (start: number, end: number, radius: number) => {
    const s = arcPoint(start, radius);
    const e = arcPoint(end, radius);
    const dist = ((end - start) % 360 + 360) % 360;
    const large = dist > 180 ? 1 : 0;
    return `M ${s.x} ${s.y} A ${radius} ${radius} 0 ${large} 1 ${e.x} ${e.y}`;
  };

  const speedRatio = speedKmh / MAX;
  const arcColor = speedRatio > 0.85 ? 'var(--red)'
    : speedRatio > 0.6 ? 'var(--amber)'
    : 'var(--cyan)';

  return (
    <div className="relative">
      <svg viewBox="0 0 300 175" className="w-full">
        {/* 背景弧 */}
        <path d={arcPath(START_ANGLE, START_ANGLE + SWEEP, R)}
          fill="none" stroke="rgba(255,255,255,0.04)" strokeWidth="8" strokeLinecap="round" />

        {/* 活跃弧 */}
        {speedKmh > 0 && (
          <path d={arcPath(START_ANGLE, needleAngle, R)}
            fill="none" stroke={arcColor}
            strokeWidth="8" strokeLinecap="round"
            style={{
              filter: `drop-shadow(0 0 4px ${arcColor}60)`,
              transition: 'stroke 300ms ease',
            }}
          />
        )}

        {/* 刻度 */}
        {[...Array(19)].map((_, i) => {
          const val = i * 5;
          const isMajor = val % 10 === 0;
          const a = START_ANGLE + (val / MAX) * SWEEP;
          const inner = arcPoint(a, isMajor ? R - 16 : R - 10);
          const outer = arcPoint(a, R - 1);
          return (
            <line key={`tick-${i}`}
              x1={inner.x} y1={inner.y} x2={outer.x} y2={outer.y}
              stroke={val <= speedKmh ? arcColor : 'rgba(255,255,255,0.08)'}
              strokeWidth={isMajor ? 1.5 : 0.5}
            />
          );
        })}

        {/* 刻度标签 */}
        {[0, 20, 40, 60, 80].map((val) => {
          const a = START_ANGLE + (val / MAX) * SWEEP;
          const p = arcPoint(a, R - 26);
          return (
            <text key={`label-${val}`}
              x={p.x} y={p.y}
              textAnchor="middle" dominantBaseline="middle"
              fontSize="9" fontWeight="500"
              fill="var(--text-dim)"
              fontFamily="-apple-system, 'Inter', sans-serif"
            >
              {val}
            </text>
          );
        })}

        {/* 指针 + 尾重锤 */}
        <g transform={`rotate(${needleAngle}, ${CX}, ${CY})`}>
          <polygon
            points={`${CX + 3},${CY - 2.5} ${CX + 3},${CY + 2.5} ${CX + R - 8},${CY + 1} ${CX + R - 8},${CY - 1}`}
            fill="var(--red)"
            style={{ filter: 'drop-shadow(0 0 3px rgba(255,69,58,0.4))' }}
          />
          <polygon
            points={`${CX - 3},${CY - 3} ${CX - 3},${CY + 3} ${CX - 14},${CY + 2} ${CX - 14},${CY - 2}`}
            fill="var(--text-dim)" opacity="0.25"
          />
        </g>

        {/* 中心圆 */}
        <circle cx={CX} cy={CY} r="8"
          fill="var(--elevated-solid)"
          stroke="rgba(255,255,255,0.08)" strokeWidth="1.5" />

        {/* 速度数字 */}
        <text x={CX} y={CY + 28}
          textAnchor="middle"
          fontSize="32" fontWeight="700"
          fill="var(--text)"
          fontFamily="'JetBrains Mono', 'SF Mono', 'Consolas', monospace"
        >
          {String(Math.round(speedKmh)).padStart(2, '0')}
        </text>
        <text x={CX} y={CY + 44}
          textAnchor="middle"
          fontSize="9" fill="var(--text-dim)"
          fontFamily="-apple-system, 'Inter', sans-serif"
        >
          km/h
        </text>

        {/* 动态光晕 */}
        <circle cx={CX} cy={CY} r={Math.max(12, speedKmh * 0.6)}
          fill="none" stroke={arcColor}
          strokeWidth="1"
          opacity={0.12 + speedRatio * 0.18}
          style={{ transition: 'r 200ms ease, opacity 200ms ease' }}
        />
      </svg>
    </div>
  );
}

/* 北京地铁站台 PIS 风格：水平站序条 */
function PISStationStrip({ stations, currentIdx, direction }: { stations: string[]; currentIdx: number; direction: string }) {
  const STATION_H = 16;
  const DOT_R = 4;
  const ACTIVE_DOT_R = 6;
  const SPACING = 36;

  return (
    <div className="overflow-x-auto" style={{ scrollbarWidth: 'none' }}>
    <svg
      width={stations.length * SPACING + 60}
      height={80}
      viewBox={`0 0 ${stations.length * SPACING + 60} 80`}
    >
        {/* ═══ 连线 ═══ */}
        {stations.map((_, i) => {
          if (i === stations.length - 1) return null;
          const x1 = 30 + i * SPACING + ACTIVE_DOT_R;
          const x2 = 30 + (i + 1) * SPACING - ACTIVE_DOT_R;
          const y = STATION_H + ACTIVE_DOT_R;
          const isPast = i < currentIdx;
          return (
            <line
              key={`line-${i}`}
              x1={x1} y1={y} x2={x2} y2={y}
              stroke={isPast ? 'rgba(255,255,255,0.08)' : 'rgba(255,255,255,0.15)'}
              strokeWidth="1.5"
            />
          );
        })}

        {/* ═══ 站点圆点 ═══ */}
        {stations.map((name, i) => {
          const cx = 30 + i * SPACING;
          const cy = STATION_H + ACTIVE_DOT_R;
          const isCurrent = i === currentIdx;
          const isPast = i < currentIdx;
          const isEnd = i === stations.length - 1;

          return (
            <g key={`dot-${i}`}>
              {/* 当前站光晕 */}
              {isCurrent && (
                <circle cx={cx} cy={cy} r={ACTIVE_DOT_R + 5}
                  fill="none"
                  stroke="var(--l9)"
                  strokeWidth="1"
                  opacity="0.25"
                />
              )}
              {/* 圆点 */}
              {isEnd && !isCurrent ? (
                <rect
                  x={cx - 4} y={cy - 4}
                  width={8} height={8}
                  rx={1.5}
                  fill={isPast ? 'rgba(255,255,255,0.1)' : 'var(--cyan)'}
                  opacity={isPast ? 0.4 : 0.6}
                />
              ) : (
                <circle
                  cx={cx} cy={cy}
                  r={isCurrent ? ACTIVE_DOT_R : DOT_R}
                  fill={
                    isCurrent ? 'var(--l9)'
                    : isPast ? 'rgba(255,255,255,0.1)'
                    : 'rgba(255,255,255,0.45)'
                  }
                  style={isCurrent ? { filter: 'drop-shadow(0 0 6px rgba(168,214,74,0.5))' } : undefined}
                />
              )}
            </g>
          );
        })}

        {/* ═══ 站名标签 ═══ */}
        {stations.map((name, i) => {
          const cx = 30 + i * SPACING;
          const isCurrent = i === currentIdx;
          const isPast = i < currentIdx;
          const tY = STATION_H + ACTIVE_DOT_R + 16;

          return (
            <text
              key={`label-${i}`}
              x={cx}
              y={tY}
              textAnchor="middle"
              fontSize={isCurrent ? 11 : 9}
              fontWeight={isCurrent ? 600 : 400}
              fill={
                isCurrent ? 'var(--text)'
                : isPast ? 'rgba(255,255,255,0.08)'
                : 'var(--text-dim)'
              }
              fontFamily="'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', sans-serif"
            >
              {name}
            </text>
          );
        })}

        {/* ═══ 当前站下划线 + 脉冲点 ═══ */}
        {(() => {
          const cx = 30 + currentIdx * SPACING;
          const cy = STATION_H + ACTIVE_DOT_R;
          return (
            <>
              <line
                x1={cx - 8} y1={cy + 24} x2={cx + 8} y2={cy + 24}
                stroke="var(--l9)" strokeWidth="1.5" opacity="0.5"
              />
              <circle cx={cx} cy={cy + 24} r={1.5} fill="var(--l9)" opacity="0.9" />
            </>
          );
        })()}
      </svg>
    </div>
  );
}

export default function SignalScreenPanel() {
  const {
    driveMode, currentStation, nextStation, distanceToNextStationM, targetDistanceM,
    currentSpeedMps, permittedSpeedMps, targetSpeedMps,
    runDirection, stationIndex, line9Stations,
  } = useSimStore();

  const dist = (distanceToNextStationM / 1000).toFixed(2);
  const pct = Math.min(100, Math.max(0, ((targetDistanceM - distanceToNextStationM) / (targetDistanceM || 1)) * 100));

  return (
    <div className="glass p-5 space-y-5">
      {/* ═══ 模式 + 方向 ═══ */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="label" style={{ color: 'var(--text-muted)' }}>MODE</span>
          <span className="board-num text-[16px] font-bold" style={{ color: 'var(--cyan)' }}>
            {driveMode}
          </span>
        </div>
        <span
          className="board-num text-[11px] rounded-md"
          style={{
            padding: '4px 9px',
            color: runDirection === 'UP' ? 'var(--cyan)' : 'var(--l9)',
            border: `1px solid ${runDirection === 'UP' ? 'rgba(100,210,255,0.15)' : 'rgba(168,214,74,0.15)'}`,
            background: runDirection === 'UP' ? 'rgba(100,210,255,0.05)' : 'rgba(168,214,74,0.05)',
          }}
        >
          {runDirection === 'UP' ? '上行' : '下行'}
        </span>
      </div>

      {/* ═══ 速度仪表盘 ═══ */}
      <SpeedGauge
        speedKmh={currentSpeedMps * 3.6}
        limitKmh={permittedSpeedMps * 3.6}
        targetKmh={targetSpeedMps * 3.6}
      />

      {/* ═══ 距离进度 ═══ */}
      <div>
        <div className="flex justify-between items-baseline mb-1.5">
          <span className="label" style={{ color: 'var(--text-muted)' }}>距下一站</span>
          <span className="board-num text-[16px] font-bold tabular-nums" style={{ color: 'var(--cyan)' }}>
            {dist} km
          </span>
        </div>
        <div className="h-1.5 w-full rounded-full overflow-hidden" style={{ background: 'rgba(255,255,255,0.04)' }}>
          <div
            className="h-full rounded-full"
            style={{
              width: `${pct}%`,
              background: 'var(--cyan)',
              transition: 'width 400ms cubic-bezier(0.33, 1, 0.68, 1)',
            }}
          />
        </div>
        <div className="flex justify-between mt-1 label" style={{ color: 'var(--text-muted)' }}>
          <span>停车点 {targetDistanceM}m</span>
          <span>{pct.toFixed(0)}%</span>
        </div>
      </div>

      {/* ═══ divider ═══ */}
      <div className="glass-divider" />

      {/* ═══ 站序 — 北京地铁 PIS 风格 ═══ */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <span className="label" style={{ color: 'var(--text-muted)' }}>线路运行</span>
          <span className="flex items-center gap-1.5">
            <span className="board-num text-[10px]" style={{ color: 'var(--text-dim)' }}>下一站</span>
            <span
              className="board-num text-[11px] px-2 py-0.5 rounded-md"
              style={{
                color: 'var(--cyan)',
                border: '1px solid rgba(100,210,255,0.12)',
                background: 'rgba(100,210,255,0.04)',
              }}
            >
              {nextStation}
            </span>
          </span>
        </div>

        <PISStationStrip stations={line9Stations} currentIdx={stationIndex} direction={runDirection} />

        {/* ═══ 当前站 → 下一站 ═══ */}
        <div className="flex items-center justify-center gap-2 mt-3 pt-3" style={{ borderTop: '1px solid rgba(255,255,255,0.04)' }}>
          <span className="board-num text-[12px]" style={{ color: 'var(--text-dim)' }}>
            {currentStation}
          </span>
          <svg width="18" height="8" viewBox="0 0 18 8" fill="none">
            <path d="M2 4H14M14 4L11 2M14 4L11 6"
              stroke="var(--cyan)" strokeWidth="1.2" opacity="0.7"
              strokeLinecap="round" strokeLinejoin="round"
            />
          </svg>
          <span className="board-num text-[13px] font-semibold" style={{ color: 'var(--cyan)' }}>
            {nextStation}
          </span>
        </div>
      </div>
    </div>
  );
}
