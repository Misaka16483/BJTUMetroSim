import { useCallback, useEffect, useRef, useState } from 'react';
import type { StationInterlockingData, InterlockingSignal } from '../types/interlocking';

interface Props { data: StationInterlockingData; }

const COLORS: Record<number, string> = { 1: '#58a6ff', 2: '#d29922', 3: '#8b949e' };
const SYMBOLS: Record<number, string> = { 1: '◆', 2: '◇', 3: '●' };
const SIG_LABELS: Record<number, string> = { 1: '主信号', 2: '调车', 3: '预告' };

export default function StationInterlockingView({ data }: Props) {
  const canvasWrapRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [scale, setScale] = useState(1);
  const offsetRef = useRef({ x: 0, y: 0 });
  const dragRef = useRef<{ sx: number; sy: number; ox: number; oy: number } | null>(null);

  const { bounds, tracks, platforms, signals, switches, routes, labels } = data;

  const updateCenter = useCallback(() => {
    const c = canvasRef.current;
    const p = canvasWrapRef.current;
    if (!c || !p) return;
    offsetRef.current = { x: (c.width / 2 - bounds.width * scale) / 2, y: (c.height / 2 - bounds.height * scale) / 2 };
  }, [scale, bounds]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d')!;
    const { x: ox, y: oy } = offsetRef.current;
    const s = scale;

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.save();
    ctx.scale(2, 2);
    ctx.translate(ox, oy);
    ctx.scale(s, s);

    ctx.fillStyle = '#040810';
    ctx.fillRect(-10, -10, bounds.width + 20, bounds.height + 20);

    for (const t of tracks) drawTrack(ctx, t.x, t.y, t.width);

    for (const sw of switches) {
      ctx.strokeStyle = '#d29922';
      ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(sw.x - 40, sw.y1); ctx.lineTo(sw.x - 20, sw.y1); ctx.lineTo(sw.x + 10, sw.y2); ctx.lineTo(sw.x + 30, sw.y2); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(sw.x - 40, sw.y2); ctx.lineTo(sw.x - 20, sw.y2); ctx.lineTo(sw.x + 10, sw.y1); ctx.lineTo(sw.x + 30, sw.y1); ctx.stroke();
      ctx.fillStyle = '#d29922';
      ctx.font = '12px monospace';
      ctx.fillText('△', sw.x - 36, sw.y1 - 5);
      ctx.fillText('△', sw.x - 36, sw.y2 + 16);
    }

    for (const p of platforms) {
      const track = tracks.find(t => t.id === p.trackId);
      if (!track) continue;
      const py = track.y;
      ctx.fillStyle = 'rgba(143,195,31,0.3)';
      ctx.fillRect(p.x - p.width / 2, py - 14, p.width, 28);
      ctx.strokeStyle = '#8FC31F';
      ctx.lineWidth = 1;
      ctx.strokeRect(p.x - p.width / 2, py - 14, p.width, 28);
    }

    for (const sig of signals) {
      const track = tracks.find(t => t.id === sig.trackId);
      if (!track) continue;
      drawSignal(ctx, sig, track.y);
    }

    for (const r of routes) {
      if (r.path.length < 2) continue;
      ctx.strokeStyle = r.color ?? 'rgba(88,166,255,0.15)';
      ctx.lineWidth = 6;
      ctx.setLineDash([6, 4]);
      ctx.beginPath(); ctx.moveTo(r.path[0].x, r.path[0].y);
      for (let i = 1; i < r.path.length; i++) ctx.lineTo(r.path[i].x, r.path[i].y);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    for (const lbl of labels) {
      ctx.fillStyle = lbl.color ?? '#3a4a5a';
      ctx.font = `${lbl.fontSize ?? 9}px ${lbl.font ?? 'sans-serif'}`;
      ctx.textAlign = lbl.align ?? 'left';
      ctx.fillText(lbl.text, lbl.x, lbl.y);
    }

    if (data.directionLabels?.up) {
      ctx.fillStyle = '#2a3a4a';
      ctx.font = '10px sans-serif';
      ctx.textAlign = 'right';
      ctx.fillText(data.directionLabels.up, bounds.width - 40, tracks.find(t => t.dir === 'up')?.y! - 45);
    }
    if (data.directionLabels?.down) {
      ctx.fillStyle = '#2a3a4a';
      ctx.font = '10px sans-serif';
      ctx.textAlign = 'left';
      ctx.fillText(data.directionLabels.down, 40, tracks.find(t => t.dir === 'down')?.y! + 50);
    }

    ctx.restore();
  }, [scale, data, tracks, platforms, signals, switches, routes, labels, bounds]);

  const handleWheel = useCallback((e: WheelEvent) => { e.preventDefault(); setScale(s => Math.max(0.3, Math.min(5, s * (e.deltaY > 0 ? 0.9 : 1.1)))); }, []);
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    dragRef.current = { sx: e.clientX, sy: e.clientY, ox: offsetRef.current.x, oy: offsetRef.current.y };
    const onMove = (ev: MouseEvent) => {
      if (!dragRef.current) return;
      offsetRef.current.x = dragRef.current.ox + (ev.clientX - dragRef.current.sx);
      offsetRef.current.y = dragRef.current.oy + (ev.clientY - dragRef.current.sy);
      draw();
    };
    const onUp = () => { dragRef.current = null; window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp); };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [draw]);

  useEffect(() => { draw(); }, [draw]);
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    el.addEventListener('wheel', handleWheel, { passive: false });
    return () => el.removeEventListener('wheel', handleWheel);
  }, [handleWheel]);

  const updateSize = useCallback(() => {
    const c = canvasRef.current, p = canvasWrapRef.current;
    if (!c || !p) return;
    const w = p.clientWidth, h = p.clientHeight;
    c.width = w * 2; c.height = h * 2;
    c.style.width = w + 'px'; c.style.height = h + 'px';
    updateCenter(); draw();
  }, [draw, updateCenter]);

  useEffect(() => { updateSize(); window.addEventListener('resize', updateSize); return () => window.removeEventListener('resize', updateSize); }, [updateSize]);

  const zoomTo = (s: number) => {
    setScale(Math.max(0.3, Math.min(5, s)));
    setTimeout(() => {
      const c = canvasRef.current, p = canvasWrapRef.current;
      if (!c || !p) return;
      offsetRef.current = { x: (c.width / 2 - bounds.width * s) / 2, y: (c.height / 2 - bounds.height * s) / 2 };
      draw();
    }, 0);
  };

  return (
    <div className="h-full flex bg-[#040810]">
      <div className="flex-1 min-w-0 flex flex-col">
        <div className="flex items-center gap-2 px-4 py-2 shrink-0 border-b border-[#172436]">
          <span className="text-[#5f7088] text-[12px] font-medium uppercase tracking-wider">{data.stationName} 联锁图</span>
          <div className="flex items-center gap-1 ml-3">
            <button onClick={() => zoomTo(scale * 1.4)} className="px-2 py-0.5 text-[13px] bg-[#0d1424] border border-[#1a2240] text-[#8b949e] hover:text-white cursor-pointer">+</button>
            <button onClick={() => zoomTo(scale / 1.4)} className="px-2 py-0.5 text-[13px] bg-[#0d1424] border border-[#1a2240] text-[#8b949e] hover:text-white cursor-pointer">−</button>
            <button onClick={() => zoomTo(1)} className="px-2 py-0.5 text-[11px] bg-[#0d1424] border border-[#1a2240] text-[#8b949e] hover:text-white cursor-pointer">重置</button>
            <span className="text-[11px] text-[#3a4a60] ml-1 font-mono">{Math.round(scale * 100)}%</span>
          </div>
          <span className="text-[#2a3040] text-[10px] ml-auto font-mono">{tracks.length} 轨道 · {signals.length} 信号 · {switches.length} 道岔</span>
        </div>

        <div ref={canvasWrapRef} className="flex-1 overflow-hidden cursor-grab active:cursor-grabbing">
          <div ref={containerRef} className="h-full">
            <canvas ref={canvasRef} className="block" onMouseDown={handleMouseDown} />
          </div>
        </div>
      </div>

      <aside className="w-[280px] shrink-0 border-l border-[#172436] bg-[#07101b] p-4 overflow-auto">
        <div className="text-[11px] uppercase tracking-[0.16em] text-[#5f7088] mb-4">Inspector</div>
        <div className="mb-5">
          <h3 className="text-[16px] font-semibold text-[#dce8f8]">{data.stationName}</h3>
          <div className="text-[12px] font-mono text-[#5f7088] mt-0.5">{data.stationCode} · 9号线</div>
        </div>

        <Section title="信号机" count={signals.length}>
          {signals.map(sig => (
            <div key={sig.id} className="flex items-center justify-between py-1.5 border-b border-[#101d2d] last:border-0">
              <div className="flex items-center gap-2">
                <span style={{ color: COLORS[sig.type] }} className="text-[12px]">{SYMBOLS[sig.type]}</span>
                <span className="text-[12px] text-[#c7d5e8]">{sig.name}</span>
              </div>
              <span className="text-[10px] font-mono text-[#5f7088]">{SIG_LABELS[sig.type]} · {sig.dir === 'up' ? '上行' : '下行'}</span>
            </div>
          ))}
        </Section>

        <Section title="站台" count={platforms.length}>
          {platforms.map(p => (
            <div key={p.id} className="py-1.5 border-b border-[#101d2d] last:border-0">
              <div className="flex items-center justify-between">
                <span className="text-[12px] text-[#c7d5e8]">{p.name}</span>
                <span className="text-[10px] font-mono text-[#8FC31F]">{p.id}</span>
              </div>
              {p.mileageM != null && <div className="text-[10px] text-[#5f7088] font-mono mt-0.5">K{(p.mileageM / 1000).toFixed(3)}</div>}
              {p.direction && <div className="text-[10px] text-[#5f7088] mt-0.5">方向 0x{p.direction}</div>}
              {p.segmentIds && p.segmentIds.length > 0 && <div className="text-[10px] text-[#3a4a5a] font-mono mt-0.5">Seg {p.segmentIds.join(', ')}</div>}
            </div>
          ))}
        </Section>

        <Section title="进路" count={routes.length}>
          {routes.map(r => (
            <div key={r.id} className="flex items-center justify-between py-1.5 border-b border-[#101d2d] last:border-0">
              <span className="text-[12px] text-[#c7d5e8]">{r.name}</span>
              <span className="text-[10px] font-mono text-[#5f7088]">#{r.id}</span>
            </div>
          ))}
        </Section>
      </aside>
    </div>
  );
}

