import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { fetchSimReport, type SimReport } from '../data/backendApi';
import { DynamicsCharts, PassengerCharts, PowerCharts } from './ReportCharts';

type TabKey = 'dynamics' | 'passenger' | 'power' | 'kpi';

const TABS: Array<{ key: TabKey; label: string }> = [
  { key: 'dynamics', label: '动力性能' },
  { key: 'passenger', label: '客流统计' },
  { key: 'power', label: '供电性能' },
  { key: 'kpi', label: '调度 KPI' },
];

function pct(x: number | null | undefined): string {
  return x == null ? '—' : `${(x * 100).toFixed(1)}%`;
}
function num(x: number | null | undefined, digits = 2): string {
  return x == null ? '—' : x.toFixed(digits);
}

function Metric({ label, value, unit }: { label: string; value: string; unit?: string }) {
  return (
    <div style={{ background: '#0d1117', border: '1px solid #30363d', borderRadius: 8, padding: '10px 12px' }}>
      <div style={{ fontSize: 10, color: '#8b949e' }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 700, color: '#e6edf3', marginTop: 3 }}>
        {value}
        {unit && <span style={{ fontSize: 10, color: '#8b949e', marginLeft: 3 }}>{unit}</span>}
      </div>
    </div>
  );
}

function MetricGrid({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))',
        gap: 10,
        marginBottom: 14,
      }}
    >
      {children}
    </div>
  );
}

function downloadReportJson(report: SimReport): void {
  const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `sim-report-${report.runId}.json`;
  link.click();
  URL.revokeObjectURL(url);
}

