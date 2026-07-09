import { useState, useMemo, useRef, useEffect, useCallback } from 'react';
import { useSimStore } from '../store/useSimStore';
import LineSelector, { lineColor } from './LineSelector';
import type { MetroLineData } from '../data/amapMetroApi';
import type { StationInterlockingData } from '../data/stationInterlockingData';
import { getInterlockingData, getInterlockingStations } from '../data/stationInterlockingData';

/* ── Canvas 颜色常量 ── */
const COLORS = {
  bg: '#07101b',
  green: '#44c854',
  amber: '#f0a030',
  red: '#f85149',
  l9: '#8FC31F',
  track: '#2b3c52',
  routeAvail: 'rgba(255,255,255,0.18)',
  switchLocked: '#f0a030',
  switchUnlocked: '#52647b',
  signalMast: '#52647b',
  stationFill: 'rgba(143,195,31,0.08)',
  stationStroke: 'rgba(143,195,31,0.45)',
  textPrimary: '#c7d5e8',
  textSecondary: '#8ba0bb',
  textMuted: '#5f7088',
};

function cAspect(aspect: string) {
  if (aspect === 'GREEN') return COLORS.green;
  if (aspect === 'YELLOW') return COLORS.amber;
  return COLORS.red;
}

function cRoute(state: string) {
  if (state === 'LOCKED') return COLORS.l9;
  if (state === 'OCCUPIED') return COLORS.red;
  return COLORS.routeAvail;
}

/* ════════════════ Canvas 联锁图组件 ════════════════ */
const LOGICAL_W = 1000;
const LOGICAL_H = 430;

function InterlockingCanvas({ data }: { data: StationInterlockingData }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const rafRef = useRef(0);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;

    const rect = container.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;

    const dpr = window.devicePixelRatio || 1;
    const w = rect.width * dpr;
    const h = rect.height * dpr;

    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
      canvas.style.width = `${rect.width}px`;
      canvas.style.height = `${rect.height}px`;
    }

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const sx = rect.width / LOGICAL_W;
    const sy = rect.height / LOGICAL_H;

    ctx.setTransform(dpr * sx, 0, 0, dpr * sy, 0, 0);

    // ── 背景 ──
    ctx.fillStyle = COLORS.bg;
    ctx.fillRect(0, 0, LOGICAL_W, LOGICAL_H);

    // ── 轨道 ──
    ctx.strokeStyle = COLORS.track;
    ctx.lineWidth = 5;
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(70, 152);
    ctx.lineTo(930, 152);
    ctx.stroke();

    ctx.beginPath();
    ctx.moveTo(70, 278);
    ctx.lineTo(930, 278);
    ctx.stroke();

    // ── 站台区域 ──
    ctx.fillStyle = COLORS.stationFill;
    ctx.strokeStyle = COLORS.stationStroke;
    ctx.lineWidth = 1;

    roundRect(ctx, 395, 105, 210, 70, 4);
    ctx.fill();
    ctx.stroke();

    roundRect(ctx, 395, 255, 210, 70, 4);
    ctx.fill();
    ctx.stroke();

    ctx.font = '18px "PingFang SC","Microsoft YaHei",sans-serif';
    ctx.fillStyle = COLORS.textPrimary;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('上行站台', 500, 140);
    ctx.fillText('下行站台', 500, 290);

    // ── 进路 ──
    for (const route of data.routes) {
      const y = route.direction === 'UP' ? 152 : 278;
      const color = cRoute(route.state);

      ctx.strokeStyle = color;
      ctx.lineWidth = 8;
      ctx.lineCap = 'round';
      ctx.shadowColor = color;
      ctx.shadowBlur = route.state !== 'AVAILABLE' ? 12 : 0;
      ctx.beginPath();
      ctx.moveTo(265, y);
      ctx.lineTo(735, y);
      ctx.stroke();
      ctx.shadowBlur = 0;
    }

    // ── 道岔 ──
    for (const sw of data.switches) {
      const cx = sw.x * 10;
      const cy = 210;
      const stroke = sw.locked ? COLORS.switchLocked : COLORS.switchUnlocked;
      const glow = sw.locked ? 10 : 0;

      // 交叉线
      ctx.strokeStyle = stroke;
      ctx.lineWidth = 4;
      ctx.lineCap = 'round';
      ctx.shadowColor = COLORS.amber;
      ctx.shadowBlur = glow;
      ctx.beginPath();
      ctx.moveTo(cx - 34, cy - 42);
      ctx.lineTo(cx + 34, cy + 42);
      ctx.stroke();
      ctx.shadowBlur = 0;

      // 圆
      ctx.fillStyle = COLORS.bg;
      ctx.strokeStyle = stroke;
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.arc(cx, cy, 12, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();

      // 标签
      ctx.font = '13px "JetBrains Mono",monospace';
      ctx.fillStyle = COLORS.textSecondary;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      ctx.fillText(sw.id, cx, cy + 34);
    }

    // ── 信号机 ──
    for (const signal of data.signals) {
      const sx2 = signal.x * 10;
      const sy2 = signal.direction === 'UP' ? 118 : 302;
      const mastEnd = signal.direction === 'UP' ? sy2 + 40 : sy2 - 40;
      const aspectClr = cAspect(signal.aspect);

      // 桅杆
      ctx.strokeStyle = COLORS.signalMast;
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.moveTo(sx2, sy2);
      ctx.lineTo(sx2, mastEnd);
      ctx.stroke();

      // 发光信号灯
      ctx.fillStyle = aspectClr;
      ctx.shadowColor = aspectClr;
      ctx.shadowBlur = 14;
      ctx.beginPath();
      ctx.arc(sx2, sy2, 10, 0, Math.PI * 2);
      ctx.fill();
      ctx.shadowBlur = 0;

      // 高光核心
      ctx.fillStyle = 'rgba(255,255,255,0.35)';
      ctx.beginPath();
      ctx.arc(sx2 - 2, sy2 - 2, 3.5, 0, Math.PI * 2);
      ctx.fill();

      // 标签
      ctx.font = '13px "JetBrains Mono",monospace';
      ctx.fillStyle = COLORS.textPrimary;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      const labelY = signal.direction === 'UP' ? sy2 - 18 : sy2 + 30;
      ctx.fillText(signal.id, sx2, labelY);
    }

    ctx.setTransform(1, 0, 0, 1, 0, 0);
  }, [data]);

  useEffect(() => {
    draw();
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = requestAnimationFrame(draw);
    });
    if (containerRef.current) observer.observe(containerRef.current);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(rafRef.current);
    };
  }, [draw]);

  return (
    <div ref={containerRef} className="h-[430px] border border-[#1b2a3d] bg-[#07101b]">
      <canvas ref={canvasRef} className="block w-full h-full" />
    </div>
  );
}

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number, y: number, w: number, h: number, r: number,
) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.arcTo(x + w, y, x + w, y + r, r);
  ctx.lineTo(x + w, y + h - r);
  ctx.arcTo(x + w, y + h, x + w - r, y + h, r);
  ctx.lineTo(x + r, y + h);
  ctx.arcTo(x, y + h, x, y + h - r, r);
  ctx.lineTo(x, y + r);
  ctx.arcTo(x, y, x + r, y, r);
  ctx.closePath();
}

