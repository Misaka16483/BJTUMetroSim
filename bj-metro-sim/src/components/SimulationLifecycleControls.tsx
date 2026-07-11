import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useSimStore } from '../store/useSimStore';

const START_STEPS = [
  '公共仿真内核与场景',
  '线路、车辆与 ATO 模型',
  '客流、车站与调度模块',
  '牵引供电网络与潮流模块',
  '实时快照与 tick 线程',
];

const STOP_STEPS = [
  '接收停止命令',
  '停止仿真时钟',
  '等待 tick 线程退出',
  '刷新最终状态快照',
  '完成运行状态收尾',
];

type Operation = 'start' | 'stop' | null;

const delay = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms));

export default function SimulationLifecycleControls() {
  const engineClockState = useSimStore((s) => s.engineClockState);
  const startBackendSim = useSimStore((s) => s.startBackendSim);
  const pauseBackendSim = useSimStore((s) => s.pauseBackendSim);
  const resumeBackendSim = useSimStore((s) => s.resumeBackendSim);
  const stopBackendSim = useSimStore((s) => s.stopBackendSim);
  const [operation, setOperation] = useState<Operation>(null);
  const [stepIndex, setStepIndex] = useState(0);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(true);
  const [panelPosition, setPanelPosition] = useState({ top: 52, right: 12 });
  const locked = useRef(false);
  const anchorRef = useRef<HTMLDivElement>(null);

  const steps = operation === 'stop' ? STOP_STEPS : START_STEPS;

  useEffect(() => {
    if (!operation || done || error) return;
    const timer = window.setInterval(() => {
      setStepIndex((value) => Math.min(value + 1, steps.length - 1));
    }, 500);
    return () => window.clearInterval(timer);
  }, [operation, done, error, steps.length]);

  useEffect(() => {
    if (!operation) return;
    const updatePosition = () => {
      const rect = anchorRef.current?.getBoundingClientRect();
      if (!rect) return;
      setPanelPosition({
        top: Math.max(8, rect.bottom + 6),
        right: Math.max(8, window.innerWidth - rect.right),
      });
    };
    updatePosition();
    window.addEventListener('resize', updatePosition);
    return () => window.removeEventListener('resize', updatePosition);
  }, [operation]);

  const run = async (nextOperation: Exclude<Operation, null>) => {
    if (locked.current) return;
    locked.current = true;
    setOperation(nextOperation);
    setStepIndex(0);
    setDone(false);
    setError(null);
    setDetailsOpen(true);
    const action = nextOperation === 'start' ? startBackendSim : stopBackendSim;
    try {
      // Keep every stage visible even when the local backend responds immediately.
      await Promise.all([action(), delay(steps.length * 500)]);
      setStepIndex(steps.length - 1);
      setDone(true);
      await delay(900);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : `${nextOperation === 'start' ? '启动' : '停止'}失败`);
      await delay(1800);
    } finally {
      locked.current = false;
      setOperation(null);
      setDone(false);
    }
  };

  return (
    <div ref={anchorRef} className="relative flex items-center gap-1" style={{ minWidth: 72, minHeight: 32 }}>
      {engineClockState !== 'RUNNING' && engineClockState !== 'PAUSED' ? (
        <ControlButton onClick={() => run('start')} color="#22c55e" label="▶" title="启动" />
      ) : engineClockState === 'RUNNING' ? (
        <>
          <ControlButton onClick={pauseBackendSim} color="#eab308" label="⏸" title="暂停" />
          <ControlButton onClick={() => run('stop')} color="#ef4444" label="⏹" title="停止" />
        </>
      ) : (
        <>
          <ControlButton onClick={resumeBackendSim} color="#22c55e" label="▶" title="恢复" />
          <ControlButton onClick={() => run('stop')} color="#ef4444" label="⏹" title="停止" />
        </>
      )}

      {operation && createPortal(
        <div style={{
          position: 'fixed', zIndex: 2147483647, top: panelPosition.top, right: panelPosition.right, width: 370,
          padding: '12px 14px', borderRadius: 9, background: 'rgba(13,17,23,.98)',
          border: `1px solid ${error ? 'rgba(248,81,73,.55)' : 'rgba(88,166,255,.45)'}`,
          boxShadow: '0 14px 40px rgba(0,0,0,.65)', pointerEvents: 'auto',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: detailsOpen ? 7 : 0 }}>
            <div style={{ flex: 1, fontSize: 11, fontWeight: 700, color: error ? '#f85149' : done ? '#3fb950' : '#58a6ff' }}>
              {error ? `仿真系统${operation === 'start' ? '启动' : '停止'}失败`
                : done ? `仿真系统${operation === 'start' ? '启动' : '停止'}完成`
                  : `正在${operation === 'start' ? '启动' : '停止'}仿真系统`}
              {!detailsOpen && !error && (
                <span style={{ marginLeft: 8, color: '#c9d1d9', fontWeight: 400 }}>
                  {steps[stepIndex]} · {done ? '完成' : `${operation === 'start' ? '启动' : '停止'}中`}
                </span>
              )}
            </div>
            <button type="button" onClick={() => setDetailsOpen((open) => !open)} style={{
              border: '1px solid rgba(255,255,255,.1)', borderRadius: 4, padding: '2px 7px',
              background: 'rgba(255,255,255,.04)', color: '#8b949e', fontSize: 9, cursor: 'pointer',
            }}>{detailsOpen ? '收起 ▲' : '详情 ▼'}</button>
          </div>
          {detailsOpen && (error ? <div style={{ color: '#c9d1d9', fontSize: 10 }}>{error}</div> : steps.map((step, index) => {
            const completed = done || index < stepIndex;
            const active = !done && index === stepIndex;
            return (
              <div key={step} style={{ display: 'flex', alignItems: 'center', gap: 7, minHeight: 22, fontSize: 10, color: completed || active ? '#c9d1d9' : '#484f58' }}>
                {completed ? <span style={{ color: '#3fb950', width: 11 }}>✓</span>
                  : active ? <span className="startup-spinner" /> : <span style={{ width: 11 }}>○</span>}
                <span>{step}</span>
                <span style={{ marginLeft: 'auto', color: completed ? '#3fb950' : active ? '#58a6ff' : '#484f58' }}>
                  {completed ? '完成' : active ? `${operation === 'start' ? '启动' : '停止'}中` : '等待中'}
                </span>
              </div>
            );
          }))}
          {detailsOpen && !error && <div style={{ marginTop: 7, paddingTop: 6, borderTop: '1px solid rgba(255,255,255,.06)', color: '#8b949e', fontSize: 9 }}>
            操作完成前，仿真控制按钮已锁定。
          </div>}
        </div>, document.body
      )}
    </div>
  );
}

function ControlButton({ onClick, color, label, title }: { onClick: () => void | Promise<void>; color: string; label: string; title: string }) {
  return (
    <button type="button" onClick={() => { void onClick(); }} title={title} style={{
      width: 30, height: 28, borderRadius: 6, cursor: 'pointer', color,
      background: `${color}14`, border: `1px solid ${color}44`, fontSize: 12,
    }}>{label}</button>
  );
}
