import { useCallback, useEffect, useRef, useState } from 'react';
import { useSimStore } from '../store/useSimStore';

const COLORS = {
  track: '#3a5a7a',
  trackHighlight: '#5a8aba',
  station: 'rgba(143,195,31,0.3)',
  stationBorder: '#8FC31F',
  train: '#8FC31F',
  text: '#dce8f8',
  muted: '#5f7088',
};

/**
 * 全线图 - 郭公庄到丰台科技园
 */
export default function FullLineInterlockingView() {
  const canvasWrapRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [scale, setScale] = useState(0.8);
  const offsetRef = useRef({ x: 100, y: 0 });
  const dragRef = useRef<{ sx: number; sy: number; ox: number; oy: number } | null>(null);
  const animationFrameRef = useRef<number>();
  const [animationTime, setAnimationTime] = useState(0);

  // 画布尺寸
  const CANVAS_WIDTH = 2400;
  const CANVAS_HEIGHT = 400;

  // 布局常量
  const Y_UP = 140;
  const Y_DN = 260;
  const STATION_WIDTH = 300;
  const INTERVAL_WIDTH = 500;

  // 车站位置
  const GGZ_START = 100;
  const GGZ_END = GGZ_START + STATION_WIDTH;
  const FSP_START = GGZ_END + INTERVAL_WIDTH;
  const FSP_END = FSP_START + STATION_WIDTH;

  // 动画更新
  useEffect(() => {
    let lastTime = performance.now();
    const animate = (time: number) => {
      const delta = (time - lastTime) / 1000;
      lastTime = time;
      setAnimationTime(t => t + delta);
      animationFrameRef.current = requestAnimationFrame(animate);
    };
    animationFrameRef.current = requestAnimationFrame(animate);
    return () => {
      if (animationFrameRef.current) cancelAnimationFrame(animationFrameRef.current);
    };
  }, []);

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

    // 背景
    ctx.fillStyle = '#040810';
    ctx.fillRect(-100, -100, CANVAS_WIDTH + 200, CANVAS_HEIGHT + 200);

    // === 绘制主轨道 ===
    drawMainTracks(ctx);

    // === 绘制郭公庄车站 ===
    drawStation(ctx, GGZ_START, GGZ_END, '郭公庄', 'GGZ · K0+313');

    // === 绘制丰台科技园车站 ===
    drawStation(ctx, FSP_START, FSP_END, '丰台科技园', 'FSP · K1+660');

    // === 绘制列车 ===
    drawTrain(ctx);

    // === 绘制距离标记 ===
    drawDistanceMarkers(ctx);

    ctx.restore();
  }, [scale, animationTime]);

  // 绘制主轨道
  function drawMainTracks(ctx: CanvasRenderingContext2D) {
    // 上行轨道
    drawTrack(ctx, GGZ_START - 50, Y_UP, FSP_END + 50);
    // 下行轨道
    drawTrack(ctx, GGZ_START - 50, Y_DN, FSP_END + 50);
  }

  // 绘制轨道
  function drawTrack(ctx: CanvasRenderingContext2D, x1: number, y: number, x2: number) {
    // 主轨道线
    ctx.strokeStyle = COLORS.track;
    ctx.lineWidth = 4;
    ctx.beginPath();
    ctx.moveTo(x1, y);
    ctx.lineTo(x2, y);
    ctx.stroke();

    // 轨道高亮边缘
    ctx.strokeStyle = COLORS.trackHighlight;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x1, y - 6);
    ctx.lineTo(x2, y - 6);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(x1, y + 6);
    ctx.lineTo(x2, y + 6);
    ctx.stroke();

    // 枕木
    ctx.strokeStyle = '#1a2a3a';
    ctx.lineWidth = 0.8;
    for (let x = x1; x < x2; x += 20) {
      ctx.beginPath();
      ctx.moveTo(x, y - 8);
      ctx.lineTo(x, y + 8);
      ctx.stroke();
    }
  }

  // 绘制车站
  function drawStation(ctx: CanvasRenderingContext2D, start: number, end: number, name: string, code: string) {
    const mid = (start + end) / 2;
    const width = end - start;

    // 车站标签
    ctx.fillStyle = COLORS.text;
    ctx.font = 'bold 18px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(name, mid, 70);
    ctx.fillStyle = COLORS.muted;
    ctx.font = '11px monospace';
    ctx.fillText(code, mid, 90);

    // 站台区域 - 上行
    ctx.fillStyle = COLORS.station;
    ctx.strokeStyle = COLORS.stationBorder;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.roundRect(start + 20, Y_UP - 22, width - 40, 44, 4);
    ctx.fill();
    ctx.stroke();

    // 站台区域 - 下行
    ctx.beginPath();
    ctx.roundRect(start + 20, Y_DN - 22, width - 40, 44, 4);
    ctx.fill();
    ctx.stroke();

    // 站台标记线
    ctx.strokeStyle = 'rgba(255,255,255,0.2)';
    ctx.lineWidth = 1;
    for (let x = start + 40; x < end - 20; x += 40) {
      ctx.beginPath();
      ctx.moveTo(x, Y_UP - 15);
      ctx.lineTo(x, Y_UP - 28);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(x, Y_DN + 15);
      ctx.lineTo(x, Y_DN + 28);
      ctx.stroke();
    }
  }

  // 绘制距离标记
  function drawDistanceMarkers(ctx: CanvasRenderingContext2D) {
    const markers = [
      { x: GGZ_START, label: '0m' },
      { x: (GGZ_END + FSP_START) / 2, label: '673m' },
      { x: FSP_END, label: '1347m' }
    ];

    ctx.fillStyle = COLORS.muted;
    ctx.font = '10px monospace';
    ctx.textAlign = 'center';

    markers.forEach(marker => {
      ctx.beginPath();
      ctx.moveTo(marker.x, Y_UP - 40);
      ctx.lineTo(marker.x, Y_DN + 40);
      ctx.strokeStyle = 'rgba(95, 112, 136, 0.3)';
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.fillText(marker.label, marker.x, Y_DN + 55);
    });
  }

  // 绘制列车
  function drawTrain(ctx: CanvasRenderingContext2D) {
    const isRunning = useSimStore.getState().isRunning;
    const segmentProgress = useSimStore.getState().segmentProgress;
    
    let progress;
    if (isRunning) {
      progress = (animationTime * 0.1) % 1;
    } else {
      progress = segmentProgress || 0;
    }
    
    const totalLength = FSP_END - GGZ_START;
    const trainX = GGZ_START + progress * totalLength;

    // 绘制上行列车
    drawTrainAt(ctx, trainX, Y_UP, 'T001', '上行');
    
    // 绘制下行列车（反向行驶）
    const downProgress = ((animationTime * 0.1) + 0.5) % 1;
    const downTrainX = FSP_END - downProgress * totalLength;
    drawTrainAt(ctx, downTrainX, Y_DN, 'T002', '下行', true);
  }

  // 在指定位置绘制列车
  function drawTrainAt(ctx: CanvasRenderingContext2D, x: number, y: number, id: string, direction: string, reversed = false) {
    const trainLength = 120;
    const carLength = 30;
    const carCount = 4;

    // 绘制车厢
    for (let i = 0; i < carCount; i++) {
      const carX = reversed 
        ? x + (carCount - 1 - i) * carLength 
        : x + i * carLength;

      // 车厢主体
      ctx.fillStyle = COLORS.train;
      ctx.beginPath();
      ctx.roundRect(carX + 2, y - 14, carLength - 4, 28, 4);
      ctx.fill();

      // 车厢边框
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1.5;
      ctx.stroke();

      // 车窗
      ctx.fillStyle = 'rgba(255,255,255,0.3)';
      for (let w = 0; w < 4; w++) {
        ctx.beginPath();
        ctx.roundRect(carX + 6 + w * 6, y - 9, 4, 18, 2);
        ctx.fill();
      }

      // 车厢连接
      if (i < carCount - 1) {
        ctx.strokeStyle = '#5a7a3a';
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.moveTo(carX + carLength - 2, y);
        ctx.lineTo(carX + carLength + 2, y);
        ctx.stroke();
      }
    }

    // 车头标记
    const headX = reversed ? x + trainLength : x;
    ctx.fillStyle = '#ff6b35';
    ctx.beginPath();
    ctx.arc(headX, y, 4, 0, Math.PI * 2);
    ctx.fill();

    // 列车ID
    ctx.fillStyle = '#ffffff';
    ctx.font = 'bold 11px monospace';
    ctx.textAlign = 'center';
    ctx.fillText(id, x + trainLength / 2, y + 4);

    // 方向标记
    ctx.font = '9px sans-serif';
    ctx.fillStyle = COLORS.muted;
    ctx.fillText(direction, x + trainLength / 2, y - 22);
  }

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

  return (
    <div className="h-full min-h-0 flex flex-col bg-[#040810]">
      {/* 工具栏 */}
      <div className="flex items-center gap-4 px-5 py-3 shrink-0 border-b border-[#172436]">
        <div>
          <div className="text-[11px] uppercase tracking-[0.18em] text-[#5f7088]">全线图</div>
          <h2 className="text-[18px] font-semibold text-[#dce8f8] mt-1">郭公庄 ↔ 丰台科技园</h2>
        </div>
        <div className="flex items-center gap-1 ml-auto">
          <button onClick={() => zoomTo(scale * 1.3)} className="px-2 py-1 text-[13px] bg-[#0d1424] border border-[#1a2240] text-[#8b949e] hover:text-white cursor-pointer">
            +
          </button>
          <button onClick={() => zoomTo(scale / 1.3)} className="px-2 py-1 text-[13px] bg-[#0d1424] border border-[#1a2240] text-[#8b949e] hover:text-white cursor-pointer">
            -
          </button>
          <button onClick={() => zoomTo(0.8)} className="px-2 py-1 text-[11px] bg-[#0d1424] border border-[#1a2240] text-[#8b949e] hover:text-white cursor-pointer">
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
          <div className="w-3 h-3 rounded" style={{ background: COLORS.stationBorder }} />
          <span>站台</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-8 h-2" style={{ background: COLORS.track }} />
          <span>轨道</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-8 h-3 rounded" style={{ background: COLORS.train }} />
          <span>列车</span>
        </div>
      </div>
    </div>
  );
}