/* ════════════════ 主组件 ════════════════ */
export default function StationInterlockingView({ data }: { data: StationInterlockingData }) {
  const metroLines = useSimStore((s) => s.metroLines);
  const [activeLineId, setActiveLineId] = useState('9');
  const [selectedStationCode, setSelectedStationCode] = useState<string>(data.stationCode);

  const activeLine = useMemo(
    () => metroLines.find((l) => l.id === activeLineId),
    [metroLines, activeLineId],
  );

  const hasInterlockingData = activeLineId === '9';
  const stationList = useMemo(
    () => (activeLineId === '9' ? getInterlockingStations() : []),
    [activeLineId],
  );

  const interlockingData = useMemo(() => {
    if (!hasInterlockingData) return null;
    return getInterlockingData(selectedStationCode);
  }, [hasInterlockingData, selectedStationCode]);

  return (
    <div className="h-full min-h-0 flex bg-[#040810]">
      <div className="flex-1 min-w-0 p-5 overflow-auto">
        <LineSelector
          lines={metroLines}
          activeLineId={activeLineId}
          onSelect={setActiveLineId}
          dataLineIds={new Set(['9'])}
        />

        {hasInterlockingData && interlockingData ? (
          <>
            <StationSelector
              stations={stationList}
              activeCode={selectedStationCode}
              onSelect={setSelectedStationCode}
            />

            <div className="flex items-end justify-between gap-4 mb-5">
              <div>
                <div className="text-[11px] uppercase tracking-[0.18em] text-[#5f7088]">
                  Station Interlocking
                </div>
                <h2 className="text-[20px] font-semibold text-[#dce8f8] mt-1">
                  {interlockingData.stationName} 联锁图
                </h2>
                <div className="mt-1 text-[11px] font-mono text-[#5f7088]">
                  {interlockingData.stationCode} · K{(interlockingData.mileageM / 1000).toFixed(3)}
                </div>
              </div>
              <div className="grid grid-cols-3 gap-2 text-right">
                <InterlockingMetric label="Signal" value={interlockingData.signals.length} />
                <InterlockingMetric label="Switch" value={interlockingData.switches.length} />
                <InterlockingMetric label="Route" value={interlockingData.routes.length} />
              </div>
            </div>

            <InterlockingCanvas data={interlockingData} />
          </>
        ) : (
          <InterlockingNoData line={activeLine} />
        )}
      </div>

      {hasInterlockingData && interlockingData && (
        <aside className="w-[300px] border-l border-[#172436] bg-[#07101b] p-4 overflow-auto">
          <div className="text-[10px] uppercase tracking-[0.16em] text-[#5f7088] mb-3">Interlocking State</div>
          <section className="space-y-2">
            {interlockingData.routes.map((route) => (
              <div key={route.id} className="border border-[#172436] bg-[#091827] px-3 py-2">
                <div className="flex items-center justify-between">
                  <span className="text-[12px] text-[#c7d5e8]">{route.id}</span>
                  <span className="text-[10px] font-mono" style={{ color: cRoute(route.state) }}>
                    {route.state}
                  </span>
                </div>
                <div className="mt-1 text-[10px] text-[#5f7088]">
                  {route.from} → {route.to} · {route.direction}
                </div>
              </div>
            ))}
          </section>

          <section className="mt-5 pt-4 border-t border-[#172436] space-y-2">
            {interlockingData.signals.map((signal) => (
              <div key={signal.id} className="flex items-center justify-between text-[11px]">
                <span className="text-[#8ba0bb]">{signal.label}</span>
                <span className="font-mono" style={{ color: cAspect(signal.aspect) }}>
                  {signal.id} {signal.aspect}
                </span>
              </div>
            ))}
          </section>
        </aside>
      )}
    </div>
  );
}

