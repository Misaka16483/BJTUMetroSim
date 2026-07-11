import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchBackendTrackMap, fetchSimState } from '../data/backendApi';
import type { SimStateResponse, TrackMapData } from '../data/backendApi';
import { ggzInterlockingData, fspInterlockingData, bwrInterlockingData, gtgInterlockingData, kylInterlockingData, ftnInterlockingData, ftdInterlockingData, qlzInterlockingData, llqInterlockingData, lleInterlockingData, jbgInterlockingData, bdzInterlockingData, bqsInterlockingData } from '../data/stationInterlockingData';
import type { StationInterlockingData } from '../types/interlocking';

const COLORS = {
  track: '#3a5a7a',
  trackHighlight: '#5a8aba',
  station: 'rgba(143,195,31,0.3)',
  stationBorder: '#8FC31F',
  train: '#8FC31F',
  text: '#dce8f8',
  muted: '#5f7088',
  signalMain: '#58a6ff',
  signalShunting: '#d29922',
  signalDistant: '#8b949e',
};

const SYMBOLS: Record<number, string> = { 1: '◆', 2: '◇', 3: '●' };

type StationConfig = {
  code: string;
  name: string;
  mileage: number;
  width: number;
  dataFn: () => StationInterlockingData;
};

const STATION_CONFIGS: StationConfig[] = [
  { code: 'GGZ', name: '郭公庄',       mileage: 313.00,    width: 580, dataFn: ggzInterlockingData },
  { code: 'FSP', name: '丰台科技园',    mileage: 1660.52,   width: 580, dataFn: fspInterlockingData },
  { code: 'KYL', name: '科怡路',        mileage: 2448.61,   width: 580, dataFn: kylInterlockingData },
  { code: 'FTN', name: '丰台南路',      mileage: 3429.32,   width: 580, dataFn: ftnInterlockingData },
  { code: 'FTD', name: '丰台东大街',    mileage: 5014.46,   width: 580, dataFn: ftdInterlockingData },
  { code: 'QLZ', name: '七里庄',        mileage: 6339.90,   width: 580, dataFn: qlzInterlockingData },
  { code: 'LLQ', name: '六里桥',        mileage: 8118.83,   width: 580, dataFn: llqInterlockingData },
  { code: 'LLE', name: '六里桥东',      mileage: 9429.16,   width: 580, dataFn: lleInterlockingData },
  { code: 'BWR', name: '北京西站',      mileage: 10598.74,  width: 640, dataFn: bwrInterlockingData },
  { code: 'JBG', name: '军事博物馆',    mileage: 11996.97,  width: 580, dataFn: jbgInterlockingData },
  { code: 'BDZ', name: '白堆子',        mileage: 13906.77,  width: 580, dataFn: bdzInterlockingData },
  { code: 'BQS', name: '白石桥南',      mileage: 14954.01,  width: 580, dataFn: bqsInterlockingData },
  { code: 'GTG', name: '国家图书馆',    mileage: 16048.92,  width: 540, dataFn: gtgInterlockingData },
];

