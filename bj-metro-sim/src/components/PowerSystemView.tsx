import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { useSimStore } from '../store/useSimStore';
import type { PowerSubstationState, PowerTopologySubstation, TrainVoltageState } from '../data/backendApi';

type HistoryPoint = {
  tick: number;
  minVoltageV: number;
  totalSubstationPowerKw: number;
  maxLoadRatio: number;
  lossesKw: number;
  absorbedRegenKw: number;
  wastedRegenKw: number;
};

type EventPoint = {
  tick: number;
  label: string;
  color: string;
};

const STATUS_COLOR: Record<string, string> = {
  NORMAL: 'var(--green)',
  IN_SERVICE: 'var(--cyan)',
  WARNING: 'var(--amber)',
  OVERLOAD: 'var(--red)',
  OUTAGE: 'var(--red)',
  OPEN: 'var(--text-muted)',
};

function fmt(value: number | undefined | null, digits = 0) {
  return Number.isFinite(value as number) ? Number(value).toFixed(digits) : '-';
}

function postJson(url: string, payload: unknown) {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then((response) => {
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json();
  });
}

export default function PowerSystemView() {
  const powerTopology = useSimStore((s) => s.powerTopology);
  const simPowerNetwork = useSimStore((s) => s.simPowerNetwork);
  const engineClockState = useSimStore((s) => s.engineClockState);
  const simTime = useSimStore((s) => s.simTime);
  const [selectedSubstationId, setSelectedSubstationId] = useState<string>('');
  const [selectedSwitchId, setSelectedSwitchId] = useState<string>('');
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [events, setEvents] = useState<EventPoint[]>([]);
  const [feederFilter, setFeederFilter] = useState<'ALL' | 'SELECTED' | 'ABNORMAL'>('ABNORMAL');
  const [actionStatus, setActionStatus] = useState<string>('READY');

  const substations = useMemo(
    () => mergeSubstations(powerTopology?.substations ?? [], simPowerNetwork?.substations ?? []),
    [powerTopology, simPowerNetwork],
  );
  const trainVoltages = simPowerNetwork?.trainVoltages ?? [];
  const feeders = simPowerNetwork?.feeders ?? [];
  const alerts = simPowerNetwork?.alerts ?? [];
  const switches = powerTopology?.switches ?? [];
  const regen = simPowerNetwork?.regen;
  const firstMileage = substations[0]?.mileageM ?? 0;
  const lastMileage = substations[substations.length - 1]?.mileageM ?? firstMileage + 1;
  const span = Math.max(lastMileage - firstMileage, 1);
  const latestTick = history[history.length - 1]?.tick ?? simPowerNetwork?.simTimeMs ?? 0;
  const visibleFeeders = feeders
    .filter((item) => {
      if (feederFilter === 'ALL') return true;
      if (feederFilter === 'SELECTED') return item.substationId === selectedSubstationId;
      return item.status !== 'NORMAL' || Math.abs(item.currentA) > 0.1 || item.loadRatio > 0;
    })
    .slice(0, 18);

  useEffect(() => {
    if (!selectedSubstationId && substations.length > 0) {
      setSelectedSubstationId(substations[0].substationId);
    }
  }, [selectedSubstationId, substations]);

  useEffect(() => {
    if (!selectedSwitchId && switches.length > 0) {
      const firstSwitch = switches[0] as { switchId?: string };
      setSelectedSwitchId(firstSwitch.switchId ?? '');
    }
  }, [selectedSwitchId, switches]);

  useEffect(() => {
    if (!simPowerNetwork) return;
    const minVoltage = Math.min(...trainVoltages.map((item) => item.voltageV), 750);
    const totalPower = substations.reduce((sum, item) => sum + Math.max(item.powerKw ?? 0, 0), 0);
    const maxLoadRatio = Math.max(...substations.map((item) => item.loadRatio ?? 0), 0);
    setHistory((items) => [
      ...items.slice(-179),
      {
        tick: simPowerNetwork.simTimeMs ?? items.length,
        minVoltageV: minVoltage,
        totalSubstationPowerKw: totalPower,
        maxLoadRatio,
        lossesKw: simPowerNetwork.lossesKw ?? 0,
        absorbedRegenKw: regen?.absorbedKw ?? 0,
        wastedRegenKw: regen?.wastedKw ?? 0,
      },
    ]);
  }, [simPowerNetwork, trainVoltages, substations, regen]);

  const minVoltageTrain = useMemo(
    () => [...trainVoltages].sort((a, b) => a.voltageV - b.voltageV)[0],
    [trainVoltages],
  );
  const busiestSubstation = useMemo(
    () => [...substations].sort((a, b) => (b.loadRatio ?? 0) - (a.loadRatio ?? 0))[0],
    [substations],
  );

  const injectOutage = () => {
    if (!selectedSubstationId) return;
    setActionStatus('POSTING FAULT');
    postJson('/api/sim/power/faults', {
      faultType: 'SUBSTATION_OUTAGE',
      targetId: selectedSubstationId,
      mode: 'N_MINUS_1_BIG_BILATERAL',
    })
      .then(() => {
        setActionStatus(`OUTAGE ${selectedSubstationId}`);
        addEvent(latestTick, `N-1 ${selectedSubstationId}`, '#ff453a');
      })
      .catch((error) => setActionStatus(error instanceof Error ? error.message : 'FAULT FAILED'));
  };

  const resetPower = () => {
    setActionStatus('RESETTING');
    postJson('/api/sim/power/reset', {})
      .then(() => {
        setActionStatus('NORMAL RESTORED');
        addEvent(latestTick, 'RESET', '#58a6ff');
      })
      .catch((error) => setActionStatus(error instanceof Error ? error.message : 'RESET FAILED'));
  };

  const operateSwitch = (state: 'OPEN' | 'CLOSED') => {
    if (!selectedSwitchId) return;
    setActionStatus(`${state} ${selectedSwitchId}`);
    postJson(`/api/sim/power/switches/${selectedSwitchId}/operate`, { state })
      .then(() => {
        setActionStatus(`${selectedSwitchId} ${state}`);
        addEvent(latestTick, `${state} ${selectedSwitchId.replace('SW-TIE-', '')}`, state === 'CLOSED' ? '#8FC31F' : '#8ba0bb');
      })
      .catch((error) => setActionStatus(error instanceof Error ? error.message : 'SWITCH FAILED'));
  };

  const addEvent = (tick: number, label: string, color: string) => {
    setEvents((items) => [...items.slice(-17), { tick, label, color }]);
  };

  return (
    <div className="h-full min-h-0 bg-[#040810] p-5 overflow-auto">
      <div className="grid grid-cols-[minmax(720px,1fr)_400px] gap-4 min-h-full">
        <main className="min-w-0 space-y-3">
          <section className="glass p-5">
            <div className="flex items-center justify-between mb-4">
              <div>
                <div className="label" style={{ color: 'var(--text-muted)' }}>TRACTION POWER SYSTEM</div>
                <h2 className="mt-1 text-[20px] font-semibold text-[#dce8f8]">Line 9 DC750V Power Flow</h2>
              </div>
              <div className="flex items-center gap-4 text-right">
                <Readout label="SIM" value={engineClockState} color={engineClockState === 'RUNNING' ? 'var(--green)' : 'var(--text-muted)'} />
                <Readout label="TIME" value={simTime} color="var(--cyan)" />
                <Readout label="QUALITY" value={powerTopology?.quality ?? '-'} color="var(--text-muted)" />
              </div>
            </div>

            <TopologyDiagram
              substations={substations}
              trainVoltages={trainVoltages}
              firstMileage={firstMileage}
              span={span}
            />
          </section>

          <section className="grid grid-cols-4 gap-3">
            <Metric label="MIN TRAIN U" value={fmt(minVoltageTrain?.voltageV, 0)} unit="V" color={(minVoltageTrain?.voltageV ?? 750) < 650 ? 'var(--amber)' : 'var(--green)'} />
            <Metric label="PEAK TS LOAD" value={fmt((busiestSubstation?.loadRatio ?? 0) * 100, 1)} unit="%" color={(busiestSubstation?.loadRatio ?? 0) >= 0.85 ? 'var(--amber)' : 'var(--cyan)'} />
            <Metric label="LOSS" value={fmt(simPowerNetwork?.lossesKw, 1)} unit="kW" color="var(--text-dim)" />
            <Metric label="REGEN WASTE" value={fmt(regen?.wastedKw, 0)} unit="kW" color={(regen?.wastedKw ?? 0) > 0 ? 'var(--amber)' : 'var(--green)'} />
          </section>

          <section className="grid grid-cols-2 gap-3">
            <TrendPanel title="Voltage / Load" subtitle="min train voltage and max substation load">
              <TrendChart
                points={history}
                events={events}
                series={[
                  { key: 'minVoltageV', label: 'MIN U', color: '#8FC31F', min: 500, max: 900 },
                  { key: 'maxLoadRatio', label: 'LOAD', color: '#ffb454', min: 0, max: 1 },
                ]}
              />
            </TrendPanel>
            <TrendPanel title="Power / Energy" subtitle="traction power, losses and regen split">
              <TrendChart
                points={history}
                events={events}
                series={[
                  { key: 'totalSubstationPowerKw', label: 'PWR', color: '#58a6ff' },
                  { key: 'lossesKw', label: 'LOSS', color: '#8ba0bb' },
                  { key: 'wastedRegenKw', label: 'WASTE', color: '#ff453a' },
                ]}
              />
            </TrendPanel>
          </section>

          <section className="grid grid-cols-[1.2fr_1fr] gap-3">
            <DataTable
              title="Substation Flow"
              columns={['ID', 'U/V', 'I/A', 'P/kW', 'LOAD', 'STATUS']}
              rows={substations.map((item) => [
                item.substationId,
                fmt(item.voltageV, 0),
                fmt(item.currentA, 0),
                fmt(item.powerKw, 0),
                `${fmt((item.loadRatio ?? 0) * 100, 1)}%`,
                item.status,
              ])}
              statusColumn={5}
            />
            <DataTable
              title="Train Voltage"
              columns={['TRAIN', 'K', 'U/V', 'I/A', 'LIMIT', 'LEVEL']}
              rows={trainVoltages.map((item) => [
                item.trainId,
                fmt((item.mileageM ?? 0) / 1000, 3),
                fmt(item.voltageV, 0),
                fmt(item.currentA, 0),
                `${fmt(item.tractionLimitRatio * 100, 0)}%`,
                item.voltageLevel,
              ])}
              statusColumn={5}
            />
          </section>
        </main>

        <aside className="min-w-0 space-y-3">
          <section className="glass p-4">
            <div className="flex items-center justify-between mb-3">
              <span className="label" style={{ color: 'var(--text-muted)' }}>SCENARIO CONTROL</span>
              <span className="board-num text-[9px]" style={{ color: 'var(--cyan)' }}>{actionStatus}</span>
            </div>
            <div className="space-y-3">
              <label className="block">
                <span className="label block mb-1" style={{ color: 'var(--text-muted)' }}>SUBSTATION</span>
                <select
                  value={selectedSubstationId}
                  onChange={(event) => setSelectedSubstationId(event.target.value)}
                  className="w-full bg-[#081321] border border-[#172436] text-[#dce8f8] px-2 py-2 text-[12px] outline-none"
                >
                  {substations.map((item) => (
                    <option key={item.substationId} value={item.substationId}>
                      {item.substationId} {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                onClick={injectOutage}
                className="w-full px-3 py-2 text-[12px] font-semibold cursor-pointer"
                style={{ background: 'rgba(255,69,58,0.16)', border: '1px solid rgba(255,69,58,0.35)', color: '#ff8a82' }}
              >
                Inject N-1 Outage
              </button>
              <button
                type="button"
                onClick={resetPower}
                className="w-full px-3 py-2 text-[12px] font-semibold cursor-pointer"
                style={{ background: 'rgba(88,166,255,0.12)', border: '1px solid rgba(88,166,255,0.32)', color: '#58a6ff' }}
              >
                Restore Normal Network
              </button>
              <label className="block">
                <span className="label block mb-1" style={{ color: 'var(--text-muted)' }}>TIE SWITCH</span>
                <select
                  value={selectedSwitchId}
                  onChange={(event) => setSelectedSwitchId(event.target.value)}
                  className="w-full bg-[#081321] border border-[#172436] text-[#dce8f8] px-2 py-2 text-[12px] outline-none"
                >
                  {switches.map((item) => {
                    const sw = item as { switchId?: string; switchType?: string; currentState?: string };
                    return (
                      <option key={sw.switchId} value={sw.switchId}>
                        {sw.switchId} {sw.switchType} {sw.currentState ?? ''}
                      </option>
                    );
                  })}
                </select>
              </label>
              <div className="grid grid-cols-2 gap-2">
                <button type="button" onClick={() => operateSwitch('CLOSED')} className="px-3 py-2 text-[12px] cursor-pointer" style={{ background: 'rgba(143,195,31,0.12)', border: '1px solid rgba(143,195,31,0.28)', color: 'var(--green)' }}>Close</button>
                <button type="button" onClick={() => operateSwitch('OPEN')} className="px-3 py-2 text-[12px] cursor-pointer" style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.10)', color: 'var(--text-muted)' }}>Open</button>
              </div>
            </div>
          </section>

          <DataTable
            title="Feeder Arms"
            toolbar={(
              <select
                value={feederFilter}
                onChange={(event) => setFeederFilter(event.target.value as 'ALL' | 'SELECTED' | 'ABNORMAL')}
                className="bg-[#081321] border border-[#172436] text-[#8ba0bb] px-2 py-1 text-[10px] outline-none"
              >
                <option value="ABNORMAL">active</option>
                <option value="SELECTED">selected TS</option>
                <option value="ALL">all</option>
              </select>
            )}
            columns={['FEEDER', 'I/A', 'LOAD', 'STATUS']}
            rows={visibleFeeders.map((item) => [
              item.feederId,
              fmt(item.currentA, 0),
              `${fmt(item.loadRatio * 100, 1)}%`,
              item.status,
            ])}
            statusColumn={3}
          />

          <section className="glass p-4">
            <div className="flex items-center justify-between mb-3">
              <span className="label" style={{ color: 'var(--text-muted)' }}>ALERTS</span>
              <span className="board-num text-[9px]" style={{ color: alerts.length ? 'var(--amber)' : 'var(--green)' }}>{alerts.length}</span>
            </div>
            <div className="space-y-2 max-h-[210px] overflow-auto">
              {alerts.length === 0 ? (
                <div className="text-[11px]" style={{ color: 'var(--text-muted)' }}>No active power-flow alerts.</div>
              ) : alerts.map((alert, index) => (
                <div key={index} className="bg-[#081321] border border-[#172436] px-2 py-2 text-[11px]">
                  <div className="font-mono" style={{ color: 'var(--amber)' }}>{String(alert.type ?? 'ALERT')}</div>
                  <div className="mt-1 font-mono" style={{ color: 'var(--text-muted)' }}>{JSON.stringify(alert)}</div>
                </div>
              ))}
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}

type MergedSubstation = PowerTopologySubstation & Partial<PowerSubstationState>;

function mergeSubstations(
  topology: PowerTopologySubstation[],
  runtime: PowerSubstationState[],
): MergedSubstation[] {
  const byId = new Map(runtime.map((item) => [item.substationId, item]));
  return topology.map((item) => ({ ...item, ...byId.get(item.substationId) }));
}

function Metric({ label, value, unit, color }: { label: string; value: string; unit: string; color: string }) {
  return (
    <div className="glass px-4 py-3">
      <div className="label mb-2" style={{ color: 'var(--text-muted)' }}>{label}</div>
      <div className="flex items-end gap-2">
        <span className="board-num text-[24px] leading-none" style={{ color }}>{value}</span>
        <span className="board-num text-[10px] mb-0.5" style={{ color: 'var(--text-muted)' }}>{unit}</span>
      </div>
    </div>
  );
}

function Readout({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div>
      <div className="label" style={{ color: 'var(--text-muted)' }}>{label}</div>
      <div className="board-num text-[12px] mt-1" style={{ color }}>{value}</div>
    </div>
  );
}

function TopologyDiagram({
  substations,
  trainVoltages,
  firstMileage,
  span,
}: {
  substations: MergedSubstation[];
  trainVoltages: TrainVoltageState[];
  firstMileage: number;
  span: number;
}) {
  const xOf = (mileageM: number) => 55 + ((mileageM - firstMileage) / span) * 1090;
  return (
    <div className="relative h-[270px] border border-[#172436] bg-[#07101b] overflow-hidden">
      <svg viewBox="0 0 1200 270" className="w-full h-full" preserveAspectRatio="none">
        <line x1="55" y1="112" x2="1145" y2="112" stroke="#8FC31F" strokeWidth="5" strokeLinecap="round" />
        <line x1="55" y1="148" x2="1145" y2="148" stroke="#2b3c52" strokeWidth="3" strokeLinecap="round" />
        <text x="55" y="236" fill="#52647b" fontSize="12" fontFamily="monospace">K{fmt(firstMileage / 1000, 3)}</text>
        <text x="1070" y="236" fill="#52647b" fontSize="12" fontFamily="monospace">K{fmt((firstMileage + span) / 1000, 3)}</text>
        {substations.map((item) => {
          const x = xOf(item.mileageM);
          const statusColor = STATUS_COLOR[item.status ?? 'IN_SERVICE'] ?? 'var(--cyan)';
          const loadHeight = Math.max(4, Math.min(58, (item.loadRatio ?? 0) * 58));
          return (
            <g key={item.substationId}>
              <line x1={x} y1="72" x2={x} y2="112" stroke={statusColor} strokeWidth="1.5" strokeDasharray="4 5" />
              <rect x={x - 7} y="62" width="14" height="14" transform={`rotate(45 ${x} 69)`} fill="rgba(88,166,255,0.18)" stroke={statusColor} strokeWidth="1.5" />
              <rect x={x - 8} y={196 - loadHeight} width="16" height={loadHeight} fill={statusColor} opacity="0.75" />
              <text x={x} y="44" textAnchor="middle" fill="#8ba0bb" fontSize="10" fontFamily="monospace">{item.substationId.replace('TS-', '')}</text>
              <text x={x} y="213" textAnchor="middle" fill="#52647b" fontSize="9" fontFamily="monospace">{fmt((item.loadRatio ?? 0) * 100, 0)}%</text>
            </g>
          );
        })}
        {trainVoltages.map((item) => {
          const x = xOf(item.mileageM ?? firstMileage);
          const color = item.voltageV < 650 ? '#ffb454' : '#8FC31F';
          return (
            <g key={item.trainId}>
              <circle cx={x} cy="132" r="8" fill={color} opacity="0.92" />
              <text x={x} y="166" textAnchor="middle" fill={color} fontSize="10" fontFamily="monospace">{fmt(item.voltageV, 0)}V</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function TrendPanel({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  return (
    <section className="glass p-4 min-w-0">
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="text-[14px] font-semibold text-[#dce8f8]">{title}</h3>
        <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>{subtitle}</span>
      </div>
      {children}
    </section>
  );
}

function TrendChart({
  points,
  events,
  series,
}: {
  points: HistoryPoint[];
  events: EventPoint[];
  series: Array<{ key: keyof HistoryPoint; label: string; color: string; min?: number; max?: number }>;
}) {
  const width = 520;
  const height = 180;
  const pad = 22;
  const firstTick = points[0]?.tick ?? 0;
  const lastTick = points[points.length - 1]?.tick ?? firstTick + 1;
  const tickRange = Math.max(lastTick - firstTick, 1);
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-[180px]">
      <rect x="0" y="0" width={width} height={height} fill="#07101b" stroke="#172436" />
      {[0.25, 0.5, 0.75].map((ratio) => (
        <line key={ratio} x1={pad} x2={width - pad} y1={pad + ratio * (height - pad * 2)} y2={pad + ratio * (height - pad * 2)} stroke="#172436" strokeDasharray="4 6" />
      ))}
      {series.map((item) => {
        const values = points.map((point) => Number(point[item.key] ?? 0));
        const min = item.min ?? Math.min(...values, 0);
        const max = item.max ?? Math.max(...values, 1);
        const range = Math.max(max - min, 1e-6);
        const path = points.map((point, index) => {
          const x = pad + (points.length <= 1 ? 0 : index / (points.length - 1)) * (width - pad * 2);
          const y = height - pad - ((Number(point[item.key] ?? 0) - min) / range) * (height - pad * 2);
          return `${index === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
        }).join(' ');
        return <path key={item.label} d={path} fill="none" stroke={item.color} strokeWidth="2" />;
      })}
      {events.map((event, index) => {
        const x = pad + ((event.tick - firstTick) / tickRange) * (width - pad * 2);
        if (x < pad || x > width - pad) return null;
        return (
          <g key={`${event.label}-${index}`}>
            <line x1={x} x2={x} y1={pad} y2={height - pad} stroke={event.color} strokeWidth="1.4" strokeDasharray="3 4" />
            <text x={x + 4} y={pad + 10 + (index % 3) * 12} fill={event.color} fontSize="9" fontFamily="monospace">{event.label}</text>
          </g>
        );
      })}
      <g transform={`translate(${pad}, ${height - 8})`}>
        {series.map((item, index) => (
          <text key={item.label} x={index * 88} y="0" fill={item.color} fontSize="10" fontFamily="monospace">{item.label}</text>
        ))}
      </g>
    </svg>
  );
}

function DataTable({
  title,
  toolbar,
  columns,
  rows,
  statusColumn,
}: {
  title: string;
  toolbar?: ReactNode;
  columns: string[];
  rows: string[][];
  statusColumn?: number;
}) {
  return (
    <section className="glass p-4 min-w-0">
      <div className="flex items-center justify-between mb-3">
        <span className="label" style={{ color: 'var(--text-muted)' }}>{title}</span>
        <div className="flex items-center gap-2">
          {toolbar}
          <span className="board-num text-[9px]" style={{ color: 'var(--text-muted)' }}>{rows.length}</span>
        </div>
      </div>
      <div className="overflow-auto max-h-[280px]">
        <table className="w-full text-[11px]">
          <thead>
            <tr className="text-left" style={{ color: 'var(--text-muted)' }}>
              {columns.map((column) => <th key={column} className="py-1.5 pr-2 font-medium">{column}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td colSpan={columns.length} className="py-4" style={{ color: 'var(--text-muted)' }}>Waiting for data.</td></tr>
            ) : rows.map((row, index) => (
              <tr key={`${row[0]}-${index}`} className="border-t border-[#101d2d]">
                {row.map((cell, cellIndex) => (
                  <td
                    key={cellIndex}
                    className="py-1.5 pr-2 font-mono whitespace-nowrap"
                    style={{
                      color: cellIndex === statusColumn ? (STATUS_COLOR[cell] ?? 'var(--text-muted)') : '#c7d5e8',
                    }}
                  >
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