/* ════════════════ 站点选择器 ════════════════ */
function StationSelector({
  stations,
  activeCode,
  onSelect,
}: {
  stations: readonly (readonly [string, string, number])[];
  activeCode: string;
  onSelect: (code: string) => void;
}) {
  return (
    <div className="mb-5">
      <div className="text-[11px] uppercase tracking-[0.18em] text-[#5f7088] mb-3">Station</div>
      <div className="flex flex-wrap gap-1.5">
        {stations.map(([code, name]) => {
          const active = code === activeCode;
          return (
            <button
              key={code}
              type="button"
              onClick={() => onSelect(code)}
              className={`px-2.5 py-1.5 rounded text-[11px] font-medium cursor-pointer
                transition-all duration-150 border
                ${active
                  ? 'border-[#2b4a6b] bg-[#0d1b30] text-[#dce8f8]'
                  : 'border-transparent bg-[#08121e] text-[#6b7d95] hover:border-[#1a2b42] hover:text-[#9aafc8]'}`}
            >
              {name}
              <span className="ml-1.5 font-mono text-[9px] opacity-50">{code}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/* ════════════════ 无数据占位 ════════════════ */
function InterlockingNoData({ line }: { line: MetroLineData | undefined }) {
  const color = line ? lineColor(line.id) : '#5f7088';
  const name = line
    ? (line.name.length <= 3 ? line.name : line.name.replace(/地铁(\d+)号线.*/, '$1号线'))
    : '未知线路';

  return (
    <div className="flex flex-col items-center justify-center pt-20 pb-20">
      <div className="relative w-full max-w-[480px] mb-8">
        <svg viewBox="0 0 480 200" className="w-full">
          <rect x="90" y="85" width="300" height="30" rx="4"
            fill="none" stroke="rgba(255,255,255,0.04)" strokeWidth="2" />
          <line x1="140" y1="100" x2="340" y2="100"
            stroke={color} strokeWidth="3" strokeLinecap="round" opacity="0.3" />
          {[140, 195, 250, 305, 340].map((x, i) => (
            <g key={i}>
              <line x1={x} y1="85" x2={x} y2="65" stroke="#52647b" strokeWidth="1.5" opacity="0.3" />
              <circle cx={x} cy="60" r="6" fill="none" stroke={color} strokeWidth="1" opacity="0.25" />
            </g>
          ))}
          <line x1="220" y1="100" x2="260" y2="60" stroke="rgba(255,255,255,0.04)" strokeWidth="2" />
          <circle cx="260" cy="60" r="7" fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="1.5" strokeDasharray="3 3" />
        </svg>
      </div>

      <div className="text-center space-y-3">
        <div className="flex items-center gap-2 justify-center">
          <span className="w-3 h-3 rounded-full" style={{ backgroundColor: color }} />
          <h3 className="text-[18px] font-semibold text-[#9fb0c8]">{name}</h3>
        </div>
        <div className="text-[12px] text-[#52647b]">暂无联锁数据</div>
        <div className="mt-4 p-4 border border-[#172436] bg-[#07101b] max-w-[360px]">
          <div className="text-[11px] text-[#5f7088] leading-relaxed">
            当前仅 <span className="text-[#8FC31F] font-mono">9号线</span> 已接入
            联锁数据（信号·道岔·进路）。
          </div>
          <div className="mt-2 text-[10px] text-[#3a4f66] leading-relaxed">
            如需扩展其他线路，请配置对应车站的联锁信息。
          </div>
        </div>
      </div>
    </div>
  );
}

function InterlockingMetric({ label, value }: { label: string; value: number }) {
  return (
    <div className="min-w-[62px] border border-[#172436] bg-[#081321] px-2 py-1.5">
      <div className="text-[10px] text-[#5a6a80]">{label}</div>
      <div className="text-[16px] font-mono text-[#f85149]">{value}</div>
    </div>
  );
}
