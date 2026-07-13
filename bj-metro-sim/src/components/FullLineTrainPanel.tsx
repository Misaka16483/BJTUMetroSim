import { useSimStore } from '../store/useSimStore';

function phaseLabel(phase: string): string {
  const map: Record<string, string> = {
    IDLE: '待命', DWELLING: '停站', DEPARTING: '出发', CRUISING: '巡行',
    APPROACHING: '进站', EMERGENCY_BRAKE: '紧急制动', SIGNAL_AT_STOP: '信号停车',
  };
  return map[phase] ?? phase;
}

function phaseColor(phase: string): string {
  const map: Record<string, string> = {
    IDLE: '#484f58', DWELLING: '#58a6ff', DEPARTING: '#f59e0b', CRUISING: '#30d158',
    APPROACHING: '#8FC31F', EMERGENCY_BRAKE: '#ef4444', SIGNAL_AT_STOP: '#ef4444',
  };
  return map[phase] ?? '#8b949e';
}

export default function FullLineTrainPanel() {
  const trains = useSimStore((s) => s.trains);
  const trainColors = useSimStore((s) => s.trainColors);
  const selectedTrainId = useSimStore((s) => s.selectedTrainId);
  const setSelectedTrainId = useSimStore((s) => s.setSelectedTrainId);

  return (
    <div className="flex flex-col h-full text-[11px]" style={{ color: '#c9d1d9' }}>
      {/* ═══ 列车列表 ═══ */}
      <div className="flex-1 min-h-0 flex flex-col rounded-lg"
        style={{ border: '1px solid rgba(255,255,255,.06)', background: 'rgba(255,255,255,.015)' }}>
        <div className="shrink-0 px-3 py-2 flex items-center justify-between"
          style={{ borderBottom: '1px solid rgba(255,255,255,.06)' }}>
          <span className="text-[10px] uppercase tracking-[0.12em]" style={{ color: '#5f7088' }}>列车</span>
          <span className="text-[18px] font-semibold font-mono" style={{ color: '#e2e8f0' }}>{trains.length}</span>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto p-2" style={{ scrollbarWidth: 'thin' }}>
          {trains.length === 0 ? (
            <div className="text-center py-6 text-[10px]" style={{ color: '#484f58' }}>暂无列车</div>
          ) : (
            <div className="flex flex-col gap-1">
              {trains.map((t) => {
                const color = trainColors[t.trainId] || '#8FC31F';
                const loadPct = t.capacityPax > 0 ? Math.round((t.onboardPax / t.capacityPax) * 100) : 0;
                const isSelected = selectedTrainId === t.trainId;
                return (
                  <div key={t.trainId} className="rounded px-2 py-1.5 cursor-pointer"
                    onClick={() => setSelectedTrainId(isSelected ? null : t.trainId)}
                    style={{
                      border: `1px solid ${isSelected ? color : 'rgba(48,54,61,.5)'}`,
                      background: isSelected ? `${color}08` : 'rgba(255,255,255,.008)',
                    }}>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-1.5">
                        <span className="w-2 h-2 rounded-full shrink-0" style={{ background: color }} />
                        <span className="text-[10px] font-semibold" style={{ color }}>{t.trainId}</span>
                        <span className="rounded px-1 py-px text-[8px]" style={{
                          color: t.operationMode === 'MANUAL' ? '#d29922' : '#58a6ff',
                          background: t.operationMode === 'MANUAL' ? 'rgba(210,153,34,.1)' : 'rgba(88,166,255,.1)',
                        }}>{t.operationMode === 'MANUAL' ? 'RM' : 'ATO'}</span>
                      </div>
                      <span className="text-[10px] font-mono" style={{ color: '#c9d1d9' }}>
                        {(t.speedMps * 3.6).toFixed(0)} km/h
                      </span>
                    </div>
                    <div className="flex items-center justify-between mt-0.5">
                      <span className="text-[9px]" style={{ color: '#6b7280' }}>
                        {t.currentStation || t.currentStationCode} → {t.nextStation || t.nextStationCode}
                      </span>
                      <div className="flex items-center gap-1.5">
                        <span className="text-[8px]" style={{ color: '#484f58' }}>
                          {t.direction === 'UP' ? '上行' : '下行'}
                        </span>
                        <span className="rounded px-1 py-px text-[8px]"
                          style={{ color: phaseColor(t.phase), background: `${phaseColor(t.phase)}15` }}>
                          {phaseLabel(t.phase)}
                        </span>
                      </div>
                    </div>
                    {t.dutyId && (
                      <div className="flex items-center justify-between mt-1 text-[8px] font-mono" style={{ color: '#7d8590' }}>
                        <span>{t.dutyId} · {t.serviceId}</span>
                        <span style={{ color: t.lifecycleState === 'IN_SERVICE' ? '#30d158' : '#d29922' }}>
                          {t.lifecycleState}
                          {typeof t.scheduleDeviationSec === 'number'
                            ? ` · ${t.scheduleDeviationSec >= 0 ? '+' : ''}${t.scheduleDeviationSec.toFixed(0)}s`
                            : ''}
                        </span>
                      </div>
                    )}
                    <div className="flex items-center gap-2 mt-1">
                      <div className="flex-1 h-1 rounded-full" style={{ background: 'rgba(255,255,255,.06)', overflow: 'hidden' }}>
                        <div className="h-full rounded-full transition-all"
                          style={{ width: `${Math.min(100, Math.round(t.segmentProgress * 100))}%`, background: color }} />
                      </div>
                      <span className="text-[8px] font-mono shrink-0" style={{ color: loadPct > 80 ? '#f59e0b' : '#6b7280' }}>
                        {t.onboardPax}人/{loadPct}%
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