export default function SimulationReport({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<SimReport | null>(null);
  const [tab, setTab] = useState<TabKey>('dynamics');

  useEffect(() => {
    if (!open) return;
    let active = true;
    setLoading(true);
    setError(null);
    fetchSimReport()
      .then((res) => {
        if (!active) return;
        if (res.ok && res.report) setReport(res.report);
        else setError(res.error || '暂无报告，请先运行并停止一次仿真');
        setLoading(false);
      })
      .catch((e) => {
        if (!active) return;
        setError(e instanceof Error ? e.message : '请求报告失败');
        setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [open]);

  if (!open) return null;

  const s = report?.summary;

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        background: 'rgba(0,0,0,0.62)',
        backdropFilter: 'blur(4px)',
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'flex-start',
        padding: '32px 16px',
        overflowY: 'auto',
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 980,
          maxWidth: '100%',
          background: '#010409',
          border: '1px solid #30363d',
          borderRadius: 12,
          boxShadow: '0 20px 60px rgba(0,0,0,.6)',
        }}
      >
        {/* header */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '14px 18px',
            borderBottom: '1px solid #21262d',
            position: 'sticky',
            top: 0,
            background: '#010409',
            zIndex: 2,
          }}
        >
          <div>
            <div style={{ fontSize: 14, fontWeight: 700, color: '#e6edf3' }}>仿真运行报告</div>
            {s && (
              <div style={{ fontSize: 10, color: '#8b949e', marginTop: 2 }}>
                {s.scenarioName} · 运行 #{s.runId} · 时长 {s.durationStr} · 列车 {s.trainCount} · 站点 {s.stationCount}
              </div>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {report && (
              <button
                type="button"
                onClick={() => downloadReportJson(report)}
                style={{
                  padding: '5px 10px',
                  borderRadius: 6,
                  fontSize: 11,
                  color: '#58a6ff',
                  background: 'rgba(88,166,255,0.08)',
                  border: '1px solid rgba(88,166,255,0.25)',
                  cursor: 'pointer',
                }}
              >
                导出 JSON
              </button>
            )}
            <button
              type="button"
              onClick={onClose}
              style={{ fontSize: 14, color: '#8b949e', background: 'none', border: 'none', cursor: 'pointer' }}
            >
              ✕
            </button>
          </div>
        </div>

        {/* body */}
        <div style={{ padding: 18 }}>
          {loading && <div style={{ padding: 40, textAlign: 'center', color: '#8b949e' }}>报告生成 / 加载中…</div>}
          {!loading && error && (
            <div style={{ padding: 40, textAlign: 'center', color: '#f85149', fontSize: 12 }}>{error}</div>
          )}
          {!loading && report && (
            <>
              {/* 概览卡片 */}
              <div style={{ fontSize: 11, fontWeight: 600, color: '#c9d1d9', margin: '0 0 8px' }}>仿真概览</div>
              <MetricGrid>
                <Metric label="运行 ID" value={`#${s?.runId ?? '-'}`} />
                <Metric label="场景" value={s?.scenarioName ?? '-'} />
                <Metric label="持续时长" value={s?.durationStr ?? '-'} />
                <Metric label="列车数量" value={`${s?.trainCount ?? '-'}`} unit="列" />
                <Metric label="站点数量" value={`${s?.stationCount ?? '-'}`} unit="站" />
                <Metric label="总事件数" value={`${s?.totalEvents ?? '-'}`} />
                <Metric label="总 Tick 数" value={`${s?.totalTicks ?? '-'}`} />
              </MetricGrid>

              {/* tabs */}
              <div style={{ display: 'flex', gap: 6, margin: '6px 0 14px' }}>
                {TABS.map((t) => (
                  <button
                    key={t.key}
                    onClick={() => setTab(t.key)}
                    style={{
                      padding: '6px 14px',
                      borderRadius: 6,
                      fontSize: 11,
                      cursor: 'pointer',
                      color: tab === t.key ? '#fff' : '#8b949e',
                      background: tab === t.key ? 'rgba(88,166,255,0.25)' : 'rgba(255,255,255,0.03)',
                      border: `1px solid ${tab === t.key ? '#58a6ff' : 'rgba(255,255,255,0.08)'}`,
                    }}
                  >
                    {t.label}
                  </button>
                ))}
              </div>

              {tab === 'dynamics' && (
                <>
                  <MetricGrid>
                    <Metric label="总能耗" value={num(report.dynamics.totalEnergyKwh)} unit="kWh" />
                    <Metric label="牵引能耗" value={num(report.dynamics.tractionEnergyKwh)} unit="kWh" />
                    <Metric label="辅助能耗" value={num(report.dynamics.auxiliaryEnergyKwh)} unit="kWh" />
                    <Metric label="再生产生" value={num(report.dynamics.regenGeneratedKwh)} unit="kWh" />
                    <Metric label="再生接受" value={num(report.dynamics.regenAcceptedKwh)} unit="kWh" />
                    <Metric label="再生浪费" value={num(report.dynamics.regenWastedKwh)} unit="kWh" />
                    <Metric label="再生利用率" value={pct(report.dynamics.regenUtilizationRate)} />
                    <Metric label="最高速度" value={num(report.dynamics.maxSpeedKmh, 1)} unit="km/h" />
                    <Metric label="平均速度" value={num(report.dynamics.avgSpeedKmh, 1)} unit="km/h" />
                    <Metric label="总里程" value={num(report.dynamics.totalDistanceKm, 1)} unit="km" />
                  </MetricGrid>
                  <DynamicsCharts charts={report.charts.dynamics} />
                </>
              )}

              {tab === 'passenger' && (
                <>
                  <MetricGrid>
                    <Metric label="总进站" value={`${report.passenger.totalArrivals}`} unit="人" />
                    <Metric label="总上车" value={`${report.passenger.totalBoardings}`} unit="人" />
                    <Metric label="总下车" value={`${report.passenger.totalAlightings}`} unit="人" />
                    <Metric label="总滞留" value={`${report.passenger.totalLeftBehind}`} unit="人" />
                    <Metric label="最大候车" value={`${report.passenger.maxWaitingPax ?? 0}`} unit="人" />
                    <Metric
                      label="平均等待"
                      value={report.passenger.avgWaitingSec == null ? '—' : num(report.passenger.avgWaitingSec, 1)}
                      unit="s"
                    />
                    <Metric
                      label="最拥挤站"
                      value={report.passenger.peakCrowdingStation ?? '—'}
                      unit={report.passenger.peakCrowdingLevel ?? undefined}
                    />
                  </MetricGrid>
                  <PassengerCharts charts={report.charts.passenger} />
                </>
              )}

              {tab === 'power' && (
                <>
                  <MetricGrid>
                    <Metric label="总消耗" value={num(report.power.totalPowerConsumedKwh)} unit="kWh" />
                    <Metric label="再生产生" value={num(report.power.totalRegenGeneratedKwh)} unit="kWh" />
                    <Metric label="再生吸收" value={num(report.power.totalRegenAbsorbedKwh)} unit="kWh" />
                    <Metric label="再生浪费" value={num(report.power.totalRegenWastedKwh)} unit="kWh" />
                    <Metric label="平均电压" value={num(report.power.avgVoltageV, 1)} unit="V" />
                    <Metric label="最低电压" value={num(report.power.minVoltageV, 1)} unit="V" />
                    <Metric label="最高电压" value={num(report.power.maxVoltageV, 1)} unit="V" />
                    <Metric label="过载事件" value={`${report.power.overloadEvents}`} unit="次" />
                  </MetricGrid>
                  <PowerCharts charts={report.charts.power} />
                </>
              )}

              {tab === 'kpi' && (
                <>
                  {!report.kpi.available && (
                    <div style={{ padding: 24, textAlign: 'center', color: '#8b949e', fontSize: 11 }}>
                      本次运行未记录调度 KPI（可能未启用调度模块）
                    </div>
                  )}
                  {report.kpi.available && (
                    <MetricGrid>
                      <Metric label="准点率" value={pct(report.kpi.onTimeRate)} />
                      <Metric label="平均等待" value={num(report.kpi.avgWaitSec, 1)} unit="s" />
                      <Metric label="平均满载率" value={pct(report.kpi.avgLoadFactor)} />
                      <Metric label="最大满载率" value={pct(report.kpi.maxLoadFactor)} />
                      <Metric label="超载事件" value={`${report.kpi.overloadEvents ?? 0}`} unit="次" />
                      <Metric label="追踪间隔违规" value={`${report.kpi.headwayViolations ?? 0}`} unit="次" />
                      <Metric
                        label="延误恢复"
                        value={report.kpi.recoveryTimeSec == null ? '—' : num(report.kpi.recoveryTimeSec, 1)}
                        unit="s"
                      />
                    </MetricGrid>
                  )}
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