/** 合并全部 13 个车站的联锁数据为一张连续联锁图 */
function getCombinedInterlockingData(): StationInterlockingData {
  const totalReal = STATION_CONFIGS[12].mileage - STATION_CONFIGS[0].mileage; // 15736m
  const totalStationW = STATION_CONFIGS.reduce((s, c) => s + c.width, 0);
  const targetCanvasW = 12000;
  const availGap = targetCanvasW - totalStationW;
  const scaleMile = availGap / totalReal;

  // 区间 seg 列表（UP 方向：GGZ → GTG）
  const UP_SEGMENT_IDS: number[][] = [
    [22, 23, 235],                                    // GGZ → FSP
    [25, 54],                                          // FSP → KYL
    [56, 57],                                          // KYL → FTN
    [59, 60, 61],                                      // FTN → FTD
    [63, 64, 66, 67, 82, 83],                          // FTD → QLZ
    [85, 86, 87],                                      // QLZ → LLQ
    [89, 90, 92, 93, 94, 96, 125],                     // LLQ → LLE
    [127, 128],                                        // LLE → BWR
    [130, 131, 133, 134, 169],                         // BWR → JBG
    [171, 172, 173],                                   // JBG → BDZ
    [178, 203],                                        // BDZ → BQS
    [205, 206],                                        // BQS → GTG
  ];

  // 区间 seg 列表（DN 方向：GTG → GGZ）
  const DN_SEGMENT_IDS: number[][] = [
    [219, 218],                                        // GTG → BQS
    [195, 194, 192, 191, 190, 188, 187, 186],           // BQS → BDZ
    [184, 183, 182, 181],                              // BDZ → JBG
    [179, 146, 145, 144, 142, 141, 140],                // JBG → BWR
    [138, 137],                                        // BWR → LLE
    [135, 116, 115, 113, 112, 111, 109, 108, 106, 105, 104], // LLE → LLQ
    [102, 101, 100, 99],                               // LLQ → QLZ
    [97, 81, 79, 78],                                  // QLZ → FTD
    [76, 75, 74, 73],                                  // FTD → FTN
    [71, 70],                                          // FTN → KYL
    [68, 52],                                          // KYL → FSP
    [50, 49, 48, 46, 45, 43, 41, 40],                   // FSP → GGZ
  ];

  // 预计算各站绝对偏移
  const offsets: number[] = [];
  const gaps: number[] = [];
  let curX = 0;
  for (let i = 0; i < STATION_CONFIGS.length; i++) {
    offsets[i] = curX;
    curX += STATION_CONFIGS[i].width;
    if (i < STATION_CONFIGS.length - 1) {
      const mileGap = STATION_CONFIGS[i + 1].mileage - STATION_CONFIGS[i].mileage;
      const gap = Math.max(60, Math.round(mileGap * scaleMile));
      gaps[i] = gap;
      curX += gap;
    }
  }
  const totalWidth = curX;

  const offsetX = (x: number, offset: number) => x + offset;

  // 收集所有站的数据
  const allTracks: StationInterlockingData['tracks'] = [];
  const allPlatforms: StationInterlockingData['platforms'] = [];
  const allSignals: StationInterlockingData['signals'] = [];
  const allSwitches: StationInterlockingData['switches'] = [];
  const allRoutes: StationInterlockingData['routes'] = [];
  const allLabels: StationInterlockingData['labels'] = [];
  let sigIdBase = 0;

  for (let i = 0; i < STATION_CONFIGS.length; i++) {
    const cfg = STATION_CONFIGS[i];
    const off = offsets[i];
    const data = cfg.dataFn!();

    // 轨道
    for (const t of data.tracks) {
      allTracks.push({ ...t, x: offsetX(t.x, off), id: `${cfg.code}-${t.id}` });
    }
    // 连接轨道（本站出口 → 下一站入口）
    if (i < STATION_CONFIGS.length - 1) {
      const gap = gaps[i];
      const endX = off + cfg.width;
      const mileDiff = Math.round(STATION_CONFIGS[i + 1].mileage - cfg.mileage);
      allTracks.push(
        { id: `conn-up-${i}`,  label: `区间 ${mileDiff}m`, y: 140, x: endX - 30, width: gap + 60, dir: 'up',   segmentIds: UP_SEGMENT_IDS[i] },
        { id: `conn-dn-${i}`,  label: `区间 ${mileDiff}m`, y: 220, x: endX - 30, width: gap + 60, dir: 'down', segmentIds: DN_SEGMENT_IDS[i] },
      );
      // 区间标签（置于轨道外侧，远离数据标注区域）
      const midX = endX + gap / 2;
      allLabels.push(
        { id: `conn-up-lbl-${i}`, x: midX, y: 90, text: `── 区间 ${mileDiff}m ──`, fontSize: 8, color: '#5a7a9a', align: 'center', font: 'monospace' },
        { id: `conn-dn-lbl-${i}`, x: midX, y: 310, text: `── 区间 ${mileDiff}m ──`, fontSize: 8, color: '#5a7a9a', align: 'center', font: 'monospace' },
      );
    }
    // 站台
    for (const p of data.platforms) {
      allPlatforms.push({ ...p, x: offsetX(p.x, off), id: `${cfg.code}-${p.id}`, trackId: `${cfg.code}-${p.trackId}` });
    }
    // 信号机
    for (const s of data.signals) {
      allSignals.push({ ...s, x: offsetX(s.x, off), id: sigIdBase + s.id, trackId: `${cfg.code}-${s.trackId}` });
    }
    sigIdBase += 1000;
    // 道岔
    for (const sw of data.switches) {
      allSwitches.push({ ...sw, x: offsetX(sw.x, off), id: `${cfg.code}-${sw.id}`, trackId1: `${cfg.code}-${sw.trackId1}`, trackId2: `${cfg.code}-${sw.trackId2}` });
    }
    // 进路
    for (const r of data.routes) {
      allRoutes.push({ ...r, id: sigIdBase + r.id, startSignalId: sigIdBase + r.startSignalId, endSignalId: sigIdBase + r.endSignalId, path: r.path.map(p => ({ x: offsetX(p.x, off), y: p.y })) });
    }
    // 标签
    for (const lbl of data.labels) {
      allLabels.push({ ...lbl, x: offsetX(lbl.x, off), id: `${cfg.code}-${lbl.id}` });
    }
  }

  return {
    stationId: 'full-line', stationName: '9号线全线', stationCode: 'LINE9',
    lineId: '9', bounds: { width: totalWidth, height: 360 },
    tracks: allTracks, platforms: allPlatforms, signals: allSignals,
    switches: allSwitches, routes: allRoutes, labels: allLabels,
    directionLabels: { up: '国家图书馆 →', down: '← 郭公庄' },
  };
}

