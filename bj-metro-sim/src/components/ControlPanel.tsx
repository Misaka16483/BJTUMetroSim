import { useEffect } from 'react';
import { useSimStore } from '../store/useSimStore';

export default function ControlPanel() {
  const {
    isRunning, toggleRunning, speed, setSpeed,
    simTime, showOnlyLines, showAllLines, tick,
  } = useSimStore();

  useEffect(() => {
    if (!isRunning) return;
    const interval = setInterval(tick, 100);
    return () => clearInterval(interval);
  }, [isRunning, tick]);

  return (
    <div className="glass shrink-0 flex items-center gap-4 px-5" style={{ height: 50 }}>
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
        {[1, 2, 5, 10].map((x) => (
          <button
            key={x}
            onClick={() => setSpeed(x)}
            className="w-8 h-7 flex items-center justify-center cursor-pointer board-num text-[10px] rounded-md"
            style={{
              background: speed === x ? 'rgba(100,210,255,0.08)' : 'transparent',
              border: speed === x ? '1px solid rgba(100,210,255,0.18)' : '1px solid transparent',
              color: speed === x ? 'var(--cyan)' : 'var(--text-muted)',
            }}
          >
            {x}x
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