function Section({ title, count, children }: { title: string; count: number; children: React.ReactNode }) {
  return (
    <div className="mb-4">
      <div className="flex items-center justify-between mb-2"><span className="text-[10px] uppercase tracking-[0.12em] text-[#5f7088]">{title}</span><span className="text-[10px] font-mono text-[#3a4a5a]">{count}</span></div>
      {children}
    </div>
  );
}

function drawTrack(ctx: CanvasRenderingContext2D, x: number, y: number, w: number) {
  ctx.strokeStyle = '#3a5a7a'; ctx.lineWidth = 3;
  ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + w, y); ctx.stroke();
  ctx.strokeStyle = '#1a2a3a'; ctx.lineWidth = 0.5;
  for (let i = 0; i < w; i += 6) { ctx.beginPath(); ctx.moveTo(x + i, y - 4); ctx.lineTo(x + i, y + 4); ctx.stroke(); }
}

function drawSignal(ctx: CanvasRenderingContext2D, sig: InterlockingSignal, trackY: number) {
  const color = COLORS[sig.type], sym = SYMBOLS[sig.type], sy = sig.dir === 'up' ? -1 : 1;
  ctx.strokeStyle = '#3a4a5a'; ctx.lineWidth = 0.8;
  ctx.beginPath(); ctx.moveTo(sig.x, trackY); ctx.lineTo(sig.x, trackY - sy * 14); ctx.stroke();
  ctx.fillStyle = color; ctx.font = '11px sans-serif'; ctx.textAlign = 'center';
  ctx.fillText(sym, sig.x, trackY - sy * 18 + 4);
  ctx.fillStyle = '#6a7a90'; ctx.font = '7px monospace'; ctx.textAlign = 'center';
  ctx.fillText(sig.name, sig.x, trackY - sy * 26);
}