export default function FullLineInterlockingView() {
  const canvasWrapRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [scale, setScale] = useState(0.35);
  const offsetRef = useRef({ x: 120, y: 0 });
  const dragRef = useRef<{ sx: number; sy: number; ox: number; oy: number } | null>(null);

  const [simState, setSimState] = useState<SimStateResponse | null>(null);
  const [trackMap, setTrackMap] = useState<TrackMapData | null>(null);
  const [loading, setLoading] = useState(true);
  const [interlockingData] = useState(() => getCombinedInterlockingData());

  // 加载数据
  useEffect(() => {
    const loadData = async () => {
      try {
        const tm = await fetchBackendTrackMap();
        setTrackMap(tm);
      } catch (error) {
        console.error('[FullLine] 加载数据失败:', error);
      } finally {
        setLoading(false);
      }
    };
    loadData();
  }, []);

  // 轮询仿真状态（暂时保留，等后端对齐）
  useEffect(() => {
    if (loading) return;
    
    const update = async () => {
      try {
        const state = await fetchSimState();
        setSimState(state);
      } catch (error) {
        console.error('[FullLine] 获取仿真状态失败:', error);
      }
    };
    
    update();
    const interval = setInterval(update, 500);
    return () => clearInterval(interval);
  }, [loading]);

  // 绘制主函数
  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || !interlockingData) return;
    const ctx = canvas.getContext('2d')!;
    const { x: ox, y: oy } = offsetRef.current;
    const s = scale;

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.save();
    ctx.scale(2, 2);
    ctx.translate(ox, oy);
    ctx.scale(s, s);

    // 背景
    ctx.fillStyle = '#040810';
    ctx.fillRect(-50, -100, interlockingData.bounds.width + 100, interlockingData.bounds.height + 100);

    // 绘制所有轨道
    for (const t of interlockingData.tracks) {
      drawTrack(ctx, t.x, t.y, t.width);
    }

    // 绘制道岔
    for (const sw of interlockingData.switches) {
      ctx.strokeStyle = '#d29922';
      ctx.lineWidth = 1.5;
      const isCrossover = (sw as any).type === 'crossover';

      if (isCrossover) {
        // 交叉渡线：X 形双线
        ctx.beginPath(); 
        ctx.moveTo(sw.x - 40, sw.y1); 
        ctx.lineTo(sw.x - 20, sw.y1); 
        ctx.lineTo(sw.x + 10, sw.y2); 
        ctx.lineTo(sw.x + 30, sw.y2); 
        ctx.stroke();
        ctx.beginPath(); 
        ctx.moveTo(sw.x - 40, sw.y2); 
        ctx.lineTo(sw.x - 20, sw.y2); 
        ctx.lineTo(sw.x + 10, sw.y1); 
        ctx.lineTo(sw.x + 30, sw.y1); 
        ctx.stroke();
        ctx.fillStyle = '#d29922';
        ctx.font = '12px monospace';
        ctx.fillText('△', sw.x - 36, sw.y1 - 5);
        ctx.fillText('△', sw.x - 36, sw.y2 + 16);
      } else {
        // 单开道岔：比例斜直线，span=round(|Δy|*0.3) 保证所有道岔斜率统一
        const dy = Math.abs(sw.y2 - sw.y1);
        const span = Math.max(20, Math.round(dy * 0.3));
        ctx.beginPath();
        ctx.moveTo(sw.x - span, sw.y1);
        ctx.lineTo(sw.x + span, sw.y2);
        ctx.stroke();
        // 辙叉三角，标在分岔点
        ctx.fillStyle = '#d29922';
        ctx.font = '12px monospace';
        ctx.fillText('△', sw.x - span + 4, sw.y1 - 5);
      }
    }

    // 绘制站台
    for (const p of interlockingData.platforms) {
      const track = interlockingData.tracks.find(t => t.id === p.trackId);
      if (!track) continue;
      const py = track.y;
      ctx.fillStyle = 'rgba(143,195,31,0.3)';
      ctx.fillRect(p.x - p.width / 2, py - 14, p.width, 28);
      ctx.strokeStyle = '#8FC31F';
      ctx.lineWidth = 1;
      ctx.strokeRect(p.x - p.width / 2, py - 14, p.width, 28);
    }

    // 绘制信号机
    for (const sig of interlockingData.signals) {
      const track = interlockingData.tracks.find(t => t.id === sig.trackId);
      if (!track) continue;
      drawSignal(ctx, sig, track.y);
    }

    // 绘制进路
    for (const r of interlockingData.routes) {
      if (r.path.length < 2) continue;
      ctx.strokeStyle = r.color ?? 'rgba(88,166,255,0.15)';
      ctx.lineWidth = 6;
      ctx.setLineDash([6, 4]);
      ctx.beginPath(); 
      ctx.moveTo(r.path[0].x, r.path[0].y);
      for (let i = 1; i < r.path.length; i++) {
        ctx.lineTo(r.path[i].x, r.path[i].y);
      }
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // 绘制标签
    for (const lbl of interlockingData.labels) {
      ctx.fillStyle = lbl.color ?? '#3a4a5a';
      ctx.font = `${lbl.fontSize ?? 8}px ${lbl.font ?? 'sans-serif'}`;
      ctx.textAlign = lbl.align ?? 'left';
      ctx.fillText(lbl.text, lbl.x, lbl.y);
    }

    // 绘制连接区间的坡度 + 限速 + 信号（对所有区间）
    if (trackMap) {
      const connTracks = interlockingData.tracks.filter(t => t.id.startsWith('conn-up-') || t.id.startsWith('conn-dn-'));
      for (const ct of connTracks) {
        drawConnectionProfile(ctx, trackMap, ct);
      }
    }

    ctx.restore();
  }, [scale, interlockingData, trackMap]);

  // 事件处理
  const handleWheel = useCallback((e: WheelEvent) => {
    e.preventDefault();
    setScale(s => Math.max(0.3, Math.min(3, s * (e.deltaY > 0 ? 0.9 : 1.1))));
  }, []);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    dragRef.current = {
      sx: e.clientX,
      sy: e.clientY,
      ox: offsetRef.current.x,
      oy: offsetRef.current.y,
    };
    const onMove = (ev: MouseEvent) => {
      if (!dragRef.current) return;
      offsetRef.current.x = dragRef.current.ox + (ev.clientX - dragRef.current.sx);
      offsetRef.current.y = dragRef.current.oy + (ev.clientY - dragRef.current.sy);
      draw();
    };
    const onUp = () => {
      dragRef.current = null;
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [draw]);

  useEffect(() => {
    draw();
  }, [draw]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    el.addEventListener('wheel', handleWheel, { passive: false });
    return () => el.removeEventListener('wheel', handleWheel);
  }, [handleWheel]);

  const updateSize = useCallback(() => {
    const c = canvasRef.current;
    const p = canvasWrapRef.current;
    if (!c || !p) return;
    const w = p.clientWidth;
    const h = p.clientHeight;
    c.width = w * 2;
    c.height = h * 2;
    c.style.width = w + 'px';
    c.style.height = h + 'px';
    draw();
  }, [draw]);

  useEffect(() => {
    updateSize();
    window.addEventListener('resize', updateSize);
    return () => window.removeEventListener('resize', updateSize);
  }, [updateSize]);

  const zoomTo = (s: number) => {
    setScale(Math.max(0.3, Math.min(3, s)));
    setTimeout(draw, 0);
  };

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center text-[#dce8f8]">
        <div>加载全线图数据中...</div>
      </div>
    );
  }

  return (
    <div className="h-full min-h-0 flex flex-col bg-[#040810]">
      {/* 工具栏 */}
      <div className="flex items-center gap-4 px-5 py-3 shrink-0 border-b border-[#172436]">
        <div>
          <div className="text-[11px] uppercase tracking-[0.18em] text-[#5f7088]">全线图</div>
          <h2 className="text-[18px] font-semibold text-[#dce8f8] mt-1">9号线全线联锁图 · 郭公庄 → 国家图书馆 (13站)</h2>
        </div>
        <div className="flex items-center gap-1 ml-auto">
          <button onClick={() => zoomTo(scale * 1.3)} className="px-2 py-1 text-[13px] bg-[#0d1424] border border-[#1a2240] text-[#8b949e] hover:text-white cursor-pointer">
            +
          </button>
          <button onClick={() => zoomTo(scale / 1.3)} className="px-2 py-1 text-[13px] bg-[#0d1424] border border-[#1a2240] text-[#8b949e] hover:text-white cursor-pointer">
            -
          </button>
          <button onClick={() => zoomTo(0.35)} className="px-2 py-1 text-[11px] bg-[#0d1424] border border-[#1a2240] text-[#8b949e] hover:text-white cursor-pointer">
            重置
          </button>
          <span className="text-[11px] text-[#3a4a60] ml-1 font-mono">{Math.round(scale * 100)}%</span>
        </div>
      </div>

      {/* 画布区域 */}
      <div ref={canvasWrapRef} className="flex-1 overflow-hidden cursor-grab active:cursor-grabbing">
        <div ref={containerRef} className="h-full">
          <canvas
            ref={canvasRef}
            className="block"
            onMouseDown={handleMouseDown}
          />
        </div>
      </div>

      {/* 图例 */}
      <div className="shrink-0 px-5 py-2 border-t border-[#172436] flex gap-6 text-[10px] text-[#5f7088]">
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded" style={{ backgroundColor: COLORS.stationBorder }} />
          <span>站台</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-8 h-2" style={{ backgroundColor: COLORS.track }} />
          <span>轨道</span>
        </div>
        <div className="flex items-center gap-2">
          <span style={{ color: COLORS.signalMain, fontSize: '12px' }}>◆</span>
          <span>主信号</span>
        </div>
        <div className="flex items-center gap-2">
          <span style={{ color: COLORS.signalShunting, fontSize: '12px' }}>◇</span>
          <span>调车信号</span>
        </div>
        <div className="flex items-center gap-2">
          <span style={{ color: COLORS.signalDistant, fontSize: '12px' }}>●</span>
          <span>预告信号</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded-full" style={{ backgroundColor: COLORS.muted }} />
          <span>列车（待后端对齐）</span>
        </div>
        {simState && (
          <div className="flex items-center gap-2 ml-auto">
            <span className={simState.clock.state === 'RUNNING' ? 'text-green-400' : 'text-amber-400'}>
              {simState.clock.state === 'RUNNING' ? '●' : '○'}
            </span>
            <span>{simState.clock.simTime}</span>
          </div>
        )}
      </div>
    </div>
  );
}

