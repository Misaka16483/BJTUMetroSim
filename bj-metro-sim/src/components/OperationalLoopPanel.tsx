import { useMemo } from 'react';
import { useSimStore } from '../store/useSimStore';

const CROWD_COLORS: Record<string, string> = {
  LOW: 'var(--green)',
  MEDIUM: 'var(--cyan)',
  HIGH: 'var(--amber)',
  CRITICAL: 'var(--red)',
};

const VOLTAGE_COLORS: Record<string, string> = {
  NORMAL: 'var(--green)',
  LIMITED: 'var(--amber)',
  UNDERVOLTAGE: 'var(--red)',
  OUTAGE: 'var(--red)',
};

function pct(value: number) {
  return `${Math.round(value * 100)}%`;
}

export default function OperationalLoopPanel() {
  const {
    simStations,
    simPower,
    dispatchDecisions,
    totalWaitingPax,
    maxPlatformDensity,
    totalTractionEnergyKwh,
    minTractionLimitRatio,
    lastDispatchAction,
  } = useSimStore();

  const crowdedStations = useMemo(
    () => [...simStations]
      .sort((a, b) => (b.waitingPax ?? 0) - (a.waitingPax ?? 0))
      .slice(0, 4),
    [simStations],
  );
  const latestDecision = dispatchDecisions[dispatchDecisions.length - 1];
  const primaryPower = simPower[0];

  return (
    <div className="glass flex flex-col h-full min-h-[280px]">
      <div className="flex items-center justify-between px-5 py-3 shrink-0">
        <span className="label" style={{ color: 'var(--text-muted)' }}>运营闭环</span>
        <span className="board-num text-[9px]" style={{ color: 'var(--text-muted)' }}>D LOOP</span>
      </div>

      <div className="px-5 pb-4 space-y-3 overflow-auto">
        <div className="grid grid-cols-2 gap-2">
          <Metric label="站台等待" value={totalWaitingPax.toLocaleString()} unit="pax" color="var(--cyan)" />
          <Metric label="最高密度" value={maxPlatformDensity.toFixed(2)} unit="p/m2" color={maxPlatformDensity >= 2.5 ? 'var(--amber)' : 'var(--green)'} />
          <Metric label="限牵系数" value={pct(minTractionLimitRatio)} unit="" color={minTractionLimitRatio < 0.8 ? 'var(--amber)' : 'var(--green)'} />
          <Metric label="牵引能耗" value={totalTractionEnergyKwh.toFixed(2)} unit="kWh" color="var(--text-dim)" />
        </div>

        <section className="card" style={{ padding: 12 }}>
          <div className="flex items-center justify-between mb-2">
            <span className="label" style={{ color: 'var(--text-muted)' }}>客流与停站</span>
            <span className="board-num text-[9px]" style={{ color: 'var(--text-muted)' }}>FLOW</span>
          </div>
          <div className="space-y-2">
            {crowdedStations.map((station) => {
              const waiting = station.waitingPax ?? 0;
              const density = station.platformDensity ?? 0;
              const crowding = station.crowdingLevel ?? 'LOW';
              const bar = Math.max(4, Math.min(100, density / 4 * 100));
              return (
                <div key={`${station.code}-${station.direction ?? 'UP'}`}>
                  <div className="flex items-center justify-between text-[10px]">
                    <span className="truncate" style={{ color: 'var(--text-dim)' }}>{station.name}</span>
                    <span className="board-num" style={{ color: CROWD_COLORS[crowding] ?? 'var(--text-muted)' }}>
                      {waiting} · {crowding}
                    </span>
                  </div>
                  <div className="h-1.5 mt-1 rounded-full overflow-hidden" style={{ background: 'rgba(255,255,255,0.05)' }}>
                    <div
                      className="h-full rounded-full"
                      style={{ width: `${bar}%`, background: CROWD_COLORS[crowding] ?? 'var(--cyan)' }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        <section className="card" style={{ padding: 12 }}>
          <div className="flex items-center justify-between mb-2">
            <span className="label" style={{ color: 'var(--text-muted)' }}>供电约束</span>
            <span className="board-num text-[9px]" style={{ color: primaryPower ? VOLTAGE_COLORS[primaryPower.voltageLevel] ?? 'var(--text-muted)' : 'var(--text-muted)' }}>
              {primaryPower?.voltageLevel ?? 'N/A'}
            </span>
          </div>
          <div className="space-y-1.5">
            {simPower.map((state) => (
              <div key={state.powerSectionId} className="flex items-center justify-between text-[10px]">
                <span className="font-mono" style={{ color: 'var(--text-dim)' }}>{state.powerSectionId}</span>
                <span className="board-num" style={{ color: VOLTAGE_COLORS[state.voltageLevel] ?? 'var(--text-muted)' }}>
                  {Math.round(state.requestedPowerKw)} / {Math.round(state.availablePowerKw)} kW · {pct(state.tractionLimitRatio)}
                </span>
              </div>
            ))}
          </div>
        </section>

        <section className="card" style={{ padding: 12 }}>
          <div className="flex items-center justify-between mb-1.5">
            <span className="label" style={{ color: 'var(--text-muted)' }}>调度决策</span>
            <span className="board-num text-[9px]" style={{ color: latestDecision?.applied === false ? 'var(--amber)' : 'var(--green)' }}>
              {lastDispatchAction}
            </span>
          </div>
          <div className="text-[10px] leading-relaxed" style={{ color: 'var(--text-dim)' }}>
            {latestDecision
              ? `${latestDecision.trainId ?? '-'} @ ${latestDecision.stationId ?? '-'} · ${latestDecision.reason} · ${latestDecision.durationSec}s`
              : '暂无调度干预，按运行图放行'}
          </div>
        </section>
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  unit,
  color,
}: {
  label: string;
  value: string;
  unit: string;
  color: string;
}) {
  return (
    <div className="card" style={{ padding: '10px 11px' }}>
      <div className="label mb-1.5" style={{ color: 'var(--text-muted)' }}>{label.toUpperCase()}</div>
      <div className="flex items-baseline gap-1 min-w-0">
        <span className="board-num text-[20px] font-bold leading-none truncate" style={{ color }}>{value}</span>
        {unit && <span className="board-num text-[9px]" style={{ color: 'var(--text-muted)' }}>{unit}</span>}
      </div>
    </div>
  );
}
