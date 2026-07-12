import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { CSSProperties, FormEvent } from 'react';
import {
  connectDriverCab,
  disconnectDriverCab,
  fetchDriverCabStatus,
  type DriverCabHardwareStatus,
} from '../data/backendApi';

const EMPTY_STATUS: DriverCabHardwareStatus = {
  state: 'DISCONNECTED',
  host: '—',
  port: 8001,
  trainId: 'T0901',
  controlState: 'IDLE',
  framesReceived: 0,
  connectedAt: null,
  lastFrameAt: null,
  lastError: null,
  lastInput: null,
  lastCommand: null,
};

const PLC_PORTS = [8001, 8002, 8003];

const STATUS_COLOR: Record<DriverCabHardwareStatus['state'], string> = {
  DISCONNECTED: '#768394',
  CONNECTING: '#64d2ff',
  CONNECTED: '#30d158',
  ERROR: '#ff5d57',
};

function statusLabel(status: DriverCabHardwareStatus): string {
  if (status.state === 'CONNECTING') return '正在连接';
  if (status.state === 'ERROR') return '重试司机台';
  if (status.state === 'CONNECTED' && status.controlState === 'ACTIVE') return '司机台控制中';
  if (status.state === 'CONNECTED') return '等待 T0901';
  return '连接司机台';
}

function statusDetail(status: DriverCabHardwareStatus): string {
  if (status.state === 'ERROR') return status.lastError ?? '连接失败';
  if (status.state === 'CONNECTED' && status.controlState === 'ACTIVE') {
    const command = status.lastCommand;
    if (!command) return `${status.framesReceived} 帧`;
    if (command.emergencyBrake) return '紧急制动';
    return `T${command.tractionPercent.toFixed(0)} · B${command.brakePercent.toFixed(0)}`;
  }
  if (status.state === 'CONNECTED') return `${status.host}:${status.port}`;
  return 'T0901 · PLC 8001';
}

export default function DriverCabConnectionButton() {
  const [status, setStatus] = useState<DriverCabHardwareStatus>(EMPTY_STATUS);
  const [busy, setBusy] = useState(false);
  const timerRef = useRef<number | null>(null);

  // 连接弹窗
  const [dialogOpen, setDialogOpen] = useState(false);
  const [dialogHost, setDialogHost] = useState('127.0.0.1');
  const [dialogPort, setDialogPort] = useState(8001);

  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const response = await fetchDriverCabStatus();
        if (active && response.ok) setStatus(response.status);
      } catch {
        // 主系统状态栏已经单独显示后端可用性，此处保留最后一次硬件状态。
      } finally {
        if (active) timerRef.current = window.setTimeout(poll, 1000);
      }
    };
    void poll();
    return () => {
      active = false;
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
  }, []);

  const doConnect = useCallback(async (host: string, port: number) => {
    if (busy) return;
    setBusy(true);
    try {
      const response = await connectDriverCab(host, port);
      if (response.ok) setStatus(response.status);
    } catch (error) {
      setStatus((previous) => ({
        ...previous,
        state: 'ERROR',
        lastError: error instanceof Error ? error.message : '连接请求失败',
      }));
    } finally {
      setBusy(false);
    }
  }, [busy]);

  const handleClick = useCallback(async () => {
    if (busy || status.state === 'CONNECTING') return;
    if (status.state === 'CONNECTED') {
      // 已连接 → 直接断开
      setBusy(true);
      try {
        const response = await disconnectDriverCab();
        if (response.ok) setStatus(response.status);
      } catch (error) {
        setStatus((previous) => ({
          ...previous,
          state: 'ERROR',
          lastError: error instanceof Error ? error.message : '断开连接失败',
        }));
      } finally {
        setBusy(false);
      }
      return;
    }
    // 未连接 → 弹出输入框
    // 如果之前已连过，预填上一次的地址
    if (status.host && status.host !== '—') {
      setDialogHost(status.host);
      setDialogPort(status.port);
    }
    setDialogOpen(true);
  }, [busy, status]);

  const handleDialogSubmit = useCallback((e: FormEvent) => {
    e.preventDefault();
    setDialogOpen(false);
    doConnect(dialogHost, dialogPort);
  }, [dialogHost, dialogPort, doConnect]);

  const handleOverlayClick = useCallback((e: React.MouseEvent) => {
    if ((e.target as HTMLElement).classList.contains('cab-dialog-overlay')) {
      setDialogOpen(false);
    }
  }, []);

  const color = STATUS_COLOR[status.state];
  const active = status.state === 'CONNECTED' && status.controlState === 'ACTIVE';
  const title = status.state === 'CONNECTED'
    ? `断开司机台 · ${status.host}:${status.port}`
    : `连接司机台 · ${status.host ?? '—'}:${status.port}`;

  return (
    <>
      <button
        type="button"
        onClick={handleClick}
        disabled={busy || status.state === 'CONNECTING'}
        className={`driver-cab-link ${active ? 'driver-cab-link--active' : ''}`}
        style={{ '--cab-state': color } as CSSProperties}
        title={title}
        aria-label={title}
        aria-live="polite"
      >
        <span className="driver-cab-link__socket" aria-hidden="true">
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none">
            <path d="M5 2.5v3M11 2.5v3M4 5.5h8v2.2A4 4 0 0 1 8 11.7v1.8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
            <path d="M6.2 13.5h3.6" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
          </svg>
        </span>
        <span className="driver-cab-link__copy">
          <span className="driver-cab-link__label">{statusLabel(status)}</span>
          <span className="driver-cab-link__detail">{statusDetail(status)}</span>
        </span>
        <span className="driver-cab-link__signal" aria-hidden="true">
          <span className="driver-cab-link__dot" />
          {status.state === 'CONNECTING' ? <span className="driver-cab-link__pulse" /> : null}
        </span>
      </button>

      {dialogOpen && createPortal(
        <div className="cab-dialog-overlay" onClick={handleOverlayClick}>
          <div className="cab-dialog glass-elevated">
            <div className="cab-dialog__header">
              <span className="cab-dialog__title">连接司机台</span>
              <span className="cab-dialog__subtitle">输入 PLC 服务器地址和端口</span>
            </div>
            <form className="cab-dialog__form" onSubmit={handleDialogSubmit}>
              <label className="cab-dialog__field">
                <span className="cab-dialog__label">主机地址</span>
                <input
                  className="cab-dialog__input"
                  type="text"
                  value={dialogHost}
                  onChange={(e) => setDialogHost(e.target.value)}
                  placeholder="192.168.100.123"
                  autoFocus
                />
              </label>
              <label className="cab-dialog__field">
                <span className="cab-dialog__label">端口</span>
                <div className="cab-dialog__port-group">
                  {PLC_PORTS.map((p) => (
                    <button
                      key={p}
                      type="button"
                      className={`cab-dialog__port-btn ${dialogPort === p ? 'cab-dialog__port-btn--active' : ''}`}
                      onClick={() => setDialogPort(p)}
                    >
                      {p}
                    </button>
                  ))}
                </div>
              </label>
              <div className="cab-dialog__actions">
                <button
                  type="button"
                  className="cab-dialog__btn cab-dialog__btn--cancel"
                  onClick={() => setDialogOpen(false)}
                >
                  取消
                </button>
                <button
                  type="submit"
                  className="cab-dialog__btn cab-dialog__btn--connect"
                  disabled={!dialogHost.trim()}
                >
                  连接
                </button>
              </div>
            </form>
          </div>
        </div>,
        document.body,
      )}
    </>
  );
}
