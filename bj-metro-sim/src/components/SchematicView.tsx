import { useMemo, useState } from 'react';
import { useSimStore } from '../store/useSimStore';
import type { MetroLineData } from '../data/amapMetroApi';

// ── 将地理坐标映射到 SVG 画布 ──
function projectLines(
  lines: MetroLineData[],
  hiddenIds: Set<string>,
  pad: number,
  svgW: number,
  svgH: number,
) {
  const allLng: number[] = [];
  const allLat: number[] = [];
  for (const line of lines) {
    if (hiddenIds.has(line.id)) continue;
    for (const seg of line.coordinates) {
      for (const [lng, lat] of seg) {
        allLng.push(lng);
        allLat.push(lat);
      }
    }
  }
  if (allLng.length === 0) return { items: [], minLng: 0, maxLng: 0, minLat: 0, maxLat: 0 };

  const minLng = Math.min(...allLng);
  const maxLng = Math.max(...allLng);
  const minLat = Math.min(...allLat);
  const maxLat = Math.max(...allLat);

  const scaleX = (svgW - pad * 2) / (maxLng - minLng || 1);
  const scaleY = (svgH - pad * 2) / (maxLat - minLat || 1);
  const scale = Math.min(scaleX, scaleY);
  const offsetX = (svgW - (maxLng - minLng) * scale) / 2;
  const offsetY = (svgH - (maxLat - minLat) * scale) / 2;

  const toX = (lng: number) => offsetX + (lng - minLng) * scale;
  const toY = (lat: number) => offsetY + (maxLat - lat) * scale; // 翻转 Y

  const items = lines.map((line) => {
    const hidden = hiddenIds.has(line.id);
    const paths = line.coordinates.map((seg) =>
      seg.map(([lng, lat]) => `${toX(lng).toFixed(1)},${toY(lat).toFixed(1)}`).join(' '),
    );
    const stationDots = line.stations.map((s) => ({
      name: s.name,
      cx: toX(s.lng),
      cy: toY(s.lat),
    }));
    return { line, hidden, paths, stationDots };
  });

  return { items, minLng, maxLng, minLat, maxLat };
}

export default function SchematicView() {
  const metroLines = useSimStore((s) => s.metroLines);
  const hiddenLines = useSimStore((s) => s.hiddenLines);
  const toggleLineVisibility = useSimStore((s) => s.toggleLineVisibility);
  const linesLoading = useSimStore((s) => s.linesLoading);

  const [container, setContainer] = useState<{ w: number; h: number } | null>(null);
  const [hoveredLine, setHoveredLine] = useState<string | null>(null);

  const pad = 40;
  const svgW = container?.w ?? 1000;
  const svgH = container?.h ?? 800;

  const { items } = useMemo(
    () => projectLines(metroLines, hiddenLines, pad, svgW, svgH),
    [metroLines, hiddenLines, svgW, svgH],
  );

  if (linesLoading) {
    return (
      <div className="w-full h-full flex items-center justify-center bg-[#06090e]">
        <span className="text-[11px] text-[#4a5568] animate-pulse">加载线路数据...</span>
      </div>
    );
  }

  return (
    <div
      ref={(el) => {
        if (el && (!container || container.w !== el.clientWidth || container.h !== el.clientHeight)) {
          setContainer({ w: el.clientWidth, h: el.clientHeight });
        }
      }}
      className="w-full h-full relative"
      style={{ background: 'radial-gradient(ellipse at 50% 50%, #0d1220 0%, #06090e 70%)' }}
    >
      {/* 网格背景 */}
      <svg className="absolute inset-0 w-full h-full pointer-events-none" style={{ opacity: 0.06 }}>
        <defs>
          <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#4a9eff" strokeWidth="0.5" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#grid)" />
      </svg>

      {container && (
        <svg className="absolute inset-0 w-full h-full" viewBox={`0 0 ${container.w} ${container.h}`}>
          {items.map(({ line, hidden, paths }) => (
            <g key={line.id} opacity={hidden ? 0.08 : hoveredLine && hoveredLine !== line.id ? 0.25 : 1}>
              {paths.map((pts, i) => (
                <polyline
                  key={i}
                  points={pts}
                  fill="none"
                  stroke={line.color}
                  strokeWidth={hoveredLine === line.id ? 3.5 : 2}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  style={{
                    filter: `drop-shadow(0 0 ${hoveredLine === line.id ? 6 : 3}px ${line.color}60)`,
                    transition: 'stroke-width 150ms',
                    cursor: 'pointer',
                  }}
                  onMouseEnter={() => setHoveredLine(line.id)}
                  onMouseLeave={() => setHoveredLine(null)}
                  onClick={() => toggleLineVisibility(line.id)}
                />
              ))}
            </g>
          ))}

          {/* 站点圆点（在所有线路上方） */}
          {items.map(({ line, hidden, stationDots }) => (
            <g key={`st-${line.id}`} opacity={hidden ? 0 : hoveredLine && hoveredLine !== line.id ? 0.2 : 1}>
              {stationDots.map((dot, i) => (
                <g key={`${line.id}-${i}`}>
                  <circle
                    cx={dot.cx}
                    cy={dot.cy}
                    r={hoveredLine === line.id ? 3 : 2}
                    fill="#e8f0ff"
                    stroke={line.color}
                    strokeWidth={1}
                    style={{ transition: 'r 150ms' }}
                  />
                  {/* 站名：仅 hover 时显示 */}
                  {hoveredLine === line.id && (
                    <text
                      x={dot.cx}
                      y={dot.cy - 5}
                      textAnchor="middle"
                      fill="#c8d8f0"
                      fontSize="8"
                      fontFamily="Inter, sans-serif"
                      style={{ pointerEvents: 'none' }}
                    >
                      {dot.name}
                    </text>
                  )}
                </g>
              ))}
            </g>
          ))}
        </svg>
      )}

      {/* 图例提示 */}
      <div className="absolute bottom-2 left-3 text-[9px] text-[#2a3a50] font-mono pointer-events-none">
        悬停线路高亮 · 点击线路切换可见性
      </div>
    </div>
  );
}
