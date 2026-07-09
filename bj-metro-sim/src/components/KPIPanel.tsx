import { useSimStore } from '../store/useSimStore';

export default function KPIPanel() {
  const {
    punctuality, avgWaitTime, avgLoadRate,
    totalBoarded, simTime, metroLines,
  } = useSimStore();

  const cards = [
    {
      label: '准点率', unit: '%', value: punctuality.toFixed(1),
      color: punctuality >= 95 ? 'var(--green)' : punctuality >= 90 ? 'var(--amber)' : 'var(--red)',
    },
    {
      label: '站台等待', unit: 'pax', value: String(avgWaitTime),
      color: avgWaitTime < 180 ? 'var(--green)' : avgWaitTime < 360 ? 'var(--amber)' : 'var(--red)',
    },
    {
      label: '满载率', unit: '%', value: String(avgLoadRate),
      color: avgLoadRate > 120 ? 'var(--red)' : avgLoadRate > 100 ? 'var(--amber)' : 'var(--green)',
    },
    {
      label: '客运量', unit: '', value: totalBoarded.toLocaleString(),
      color: 'var(--text-dim)',
    },
  ];

  return (
    <div className="glass p-5 flex flex-col h-full">
      <div className="flex items-center justify-between mb-3">
        <span className="label" style={{ color: 'var(--text-muted)' }}>运营指标</span>
        <span className="board-num text-[9px]" style={{ color: 'var(--text-muted)' }}>KPI</span>
      </div>

      <div className="grid grid-cols-2 gap-2 flex-1">
        {cards.map((c) => (
          <div
            key={c.label}
            className="card flex flex-col justify-between"
            style={{ padding: '13px 14px' }}
          >
            <span className="label mb-2.5" style={{ color: 'var(--text-muted)' }}>
              {c.label.toUpperCase()}
            </span>
            <div className="flex items-baseline gap-0.5">
              <span
                className="board-num text-[28px] font-bold leading-none tabular-nums"
                style={{ color: c.color }}
              >
                {c.value}
              </span>
              {c.unit && (
                <span className="board-num text-[12px]" style={{ color: 'var(--text-dim)' }}>
                  {c.unit}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>

      <div className="flex justify-between mt-2 text-[9px] board-num" style={{ color: 'var(--text-muted)' }}>
        <span>LINES <span style={{ color: 'var(--cyan)' }}>{metroLines.length}</span></span>
        <span>CLK <span style={{ color: 'var(--cyan)' }}>{simTime}</span></span>
      </div>
    </div>
  );
}
