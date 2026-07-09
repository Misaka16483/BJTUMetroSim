import { useMemo, useState } from 'react';
import { useSimStore } from '../store/useSimStore';
import type { Line9Station } from '../data/backendApi';
import type { MetroLineData } from '../data/amapMetroApi';
import LineSelector, { lineColor } from './LineSelector';

function fmt(value: number, digits = 1) {
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export default function MicroTrackView() {
  const trackMap = useSimStore((s) => s.trackMap);
  const metroLines = useSimStore((s) => s.metroLines);
  const setViewMode = useSimStore((s) => s.setViewMode);
  const setSelectedStationCode = useSimStore((s) => s.setSelectedStationCode);
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [activeLineId, setActiveLineId] = useState('9');

  // 当前选中的线路信息
  const activeLine = useMemo(
    () => metroLines.find((l) => l.id === activeLineId),
    [metroLines, activeLineId],
  );

  // 是否有该线路的轨道数据
  const hasTrackData = activeLineId === '9' && !!trackMap;

  const selectedStation = useMemo(() => {
    if (!trackMap) return null;
    return trackMap.stations.find((station) => station.stationCode === selectedCode)
      ?? trackMap.stations[0]
      ?? null;
  }, [selectedCode, trackMap]);

  return (
    <div className="h-full min-h-0 flex bg-[#040810]">
      <div className="flex-1 min-w-0 p-5 overflow-auto">
        {/* ── 线路选择器 ── */}
        <LineSelector
          lines={metroLines}
          activeLineId={activeLineId}
          onSelect={setActiveLineId}
        />

        {hasTrackData ? (
          <TrackContent
            trackMap={trackMap!}
            selectedStation={selectedStation}
            selectedCode={selectedCode}
            onStationClick={setSelectedCode}
            onStationDoubleClick={(code) => {
              setSelectedStationCode(code);
              setViewMode('interlocking');
            }}
          />
        ) : (
          <NoDataPlaceholder line={activeLine} />
        )}
      </div>

      {/* ── 右侧 Inspector（仅 9号线有数据时显示）── */}
      {hasTrackData && (
        <aside className="w-[300px] border-l border-[#172436] bg-[#07101b] p-4 overflow-auto">
          <div className="text-[10px] uppercase tracking-[0.16em] text-[#5f7088] mb-3">Inspector</div>
          {selectedStation && <StationInspector station={selectedStation} />}
        </aside>
      )}
    </div>
  );
}

/* ════════════════ 无数据占位 ════════════════ */
function NoDataPlaceholder({ line }: { line: MetroLineData | undefined }) {
  const color = line ? lineColor(line.id) : '#5f7088';
  const name = line ? (line.name.length <= 3 ? line.name : `${line.id}号线`) : '未知线路';
  const stationCount = line?.stations?.length ?? 0;

  return (
    <div className="flex flex-col items-center justify-center pt-16 pb-20">
      {/* 装饰性轨道线 */}
      <div className="relative w-full max-w-[480px] mb-8">
        <svg viewBox="0 0 480 180" className="w-full">
          {/* 灰色轨道背景 */}
          <line x1="40" y1="90" x2="440" y2="90"
            stroke="rgba(255,255,255,0.04)" strokeWidth="3" />
          {/* 线路色轨道片段 */}
          <line x1="160" y1="90" x2="320" y2="90"
            stroke={color} strokeWidth="3" strokeLinecap="round"
            opacity="0.35" />
          {/* 装饰性站点圆点 */}
          {[0.2, 0.35, 0.5, 0.65, 0.8].map((ratio, i) => (
            <circle key={i}
              cx={80 + ratio * 320} cy="90" r="5"
              fill="none" stroke={color}
              strokeWidth="1.2" opacity={0.3} />
          ))}
          {/* 虚线圈指示器 */}
          <circle cx="240" cy="90" r="24"
            fill="none" stroke={color} strokeWidth="1"
            strokeDasharray="4 6" opacity="0.2" />
        </svg>
      </div>

      {/* 文字信息 */}
      <div className="text-center space-y-3">
        <div className="flex items-center gap-2 justify-center">
          <span className="w-3 h-3 rounded-full" style={{ backgroundColor: color }} />
          <h3 className="text-[18px] font-semibold text-[#9fb0c8]">{name}</h3>
        </div>
        {stationCount > 0 && (
          <div className="text-[12px] text-[#52647b]">
            {stationCount} 座车站 · 暂无轨道级数据
          </div>
        )}
        <div className="mt-4 p-4 border border-[#172436] bg-[#07101b] max-w-[360px]">
          <div className="text-[11px] text-[#5f7088] leading-relaxed">
            当前仅 <span className="text-[#8FC31F] font-mono">9号线</span> 已接入完整的
            轨道级数据（区段·信号机·站台·进路·限速·坡度等）。
          </div>
          <div className="mt-2 text-[10px] text-[#3a4f66] leading-relaxed">
            如需扩展其他线路，请将对应 Excel 工作表导入至
            <code className="text-[#5a6a80] mx-1 bg-[#0a1520] px-1 py-0.5 rounded">data/cache/line_map.json</code>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ════════════════ 9号线轨道内容 ════════════════ */
function TrackContent({
  trackMap,
  selectedStation,
  selectedCode,
  onStationClick,
  onStationDoubleClick,
}: {
  trackMap: NonNullable<ReturnType<typeof useSimStore.getState>['trackMap']>;
  selectedStation: Line9Station | null;
  selectedCode: string | null;
  onStationClick: (code: string) => void;
  onStationDoubleClick: (code: string) => void;
}) {
  const firstMileage = trackMap.stations[0]?.mileageM ?? 0;
  const span = Math.max(trackMap.lengthM, 1);

  return (
    <>
      <div className="flex items-end justify-between gap-4 mb-5">
        <div>
          <div className="text-[11px] uppercase tracking-[0.18em] text-[#5f7088]">Track Level</div>
          <h2 className="text-[20px] font-semibold text-[#dce8f8] mt-1">9号线轨道级静态视图</h2>
        </div>
        <div className="grid grid-cols-4 gap-2 text-right">
          <Metric label="Seg" value={trackMap.counts.segments} />
          <Metric label="Signal" value={trackMap.counts.signals} />
          <Metric label="Platform" value={trackMap.counts.platforms} />
          <Metric label="Route" value={trackMap.counts.routes} />
        </div>
      </div>

      <div className="relative h-[360px] border border-[#1b2a3d] bg-[#07101b] overflow-hidden">
        <div className="absolute left-8 right-8 top-[172px] h-[3px] bg-[#8FC31F]" />
        <div className="absolute left-8 right-8 top-[190px] h-px bg-[#2b3c52]" />
        {trackMap.stations.map((station) => (
          <StationMarker
            key={station.stationCode}
            station={station}
            leftPercent={8 + ((station.mileageM - firstMileage) / span) * 84}
            active={selectedStation?.stationCode === station.stationCode}
            onClick={() => onStationClick(station.stationCode)}
            onDoubleClick={() => onStationDoubleClick(station.stationCode)}
          />
        ))}
        <div className="absolute left-8 right-8 bottom-5 flex justify-between text-[10px] text-[#52647b] font-mono">
          <span>K{fmt(firstMileage / 1000, 3)}</span>
          <span>{fmt(trackMap.lengthM / 1000, 2)} km</span>
          <span>K{fmt((firstMileage + trackMap.lengthM) / 1000, 3)}</span>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-3 gap-3">
        <LayerStat title="静态限速" value={trackMap.counts.speedRestrictions} detail="已接入 speedRestrictions" />
        <LayerStat title="坡度" value={trackMap.counts.gradients} detail="已接入 gradients" />
        <LayerStat title="区段" value={trackMap.counts.axleSections + trackMap.counts.logicalSections} detail="计轴 + 逻辑区段" />
      </div>
    </>
  );
}

/* ════════════════ 子组件 ════════════════ */

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="min-w-[62px] border border-[#172436] bg-[#081321] px-2 py-1.5">
      <div className="text-[10px] text-[#5a6a80]">{label}</div>
      <div className="text-[16px] font-mono text-[#8FC31F]">{value}</div>
    </div>
  );
}

function StationMarker({
  station,
  leftPercent,
  active,
  onClick,
  onDoubleClick,
}: {
  station: Line9Station;
  leftPercent: number;
  active: boolean;
  onClick: () => void;
  onDoubleClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      onDoubleClick={onDoubleClick}
      className="absolute top-[152px] -translate-x-1/2 text-left cursor-pointer"
      style={{ left: `${leftPercent}%` }}
      title={`${station.stationName} ${station.stationCode}`}
    >
      <span
        className="block w-4 h-4 rounded-full border-2 bg-[#07101b]"
        style={{
          borderColor: active ? '#ffffff' : '#8FC31F',
          boxShadow: active ? '0 0 14px rgba(143,195,31,0.85)' : '0 0 8px rgba(143,195,31,0.35)',
        }}
      />
      <span
        className="absolute left-1/2 -translate-x-1/2 top-7 w-[84px] text-center text-[10px] leading-tight"
        style={{ color: active ? '#ffffff' : '#9fb0c8' }}
      >
        {station.stationName}
      </span>
      <span className="absolute left-1/2 -translate-x-1/2 top-14 text-[9px] font-mono text-[#52647b]">
        {station.stationCode}
      </span>
    </button>
  );
}

function LayerStat({ title, value, detail }: { title: string; value: number; detail: string }) {
  return (
    <div className="border border-[#172436] bg-[#07101b] px-3 py-3">
      <div className="flex items-center justify-between">
        <span className="text-[12px] text-[#b8c7dc]">{title}</span>
        <span className="text-[15px] font-mono text-[#58a6ff]">{value}</span>
      </div>
      <div className="mt-1 text-[10px] text-[#52647b]">{detail}</div>
    </div>
  );
}

function StationInspector({ station }: { station: Line9Station }) {
  return (
    <div>
      <h3 className="text-[18px] font-semibold text-[#dce8f8]">{station.stationName}</h3>
      <div className="mt-1 text-[11px] font-mono text-[#5f7088]">
        {station.stationCode} · K{fmt(station.mileageM / 1000, 3)}
      </div>

      <div className="mt-5 space-y-3">
        <InfoRow label="站台 ID" value={station.platformIds.join(', ')} />
        <InfoRow label="关联 Seg" value={station.platformSegmentIds.join(', ')} />
        <InfoRow label="停站时间" value={`${station.dwellSeconds}s`} />
        <InfoRow label="下一区间限速" value={`${station.speedLimitToNextKmh} km/h`} />
      </div>

      <div className="mt-5 pt-4 border-t border-[#172436]">
        <div className="text-[11px] text-[#8ba0bb] mb-2">站台方向</div>
        <div className="space-y-2">
          {station.platforms.map((platform) => (
            <div key={platform.id} className="flex items-center justify-between text-[11px] bg-[#091827] px-2 py-1.5">
              <span className="text-[#c7d5e8]">P{platform.id}</span>
              <span className="font-mono text-[#5f7088]">Seg {platform.segmentId}</span>
              <span className="font-mono text-[#8FC31F]">{platform.direction ?? '-'}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between border-b border-[#101d2d] pb-2">
      <span className="text-[11px] text-[#5f7088]">{label}</span>
      <span className="text-[12px] text-[#c7d5e8] font-mono">{value || '-'}</span>
    </div>
  );
}
