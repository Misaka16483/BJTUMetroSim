import { useMemo } from 'react';
import { useSimStore } from '../store/useSimStore';

const STATUS_COLOR: Record<string, string> = {
  NORMAL: 'var(--green)',
  WARNING: 'var(--amber)',
  OVERLOAD: 'var(--red)',
  OUTAGE: 'var(--red)',
  OPEN: 'var(--text-muted)',
};

function fmt(value: number, digits = 0) {
  return Number.isFinite(value) ? value.toFixed(digits) : '-';
}

export default function PowerNetworkPanel() {
  const {
    simPowerNetwork,
    minTrainVoltageV,
    totalAbsorbedRegenKw,
    totalWastedRegenKw,
    powerLossesKw,
  } = useSimStore();

  const substations = simPowerNetwork?.substations ?? [];
  const trainVoltages = simPowerNetwork?.trainVoltages ?? [];
  const alerts = simPowerNetwork?.alerts ?? [];
  const regen = simPowerNetwork?.regen;
  const busiest = useMemo(
    () => [...substations].sort((a, b) => b.loadRatio - a.loadRatio).slice(0, 5),
    [substations],
  );
  const minVoltageTrain = useMemo(
    () => [...trainVoltages].sort((a, b) => a.voltageV - b.voltageV)[0],
    [trainVoltages],
  );

  return (
    <div className="glass flex flex-col h-full min-h-[300px]">
      <div className="flex items-center justify-between px-5 py-3 shrink-0">
        <span className="label" style={{ color: 'var(--text-muted)' }}>牵引供电</span>
        <span className="board-num text-[9px]" style={{ color: alerts.length ? 'var(--amber)' : 'var(--green)' }}>
          {alerts.length ? `${alerts.length} 条告警` : '正常'}
        </span>
      </div>

      <div className="px-5 pb-4 space-y-3 overflow-auto">
        <div className="grid grid-cols-2 gap-2">
          <Metric label="最低电压" value={fmt(minVoltageTrain?.voltageV ?? minTrainVoltageV, 0)} unit="V" color={(minVoltageTrain?.voltageV ?? minTrainVoltageV) < 650 ? 'var(--amber)' : 'var(--green)'} />
          <Metric label="线路损耗" value={fmt(simPowerNetwork?.lossesKw ?? powerLossesKw, 1)} unit="kW" color="var(--text-dim)" />
          <Metric label="再生吸收" value={fmt(regen?.absorbedKw ?? totalAbsorbedRegenKw, 0)} unit="kW" color="var(--cyan)" />
          <Metric label="再生浪费" value={fmt(regen?.wastedKw ?? totalWastedRegenKw, 0)} unit="kW" color={(regen?.wastedKw ?? totalWastedRegenKw) > 0 ? 'var(--amber)' : 'var(--green)'} />
        </div>

        <section className="card" style={{ padding: 12 }}>
          <div className="flex items-center justify-between mb-2">
            <span className="label" style={{ color: 'var(--text-muted)' }}>牵引变电所负载</span>
            <span className="board-num text-[9px]" style={{ color: 'var(--text-muted)' }}>{substations.length}</span>
          </div>
          <div className="space-y-1.5">
            {busiest.map((item) => (
              <div key={item.substationId}>
                <div className="flex items-center justify-between text-[10px]">
                  <span className="font-mono" style={{ color: 'var(--text-dim)' }}>{item.substationId}</span>
                  <span className="board-num" style={{ color: STATUS_COLOR[item.status] ?? 'var(--text-muted)' }}>
                    {fmt(item.voltageV, 0)}V / {fmt(item.currentA, 0)}A
                  </span>
                </div>
                <div className="h-1.5 mt-1 rounded-full overflow-hidden" style={{ background: 'rgba(255,255,255,0.05)' }}>
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${Math.max(3, Math.min(100, item.loadRatio * 100))}%`,
                      background: STATUS_COLOR[item.status] ?? 'var(--cyan)',
                    }}
                  />
                </div>
              </div>
            ))}
            {busiest.length === 0 && (
              <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>等待后端供电潮流数据</div>
            )}
          </div>
        </section>

        <section className="card" style={{ padding: 12 }}>
          <div className="flex items-center justify-between mb-2">
            <span className="label" style={{ color: 'var(--text-muted)' }}>列车受电状态</span>
            <span className="board-num text-[9px]" style={{ color: minVoltageTrain ? 'var(--cyan)' : 'var(--text-muted)' }}>
              {minVoltageTrain?.trainId ?? '-'}
            </span>
          </div>
          <div className="space-y-1.5">
            {trainVoltages.slice(0, 4).map((item) => (
              <div key={item.trainId} className="flex items-center justify-between text-[10px]">
                <span className="font-mono" style={{ color: 'var(--text-dim)' }}>{item.trainId}</span>
                <span className="board-num" style={{ color: item.voltageV < 650 ? 'var(--amber)' : 'var(--green)' }}>
                  {fmt(item.voltageV, 0)}V / {fmt(item.currentA, 0)}A / {Math.round(item.tractionLimitRatio * 100)}%
                </span>
              </div>
            ))}
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
      <div className="label mb-1.5" style={{ color: 'var(--text-muted)' }}>{label}</div>
      <div className="flex items-baseline gap-1 min-w-0">
        <span className="board-num text-[20px] font-bold leading-none truncate" style={{ color }}>{value}</span>
        <span className="board-num text-[9px]" style={{ color: 'var(--text-muted)' }}>{unit}</span>
      </div>
    </div>
  );
}
