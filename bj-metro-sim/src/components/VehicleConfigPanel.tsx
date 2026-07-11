import { useState } from 'react';
import { useSimStore } from '../store/useSimStore';

const LABEL_CLASS = 'text-[10px] font-medium';
const INPUT_CLASS =
  'w-full bg-transparent border rounded-md px-2.5 py-1.5 text-[12px] tabular-nums outline-none transition-colors';
const INPUT_BORDER = 'border-white/8 focus:border-white/20';
export default function VehicleConfigPanel() {
  const vehicleConfig = useSimStore((s) => s.vehicleConfig);
  const setVehicleConfig = useSimStore((s) => s.setVehicleConfig);
  const submitVehicleConfig = useSimStore((s) => s.submitVehicleConfig);
  const startBackendSim = useSimStore((s) => s.startBackendSim);
  const setShowVehicleConfig = useSimStore((s) => s.setShowVehicleConfig);
  const vehicleConfigResponse = useSimStore((s) => s.vehicleConfigResponse);
  const backendStatus = useSimStore((s) => s.backendStatus);
  const engineClockState = useSimStore((s) => s.engineClockState);

  const [submitting, setSubmitting] = useState(false);

  const formation = vehicleConfig.formation;
  const formationCars = formation.split('-');
  const carCount = formationCars.length;

  const massInputs = vehicleConfig.carMassesKg;
  const totalMass = massInputs.reduce((a, b) => a + b, 0);

  const headCount = formationCars.filter((c) => c === 'Tc').length;
  const middleCount = carCount - headCount;
  const totalLength =
    headCount * vehicleConfig.headCarLengthM + middleCount * vehicleConfig.middleCarLengthM;

  const canStart =
    backendStatus === 'connected' && (engineClockState === 'IDLE' || engineClockState === 'STOPPED');

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      await submitVehicleConfig();
      if (canStart) {
        await startBackendSim();
      }
    } finally {
      setSubmitting(false);
    }
  };

  const setMass = (i: number, v: number) => {
    const next = [...massInputs];
    next[i] = v;
    setVehicleConfig({ carMassesKg: next });
  };

  const setFormation = (f: string) => {
    const newCars = f.split('-');
    const oldCount = formationCars.length;
    const newCount = newCars.length;
    const newMasses = [...massInputs];
    if (newCount > oldCount) {
      for (let i = oldCount; i < newCount; i++) {
        const isHead = newCars[i] === 'Tc';
        newMasses.push(isHead ? 34500 : 39000);
      }
    } else {
      newMasses.length = newCount;
    }
    setVehicleConfig({ formation: f, carMassesKg: newMasses });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: 'rgba(0,0,0,0.7)' }}>
      <div
        className="flex flex-col"
        style={{
          width: 520,
          maxHeight: '85vh',
          background: 'rgb(18,20,26)',
          border: '1px solid rgba(255,255,255,0.08)',
          borderRadius: 12,
          padding: '24px 28px',
          gap: 16,
          color: '#ddd',
        }}
      >
        {/* header */}
        <div className="flex items-center justify-between">
          <div>
            <span className="text-[15px] font-semibold" style={{ color: '#fff' }}>
              车辆参数配置
            </span>
            <span className="ml-2 chip text-[9px]" style={{ color: 'var(--cyan)', border: '1px solid rgba(100,210,255,0.15)' }}>
              LINE 9
            </span>
          </div>
          <button
            onClick={() => setShowVehicleConfig(false)}
            className="w-7 h-7 flex items-center justify-center rounded-md cursor-pointer text-[14px]"
            style={{ color: 'var(--text-muted)', border: '1px solid rgba(255,255,255,0.08)' }}
          >
            ✕
          </button>
        </div>

        {/* 列车编组 */}
        <div>
          <label className={LABEL_CLASS} style={{ color: 'var(--text-muted)' }}>列车编组</label>
          <div className="flex items-center gap-2 mt-1">
            <input
              type="text"
              value={formation}
              onChange={(e) => setFormation(e.target.value)}
              className={`${INPUT_CLASS} ${INPUT_BORDER}`}
              style={{ flex: 1, color: '#fff', background: 'rgba(255,255,255,0.03)' }}
            />
            <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
              {carCount} 辆
            </span>
          </div>
        </div>

        {/* 各编组车辆空车车重 */}
        <div>
          <div className="flex items-center justify-between">
            <label className={LABEL_CLASS} style={{ color: 'var(--text-muted)' }}>各编组车辆空车车重 (kg)</label>
            <span className="text-[10px] tabular-nums" style={{ color: 'var(--cyan)' }}>
              总计 {totalMass.toLocaleString()} kg
            </span>
          </div>
          <div className="grid grid-cols-3 gap-2 mt-1">
            {formationCars.map((car, i) => (
              <div key={i} className="flex items-center gap-1">
                <span className="text-[10px] font-mono w-8 text-center" style={{ color: car === 'Tc' ? 'var(--amber)' : 'var(--text-muted)' }}>
                  {car}
                </span>
                <input
                  type="number"
                  value={massInputs[i] ?? 0}
                  onChange={(e) => setMass(i, Number(e.target.value))}
                  className={`${INPUT_CLASS} ${INPUT_BORDER}`}
                  style={{ flex: 1, color: '#fff', background: 'rgba(255,255,255,0.03)' }}
                />
              </div>
            ))}
          </div>
        </div>

        <div className="h-px" style={{ background: 'rgba(255,255,255,0.06)' }} />

        {/* 车长 */}
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className={LABEL_CLASS} style={{ color: 'var(--text-muted)' }}>头车车长 (m)</label>
            <input
              type="number"
              step="0.01"
              value={vehicleConfig.headCarLengthM}
              onChange={(e) => setVehicleConfig({ headCarLengthM: Number(e.target.value) })}
              className={`${INPUT_CLASS} ${INPUT_BORDER} mt-1`}
              style={{ color: '#fff', background: 'rgba(255,255,255,0.03)' }}
            />
          </div>
          <div>
            <label className={LABEL_CLASS} style={{ color: 'var(--text-muted)' }}>中间车车长 (m)</label>
            <input
              type="number"
              step="0.01"
              value={vehicleConfig.middleCarLengthM}
              onChange={(e) => setVehicleConfig({ middleCarLengthM: Number(e.target.value) })}
              className={`${INPUT_CLASS} ${INPUT_BORDER} mt-1`}
              style={{ color: '#fff', background: 'rgba(255,255,255,0.03)' }}
            />
          </div>
        </div>

        {/* 车轮半径 */}
        <div>
          <label className={LABEL_CLASS} style={{ color: 'var(--text-muted)' }}>车轮半径 (m)</label>
          <input
            type="number"
            step="0.01"
            value={vehicleConfig.wheelRadiusM}
            onChange={(e) => setVehicleConfig({ wheelRadiusM: Number(e.target.value) })}
            className={`${INPUT_CLASS} ${INPUT_BORDER} mt-1`}
            style={{ maxWidth: 160, color: '#fff', background: 'rgba(255,255,255,0.03)' }}
          />
        </div>

        <div className="h-px" style={{ background: 'rgba(255,255,255,0.06)' }} />

        {/* 计算汇总 */}
        <div
          className="grid grid-cols-2 gap-3 p-3 rounded-lg"
          style={{ background: 'rgba(168,214,74,0.03)', border: '1px solid rgba(168,214,74,0.08)' }}
        >
          <div>
            <span className="text-[9px]" style={{ color: 'var(--text-muted)' }}>总质量</span>
            <div className="text-[14px] font-semibold tabular-nums" style={{ color: 'var(--l9)' }}>
              {totalMass.toLocaleString()} kg
            </div>
          </div>
          <div>
            <span className="text-[9px]" style={{ color: 'var(--text-muted)' }}>总车长</span>
            <div className="text-[14px] font-semibold tabular-nums" style={{ color: 'var(--l9)' }}>
              {totalLength.toFixed(1)} m
            </div>
          </div>
        </div>

        {/* 上次提交回显 */}
        {vehicleConfigResponse && (
          <div
            className="p-2 rounded-md text-[10px]"
            style={{ background: 'rgba(48,209,88,0.04)', border: '1px solid rgba(48,209,88,0.1)' }}
          >
            <span style={{ color: 'var(--green)' }}>已配置 </span>
            <span style={{ color: 'var(--text-muted)' }}>
              mass={vehicleConfigResponse.vehicleConfig.massKg}kg,
              length={vehicleConfigResponse.vehicleConfig.trainLengthM}m,
              wheel={vehicleConfigResponse.vehicleConfig.wheelRadiusM}m
            </span>
          </div>
        )}

        {/* 按钮 */}
        <div className="flex items-center gap-3 pt-2">
          <button
            onClick={() => setShowVehicleConfig(false)}
            className="rounded-md cursor-pointer text-[11px] font-medium px-4 py-2"
            style={{
              color: 'var(--text-muted)',
              border: '1px solid rgba(255,255,255,0.08)',
            }}
          >
            取消
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting}
            className="flex items-center gap-1.5 rounded-md cursor-pointer text-[11px] font-medium px-5 py-2"
            style={{
              color: '#fff',
              background: canStart ? 'rgba(48,209,88,0.15)' : 'rgba(100,210,255,0.12)',
              border: canStart
                ? '1px solid rgba(48,209,88,0.25)'
                : '1px solid rgba(100,210,255,0.2)',
              opacity: submitting ? 0.5 : 1,
            }}
          >
            {submitting ? (
              '提交中...'
            ) : canStart ? (
              <>
                <svg width="7" height="7" viewBox="0 0 7 7">
                  <polygon points="1.5,0.5 6,3.5 1.5,6.5" fill="currentColor" />
                </svg>
                确认并启动仿真
              </>
            ) : (
              '确认参数'
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
