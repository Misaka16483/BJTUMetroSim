import { useState } from 'react';
import { useSimStore } from '../store/useSimStore';
import type { AddTrainPayload, VehicleConfigPayload } from '../data/backendApi';

const DEF_VEHICLE: VehicleConfigPayload = {
  formation: 'Tc-M-M-M-M-Tc',
  carMassesKg: [34500, 39000, 39000, 39000, 39000, 34500],
  headCarLengthM: 20.2,
  middleCarLengthM: 19.4,
  wheelRadiusM: 0.46,
  maxSpeedMps: 22.22,
  maxTractionForceN: 300000,
  maxServiceBrakeForceN: 300000,
  emergencyBrakeForceN: 337500,
};

export default function TrainManagementPanel() {
  const trains = useSimStore((s) => s.trains);
  const trainColors = useSimStore((s) => s.trainColors);
  const trackMap = useSimStore((s) => s.trackMap);
  const addTrain = useSimStore((s) => s.addTrain);
  const removeTrain = useSimStore((s) => s.removeTrain);
  const engineClockState = useSimStore((s) => s.engineClockState);
  const backendStatus = useSimStore((s) => s.backendStatus);

  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({
    trainId: '',
    initialStationCode: '',
    direction: 'UP' as 'UP' | 'DOWN',
    operationMode: 'ATO' as 'ATO' | 'MANUAL',
    capacityPax: 600,
    initialLoadPax: 0,
    color: '#8FC31F',
  });
  const [showVehicleForm, setShowVehicleForm] = useState(false);
  const [vehicleForm, setVehicleForm] = useState<VehicleConfigPayload>({ ...DEF_VEHICLE });
  const [addError, setAddError] = useState<string | null>(null);

  const connected = backendStatus === 'connected';
  const stationOptions = trackMap?.stations.map((station) => ({
    code: station.stationCode,
    name: station.stationName,
  })) ?? [];
  let trainIdSeq = 1;
  while (trains.some((train) => train.trainId === `T09${String(trainIdSeq).padStart(2, '0')}`)) {
    trainIdSeq += 1;
  }

  const handleAdd = async () => {
    setAddError(null);
    const id = form.trainId || `T09${String(trainIdSeq).padStart(2, '0')}`;
    const station = form.initialStationCode || stationOptions[0]?.code || 'GGZ';
    const payload: AddTrainPayload = {
      trainId: id,
      initialStationCode: station,
      direction: form.direction,
      operationMode: form.operationMode,
      capacityPax: form.capacityPax,
      initialLoadPax: form.initialLoadPax,
      color: form.color,
    };
    if (showVehicleForm) {
      payload.vehicleConfig = vehicleForm;
    }
    try {
      const ok = await addTrain(payload);
      if (ok) {
        setShowForm(false);
        setForm((f) => ({
          ...f,
          trainId: '',
        }));
      } else {
        setAddError('添加失败：请检查列车ID、起点站和载客参数');
      }
    } catch (error) {
      setAddError(error instanceof Error ? `添加失败：${error.message}` : '添加失败：后端不可用');
    }
  };

  const handleDelete = async (id: string) => {
    await removeTrain(id);
  };

  return (
    <div
      className="flex flex-col"
      style={{
        padding: 12,
        background: 'rgba(22,27,34,0.95)',
        border: '1px solid #21262d',
        borderRadius: 8,
        height: '100%',
        overflow: 'auto',
      }}
    >
      <div className="flex items-center justify-between mb-3 shrink-0">
        <div className="flex items-center gap-2">
          <span
            className="w-2 h-2 rounded-full"
            style={{ background: connected && engineClockState === 'RUNNING' ? 'var(--green)' : '#484f58' }}
          />
          <span style={{ fontSize: 12, fontWeight: 600, color: '#c9d1d9' }}>
            列车管理
          </span>
          <span
            style={{
              fontSize: 10,
              color: '#58a6ff',
              background: 'rgba(88,166,255,0.08)',
              borderRadius: 4,
              padding: '1px 6px',
            }}
          >
            {trains.length}
          </span>
        </div>
        <button
          onClick={() => { setShowForm((v) => !v); setShowVehicleForm(false); setAddError(null); }}
          disabled={!connected}
          style={{
            fontSize: 10,
            fontWeight: 600,
            color: connected ? '#58a6ff' : '#484f58',
            background: connected ? 'rgba(88,166,255,0.08)' : 'rgba(255,255,255,0.02)',
            border: `1px solid ${connected ? 'rgba(88,166,255,0.2)' : 'rgba(255,255,255,0.04)'}`,
            borderRadius: 4,
            padding: '3px 10px',
            cursor: connected ? 'pointer' : 'default',
          }}
        >
          + 添加
        </button>
      </div>

      {showForm && (
        <div
          className="shrink-0 mb-3"
          style={{
            padding: 10,
            background: 'rgba(13,17,23,0.8)',
            border: '1px solid #30363d',
            borderRadius: 6,
          }}
        >
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
            <Field label="列车ID" small>
              <input
                value={form.trainId}
                onChange={(e) => setForm({ ...form, trainId: e.target.value })}
                placeholder={`T09${String(trainIdSeq).padStart(2, '0')}`}
                style={inputStyle}
              />
            </Field>
            <Field label="起点站" small>
              <select
                value={form.initialStationCode}
                onChange={(e) => setForm({ ...form, initialStationCode: e.target.value })}
                style={inputStyle}
              >
                {stationOptions.map((station) => (
                  <option key={station.code} value={station.code}>
                    {station.name} ({station.code})
                  </option>
                ))}
              </select>
            </Field>
            <Field label="方向" small>
              <select
                value={form.direction}
                onChange={(e) => setForm({ ...form, direction: e.target.value as 'UP' | 'DOWN' })}
                style={inputStyle}
              >
                <option value="UP">上行 (UP)</option>
                <option value="DOWN">下行 (DOWN)</option>
              </select>
            </Field>
            <Field label="驾驶模式" small>
              <select
                value={form.operationMode}
                onChange={(e) => setForm({ ...form, operationMode: e.target.value as 'ATO' | 'MANUAL' })}
                style={inputStyle}
              >
                <option value="ATO">ATO (自动驾驶)</option>
                <option value="MANUAL">MANUAL (手动驾驶)</option>
              </select>
            </Field>
            <Field label="图例颜色" small>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <input
                  type="color"
                  value={form.color}
                  onChange={(e) => setForm({ ...form, color: e.target.value })}
                  style={{ width: 28, height: 24, padding: 0, border: '1px solid #30363d', borderRadius: 4, cursor: 'pointer', background: 'none' }}
                />
                <span style={{ fontSize: 9, color: '#8b949e', fontFamily: 'monospace' }}>{form.color}</span>
              </div>
            </Field>
            <Field label="定员" small>
              <input
                type="number"
                value={form.capacityPax}
                onChange={(e) => setForm({ ...form, capacityPax: Number(e.target.value) })}
                style={inputStyle}
              />
            </Field>
            <Field label="初始载客" small>
              <input
                type="number"
                value={form.initialLoadPax}
                onChange={(e) => setForm({ ...form, initialLoadPax: Number(e.target.value) })}
                style={inputStyle}
              />
            </Field>
          </div>

          <div style={{ marginTop: 8 }}>
            <button
              onClick={() => setShowVehicleForm((v) => !v)}
              style={{
                fontSize: 10,
                color: '#8b949e',
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                padding: 0,
              }}
            >
              {showVehicleForm ? '- 隐藏车辆参数' : '+ 车辆参数配置'}
            </button>
          </div>

          {showVehicleForm && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginTop: 6 }}>
              <Field label="编组" small>
                <input value={vehicleForm.formation} onChange={(e) => setVehicleForm({ ...vehicleForm, formation: e.target.value })} style={inputStyle} />
              </Field>
              <Field label="最高速度(m/s)" small>
                <input type="number" value={vehicleForm.maxSpeedMps} onChange={(e) => setVehicleForm({ ...vehicleForm, maxSpeedMps: Number(e.target.value) })} style={inputStyle} />
              </Field>
              <Field label="最大牵引力(N)" small>
                <input type="number" value={vehicleForm.maxTractionForceN} onChange={(e) => setVehicleForm({ ...vehicleForm, maxTractionForceN: Number(e.target.value) })} style={inputStyle} />
              </Field>
              <Field label="最大制动(N)" small>
                <input type="number" value={vehicleForm.maxServiceBrakeForceN} onChange={(e) => setVehicleForm({ ...vehicleForm, maxServiceBrakeForceN: Number(e.target.value) })} style={inputStyle} />
              </Field>
              <Field label="轮径(m)" small>
                <input type="number" step="0.01" value={vehicleForm.wheelRadiusM} onChange={(e) => setVehicleForm({ ...vehicleForm, wheelRadiusM: Number(e.target.value) })} style={inputStyle} />
              </Field>
              <Field label="紧急制动(N)" small>
                <input type="number" value={vehicleForm.emergencyBrakeForceN} onChange={(e) => setVehicleForm({ ...vehicleForm, emergencyBrakeForceN: Number(e.target.value) })} style={inputStyle} />
              </Field>
            </div>
          )}

          {addError && (
            <div style={{ marginTop: 8, fontSize: 10, color: '#f85149' }}>
              {addError}
            </div>
          )}

          <div className="flex justify-end gap-2 mt-3">
            <button
              onClick={() => { setShowForm(false); setShowVehicleForm(false); }}
              style={{
                fontSize: 10, color: '#8b949e', background: 'none', border: '1px solid #30363d',
                borderRadius: 4, padding: '3px 8px', cursor: 'pointer',
              }}
            >
              取消
            </button>
            <button
              onClick={handleAdd}
              style={{
                fontSize: 10, fontWeight: 600, color: '#fff', background: '#238636',
                border: 'none', borderRadius: 4, padding: '3px 12px', cursor: 'pointer',
              }}
            >
              确认添加
            </button>
          </div>
        </div>
      )}

      <div className="flex-1" style={{ minHeight: 0, overflowY: 'auto' }}>
        {trains.length === 0 ? (
          <div style={{ textAlign: 'center', color: '#484f58', fontSize: 11, padding: 20 }}>
            暂无列车 — 点击「+ 添加」添加列车
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {trains.map((t) => {
              const color = trainColors[t.trainId] || '#8FC31F';
              return (
                <div
                  key={t.trainId}
                  style={{
                    padding: '6px 8px',
                    borderRadius: 4,
                    background: 'rgba(255,255,255,0.01)',
                    border: '1px solid rgba(48,54,61,0.6)',
                  }}
                >
                  <div className="flex items-center justify-between">
                    <span style={{ fontSize: 11, fontWeight: 600, color }}>
                      {t.trainId}
                    </span>
                    <div className="flex items-center gap-1">
                      <span style={{
                        fontSize: 9,
                        color: t.operationMode === 'MANUAL' ? '#d29922' : '#58a6ff',
                        background: t.operationMode === 'MANUAL' ? 'rgba(210,153,34,0.12)' : 'rgba(88,166,255,0.12)',
                        borderRadius: 3, padding: '1px 4px',
                      }}>
                        {t.operationMode === 'MANUAL' ? 'RM' : 'ATO'}
                      </span>
                      <span style={{ fontSize: 9, color: '#8b949e' }}>
                        {t.speedMps.toFixed(1)} m/s
                      </span>
                      <button
                        onClick={() => handleDelete(t.trainId)}
                        style={{
                          fontSize: 9, color: '#f85149', background: 'none',
                          border: 'none', cursor: 'pointer', padding: 0, marginLeft: 4,
                        }}
                      >
                        删除
                      </button>
                    </div>
                  </div>
                  <div style={{ fontSize: 10, color: '#6b7280', marginTop: 2 }}>
                    {t.currentStation} → {t.nextStation}
                  </div>
                  <div style={{ fontSize: 9, color: '#484f58', marginTop: 1 }}>
                    {t.direction === 'UP' ? '上行' : '下行'} · {t.onboardPax}/{t.capacityPax} 人 · {t.phase}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function Field({ label, children, small }: { label: string; children: React.ReactNode; small?: boolean }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <span style={{ fontSize: small ? 9 : 10, color: '#8b949e', fontWeight: 500 }}>{label}</span>
      {children}
    </label>
  );
}

const inputStyle: React.CSSProperties = {
  fontSize: 10,
  padding: '4px 6px',
  background: '#0d1117',
  border: '1px solid #30363d',
  borderRadius: 4,
  color: '#c9d1d9',
  outline: 'none',
  width: '100%',
  boxSizing: 'border-box',
};
