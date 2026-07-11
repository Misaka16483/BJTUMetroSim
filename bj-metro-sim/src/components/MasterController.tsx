import { useState, useCallback, useRef, useEffect } from 'react';
import { useSimStore } from '../store/useSimStore';

export default function MasterController() {
  const manualTraction = useSimStore((s) => s.manualTraction);
  const manualBrake = useSimStore((s) => s.manualBrake);
  const sendManualCommand = useSimStore((s) => s.sendManualCommand);

  const barRef = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState(false);

  const TRACK_H = 280;
  const HANDLE_H = 36;
  const NEUTRAL_Y = TRACK_H / 2 - HANDLE_H / 2;

  const valueFromY = useCallback(
    (y: number) => {
      const clamped = Math.max(0, Math.min(y, TRACK_H - HANDLE_H));
      const ratio = (NEUTRAL_Y - clamped) / (NEUTRAL_Y);
      if (ratio >= 0) return { traction: Math.round(ratio * 100), brake: 0 };
      const brakeVal = Math.max(0, Math.min(100, Math.round(((clamped - NEUTRAL_Y) / (TRACK_H - HANDLE_H - NEUTRAL_Y)) * 100)));
      return { traction: 0, brake: brakeVal > 0 ? brakeVal : 0 };
    },
    [TRACK_H, HANDLE_H, NEUTRAL_Y],
  );

  const handleStart = useCallback(
    (clientY: number) => {
      setDragging(true);
      const rect = barRef.current?.getBoundingClientRect();
      if (!rect) return;
      const y = clientY - rect.top;
      const { traction, brake } = valueFromY(y);
      sendManualCommand(traction, brake);
    },
    [valueFromY, sendManualCommand],
  );

  const handleMove = useCallback(
    (clientY: number) => {
      if (!dragging) return;
      const rect = barRef.current?.getBoundingClientRect();
      if (!rect) return;
      const y = clientY - rect.top;
      const { traction, brake } = valueFromY(y);
      sendManualCommand(traction, brake);
    },
    [dragging, valueFromY, sendManualCommand],
  );

  const handleEnd = useCallback(() => {
    setDragging(false);
  }, []);

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => handleMove(e.clientY);
    const onMouseUp = () => handleEnd();
    const onTouchMove = (e: TouchEvent) => handleMove(e.touches[0].clientY);
    const onTouchEnd = () => handleEnd();
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
    window.addEventListener('touchmove', onTouchMove, { passive: false });
    window.addEventListener('touchend', onTouchEnd);
    return () => {
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
      window.removeEventListener('touchmove', onTouchMove);
      window.removeEventListener('touchend', onTouchEnd);
    };
  }, [handleMove, handleEnd]);

  const tractionActive = manualTraction > 0;
  const brakeActive = manualBrake > 0;

  const handleY = tractionActive
    ? NEUTRAL_Y - (manualTraction / 100) * NEUTRAL_Y
    : brakeActive
      ? NEUTRAL_Y + (manualBrake / 100) * (TRACK_H - HANDLE_H - NEUTRAL_Y)
      : NEUTRAL_Y;

  return (
    <div className="flex flex-col items-center select-none" style={{ gap: 8 }}>
      <span className="text-[7px] font-semibold uppercase tracking-[0.14em] text-[#6b7280]">PWR</span>

      <div
        ref={barRef}
        className="relative cursor-pointer"
        style={{
          width: 48,
          height: TRACK_H,
          background: 'rgba(255,255,255,0.02)',
          border: '1px solid rgba(255,255,255,0.06)',
          borderRadius: 10,
          overflow: 'hidden',
          touchAction: 'none',
        }}
        onMouseDown={(e) => handleStart(e.clientY)}
        onTouchStart={(e) => { handleStart(e.touches[0].clientY); e.preventDefault(); }}
      >
        {/* 牵引区域 */}
        <div
          style={{
            position: 'absolute', top: 0, left: 0, right: 0,
            height: NEUTRAL_Y,
            background: `linear-gradient(to bottom, rgba(34,197,94,0.12), rgba(34,197,94,0.02))`,
          }}
        />
        {/* 制动区域 */}
        <div
          style={{
            position: 'absolute', bottom: 0, left: 0, right: 0,
            height: TRACK_H - HANDLE_H - NEUTRAL_Y,
            background: `linear-gradient(to top, rgba(239,68,68,0.12), rgba(239,68,68,0.02))`,
          }}
        />

        {/* 中线 */}
        <div style={{ position: 'absolute', top: NEUTRAL_Y + HANDLE_H / 2, left: 0, right: 0, height: 1, background: 'rgba(255,255,255,0.08)' }} />

        {/* 手柄 */}
        <div
          style={{
            position: 'absolute',
            left: 4,
            right: 4,
            height: HANDLE_H,
            top: handleY,
            background: tractionActive
              ? 'linear-gradient(180deg, rgba(34,197,94,0.4), rgba(34,197,94,0.15))'
              : brakeActive
                ? 'linear-gradient(180deg, rgba(239,68,68,0.4), rgba(239,68,68,0.15))'
                : 'rgba(255,255,255,0.08)',
            border: tractionActive
              ? '1px solid rgba(34,197,94,0.3)'
              : brakeActive
                ? '1px solid rgba(239,68,68,0.3)'
                : '1px solid rgba(255,255,255,0.1)',
            borderRadius: 6,
            transition: dragging ? 'none' : 'top 150ms ease-out',
          }}
        >
          <div
            style={{
              position: 'absolute',
              inset: 3,
              background: tractionActive
                ? 'rgba(34,197,94,0.2)'
                : brakeActive
                  ? 'rgba(239,68,68,0.2)'
                  : 'rgba(255,255,255,0.03)',
              borderRadius: 4,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
          >
            <span
              className="text-[9px] font-bold font-mono"
              style={{
                color: tractionActive ? '#22c55e' : brakeActive ? '#ef4444' : '#6b7280',
              }}
            >
              {manualTraction > 0 ? `T${manualTraction}` : manualBrake > 0 ? `B${manualBrake}` : 'N'}
            </span>
          </div>
        </div>

        {/* 刻度 */}
        {[0, 25, 50, 75, 100].map((v) => (
          <div key={`t-${v}`} style={{
            position: 'absolute', left: 36, top: NEUTRAL_Y - (v / 100) * NEUTRAL_Y - 4,
            fontSize: 7, color: '#475569', fontFamily: 'monospace',
          }}>{v}</div>
        ))}
        {[0, 25, 50, 75, 100].map((v) => (
          <div key={`b-${v}`} style={{
            position: 'absolute', left: 36, top: NEUTRAL_Y + HANDLE_H + (v / 100) * (TRACK_H - HANDLE_H - NEUTRAL_Y) - 4,
            fontSize: 7, color: '#475569', fontFamily: 'monospace',
          }}>{v}</div>
        ))}
      </div>

      <span className="text-[7px] font-semibold uppercase tracking-[0.14em] text-[#6b7280]">BRK</span>
    </div>
  );
}
