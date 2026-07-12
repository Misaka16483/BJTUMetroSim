import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { useSimStore } from '../store/useSimStore';
import type {
  ContactRailPowerFlowState,
  PowerSubstationState,
  PowerTopologyContactRailSection,
  PowerTopologySubstation,
  TrainVoltageState,
} from '../data/backendApi';
import {
  powerAlertLabel,
  powerQualityLabel,
  powerStatusLabel,
  simulationStateLabel,
  substationDisplayName,
  switchTypeLabel,
} from '../data/powerLabels';

type HistoryPoint = {
  tick: number;
  minVoltageV: number;
  netSubstationPowerKw: number;
  rectifierPowerKw: number;
  feedbackPowerKw: number;
  maxLoadRatio: number;
  lossesKw: number;
  absorbedRegenKw: number;
  generatedRegenKw: number;
  selfConsumedRegenKw: number;
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

function formatAlertDetail(alert: Record<string, unknown>) {
  const parts: string[] = [];
  if (alert.targetId) parts.push(`设备：${String(alert.targetId)}`);
  if (typeof alert.voltageV === 'number') parts.push(`电压：${fmt(alert.voltageV, 0)} V`);
  if (typeof alert.currentA === 'number') parts.push(`电流：${fmt(alert.currentA, 0)} A`);
  if (typeof alert.powerKw === 'number') parts.push(`功率：${fmt(alert.powerKw, 1)} kW`);
  return parts.join(' · ') || '请检查供电网络状态';
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
  const trains = useSimStore((s) => s.trains);
  const [selectedSubstationId, setSelectedSubstationId] = useState<string>('');
  const [selectedSwitchId, setSelectedSwitchId] = useState<string>('');
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [events, setEvents] = useState<EventPoint[]>([]);
  const [feederFilter, setFeederFilter] = useState<'ALL' | 'SELECTED' | 'ABNORMAL'>('ABNORMAL');
  const [actionStatus, setActionStatus] = useState<string>('就绪');
  const [actionPending, setActionPending] = useState(false);

  const substations = useMemo(
    () => mergeSubstations(powerTopology?.substations ?? [], simPowerNetwork?.substations ?? []),
    [powerTopology, simPowerNetwork],
  );
  const trainVoltages = useMemo(() => simPowerNetwork?.trainVoltages ?? [], [simPowerNetwork?.trainVoltages]);
  const feeders = useMemo(() => simPowerNetwork?.feeders ?? [], [simPowerNetwork?.feeders]);
  const contactRailFlows = useMemo(
    () => simPowerNetwork?.contactRailFlows ?? [],
    [simPowerNetwork?.contactRailFlows],
  );
  const alerts = useMemo(() => simPowerNetwork?.alerts ?? [], [simPowerNetwork?.alerts]);
  const switches = useMemo(
    () => simPowerNetwork?.switches ?? powerTopology?.switches ?? [],
    [simPowerNetwork?.switches, powerTopology?.switches],
  );
  const regen = simPowerNetwork?.regen;
  const solver = simPowerNetwork?.solver;
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
    const netPower = substations.reduce((sum, item) => sum + (item.powerKw ?? 0), 0);
    const rectifierPower = substations.reduce((sum, item) => sum + Math.max(item.rectifierPowerKw ?? 0, 0), 0);
    const feedbackPower = substations.reduce((sum, item) => sum - Math.max(item.feedbackPowerKw ?? 0, 0), 0);
    const maxLoadRatio = Math.max(...substations.map((item) => item.loadRatio ?? 0), 0);
    setHistory((items) => {
      const previous = items[items.length - 1];
      const point: HistoryPoint = {
        tick: simPowerNetwork.simTimeMs ?? (previous?.tick ?? -1) + 1,
        minVoltageV: minVoltage,
        netSubstationPowerKw: netPower,
        rectifierPowerKw: rectifierPower,
        feedbackPowerKw: feedbackPower,
        maxLoadRatio,
        lossesKw: simPowerNetwork.lossesKw ?? 0,
        absorbedRegenKw: regen?.absorbedKw ?? 0,
        generatedRegenKw: regen?.generatedKw ?? 0,
        selfConsumedRegenKw: regen?.selfConsumedKw ?? 0,
        wastedRegenKw: regen?.wastedKw ?? 0,
      };
      if (previous && point.tick < previous.tick) return items;
      if (previous?.tick === point.tick) return [...items.slice(0, -1), point];
      return [...items.slice(-179), point];
    });
  }, [simPowerNetwork, trainVoltages, substations, regen]);

  useEffect(() => {
    const result = simPowerNetwork?.commandResults?.at(-1);
    if (!result) return;
    if (result.status === 'APPLIED') setActionStatus(`${result.commandId} 已执行`);
    if (result.status === 'REJECTED') setActionStatus(`${result.commandId} 被拒绝：${result.error ?? '未知原因'}`);
  }, [simPowerNetwork?.commandResults]);

  const minVoltageTrain = useMemo(
    () => [...trainVoltages].sort((a, b) => a.voltageV - b.voltageV)[0],
    [trainVoltages],
  );
  const busiestSubstation = useMemo(
    () => [...substations].sort((a, b) => (b.loadRatio ?? 0) - (a.loadRatio ?? 0))[0],
    [substations],
  );
  const powerScale = useMemo(() => {
    const values = history.flatMap((point) => [
      point.netSubstationPowerKw,
      point.rectifierPowerKw,
      point.feedbackPowerKw,
      point.lossesKw,
      point.generatedRegenKw,
      point.selfConsumedRegenKw,
      point.wastedRegenKw,
    ]);
    const min = Math.min(0, ...values);
    const max = Math.max(1, ...values);
    const margin = Math.max((max - min) * 0.08, 10);
    return { min: min - margin, max: max + margin };
  }, [history]);

  const injectOutage = () => {
    if (!selectedSubstationId || actionPending) return;
    setActionPending(true);
    setActionStatus('正在注入故障');
    postJson('/api/sim/power/faults', {
      faultType: 'SUBSTATION_OUTAGE',
      targetId: selectedSubstationId,
      mode: 'N_MINUS_1_BIG_BILATERAL',
    })
      .then(() => {
        setActionStatus(`${selectedSubstationId} 故障命令已排队`);
        addEvent(latestTick, `N-1 ${selectedSubstationId}`, '#ff453a');
      })
      .catch((error) => setActionStatus(error instanceof Error ? `故障注入失败：${error.message}` : '故障注入失败'))
      .finally(() => setActionPending(false));
  };

  const resetPower = () => {
    if (actionPending) return;
    setActionPending(true);
    setActionStatus('正在恢复供电网络');
    postJson('/api/sim/power/reset', {})
      .then(() => {
        setActionStatus('供电网络复位命令已排队');
        addEvent(latestTick, '网络复位', '#58a6ff');
      })
      .catch((error) => setActionStatus(error instanceof Error ? `恢复失败：${error.message}` : '恢复失败'))
      .finally(() => setActionPending(false));
  };

  const operateSwitch = (state: 'OPEN' | 'CLOSED') => {
    if (!selectedSwitchId || actionPending) return;
    setActionPending(true);
    setActionStatus(`正在${state === 'CLOSED' ? '闭合' : '断开'} ${selectedSwitchId}`);
    postJson(`/api/sim/power/switches/${selectedSwitchId}/operate`, { state })
      .then(() => {
        setActionStatus(`${selectedSwitchId} ${state === 'CLOSED' ? '闭合' : '断开'}命令已排队`);
        addEvent(latestTick, `${state === 'CLOSED' ? '闭合' : '断开'} ${selectedSwitchId.replace('SW-TIE-', '')}`, state === 'CLOSED' ? '#8FC31F' : '#8ba0bb');
      })
      .catch((error) => setActionStatus(error instanceof Error ? `开关操作失败：${error.message}` : '开关操作失败'))
      .finally(() => setActionPending(false));
  };

  const addEvent = (tick: number, label: string, color: string) => {
    setEvents((items) => [...items.slice(-17), { tick, label, color }]);
  };

  return (
    <div className="h-full min-h-0 min-w-0 bg-[#040810] p-3 lg:p-5 overflow-auto">
      <div className="grid grid-cols-[minmax(0,1fr)] xl:grid-cols-[minmax(720px,1fr)_400px] gap-4 min-h-full min-w-0">
        <main className="min-w-0 space-y-3">
          <section className="glass p-5">
            <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-3 mb-4">
              <div>
                <div className="label" style={{ color: 'var(--text-muted)' }}>牵引供电系统</div>
                <h2 className="mt-1 text-[20px] font-semibold text-[#dce8f8]">9号线 DC750V 牵引供电潮流</h2>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:flex items-center gap-3 lg:gap-4 text-left lg:text-right">
                <Readout label="仿真状态" value={simulationStateLabel(engineClockState)} color={engineClockState === 'RUNNING' ? 'var(--green)' : 'var(--text-muted)'} />
                <Readout label="仿真时间" value={simTime} color="var(--cyan)" />
                <Readout label="数据质量" value={powerQualityLabel(powerTopology?.quality)} color="var(--text-muted)" />
                <Readout label="潮流求解" value={solver ? `${fmt(solver.solveTimeMs, 1)} ms` : '--'} color={solver?.converged === false ? 'var(--amber)' : 'var(--green)'} />
                <Readout label="平衡误差" value={solver ? `${fmt(solver.powerBalanceErrorRatio * 100, 3)}%` : '--'} color={(solver?.powerBalanceErrorRatio ?? 0) >= 0.01 ? 'var(--amber)' : 'var(--cyan)'} />
              </div>
            </div>

            <TopologyDiagram
              substations={substations}
              trainVoltages={trainVoltages}
              contactRailSections={powerTopology?.contactRailSections ?? []}
              contactRailFlows={contactRailFlows}
              firstMileage={firstMileage}
              span={span}
            />
            {simPowerNetwork?.solverFailure && (
              <div className="mt-3 border border-[#ff453a66] bg-[#ff453a14] px-3 py-2 text-[11px] text-[#ff8a82]">
                潮流结果未发布，仿真已暂停。原因：{simPowerNetwork.solverFailure.reasons.join(' / ')}；当前画面保留上一有效快照。
              </div>
            )}
          </section>

          <div className="flex items-center justify-end text-[9px]" style={{ color: 'var(--text-muted)' }}>
            指标卡为当前瞬时值；趋势图保留最近 {history.length} 个仿真采样点
          </div>
          <section className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Metric label="最低列车电压" value={fmt(minVoltageTrain?.voltageV, 0)} unit="V" color={(minVoltageTrain?.voltageV ?? 750) < 650 ? 'var(--amber)' : 'var(--green)'} />
            <Metric label="最大牵引所负载" value={fmt((busiestSubstation?.loadRatio ?? 0) * 100, 1)} unit="%" color={(busiestSubstation?.loadRatio ?? 0) >= 0.85 ? 'var(--amber)' : 'var(--cyan)'} />
            <Metric label="线路损耗" value={fmt(simPowerNetwork?.lossesKw, 1)} unit="kW" color="var(--text-dim)" />
            <Metric label="再生浪费" value={fmt(regen?.wastedKw, 0)} unit="kW" color={(regen?.wastedKw ?? 0) > 0 ? 'var(--amber)' : 'var(--green)'} />
            <Metric label="再生生成" value={fmt(regen?.generatedKw, 0)} unit="kW" color="var(--cyan)" />
            <Metric label="本车自用" value={fmt(regen?.selfConsumedKw, 0)} unit="kW" color="var(--green)" />
            <Metric label="跨车吸收" value={fmt(regen?.absorbedKw, 0)} unit="kW" color="var(--cyan)" />
            <Metric label="变电所反馈" value={fmt(regen?.feedbackKw, 0)} unit="kW" color="#b57cff" />
          </section>

          <section className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <TrendPanel title="电压与负载" subtitle="历史窗口：最低列车电压 / 最大牵引所负载">
              <TrendChart
                points={history}
                events={events}
                series={[
                  { key: 'minVoltageV', label: '最低电压', color: '#8FC31F', min: 500, max: 900, unit: 'V', axis: 'left' },
                  { key: 'maxLoadRatio', label: '最大负载', color: '#ffb454', min: 0, max: 1, unit: 'p.u.', axis: 'right' },
                ]}
              />
            </TrendPanel>
            <TrendPanel title="功率与能量" subtitle="历史窗口：正值整流供电，负值回馈上网（kW）">
              <TrendChart
                points={history}
                events={events}
                series={[
                  { key: 'netSubstationPowerKw', label: '变电所净功率', color: '#58a6ff', min: powerScale.min, max: powerScale.max, unit: 'kW', axis: 'left' },
                  { key: 'rectifierPowerKw', label: '整流输入', color: '#8FC31F', min: powerScale.min, max: powerScale.max, unit: 'kW', axis: 'left' },
                  { key: 'feedbackPowerKw', label: '回馈输出(-)', color: '#b57cff', min: powerScale.min, max: powerScale.max, unit: 'kW', axis: 'left' },
                  { key: 'lossesKw', label: '线路损耗', color: '#8ba0bb', min: powerScale.min, max: powerScale.max, unit: 'kW', axis: 'left' },
                  { key: 'wastedRegenKw', label: '再生浪费', color: '#ff453a', min: powerScale.min, max: powerScale.max, unit: 'kW', axis: 'left' },
                  { key: 'generatedRegenKw', label: '再生生成', color: '#22c55e', min: powerScale.min, max: powerScale.max, unit: 'kW', axis: 'left' },
                  { key: 'selfConsumedRegenKw', label: '本车自用', color: '#f59e0b', min: powerScale.min, max: powerScale.max, unit: 'kW', axis: 'left' },
                ]}
              />
            </TrendPanel>
          </section>

          <section className="grid grid-cols-1 lg:grid-cols-[1.2fr_1fr] gap-3">
            <DataTable
              title="牵引所潮流"
              columns={['设备编号', '电压/V', '电流/A', '功率/kW', '负载率', '状态']}
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
              title="列车受电状态"
              columns={['列车', '阶段', '控制指令', '牵引/kW', '再生/kW', '电压/V', '电流/A', '限牵', '等级']}
              rows={trainVoltages.map((item) => {
                const train = trains.find((candidate) => candidate.trainId === item.trainId);
                const traction = train?.tractionPercent ?? 0;
                const brake = train?.brakePercent ?? 0;
                const command = brake > 0
                  ? `制动 ${fmt(brake, 0)}%`
                  : traction > 0
                    ? `牵引 ${fmt(traction, 0)}%`
                    : '惰行/停站';
                return [
                  item.trainId,
                  train?.phase ?? '-',
                  command,
                  fmt(train?.tractionPowerDeliveredKw, 0),
                  fmt(train?.regenPowerAvailableKw, 0),
                  fmt(item.voltageV, 0),
                  fmt(item.currentA, 0),
                  `${fmt(item.tractionLimitRatio * 100, 0)}%`,
                  item.voltageLevel,
                ];
              })}
              statusColumn={8}
              minWidthPx={760}
            />
          </section>
          <section className="grid grid-cols-1 lg:grid-cols-[1fr_1.25fr] gap-3">
            <DataTable
              title="接触轨分段潮流（正值指向公里标增大方向）"
              columns={['分段', '方向', '电流/A', '功率/kW', '负载率', '状态']}
              rows={contactRailFlows.map((item) => [
                item.sectionId,
                item.direction,
                `${item.currentA >= 0 ? '+' : ''}${fmt(item.currentA, 0)}`,
                `${item.powerKw >= 0 ? '+' : ''}${fmt(item.powerKw, 1)}`,
                `${fmt(item.loadRatio * 100, 1)}%`,
                item.status,
              ])}
              statusColumn={5}
            />
            <DataTable
              title="再生能量路径"
              columns={['源列车', '去向', '汇点', '生成/kW', '送达/kW', '损耗/kW', '路径电流/A']}
              rows={(regen?.paths ?? []).map((item) => [
                item.sourceTrainId,
                item.sinkType,
                item.sinkId,
                fmt(item.generatedKw, 1),
                fmt(item.deliveredKw, 1),
                fmt(item.lossesKw, 2),
                fmt(item.currentA, 1),
              ])}
            />
          </section>
        </main>

        <aside className="min-w-0 space-y-3">
          <section className="glass p-4">
            <div className="flex items-center justify-between mb-3">
              <span className="label" style={{ color: 'var(--text-muted)' }}>工况控制</span>
              <span className="board-num text-[9px]" style={{ color: 'var(--cyan)' }}>{actionStatus}</span>
            </div>
            <div className="space-y-3">
              <label className="block">
                <span className="label block mb-1" style={{ color: 'var(--text-muted)' }}>牵引变电所</span>
                <select
                  value={selectedSubstationId}
                  onChange={(event) => setSelectedSubstationId(event.target.value)}
                  className="w-full bg-[#081321] border border-[#172436] text-[#dce8f8] px-2 py-2 text-[12px] outline-none"
                >
                  {substations.map((item) => (
                    <option key={item.substationId} value={item.substationId}>
                      {item.substationId} {substationDisplayName(item.substationId, item.name)}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                onClick={injectOutage}
                disabled={actionPending}
                className="w-full px-3 py-2 text-[12px] font-semibold cursor-pointer disabled:cursor-not-allowed disabled:opacity-50"
                style={{ background: 'rgba(255,69,58,0.16)', border: '1px solid rgba(255,69,58,0.35)', color: '#ff8a82' }}
              >
                注入 N-1 牵引所故障
              </button>
              <button
                type="button"
                onClick={resetPower}
                disabled={actionPending}
                className="w-full px-3 py-2 text-[12px] font-semibold cursor-pointer disabled:cursor-not-allowed disabled:opacity-50"
                style={{ background: 'rgba(88,166,255,0.12)', border: '1px solid rgba(88,166,255,0.32)', color: '#58a6ff' }}
              >
                恢复正常供电网络
              </button>
              <label className="block">
                <span className="label block mb-1" style={{ color: 'var(--text-muted)' }}>联络开关</span>
                <select
                  value={selectedSwitchId}
                  onChange={(event) => setSelectedSwitchId(event.target.value)}
                  className="w-full bg-[#081321] border border-[#172436] text-[#dce8f8] px-2 py-2 text-[12px] outline-none"
                >
                  {switches.map((item) => {
                    const sw = item as { switchId?: string; switchType?: string; currentState?: string };
                    return (
                      <option key={sw.switchId} value={sw.switchId}>
                        {sw.switchId} {switchTypeLabel(sw.switchType)} {powerStatusLabel(sw.currentState)}
                      </option>
                    );
                  })}
                </select>
              </label>
              <div className="grid grid-cols-2 gap-2">
                <button type="button" disabled={actionPending} onClick={() => operateSwitch('CLOSED')} className="px-3 py-2 text-[12px] cursor-pointer disabled:cursor-not-allowed disabled:opacity-50" style={{ background: 'rgba(143,195,31,0.12)', border: '1px solid rgba(143,195,31,0.28)', color: 'var(--green)' }}>闭合</button>
                <button type="button" disabled={actionPending} onClick={() => operateSwitch('OPEN')} className="px-3 py-2 text-[12px] cursor-pointer disabled:cursor-not-allowed disabled:opacity-50" style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.10)', color: 'var(--text-muted)' }}>断开</button>
              </div>
            </div>
          </section>

          <DataTable
            title="馈电臂"
            toolbar={(
              <select
                value={feederFilter}
                onChange={(event) => setFeederFilter(event.target.value as 'ALL' | 'SELECTED' | 'ABNORMAL')}
                className="bg-[#081321] border border-[#172436] text-[#8ba0bb] px-2 py-1 text-[10px] outline-none"
              >
                <option value="ABNORMAL">异常或有负载</option>
                <option value="SELECTED">当前牵引所</option>
                <option value="ALL">全部</option>
              </select>
            )}
            columns={['馈电臂编号', '电流/A', '负载率', '状态']}
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
              <span className="label" style={{ color: 'var(--text-muted)' }}>供电告警</span>
              <span className="board-num text-[9px]" style={{ color: alerts.length ? 'var(--amber)' : 'var(--green)' }}>{alerts.length}</span>
            </div>
            <div className="space-y-2 max-h-[210px] overflow-auto">
              {alerts.length === 0 ? (
                <div className="text-[11px]" style={{ color: 'var(--text-muted)' }}>当前无供电潮流告警</div>
              ) : alerts.map((alert, index) => (
                <div key={index} className="bg-[#081321] border border-[#172436] px-2 py-2 text-[11px]">
                  <div className="font-mono" style={{ color: 'var(--amber)' }}>{powerAlertLabel(alert.type)}</div>
                  <div className="mt-1 font-mono" style={{ color: 'var(--text-muted)' }}>{formatAlertDetail(alert)}</div>
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
  contactRailSections,
  contactRailFlows,
  firstMileage,
  span,
}: {
  substations: MergedSubstation[];
  trainVoltages: TrainVoltageState[];
  contactRailSections: PowerTopologyContactRailSection[];
  contactRailFlows: ContactRailPowerFlowState[];
  firstMileage: number;
  span: number;
}) {
  const xOf = (mileageM: number) => 55 + ((mileageM - firstMileage) / span) * 1090;
  const flowById = new Map(contactRailFlows.map((item) => [item.sectionId, item]));
  return (
    <div className="relative h-[270px] border border-[#172436] bg-[#07101b] overflow-hidden">
      <svg viewBox="0 0 1200 270" className="w-full h-full" preserveAspectRatio="none">
        <line x1="55" y1="112" x2="1145" y2="112" stroke="#24374c" strokeWidth="5" strokeLinecap="round" />
        <line x1="55" y1="148" x2="1145" y2="148" stroke="#2b3c52" strokeWidth="3" strokeLinecap="round" />
        <text x="55" y="236" fill="#52647b" fontSize="12" fontFamily="monospace">K{fmt(firstMileage / 1000, 3)}</text>
        <text x="1070" y="236" fill="#52647b" fontSize="12" fontFamily="monospace">K{fmt((firstMileage + span) / 1000, 3)}</text>
        {contactRailSections.filter((item) => item.direction === 'UP').map((section) => {
          const flow = flowById.get(section.sectionId);
          const color = flow?.status === 'DEENERGIZED' ? '#ff453a' : flow?.status === 'OVERLOAD' ? '#ffb454' : '#8FC31F';
          const x1 = xOf(section.fromMileageM);
          const x2 = xOf(section.toMileageM);
          return (
            <g key={section.sectionId}>
              <line x1={x1} y1="112" x2={x2} y2="112" stroke={color} strokeWidth="5" />
              <text x={(x1 + x2) / 2} y="101" textAnchor="middle" fill={color} fontSize="8" fontFamily="monospace">
                {flow ? `${flow.currentA >= 0 ? '+' : ''}${fmt(flow.currentA, 0)} A` : '-- A'}
              </text>
            </g>
          );
        })}
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
  series: Array<{
    key: keyof HistoryPoint;
    label: string;
    color: string;
    min?: number;
    max?: number;
    unit?: string;
    axis?: 'left' | 'right';
  }>;
}) {
  const width = 520;
  const height = 180;
  const pad = 22;
  const firstTick = points[0]?.tick ?? 0;
  const lastTick = points[points.length - 1]?.tick ?? firstTick + 1;
  const tickRange = Math.max(lastTick - firstTick, 1);
  const leftAxis = series.find((item) => item.axis !== 'right') ?? series[0];
  const rightAxis = series.find((item) => item.axis === 'right');
  const axisRange = (item: (typeof series)[number]) => {
    const values = points.map((point) => Number(point[item.key] ?? 0));
    const min = item.min ?? Math.min(...values, 0);
    const max = item.max ?? Math.max(...values, 1);
    return { min, max, range: Math.max(max - min, 1e-6) };
  };
  const leftRange = axisRange(leftAxis);
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-[180px]">
      <rect x="0" y="0" width={width} height={height} fill="#07101b" stroke="#172436" />
      {[0.25, 0.5, 0.75].map((ratio) => (
        <line key={ratio} x1={pad} x2={width - pad} y1={pad + ratio * (height - pad * 2)} y2={pad + ratio * (height - pad * 2)} stroke="#172436" strokeDasharray="4 6" />
      ))}
      {[0, 0.5, 1].map((ratio) => {
        const y = pad + ratio * (height - pad * 2);
        const leftValue = leftRange.max - ratio * leftRange.range;
        const rightRange = rightAxis ? axisRange(rightAxis) : null;
        const rightValue = rightRange ? rightRange.max - ratio * rightRange.range : null;
        return (
          <g key={`axis-${ratio}`}>
            <text x="3" y={y + 3} fill="#61758e" fontSize="8" fontFamily="monospace">
              {fmt(leftValue, Math.abs(leftValue) < 10 ? 1 : 0)}
            </text>
            {rightValue !== null && (
              <text x={width - 3} y={y + 3} textAnchor="end" fill="#61758e" fontSize="8" fontFamily="monospace">
                {fmt(rightValue, 1)}
              </text>
            )}
          </g>
        );
      })}
      <text x={pad} y="12" fill={leftAxis.color} fontSize="8" fontFamily="monospace">{leftAxis.unit ?? ''}</text>
      {rightAxis && <text x={width - pad} y="12" textAnchor="end" fill={rightAxis.color} fontSize="8" fontFamily="monospace">{rightAxis.unit ?? ''}</text>}
      {leftRange.min < 0 && leftRange.max > 0 && (
        <line
          x1={pad}
          x2={width - pad}
          y1={height - pad - ((0 - leftRange.min) / leftRange.range) * (height - pad * 2)}
          y2={height - pad - ((0 - leftRange.min) / leftRange.range) * (height - pad * 2)}
          stroke="#52647b"
          strokeWidth="1"
        />
      )}
      {series.map((item) => {
        const values = points.map((point) => Number(point[item.key] ?? 0));
        const min = item.min ?? Math.min(...values, 0);
        const max = item.max ?? Math.max(...values, 1);
        const range = Math.max(max - min, 1e-6);
        const path = points.map((point, index) => {
          const x = pad + ((point.tick - firstTick) / tickRange) * (width - pad * 2);
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
      <g transform={`translate(${pad}, ${height - 13})`}>
        {series.map((item, index) => (
          <text
            key={item.label}
            x={(index % 3) * 158}
            y={Math.floor(index / 3) * 11}
            fill={item.color}
            fontSize="9"
            fontFamily="monospace"
          >{item.label}</text>
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
  minWidthPx,
}: {
  title: string;
  toolbar?: ReactNode;
  columns: string[];
  rows: string[][];
  statusColumn?: number;
  minWidthPx?: number;
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
        <table className="w-full text-[11px]" style={minWidthPx ? { minWidth: minWidthPx } : undefined}>
          <thead>
            <tr className="text-left" style={{ color: 'var(--text-muted)' }}>
              {columns.map((column) => <th key={column} className="py-1.5 pr-2 font-medium">{column}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td colSpan={columns.length} className="py-4" style={{ color: 'var(--text-muted)' }}>等待后端数据</td></tr>
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
                    {cellIndex === statusColumn ? powerStatusLabel(cell) : cell}
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
