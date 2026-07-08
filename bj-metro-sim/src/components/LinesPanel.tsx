import { useState, useMemo } from 'react';
import { useSimStore } from '../store/useSimStore';
import { computeStationTransfers } from '../data/transferUtils';

export default function LinesPanel() {
  const [expandedLines, setExpandedLines] = useState<Set<string>>(new Set());
  const metroLines = useSimStore((s) => s.metroLines);
  const linesLoading = useSimStore((s) => s.linesLoading);
  const hiddenLines = useSimStore((s) => s.hiddenLines);
  const toggleLineVisibility = useSimStore((s) => s.toggleLineVisibility);
  const activeCount = metroLines.length - hiddenLines.size;

  // 预计算所有站点的换乘关系
  const stationTransfers = useMemo(() => computeStationTransfers(metroLines), [metroLines]);

  // 线路名 -> 线路对象映射（用于换乘站点显示其他线路颜色）
  const lineById = useMemo(() => {
    const map = new Map<string, (typeof metroLines)[number]>();
    for (const l of metroLines) map.set(l.id, l);
    return map;
  }, [metroLines]);

  const toggleExpand = (lineId: string) => {
    setExpandedLines((prev) => {
      const next = new Set(prev);
      if (next.has(lineId)) next.delete(lineId);
      else next.add(lineId);
      return next;
    });
  };

  return (
    <div
      className="h-full flex flex-col select-none relative"
      style={{ background: 'linear-gradient(180deg, #0a0e16 0%, #06090e 100%)' }}
    >
      {/* 顶边发光 */}
      <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-[#4a9eff]/20 to-transparent" />

      {/* 面板头部 */}
      <div
        className="flex items-center justify-between px-3 py-2.5 shrink-0"
        style={{ borderBottom: '1px solid rgba(74, 158, 255, 0.08)' }}
      >
            <span className="text-[12px] font-medium tracking-[0.1em] uppercase text-[#6a7a90]">
              Lines
            </span>
            <div className="flex items-center gap-1.5">
              <span
                className="font-mono text-[14px] text-[#4a9eff] tabular-nums"
                style={{ textShadow: '0 0 8px rgba(74, 158, 255, 0.3)' }}
              >
                {activeCount}
              </span>
              <span className="text-[11px] text-[#2a3040]">/</span>
              <span className="font-mono text-[13px] text-[#3a4a60] tabular-nums">
                {metroLines.length}
              </span>
            </div>
          </div>

          {/* 线路列表 */}
          <div className="flex-1 overflow-y-auto overflow-x-hidden" style={{ scrollbarWidth: 'thin', scrollbarColor: '#1a2240 transparent' }}>
            {linesLoading && (
              <div className="flex items-center justify-center py-8 text-[#4a5568] text-[12px] animate-pulse">
                加载线路数据...
              </div>
            )}
            {!linesLoading && metroLines.length === 0 && (
              <div className="flex flex-col items-center justify-center py-8 text-[12px] text-[#3a4a60] gap-1">
                <span>暂无数据</span>
                <span className="text-[11px] opacity-60">正在从 OSM 获取</span>
              </div>
            )}
            {metroLines.map((line) => {
              const visible = !hiddenLines.has(line.id);
              const isExpanded = expandedLines.has(line.id);

              // 获取该线路所有站点的换乘信息
              const lineStationsWithTransfers = stationTransfers.filter(
                (s) => s.lineId === line.id
              );

              return (
                <div
                  key={line.id}
                  style={{
                    borderBottom: '1px solid rgba(255,255,255,0.02)',
                    opacity: visible ? 1 : 0.3,
                  }}
                >
                  {/* 线路头部 */}
                  <div
                    className="flex items-center gap-2.5 px-3 py-2 cursor-pointer transition-colors duration-150"
                    onClick={() => toggleExpand(line.id)}
                    onMouseEnter={(e) => {
                      if (visible) e.currentTarget.style.background = 'rgba(74,158,255,0.04)';
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.background = 'transparent';
                    }}
                  >
                    {/* 状态 LED — 独立点击切换可见性 */}
                    <span
                      className="shrink-0 block cursor-pointer relative z-10"
                      style={{
                        width: '5px',
                        height: '5px',
                        backgroundColor: visible ? '#00ff88' : '#1a2240',
                        boxShadow: visible
                          ? '0 0 6px rgba(0,255,136,0.6), 0 0 12px rgba(0,255,136,0.2)'
                          : 'none',
                      }}
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleLineVisibility(line.id);
                      }}
                    />

                    {/* 线路色标 */}
                    <span
                      className="shrink-0 block"
                      style={{
                        width: '18px',
                        height: '2px',
                        backgroundColor: visible ? line.color : '#1a2240',
                        boxShadow: visible ? `0 0 4px ${line.color}40` : 'none',
                      }}
                    />

                    {/* 线路名 */}
                    <span
                      className="flex-1 text-[13px] truncate"
                      style={{
                        color: visible ? '#a0b8d0' : '#2a3040',
                        fontWeight: 400,
                      }}
                    >
                      {line.name}
                    </span>

                    {/* 站点数 */}
                    <span
                      className="font-mono text-[11px] tabular-nums shrink-0"
                      style={{ color: visible ? '#4a5568' : '#1a2240' }}
                    >
                      {line.stations.length}
                    </span>

                    {/* 展开箭头 */}
                    <svg
                      width="6"
                      height="4"
                      viewBox="0 0 6 4"
                      fill="none"
                      className="shrink-0 transition-transform duration-200"
                      style={{
                        transform: isExpanded ? 'rotate(180deg)' : 'rotate(0deg)',
                        color: visible ? '#4a5568' : '#1a2240',
                      }}
                    >
                      <path d="M0.5 0.5L3 3L5.5 0.5" stroke="currentColor" strokeWidth="0.8" />
                    </svg>
                  </div>

                  {/* 展开的站点列表 */}
                  <div
                    className="overflow-hidden transition-all duration-200"
                    style={{
                      maxHeight: isExpanded ? `${lineStationsWithTransfers.length * 28 + 16}px` : '0px',
                      opacity: isExpanded ? 1 : 0,
                    }}
                  >
                    <div
                      className="px-3 pb-2"
                      style={{
                        borderTop: isExpanded ? '1px solid rgba(74,158,255,0.05)' : 'none',
                      }}
                    >
                      {lineStationsWithTransfers.map((st, idx) => (
                        <div
                          key={`${st.name}-${idx}`}
                          className="flex items-center gap-1.5 py-[3px]"
                          style={{ paddingLeft: '2px' }}
                        >
                          {/* 站点序号圆点 */}
                          <span
                            className="shrink-0 font-mono text-[10px] flex items-center justify-center"
                            style={{
                              width: '14px',
                              height: '14px',
                              color: visible ? '#4a5568' : '#1a2240',
                            }}
                          >
                            {idx + 1}
                          </span>

                          {/* 站点名 */}
                          <span
                            className="text-[12px] truncate"
                            style={{ color: visible ? '#8090a8' : '#1a2240' }}
                          >
                            {st.name}
                          </span>

                          {/* 换乘标签 */}
                          {st.transfers.length > 0 && (
                            <div className="flex items-center gap-1 ml-auto shrink-0">
                              {st.transfers.map((transferLineId) => {
                                const transferLine = lineById.get(transferLineId);
                                return (
                                  <span
                                    key={transferLineId}
                                    className="text-[9px] px-1.5 py-0.5 font-medium whitespace-nowrap"
                                    style={{
                                      backgroundColor: transferLine
                                        ? `${transferLine.color}25`
                                        : 'rgba(255,255,255,0.06)',
                                      color: transferLine ? transferLine.color : '#4a5568',
                                      border: transferLine
                                        ? `1px solid ${transferLine.color}40`
                                        : '1px solid rgba(255,255,255,0.06)',
                                      borderRadius: '2px',
                                      lineHeight: '12px',
                                    }}
                                  >
                                    {transferLine?.name || transferLineId}
                                  </span>
                                );
                              })}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
    </div>
  );
}
