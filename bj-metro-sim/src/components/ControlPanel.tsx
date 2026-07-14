import { useEffect, useRef, useState } from 'react';
import { useSimStore } from '../store/useSimStore';

const STARTUP_STEPS = [
  '公共仿真内核与场景',
  '线路、车辆与 ATO 模型',
  '客流、车站与调度模块',
  '牵引供电网络与潮流模块',
  '实时快照与 tick 线程',
];

const SHUTDOWN_STEPS = [
  '接收停止命令',
  '停止仿真时钟',
  '等待 tick 线程退出',
  '刷新最终状态快照',
  '完成运行状态收尾',
];

export default function ControlPanel() {
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [operationStep, setOperationStep] = useState(0);
  const [operationDone, setOperationDone] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const operationRef = useRef(false);
  const {
    isRunning, toggleRunning, speed, setSpeed,
    simTime, showOnlyLines, showAllLines, tick,
    backendStatus, engineClockState, dataMode,
    startBackendSim, pauseBackendSim, resumeBackendSim, stopBackendSim,
  } = useSimStore();

  useEffect(() => {
    if (dataMode !== 'DEMO') return;
    if (!isRunning) return;
    const interval = setInterval(tick, 100);
    return () => clearInterval(interval);
  }, [isRunning, tick, dataMode]);

  useEffect(() => {
    if (!starting && !stopping) return;
    const steps = starting ? STARTUP_STEPS : SHUTDOWN_STEPS;
    setOperationStep(0);
    setOperationDone(false);
    const interval = window.setInterval(() => {
      setOperationStep((step) => Math.min(step + 1, steps.length - 2));
    }, 450);
    return () => window.clearInterval(interval);
  }, [starting, stopping]);

  const beginStart = async () => {
    if (operationRef.current) return;
    operationRef.current = true;
    setStarting(true);
    setStartError(null);
    try {
      await startBackendSim();
      setOperationStep(STARTUP_STEPS.length - 1);
      setOperationDone(true);
      await new Promise((resolve) => window.setTimeout(resolve, 650));
    } catch (error) {
      setStartError(error instanceof Error ? error.message : '启动失败');
      await new Promise((resolve) => window.setTimeout(resolve, 1200));
    } finally {
      operationRef.current = false;
      setStarting(false);
      setOperationDone(false);
    }
  };

  const beginStop = async () => {
    if (operationRef.current) return;
    operationRef.current = true;
    setStopping(true);
    setStartError(null);
    try {
      await stopBackendSim();
      setOperationStep(SHUTDOWN_STEPS.length - 1);
      setOperationDone(true);
      await new Promise((resolve) => window.setTimeout(resolve, 650));
    } catch (error) {
      setStartError(error instanceof Error ? error.message : '停止失败');
      await new Promise((resolve) => window.setTimeout(resolve, 1200));
    } finally {
      operationRef.current = false;
      setStopping(false);
      setOperationDone(false);
    }
  };

  const isBackend = backendStatus === 'connected';
  const backendState = engineClockState;
  const canChangeSpeed = isBackend ? backendState === 'RUNNING' : isRunning;
  const stateColor = backendState === 'RUNNING' ? 'var(--green)'
    : starting ? 'var(--cyan)'
    : stopping ? 'var(--amber)'
    : backendState === 'PAUSED' ? 'var(--amber)'
    : 'var(--text-muted)';

  return (
    <div className="glass shrink-0 flex items-center gap-4 px-5" style={{ height: 50, position: 'relative' }}>
      {/* ─── 控制按钮 ─── */}
      {isBackend ? (
        <div className="flex items-center gap-1.5">
          {backendState !== 'RUNNING' && backendState !== 'PAUSED' ? (
            <button
              onClick={beginStart}
              disabled={starting}
              className="flex items-center gap-1.5 cursor-pointer label rounded-lg"
              style={{
                padding: '6px 14px',
                background: 'rgba(48,209,88,0.06)',
                border: '1px solid rgba(48,209,88,0.2)',
                color: 'var(--green)',
              }}
            >
              {starting ? <span className="startup-spinner" /> : (
                <svg width="8" height="8" viewBox="0 0 8 8">
                  <polygon points="2,1 7,4 2,7" fill="currentColor" />
                </svg>
              )}
              {starting ? 'STARTING…' : 'START'}
            </button>
          ) : (
            <>
              {backendState === 'RUNNING' ? (
                <button
                  onClick={() => { pauseBackendSim(); }}
                  className="flex items-center gap-1.5 cursor-pointer label rounded-lg"
                  style={{
                    padding: '6px 12px',
                    background: 'rgba(255,204,0,0.06)',
                    border: '1px solid rgba(255,204,0,0.2)',
                    color: 'var(--amber)',
                  }}
                >
                  <svg width="8" height="8" viewBox="0 0 8 8">
                    <rect x="1" y="1" width="2.5" height="6" rx="0.5" fill="currentColor" />
                    <rect x="4.5" y="1" width="2.5" height="6" rx="0.5" fill="currentColor" />
                  </svg>
                  PAUSE
                </button>
              ) : (
                <button
                  onClick={() => { resumeBackendSim(); }}
                  className="flex items-center gap-1.5 cursor-pointer label rounded-lg"
                  style={{
                    padding: '6px 12px',
                    background: 'rgba(48,209,88,0.06)',
                    border: '1px solid rgba(48,209,88,0.2)',
                    color: 'var(--green)',
                  }}
                >
                  <svg width="8" height="8" viewBox="0 0 8 8">
                    <polygon points="2,1 7,4 2,7" fill="currentColor" />
                  </svg>
                  RESUME
                </button>
              )}
              <button
                onClick={beginStop}
                className="flex items-center gap-1.5 cursor-pointer label rounded-lg"
                style={{
                  padding: '6px 14px',
                  background: 'rgba(255,69,58,0.06)',
                  border: '1px solid rgba(255,69,58,0.2)',
                  color: 'var(--red)',
                }}
              >
                <svg width="8" height="8" viewBox="0 0 8 8">
                  <rect x="1" y="1" width="6" height="6" rx="0.5" fill="currentColor" />
                </svg>
                STOP
              </button>
            </>
          )}
          {/* 后端状态指示 */}
          <span
            className="chip text-[9px]"
            style={{
              color: stateColor,
              border: '1px solid rgba(255,255,255,0.06)',
            }}
          >
            {starting ? 'STARTING' : stopping ? 'STOPPING' : backendState}
          </span>
          {(starting || stopping) && (
            <div style={{
              position: 'absolute', top: 2, left: 10, zIndex: 100, width: 350,
              padding: '10px 12px', borderRadius: 7, background: '#0d1117',
              border: `1px solid ${startError ? 'rgba(248,81,73,.45)' : 'rgba(88,166,255,.35)'}`,
              boxShadow: '0 10px 30px rgba(0,0,0,.4)', fontSize: 10,
              pointerEvents: 'auto',
            }}>
              <div style={{ color: startError ? '#f85149' : '#58a6ff', fontWeight: 600, marginBottom: 5 }}>
                {startError ? (starting ? '仿真启动失败' : '仿真停止失败')
                  : operationDone ? (starting ? '仿真系统启动完成' : '仿真系统停止完成')
                    : (starting ? '正在启动仿真系统' : '正在停止仿真系统')}
              </div>
              {startError ? <div style={{ color: '#c9d1d9' }}>{startError}</div> : (
                <>
                  {(starting ? STARTUP_STEPS : SHUTDOWN_STEPS).map((step, index) => (
                    <div key={step} style={{ color: index <= operationStep ? '#c9d1d9' : '#484f58', marginTop: 3, display: 'flex', alignItems: 'center', gap: 5 }}>
                      {index < operationStep || operationDone ? <span style={{ color: '#3fb950' }}>✓</span>
                        : index === operationStep ? <span className="startup-spinner" /> : <span>○</span>}
                      {step} · {index === operationStep && !operationDone ? (starting ? '启动中' : '停止中') : index < operationStep || operationDone ? '完成' : '等待中'}
                    </div>
                  ))}
                  <div style={{ color: '#8b949e', marginTop: 6 }}>
                    {starting ? '完成前暂停和终止操作不可用。' : '完成前启动和其他运行控制不可用。'}
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      ) : (
        <button
          onClick={toggleRunning}
          className="flex items-center gap-1.5 cursor-pointer label rounded-lg"
          style={{
            padding: '6px 14px',
            background: isRunning ? 'rgba(255,69,58,0.08)' : 'rgba(48,209,88,0.06)',
            border: isRunning ? '1px solid rgba(255,69,58,0.2)' : '1px solid rgba(48,209,88,0.2)',
            color: isRunning ? 'var(--red)' : 'var(--green)',
          }}
        >
          {isRunning ? (
            <svg width="8" height="8" viewBox="0 0 8 8">
              <rect x="1" y="1" width="2.5" height="6" rx="0.5" fill="currentColor" />
              <rect x="4.5" y="1" width="2.5" height="6" rx="0.5" fill="currentColor" />
            </svg>
          ) : (
            <svg width="8" height="8" viewBox="0 0 8 8">
              <polygon points="2,1 7,4 2,7" fill="currentColor" />
            </svg>
          )}
          {isRunning ? 'STOP' : 'START'}
        </button>
      )}

      <div style={{ width: 1, height: 20, background: 'rgba(255,255,255,0.06)' }} />

      <div className="flex items-baseline gap-2">
        <span
          className="board-xl text-[26px] leading-none tabular-nums"
          style={{ color: 'var(--text)' }}
        >
          {simTime}
        </span>
        <span className="label" style={{ color: 'var(--text-muted)' }}>SIM TIME</span>
      </div>

      <div style={{ width: 1, height: 20, background: 'rgba(255,255,255,0.06)' }} />

      <div className="flex items-center gap-1">
        <span className="label" style={{ color: 'var(--text-muted)' }}>SPD</span>
        {[
          { multiplier: 1, label: '1秒' },
          { multiplier: 10, label: '10秒' },
          { multiplier: 60, label: '1分钟' },
        ].map(({ multiplier, label }) => (
          <button
            key={multiplier}
            onClick={() => { if (canChangeSpeed) setSpeed(multiplier); }}
            disabled={!canChangeSpeed}
            title={`每现实秒推进约 ${label} 仿真时间`}
            className="h-7 min-w-9 px-1 flex items-center justify-center cursor-pointer board-num text-[10px] rounded-md"
            style={{
              background: speed === multiplier && canChangeSpeed ? 'rgba(100,210,255,0.08)' : 'transparent',
              border: speed === multiplier && canChangeSpeed ? '1px solid rgba(100,210,255,0.18)' : '1px solid transparent',
              color: canChangeSpeed && speed === multiplier ? 'var(--cyan)' : 'var(--text-muted)',
              cursor: canChangeSpeed ? 'pointer' : 'not-allowed',
              opacity: canChangeSpeed ? 1 : .45,
            }}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="flex-1" />

      <div className="flex items-center gap-2">
        <button
          onClick={showAllLines}
          className="label rounded-md cursor-pointer"
          style={{ padding: '5px 10px', color: 'var(--text-muted)', border: '1px solid rgba(255,255,255,0.06)' }}
        >
          全览
        </button>
        <button
          onClick={() => showOnlyLines(['9'])}
          className="label rounded-md cursor-pointer"
          style={{
            color: 'var(--l9)',
            border: '1px solid rgba(168,214,74,0.18)',
            background: 'rgba(168,214,74,0.06)',
            padding: '5px 10px',
          }}
        >
          9号线
        </button>
      </div>
    </div>
  );
}