// ===== 连接区间坡度 + 限速绘制 + 区间信号 =====
function drawConnectionProfile(
  ctx: CanvasRenderingContext2D,
  trackMap: TrackMapData,
  connTrack: { id: string; x: number; width: number; y: number; dir: string; segmentIds: number[] },
) {
  const segIds = connTrack.segmentIds;
  if (!segIds || segIds.length === 0) return;

  const gapX1 = connTrack.x;
  const gapW = connTrack.width;
  const segMap = new Map(trackMap.segments.map(s => [s.id, s]));
  const buildProfile = (ids: number[]) => {
    const segs = ids.map(id => segMap.get(id)).filter(Boolean) as { id: number; lengthM: number }[];
    if (segs.length === 0) return null;
    let cum = 0;
    const cumStarts = segs.map(s => { const start = cum; cum += s.lengthM; return { segId: s.id, start, len: s.lengthM }; });
    const totalLen = cum;

    // 坡度：用 startSegmentId/endSegmentId 匹配
    const grads = trackMap.gradients
      .filter(g => ids.includes(g.startSegmentId) || ids.includes(g.endSegmentId))
      .map(g => {
        const sEntry = cumStarts.find(c => c.segId === g.startSegmentId);
        const eEntry = cumStarts.find(c => c.segId === g.endSegmentId);
        const absStart = sEntry ? sEntry.start + g.startOffsetM : 0;
        const absEnd = eEntry ? eEntry.start + g.endOffsetM : totalLen;
        return { absStart, absEnd, slope: g.slopePermille };
      })
      .sort((a, b) => a.absStart - b.absStart);

    const speeds = trackMap.speedRestrictions
      .filter(s => ids.includes(s.segmentId))
      .map(s => {
        const entry = cumStarts.find(c => c.segId === s.segmentId);
        const absStart = entry ? entry.start + s.startOffsetM : 0;
        const absEnd = entry ? entry.start + s.endOffsetM : totalLen;
        return { absStart, absEnd, speedKmh: Math.round(s.speedLimitMps * 3.6) };
      })
      .sort((a, b) => a.absStart - b.absStart);

    // 区间信号：计算每个信号在区间内的绝对里程
    const sigs: { absPos: number; signal: typeof trackMap.signals[0] }[] = [];
    for (const sig of trackMap.signals) {
      if (!ids.includes(sig.segmentId)) continue;
      const entry = cumStarts.find(c => c.segId === sig.segmentId);
      if (!entry) continue;
      const absPos = entry.start + sig.offsetM;
      sigs.push({ absPos, signal: sig });
    }
    sigs.sort((a, b) => a.absPos - b.absPos);

    return { totalLen, grads, speeds, sigs };
  }

  const profile = buildProfile(segIds);
  if (!profile) return;

  const mileToX = (m: number) => gapX1 + (m / profile.totalLen) * gapW;
  const trackY = connTrack.y;
  // sign: UP 轨道(y=140) 绘制在下方(-1)，DN 轨道(y=220) 绘制在上方(+1)
  const sign = connTrack.dir === 'up' ? -1 : 1;

  // 第一层：坡度标签（靠近轨道）
  for (const g of profile.grads) {
    const x1 = mileToX(g.absStart);
    const x2 = mileToX(g.absEnd);
    const midX = (x1 + x2) / 2;
    const w = x2 - x1;

    // 变坡点竖线标记
    ctx.strokeStyle = '#5a7a9a';
    ctx.lineWidth = 0.5;
    ctx.beginPath();
    ctx.moveTo(x1, trackY - sign * 4);
    ctx.lineTo(x1, trackY + sign * 4);
    ctx.stroke();

    if (w > 30) {
      ctx.fillStyle = '#aab8cc';
      ctx.font = '8px monospace';
      ctx.textAlign = 'center';
      const label = g.slope > 0 ? `+${g.slope}‰` : g.slope < 0 ? `${g.slope}‰` : '0‰';
      ctx.fillText(label, midX, trackY - sign * 14);
    }
  }

  // 第二层：限速标签（错开坡度标签）
  for (const sp of profile.speeds) {
    const x1 = mileToX(sp.absStart);
    const x2 = mileToX(sp.absEnd);
    const midX = (x1 + x2) / 2;

    if (x2 - x1 > 10) {
      ctx.fillStyle = '#ffc850';
      ctx.font = 'bold 9px monospace';
      ctx.textAlign = 'center';
      ctx.fillText(`${sp.speedKmh} km/h`, midX, trackY - sign * 30);
    }
  }

  // 第三层：区间信号机
  for (const { absPos, signal: sig } of profile.sigs) {
    const sx = mileToX(absPos);
    const color = sig.type === 1 ? COLORS.signalMain : sig.type === 2 ? COLORS.signalShunting : COLORS.signalDistant;
    const sym = SYMBOLS[sig.type] || '●';
    const sy = sign;

    // 信号机竖线
    ctx.strokeStyle = '#3a4a5a';
    ctx.lineWidth = 0.6;
    ctx.beginPath();
    ctx.moveTo(sx, trackY);
    ctx.lineTo(sx, trackY - sy * 14);
    ctx.stroke();

    // 信号符号
    ctx.fillStyle = color;
    ctx.font = '10px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(sym, sx, trackY - sy * 18 + 4);

    // 信号名称（仅当有足够空间时）
    const nextSigX = profile.sigs.find(s => s.absPos > absPos);
    const prevSigX = [...profile.sigs].reverse().find(s => s.absPos < absPos);
    const minGap = Math.min(
      nextSigX ? mileToX(nextSigX.absPos) - sx : Infinity,
      prevSigX ? sx - mileToX(prevSigX.absPos) : Infinity
    );
    if (minGap > 40) {
      ctx.fillStyle = '#6a7a90';
      ctx.font = '6px monospace';
      ctx.fillText(sig.name, sx, trackY - sy * 28);
    }
  }
}

