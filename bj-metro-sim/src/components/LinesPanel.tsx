import { useState, useRef, useEffect } from 'react';
import { useSimStore } from '../store/useSimStore';
import { computeStationTransfers } from '../data/transferUtils';

export default function LinesPanel() {
  const [expandedLines, setExpandedLines] = useState<Set<string>>(new Set(['9']));
  const metroLines = useSimStore((s) => s.metroLines);
  const hiddenLines = useSimStore((s) => s.hiddenLines);
  const toggleLineVisibility = useSimStore((s) => s.toggleLineVisibility);
  const autoExpanded = useRef(false);

  useEffect(() => {
    if (autoExpanded.current) return;
    if (metroLines.some((l) => l.id === '9')) autoExpanded.current = true;
  }, [metroLines]);

  const transfers = computeStationTransfers(metroLines);
  const lineMap = new Map(metroLines.map((l) => [l.id, l]));

  return (
    <div className="glass flex flex-col h-full">
      <div className="flex items-center justify-between px-5 py-3 shrink-0">
        <span className="label" style={{ color: 'var(--text-muted)' }}>线路</span>
        <span className="board-num text-[9px]" style={{ color: 'var(--text-muted)' }}>LINES</span>
      </div>

      <div className="flex-1 overflow-auto">
        {metroLines.map((line) => {
          const visible = !hiddenLines.has(line.id);
          const expanded = expandedLines.has(line.id);
          const is9 = line.id === '9';

          return (
            <div key={line.id}>
              <div
                onClick={() => setExpandedLines((prev) => {
                  const next = new Set(prev);
                  next.has(line.id) ? next.delete(line.id) : next.add(line.id);
                  return next;
                })}
                className="flex items-center gap-2 px-5 py-2 cursor-pointer"
                style={{
                  background: is9 ? 'rgba(168,214,74,0.04)' : 'transparent',
                  borderLeft: is9 ? '2px solid var(--l9)' : '2px solid transparent',
                  borderBottom: '1px solid rgba(255,255,255,0.03)',
                }}
              >
                <div
                  className="led shrink-0"
                  style={{
                    background: visible ? line.color : 'var(--text-muted)',
                    boxShadow: visible ? `0 0 6px ${line.color}50` : 'none',
                  }}
                />
                <span
                  className="text-[12px] font-medium flex-1 truncate tracking-tight"
                  style={{ color: visible ? 'var(--text-dim)' : 'var(--text-muted)' }}
                >
                  {line.name}
                </span>
                <span className="board-num text-[9px] shrink-0" style={{ color: 'var(--text-muted)' }}>
                  {line.stations.length}
                </span>
                <svg
                  width="8" height="5" viewBox="0 0 8 5" fill="none"
                  className="shrink-0"
                  style={{
                    transform: expanded ? 'rotate(180deg)' : 'rotate(0)',
                    transition: 'transform 240ms cubic-bezier(0.33, 1, 0.68, 1)',
                  }}
                >
                  <path d="M1.5 1L4 3.5L6.5 1" stroke="#636366" strokeWidth="1" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                <div
                  onClick={(e) => { e.stopPropagation(); toggleLineVisibility(line.id); }}
                  className="shrink-0 cursor-pointer"
                >
                  <div
                    className="w-7 h-4.5 rounded-full relative"
                    style={{
                      background: visible ? `${line.color}20` : 'rgba(255,255,255,0.04)',
                      border: visible ? `1px solid ${line.color}25` : '1px solid rgba(255,255,255,0.04)',
                      transition: 'all 240ms cubic-bezier(0.33, 1, 0.68, 1)',
                    }}
                  >
                    <div
                      className="rounded-full absolute"
                      style={{
                        width: 11, height: 11,
                        left: visible ? 'calc(100% - 13px)' : '2px',
                        top: '1px',
                        background: visible ? line.color : 'var(--text-muted)',
                        transition: 'left 280ms cubic-bezier(0.33, 1, 0.68, 1)',
                      }}
                    />
                  </div>
                </div>
              </div>

              {expanded && (
                <div style={{ background: 'rgba(0,0,0,0.15)' }}>
                  {line.stations.map((st, i) => {
                    const t = transfers.find(
                      (tr) => tr.name === st.name && tr.lineId === line.id && tr.transfers.length > 0
                    );
                    return (
                      <div
                        key={st.name}
                        className="flex items-center gap-1.5 text-[10px] py-1 px-5"
                        style={{ color: 'var(--text-dim)' }}
                      >
                        <span className="board-num text-[9px] w-3 shrink-0" style={{ color: 'var(--text-muted)' }}>
                          {String(i + 1).padStart(2, '0')}
                        </span>
                        <span className="flex-1 truncate">{st.name}</span>
                        {t && (
                          <div className="flex items-center gap-1 shrink-0" style={{ maxWidth: 160, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                            {t.transfers.map((tid) => {
                              const tl = lineMap.get(tid);
                              if (!tl) return null;
                              return (
                                <span
                                  key={tid}
                                  className="text-[8px] px-1 rounded-sm"
                                  style={{
                                    background: `${tl.color}18`,
                                    color: tl.color,
                                    border: `1px solid ${tl.color}30`,
                                    whiteSpace: 'nowrap',
                                  }}
                                >
                                  {tl.name}
                                </span>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
