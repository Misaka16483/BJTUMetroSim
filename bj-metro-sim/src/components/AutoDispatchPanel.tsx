import { useEffect, useMemo, useState } from 'react';
import { useSimStore } from '../store/useSimStore';

interface Props {
  open: boolean;
  onClose: () => void;
}

const DUTY_LABELS: Record<string, string> = {
  IN_DEPOT: '车库待命',
  READY: '整备完成',
  DEPARTURE_REQUESTED: '请求发车',
  IN_SERVICE: '载客运行',
  TURNBACK: '折返作业',
  RETURN_REQUESTED: '等待返程',
  STORED: '任务完成',
};

const DUTY_COLORS: Record<string, string> = {
  IN_DEPOT: '#8b949e',
  READY: '#58a6ff',
  DEPARTURE_REQUESTED: '#eab308',
  IN_SERVICE: '#3fb950',
  TURNBACK: '#d2a8ff',
  RETURN_REQUESTED: '#f0883e',
  STORED: '#6e7681',
};

function formatClock(seconds?: number | null) {
  if (seconds == null || !Number.isFinite(seconds)) return '--:--:--';
  const whole = Math.max(0, Math.round(seconds)) % 86400;
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const s = whole % 60;
  return [h, m, s].map((value) => String(value).padStart(2, '0')).join(':');
}

function formatCountdown(seconds: number) {
  const rounded = Math.max(0, Math.ceil(seconds));
  const minutes = Math.floor(rounded / 60);
  const rest = rounded % 60;
  return minutes > 0 ? `${minutes}分${String(rest).padStart(2, '0')}秒` : `${rest}秒`;
}