// 辅助绘制函数
function drawTrack(ctx: CanvasRenderingContext2D, x: number, y: number, w: number) {
  ctx.strokeStyle = '#3a5a7a'; 
  ctx.lineWidth = 3;
  ctx.beginPath(); 
  ctx.moveTo(x, y); 
  ctx.lineTo(x + w, y); 
  ctx.stroke();
  ctx.strokeStyle = '#1a2a3a'; 
  ctx.lineWidth = 0.5;
  for (let i = 0; i < w; i += 6) { 
    ctx.beginPath(); 
    ctx.moveTo(x + i, y - 4); 
    ctx.lineTo(x + i, y + 4); 
    ctx.stroke(); 
  }
}

function drawSignal(ctx: CanvasRenderingContext2D, sig: any, trackY: number) {
  const color = sig.type === 1 ? COLORS.signalMain : sig.type === 2 ? COLORS.signalShunting : COLORS.signalDistant;
  const sym = SYMBOLS[sig.type] || '●';
  const sy = sig.dir === 'up' ? -1 : 1;
  ctx.strokeStyle = '#3a4a5a'; 
  ctx.lineWidth = 0.8;
  ctx.beginPath(); 
  ctx.moveTo(sig.x, trackY); 
  ctx.lineTo(sig.x, trackY - sy * 14); 
  ctx.stroke();
  ctx.fillStyle = color; 
  ctx.font = '11px sans-serif'; 
  ctx.textAlign = 'center';
  ctx.fillText(sym, sig.x, trackY - sy * 18 + 4);
  ctx.fillStyle = '#6a7a90'; 
  ctx.font = '7px monospace'; 
  ctx.fillText(sig.name, sig.x, trackY - sy * 26);
}
