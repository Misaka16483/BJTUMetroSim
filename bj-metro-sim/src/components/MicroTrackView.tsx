import { useMemo, useState } from 'react';
import { useSimStore } from '../store/useSimStore';
import type { Line9Station } from '../data/backendApi';

function fmt(value: number, digits = 1) {
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export default function MicroTrackView() {
  const trackMap = useSimStore((s) => s.trackMap);
  const setViewMode = useSimStore((s) => s.setViewMode);
  const setSelectedStationCode = useSimStore((s) => s.setSelectedStationCode);
  const [selectedCode, setSelectedCode] = useState<string | null>(null);

  const selectedStation = useMemo(() => {
    if (!trackMap) return null;
    return trackMap.stations.find((station) => station.stationCode === selectedCode)
      ?? trackMap.stations[0]
      ?? null;
  }, [selectedCode, trackMap]);

  if (!trackMap) {
    return (
      <div className="h-full flex items-center justify-center text-[#4a5568] text-[12px]">
        正在加载轨道级数据...
      </div>
    );
  }

  const firstMileage = trackMap.stations[0]?.mileageM ?? 0;
  const span = Math.max(trackMap.lengthM, 1);

  return (
    <div className="h-full min-h-0 flex bg-[#040810]">
      <div className="flex-1 min-w-0 p-5 overflow-auto">
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
              onClick={() => setSelectedCode(station.stationCode)}
              onDoubleClick={() => {
                setSelectedStationCode(station.stationCode);
                setViewMode('interlocking');
              }}
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
      </div>

      <aside className="w-[300px] border-l border-[#172436] bg-[#07101b] p-4 overflow-auto">
        <div className="text-[10px] uppercase tracking-[0.16em] text-[#5f7088] mb-3">Inspector</div>
        {selectedStation && <StationInspector station={selectedStation} />}
      </aside>
    </div>
  );
}

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