export default function AutoDispatchPanel({ open, onClose }: Props) {
  const backendStatus = useSimStore((s) => s.backendStatus);
  const engineClockState = useSimStore((s) => s.engineClockState);
  const simTime = useSimStore((s) => s.simTime);
  const simTimeMs = useSimStore((s) => s.simTimeMs);
  const trains = useSimStore((s) => s.trains);
  const operationPlan = useSimStore((s) => s.operationPlan);
  const dispatchRuntime = useSimStore((s) => s.dispatchRuntime);
  const simStations = useSimStore((s) => s.simStations);
  const startBackendSim = useSimStore((s) => s.startBackendSim);
  const pauseBackendSim = useSimStore((s) => s.pauseBackendSim);
  const resumeBackendSim = useSimStore((s) => s.resumeBackendSim);
  const [commandPending, setCommandPending] = useState(false);
  const [commandError, setCommandError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [open, onClose]);

  const stationNames = useMemo(() => {
    const entries = simStations.map((station) => [station.code, station.name] as const);
    for (const service of operationPlan?.services ?? []) {
      for (const stop of service.stops) entries.push([stop.stationCode, stop.stationName]);
    }
    return new Map(entries);
  }, [operationPlan?.services, simStations]);

  const duties = useMemo(
    () => [...(operationPlan?.duties ?? [])].sort((a, b) => a.plannedStartS - b.plannedStartS),
    [operationPlan?.duties],
  );
  const servicesById = useMemo(
    () => new Map((operationPlan?.services ?? []).map((service) => [service.serviceId, service])),
    [operationPlan?.services],
  );
  const nextDuty = duties.find((duty) => duty.lifecycleState === 'IN_DEPOT' || duty.lifecycleState === 'READY');
  const nextService = nextDuty ? servicesById.get(nextDuty.serviceIds[0]) : undefined;
  const activeDutyCount = duties.filter((duty) => !['IN_DEPOT', 'STORED'].includes(duty.lifecycleState)).length;
  const completedDutyCount = duties.filter((duty) => duty.lifecycleState === 'STORED').length;
  const planEnabled = operationPlan?.enabled === true;
  const currentSeconds = simTimeMs / 1000;
  const countdown = nextDuty ? nextDuty.plannedStartS - currentSeconds : null;
  const recentDepartures = [...(dispatchRuntime?.recentDepartures ?? [])].reverse();

  const runCommand = async () => {
    if (commandPending || backendStatus !== 'connected') return;
    setCommandPending(true);
    setCommandError(null);
    try {
      if (engineClockState === 'RUNNING') await pauseBackendSim();
      else if (engineClockState === 'PAUSED') await resumeBackendSim();
      else await startBackendSim();
    } catch (error) {
      setCommandError(error instanceof Error ? error.message : '仿真控制失败');
    } finally {
      setCommandPending(false);
    }
  };

  if (!open) return null;

  const actionLabel = backendStatus !== 'connected'
    ? '等待后端连接'
    : commandPending
      ? '命令执行中…'
      : engineClockState === 'RUNNING'
        ? '暂停自动发车'
        : engineClockState === 'PAUSED'
          ? '继续自动发车'
          : '启动自动发车';

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.68)', backdropFilter: 'blur(7px)', padding: 24 }}
      onMouseDown={onClose}
      role="presentation"
    >
      <section
        className="glass flex flex-col overflow-hidden"
        style={{ width: 'min(1040px, 96vw)', height: 'min(760px, 90vh)', background: '#0d1117', border: '1px solid rgba(88,166,255,.25)' }}
        onMouseDown={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="auto-dispatch-title"
      >
        <header className="flex items-center justify-between shrink-0" style={{ padding: '16px 20px', borderBottom: '1px solid rgba(255,255,255,.08)' }}>
          <div className="flex items-center gap-3">
            <span className={`led ${engineClockState === 'RUNNING' ? 'led-online' : ''}`} />
            <div>
              <div id="auto-dispatch-title" style={{ color: '#e6edf3', fontSize: 15, fontWeight: 700 }}>运行图自动发车</div>
              <div style={{ color: '#8b949e', fontSize: 10, marginTop: 3 }}>按当前后端场景自动生成交路、上线列车并执行计划发车</div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className="chip" style={{ color: planEnabled ? '#3fb950' : '#eab308', border: `1px solid ${planEnabled ? 'rgba(63,185,80,.3)' : 'rgba(234,179,8,.3)'}`, background: planEnabled ? 'rgba(63,185,80,.08)' : 'rgba(234,179,8,.08)' }}>
              {planEnabled ? 'AUTO PLAN READY' : 'AUTO PLAN OFF'}
            </span>
            <button type="button" onClick={onClose} aria-label="关闭自动发车面板" style={{ width: 28, height: 28, borderRadius: 6, color: '#8b949e', border: '1px solid rgba(255,255,255,.1)', background: 'rgba(255,255,255,.03)', cursor: 'pointer' }}>✕</button>
          </div>
        </header>

        <div className="grid grid-cols-4 gap-2 shrink-0" style={{ padding: '14px 20px 12px' }}>
          <SummaryCard label="仿真时刻" value={simTime || '--:--:--'} detail={engineClockState} color="#58a6ff" />
          <SummaryCard label="计划车组" value={String(duties.length)} detail={`${operationPlan?.services.length ?? 0} 个运营任务`} color="#a8d64a" />
          <SummaryCard label="在线 / 运行" value={`${dispatchRuntime?.registeredTrainCount ?? trains.length} / ${activeDutyCount}`} detail={`${completedDutyCount} 组已完成`} color="#3fb950" />
          <SummaryCard label="累计发车" value={String(dispatchRuntime?.departureCount ?? 0)} detail="全线站台发车记录" color="#d2a8ff" />
        </div>

        <div className="grid min-h-0 flex-1" style={{ gridTemplateColumns: 'minmax(0, 1.55fr) minmax(300px, .9fr)', gap: 12, padding: '0 20px 18px' }}>
          <div className="card flex flex-col min-h-0" style={{ padding: 0 }}>
            <div className="flex items-center justify-between shrink-0" style={{ padding: '12px 14px', borderBottom: '1px solid rgba(255,255,255,.07)' }}>
              <div>
                <div className="label" style={{ color: '#c9d1d9' }}>发车队列</div>
                <div style={{ color: '#6e7681', fontSize: 9, marginTop: 3 }}>列车由运行图到点自动上线，无需手动添加</div>
              </div>
              <span className="board-num" style={{ color: '#8b949e', fontSize: 9 }}>{duties.length} DUTIES</span>
            </div>
            <div className="overflow-auto min-h-0">
              {duties.length > 0 ? duties.map((duty, index) => {
                const service = servicesById.get(duty.activeServiceId ?? duty.serviceIds[0]);
                const color = DUTY_COLORS[duty.lifecycleState] ?? '#8b949e';
                return (
                  <div key={duty.dutyId} className="grid items-center" style={{ gridTemplateColumns: '34px 82px minmax(120px, 1fr) 94px 90px', minHeight: 46, padding: '0 14px', borderBottom: '1px solid rgba(255,255,255,.045)', fontSize: 10 }}>
                    <span className="board-num" style={{ color: '#484f58' }}>{String(index + 1).padStart(2, '0')}</span>
                    <span className="font-mono" style={{ color: '#c9d1d9' }}>{duty.trainId}</span>
                    <span className="truncate" style={{ color: '#8b949e' }}>
                      {service ? `${stationNames.get(service.originStationCode) ?? service.originStationCode} → ${stationNames.get(service.terminalStationCode) ?? service.terminalStationCode}` : duty.dutyId}
                    </span>
                    <span className="board-num" style={{ color: '#c9d1d9' }}>{formatClock(duty.plannedStartS)}</span>
                    <span className="chip justify-self-end" style={{ color, border: `1px solid ${color}44`, background: `${color}12`, minWidth: 72, textAlign: 'center' }}>
                      {DUTY_LABELS[duty.lifecycleState] ?? duty.lifecycleState}
                    </span>
                  </div>
                );
              }) : (
                <EmptyState text={backendStatus === 'connected' ? '当前场景未启用自动运行图' : '连接后端后显示自动发车计划'} />
              )}
            </div>
          </div>

          <div className="flex flex-col min-h-0 gap-2">
            <div className="card shrink-0" style={{ padding: 14, border: nextDuty ? '1px solid rgba(88,166,255,.22)' : undefined }}>
              <div className="flex items-center justify-between">
                <span className="label" style={{ color: '#8b949e' }}>下一计划发车</span>
                <span className="board-num" style={{ color: countdown != null && countdown <= 30 ? '#eab308' : '#58a6ff', fontSize: 10 }}>
                  {nextDuty ? (countdown != null && countdown > 0 ? `T-${formatCountdown(countdown)}` : '等待发车条件') : '计划已完成'}
                </span>
              </div>
              <div className="flex items-end justify-between" style={{ marginTop: 12 }}>
                <div>
                  <div className="board-num" style={{ color: '#e6edf3', fontSize: 24, fontWeight: 700 }}>{nextDuty?.trainId ?? '—'}</div>
                  <div style={{ color: '#8b949e', fontSize: 10, marginTop: 5 }}>
                    {nextService ? `${stationNames.get(nextService.originStationCode) ?? nextService.originStationCode} · ${nextService.direction === 'UP' ? '上行' : '下行'}` : '暂无待发车组'}
                  </div>
                </div>
                <div className="board-num" style={{ color: '#58a6ff', fontSize: 16 }}>{formatClock(nextDuty?.plannedStartS)}</div>
              </div>
              <button type="button" onClick={() => { void runCommand(); }} disabled={backendStatus !== 'connected' || commandPending || !planEnabled} style={{ width: '100%', height: 34, marginTop: 14, borderRadius: 7, cursor: backendStatus === 'connected' && planEnabled ? 'pointer' : 'not-allowed', color: engineClockState === 'RUNNING' ? '#eab308' : '#fff', fontWeight: 650, fontSize: 11, opacity: planEnabled ? 1 : .55, background: engineClockState === 'RUNNING' ? 'rgba(234,179,8,.1)' : 'rgba(88,166,255,.18)', border: `1px solid ${engineClockState === 'RUNNING' ? 'rgba(234,179,8,.35)' : 'rgba(88,166,255,.4)'}` }}>
                {actionLabel}
              </button>
              {commandError && <div style={{ color: '#f85149', fontSize: 9, marginTop: 7 }}>{commandError}</div>}
            </div>

            <div className="card flex flex-col min-h-0 flex-1" style={{ padding: 0 }}>
              <div className="flex items-center justify-between shrink-0" style={{ padding: '11px 13px', borderBottom: '1px solid rgba(255,255,255,.07)' }}>
                <span className="label" style={{ color: '#c9d1d9' }}>最近发车</span>
                <span className="board-num" style={{ color: '#6e7681', fontSize: 9 }}>LIVE</span>
              </div>
              <div className="overflow-auto min-h-0">
                {recentDepartures.length > 0 ? recentDepartures.map((departure, index) => (
                  <div key={`${departure.trainId}-${departure.stationId}-${departure.simTimeS}-${index}`} className="flex items-center gap-3" style={{ minHeight: 42, padding: '0 13px', borderBottom: '1px solid rgba(255,255,255,.045)', fontSize: 10 }}>
                    <span className="led led-online" />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-mono" style={{ color: '#c9d1d9' }}>{departure.trainId}</span>
                        <span className="board-num" style={{ color: '#58a6ff' }}>{formatClock(departure.simTimeS)}</span>
                      </div>
                      <div className="truncate" style={{ color: '#6e7681', marginTop: 2 }}>
                        {stationNames.get(departure.stationId) ?? departure.stationId} · {departure.direction === 'UP' ? '上行' : '下行'}发车
                      </div>
                    </div>
                  </div>
                )) : <EmptyState text="仿真启动后，发车记录将在这里实时更新" compact />}
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

function SummaryCard({ label, value, detail, color }: { label: string; value: string; detail: string; color: string }) {
  return (
    <div className="card" style={{ padding: '11px 13px' }}>
      <div className="label" style={{ color: '#6e7681' }}>{label}</div>
      <div className="board-num" style={{ color, fontSize: 21, fontWeight: 700, marginTop: 7 }}>{value}</div>
      <div className="truncate" style={{ color: '#8b949e', fontSize: 9, marginTop: 4 }}>{detail}</div>
    </div>
  );
}

function EmptyState({ text, compact = false }: { text: string; compact?: boolean }) {
  return (
    <div className="flex items-center justify-center text-center" style={{ minHeight: compact ? 110 : 220, padding: 24, color: '#6e7681', fontSize: 10 }}>
      {text}
    </div>
  );
}
