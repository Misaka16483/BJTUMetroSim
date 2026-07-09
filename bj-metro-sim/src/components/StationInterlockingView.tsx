import type { StationInterlockingData } from '../data/stationInterlockingData';

function aspectColor(aspect: string) {
  if (aspect === 'GREEN') return 'var(--green)';
  if (aspect === 'YELLOW') return 'var(--amber)';
  return 'var(--red)';
}

function routeColor(state: string) {
  if (state === 'LOCKED') return 'var(--l9)';
  if (state === 'OCCUPIED') return 'var(--red)';
  return 'rgba(255,255,255,0.18)';
}

export default function StationInterlockingView({ data }: { data: StationInterlockingData }) {
  return (
    <div className="h-full min-h-0 flex bg-[#040810]">
      <div className="flex-1 min-w-0 p-5 overflow-auto">
        <div className="flex items-end justify-between gap-4 mb-5">
          <div>
            <div className="text-[11px] uppercase tracking-[0.18em] text-[#5f7088]">
              Station Interlocking
            </div>
            <h2 className="text-[20px] font-semibold text-[#dce8f8] mt-1">
              {data.stationName} 联锁图
            </h2>
            <div className="mt-1 text-[11px] font-mono text-[#5f7088]">
              {data.stationCode} · K{(data.mileageM / 1000).toFixed(3)}
            </div>
          </div>
          <div className="grid grid-cols-3 gap-2 text-right">
            <Metric label="Signal" value={data.signals.length} />
            <Metric label="Switch" value={data.switches.length} />
            <Metric label="Route" value={data.routes.length} />
          </div>
        </div>

        <div className="relative h-[430px] border border-[#1b2a3d] bg-[#07101b] overflow-hidden">
          <svg viewBox="0 0 1000 430" className="absolute inset-0 w-full h-full">
            <defs>
              <filter id="interlockingGlow" x="-30%" y="-30%" width="160%" height="160%">
                <feGaussianBlur stdDeviation="3" result="blur" />
                <feMerge>
                  <feMergeNode in="blur" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
            </defs>

            <line x1="70" y1="168" x2="930" y2="168" stroke="#2b3c52" strokeWidth="5" />
            <line x1="70" y1="252" x2="930" y2="252" stroke="#2b3c52" strokeWidth="5" />
            <rect x="395" y="126" width="210" height="52" rx="4" fill="rgba(143,195,31,0.08)" stroke="rgba(143,195,31,0.45)" />
            <rect x="395" y="242" width="210" height="52" rx="4" fill="rgba(143,195,31,0.08)" stroke="rgba(143,195,31,0.45)" />
            <text x="500" y="158" textAnchor="middle" fill="#c7d5e8" fontSize="18">上行站台</text>
            <text x="500" y="275" textAnchor="middle" fill="#c7d5e8" fontSize="18">下行站台</text>

            {data.routes.map((route) => (
              <line
                key={route.id}
                x1="265"
                y1={route.direction === 'UP' ? 168 : 252}
                x2="735"
                y2={route.direction === 'UP' ? 168 : 252}
                stroke={routeColor(route.state)}
                strokeWidth="8"
                strokeLinecap="round"
                filter={route.state !== 'AVAILABLE' ? 'url(#interlockingGlow)' : undefined}
              />
            ))}

            {data.switches.map((sw) => (
              <g key={sw.id} transform={`translate(${sw.x * 10}, 210)`}>
                <line x1="-34" y1="-42" x2="34" y2="42" stroke={sw.locked ? 'var(--amber)' : '#52647b'} strokeWidth="4" />
                <circle r="12" fill="#07101b" stroke={sw.locked ? 'var(--amber)' : '#52647b'} strokeWidth="3" />
                <text y="34" textAnchor="middle" fill="#8ba0bb" fontSize="13">{sw.id}</text>
              </g>
            ))}

            {data.signals.map((signal) => (
              <g key={signal.id} transform={`translate(${signal.x * 10}, ${signal.direction === 'UP' ? 118 : 302})`}>
                <line x1="0" y1="0" x2="0" y2={signal.direction === 'UP' ? 40 : -40} stroke="#52647b" strokeWidth="3" />
                <circle r="10" fill={aspectColor(signal.aspect)} filter="url(#interlockingGlow)" />
                <text x="0" y={signal.direction === 'UP' ? -18 : 30} textAnchor="middle" fill="#c7d5e8" fontSize="13">
                  {signal.id}
                </text>
              </g>
            ))}
          </svg>
        </div>
      </div>

      <aside className="w-[300px] border-l border-[#172436] bg-[#07101b] p-4 overflow-auto">
        <div className="text-[10px] uppercase tracking-[0.16em] text-[#5f7088] mb-3">Interlocking State</div>
        <section className="space-y-2">
          {data.routes.map((route) => (
            <div key={route.id} className="border border-[#172436] bg-[#091827] px-3 py-2">
              <div className="flex items-center justify-between">
                <span className="text-[12px] text-[#c7d5e8]">{route.id}</span>
                <span className="text-[10px] font-mono" style={{ color: routeColor(route.state) }}>
                  {route.state}
                </span>
              </div>
              <div className="mt-1 text-[10px] text-[#5f7088]">
                {route.from} → {route.to} · {route.direction}
              </div>
            </div>
          ))}
        </section>

        <section className="mt-5 pt-4 border-t border-[#172436] space-y-2">
          {data.signals.map((signal) => (
            <div key={signal.id} className="flex items-center justify-between text-[11px]">
              <span className="text-[#8ba0bb]">{signal.label}</span>
              <span className="font-mono" style={{ color: aspectColor(signal.aspect) }}>
                {signal.id} {signal.aspect}
              </span>
            </div>
          ))}
        </section>
      </aside>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="min-w-[62px] border border-[#172436] bg-[#081321] px-2 py-1.5">
      <div className="text-[10px] text-[#5a6a80]">{label}</div>
      <div className="text-[16px] font-mono text-[#f85149]">{value}</div>
    </div>
  );
}
