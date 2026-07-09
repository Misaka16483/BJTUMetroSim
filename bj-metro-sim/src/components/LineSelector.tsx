import type { MetroLineData } from '../data/amapMetroApi';

/** 北京地铁各线路官方配色 */
const LINE_COLORS: Record<string, string> = {
  '1': '#C23A30', '2': '#006098', '3': '#D86018', '4': '#008C95', '5': '#AA0061',
  '6': '#B58500', '7': '#FFC56E', '8': '#009B6B', '9': '#8FC31F',
  '10': '#009BC0', '11': '#ED796B', '12': '#A567BE', '13': '#F9E701',
  '14': '#CA9A8E', '15': '#753BBD', '16': '#6BA539', '17': '#00B2A9',
  '18': '#D22668', '19': '#D3ABE7', '22': '#D49C3D',
  '24': '#DE82B2', '25': '#EF7E21', '27': '#FF5A93',
  '八通': '#C23A30', '昌平': '#DE82B2', '亦庄': '#D0006E',
  '房山': '#D86018', '大兴': '#006098', '机场': '#A288B3',
  'S1': '#A45A2A', '西郊': '#CE3D3A', '燕房': '#EF7E21',
  '首都机场': '#A192B2', '大兴机场': '#004A9F',
};

export function lineColor(lineId: string): string {
  return LINE_COLORS[lineId] ?? '#5f7088';
}

/** 线路名称简写 */
export function lineLabel(line: MetroLineData): string {
  if (line.name.length <= 3) return line.name;
  // 地铁X号线 → X号线
  const m = line.name.match(/地铁(\d+)号线/);
  if (m) return `${m[1]}号线`;
  return line.name.slice(0, 6);
}

interface LineSelectorProps {
  lines: MetroLineData[];
  activeLineId: string;
  onSelect: (id: string) => void;
  /** 哪些线路有数据 (默认仅 '9') */
  dataLineIds?: Set<string>;
}

export default function LineSelector({
  lines,
  activeLineId,
  onSelect,
  dataLineIds,
}: LineSelectorProps) {
  const hasData = dataLineIds ?? new Set(['9']);

  return (
    <div className="mb-5">
      <div className="text-[11px] uppercase tracking-[0.18em] text-[#5f7088] mb-3">Select Line</div>
      <div className="flex flex-wrap gap-1.5">
        {lines.map((line) => {
          const color = lineColor(line.id);
          const active = line.id === activeLineId;
          const lineHasData = hasData.has(line.id);
          const label = lineLabel(line);

          return (
            <button
              key={line.id}
              type="button"
              onClick={() => onSelect(line.id)}
              className={`group relative flex items-center gap-1.5 px-2.5 py-1.5 rounded text-[11px] font-medium
                transition-all duration-150 border cursor-pointer
                ${active ? 'border-[#2b4a6b] bg-[#0d1b30] text-[#dce8f8]' : 'border-transparent bg-[#08121e] text-[#6b7d95] hover:border-[#1a2b42] hover:text-[#9aafc8]'}`}
            >
              <span
                className="w-2 h-2 rounded-full flex-shrink-0"
                style={{ backgroundColor: color }}
              />
              <span>{label}</span>
              {lineHasData ? (
                <span className="w-1.5 h-1.5 rounded-full bg-[#8FC31F] flex-shrink-0" title="有数据" />
              ) : (
                <span className="w-1.5 h-1.5 rounded-full border border-[#2b3c52] flex-shrink-0 opacity-40" />
              )}
              {active && (
                <span
                  className="absolute bottom-0 left-2 right-2 h-0.5 rounded"
                  style={{ backgroundColor: color }}
                />
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
