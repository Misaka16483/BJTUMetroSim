import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { CSSProperties, FormEvent } from 'react';
import {
  connectDriverCab,
  connectDriverCabEndpoint,
  disconnectDriverCab,
  disconnectDriverCabEndpoint,
  fetchDriverCabStatus,
  type DriverCabEndpoint,
  type DriverCabHardwareStatus,
} from '../data/backendApi';

const EMPTY_STATUS: DriverCabHardwareStatus = {
  state: 'DISCONNECTED',
  host: '192.168.100.123',
  port: 8001,
  trainId: 'T0901',
  controlState: 'IDLE',
  framesReceived: 0,
  connectedAt: null,
  lastFrameAt: null,
  lastError: null,
  lastInput: null,
  lastCommand: null,
  plcOutput: {
    atoAvailable: false,
    atoActive: false,
    frameLength: 26,
    speedCmps: null,
  },
  networkScreenHost: '192.168.100.122',
  networkScreenPort: 8888,
  signalScreenHost: '192.168.100.121',
  signalScreenPort: 9999,
  networkScreen: {
    state: 'DISCONNECTED',
    host: '192.168.100.122',
    port: 8888,
    framesSent: 0,
    connectedAt: null,
    lastFrameAt: null,
    lastError: null,
  },
  signalScreen: {
    state: 'DISCONNECTED',
    host: '192.168.100.121',
    port: 9999,
    framesSent: 0,
    connectedAt: null,
    lastFrameAt: null,
    lastError: null,
  },
};

const PLC_PORTS = [8001, 8002, 8003];

const STATUS_COLOR: Record<DriverCabHardwareStatus['state'], string> = {
  DISCONNECTED: '#768394',
  CONNECTING: '#64d2ff',
  CONNECTED: '#30d158',
  ERROR: '#ff5d57',
};

type EndpointState = 'DISCONNECTED' | 'CONNECTING' | 'CONNECTED' | 'RETRYING' | 'ERROR';

interface HardwareEndpointCardProps {
  code: string;
  name: string;
  endpoint: DriverCabEndpoint;
  state: EndpointState;
  host: string;
  port: number;
  frames: number;
  frameDirection: 'RX' | 'TX';
  lastFrameAt: string | null;
  lastError: string | null;
  busy: boolean;
  portOptions?: number[];
  onHostChange: (host: string) => void;
  onPortChange?: (port: number) => void;
  onReconnect: () => void;
  onDisconnect: () => void;
}

function endpointColor(state: string): string {
  if (state === 'CONNECTED') return '#30d158';
  if (state === 'CONNECTING' || state === 'RETRYING') return '#ff9f0a';
  if (state === 'ERROR') return '#ff453a';
  return '#636366';
}

function endpointStateLabel(state: EndpointState): string {
  if (state === 'CONNECTED') return '在线';
  if (state === 'CONNECTING') return '连接中';
  if (state === 'RETRYING') return '重连中';
  if (state === 'ERROR') return '故障';
  return '离线';
}

function formatFrameTime(value: string | null): string {
  if (!value) return '尚无通信';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '时间未知';
  return `最近 ${date.toLocaleTimeString('zh-CN', { hour12: false })}`;
}

function statusLabel(status: DriverCabHardwareStatus): string {
  const connectedCount = [
    status.state === 'CONNECTED',
    status.networkScreen.state === 'CONNECTED',
    status.signalScreen.state === 'CONNECTED',
  ].filter(Boolean).length;
  if (connectedCount === 3) return '三路硬件已连接';
  if (connectedCount > 0) return `${connectedCount}/3 路在线`;
  if (
    status.state === 'CONNECTING'
    || status.networkScreen.state === 'CONNECTING'
    || status.signalScreen.state === 'CONNECTING'
    || status.networkScreen.state === 'RETRYING'
    || status.signalScreen.state === 'RETRYING'
  ) return '硬件连接中';
  return '管理硬件连接';
}

function statusDetail(status: DriverCabHardwareStatus): string {
  if (status.state === 'CONNECTED' && status.controlState === 'ATO_ACTIVE') {
    return `PLC · ATO运行 · ${status.framesReceived} RX`;
  }
  if (status.state === 'CONNECTED' && status.controlState === 'ACTIVE') {
    const command = status.lastCommand;
    if (!command) return `T0901 · ${status.framesReceived} RX`;
    if (command.emergencyBrake) return 'PLC · 紧急制动';
    return `PLC · T${command.tractionPercent.toFixed(0)} B${command.brakePercent.toFixed(0)}`;
  }
  if (status.state === 'ERROR') return 'PLC 连接故障';
  return 'PLC · HMI · MMI';
}

function HardwareEndpointCard({
  code,
  name,
  state,
  host,
  port,
  frames,
  frameDirection,
  lastFrameAt,
  lastError,
  busy,
  portOptions,
  onHostChange,
  onPortChange,
  onReconnect,
  onDisconnect,
}: HardwareEndpointCardProps) {
  const color = endpointColor(state);
  return (
    <section
      className={`cab-manager-card cab-manager-card--${state.toLowerCase()}`}
      style={{ '--endpoint-state': color } as CSSProperties}
    >
      <div className="cab-manager-card__identity">
        <span className="cab-manager-card__code">{code}</span>
        <div>
          <span className="cab-manager-card__name">{name}</span>
          <span className="cab-manager-card__transport">TCP · {port}</span>
        </div>
      </div>

      <div className="cab-manager-card__status">
        <span className="cab-manager-card__status-dot" />
        <span>{endpointStateLabel(state)}</span>
      </div>

      <div className="cab-manager-card__address">
        <label className="cab-dialog__field">
          <span className="cab-dialog__label">IP 地址</span>
          <input
            className="cab-dialog__input"
            type="text"
            value={host}
            onChange={(event) => onHostChange(event.target.value)}
            spellCheck={false}
          />
        </label>
        {portOptions && onPortChange ? (
          <label className="cab-dialog__field cab-manager-card__port-field">
            <span className="cab-dialog__label">端口</span>
            <select
              className="cab-dialog__input cab-manager-card__port-select"
              value={port}
              onChange={(event) => onPortChange(Number(event.target.value))}
            >
              {portOptions.map((option) => <option key={option} value={option}>{option}</option>)}
            </select>
          </label>
        ) : null}
      </div>

      <div className="cab-manager-card__telemetry">
        <span>{frameDirection} <b>{frames.toLocaleString()}</b> 帧</span>
        <span>{formatFrameTime(lastFrameAt)}</span>
      </div>

      <div className="cab-manager-card__actions">
        <button
          type="button"
          className="cab-manager-card__action cab-manager-card__action--primary"
          disabled={busy || !host.trim()}
          onClick={onReconnect}
        >
          {state === 'DISCONNECTED' ? '连接' : '重新连接'}
        </button>
        <button
          type="button"
          className="cab-manager-card__action cab-manager-card__action--danger"
          disabled={busy || state === 'DISCONNECTED'}
          onClick={onDisconnect}
        >
          断开
        </button>
      </div>

      {lastError ? <div className="cab-manager-card__error" title={lastError}>{lastError}</div> : null}
    </section>
  );
}

export default function DriverCabConnectionButton() {
  const [status, setStatus] = useState<DriverCabHardwareStatus>(EMPTY_STATUS);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const timerRef = useRef<number | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [dialogHost, setDialogHost] = useState('192.168.100.123');
  const [dialogPort, setDialogPort] = useState(8001);
  const [dialogNetworkScreenHost, setDialogNetworkScreenHost] = useState('192.168.100.122');
  const [dialogSignalScreenHost, setDialogSignalScreenHost] = useState('192.168.100.121');

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

  const openManager = useCallback(() => {
    setDialogHost(status.host || '192.168.100.123');
    setDialogPort(status.port);
    setDialogNetworkScreenHost(status.networkScreen.host || status.networkScreenHost);
    setDialogSignalScreenHost(status.signalScreen.host || status.signalScreenHost);
    setActionError(null);
    setDialogOpen(true);
  }, [status]);

  const runAction = useCallback(async (
    action: string,
    request: () => Promise<{ ok: boolean; status: DriverCabHardwareStatus }>,
  ) => {
    if (busyAction) return;
    setBusyAction(action);
    setActionError(null);
    try {
      const response = await request();
      if (response.ok) setStatus(response.status);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '硬件操作失败');
    } finally {
      setBusyAction(null);
    }
  }, [busyAction]);

  const handleConnectAll = useCallback((event: FormEvent) => {
    event.preventDefault();
    void runAction('all-connect', () => connectDriverCab(
      dialogHost,
      dialogPort,
      dialogNetworkScreenHost,
      dialogSignalScreenHost,
    ));
  }, [dialogHost, dialogNetworkScreenHost, dialogPort, dialogSignalScreenHost, runAction]);

  const reconnectEndpoint = useCallback((endpoint: DriverCabEndpoint) => {
    const host = endpoint === 'plc'
      ? dialogHost
      : endpoint === 'network-screen'
        ? dialogNetworkScreenHost
        : dialogSignalScreenHost;
    const port = endpoint === 'plc' ? dialogPort : undefined;
    void runAction(`${endpoint}-connect`, () => connectDriverCabEndpoint(endpoint, host, port));
  }, [dialogHost, dialogNetworkScreenHost, dialogPort, dialogSignalScreenHost, runAction]);

  const disconnectEndpoint = useCallback((endpoint: DriverCabEndpoint) => {
    void runAction(`${endpoint}-disconnect`, () => disconnectDriverCabEndpoint(endpoint));
  }, [runAction]);

  const handleOverlayClick = useCallback((event: React.MouseEvent) => {
    if ((event.target as HTMLElement).classList.contains('cab-dialog-overlay')) setDialogOpen(false);
  }, []);

  const connectedCount = [
    status.state === 'CONNECTED',
    status.networkScreen.state === 'CONNECTED',
    status.signalScreen.state === 'CONNECTED',
  ].filter(Boolean).length;
  const hasActiveSession = [
    status.state,
    status.networkScreen.state,
    status.signalScreen.state,
  ].some((state) => state !== 'DISCONNECTED');
  const color = connectedCount === 3 ? '#30d158' : connectedCount > 0 ? '#ff9f0a' : STATUS_COLOR[status.state];
  const endpointStates = [
    { label: 'PLC', state: status.state },
    { label: 'HMI', state: status.networkScreen.state },
    { label: 'MMI', state: status.signalScreen.state },
  ];
  const allHostsValid = Boolean(
    dialogHost.trim() && dialogNetworkScreenHost.trim() && dialogSignalScreenHost.trim(),
  );

  return (
    <>
      <button
        type="button"
        onClick={openManager}
        className={`driver-cab-link ${connectedCount > 0 ? 'driver-cab-link--active' : ''}`}
        style={{ '--cab-state': color } as CSSProperties}
        title="打开司机台硬件连接管理"
        aria-label="打开司机台硬件连接管理"
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
        <span className="driver-cab-link__signals" aria-hidden="true">
          {endpointStates.map((endpoint) => (
            <span
              key={endpoint.label}
              className="driver-cab-link__endpoint-dot"
              style={{ '--endpoint-state': endpointColor(endpoint.state) } as CSSProperties}
            />
          ))}
        </span>
      </button>

      {dialogOpen && createPortal(
        <div className="cab-dialog-overlay" onClick={handleOverlayClick}>
          <div className="cab-dialog cab-manager glass-elevated" role="dialog" aria-modal="true" aria-labelledby="cab-manager-title">
            <div className="cab-dialog__header cab-manager__header">
              <div>
                <span className="cab-dialog__title" id="cab-manager-title">硬件连接管理</span>
                <span className="cab-dialog__subtitle">PLC 控制台 · HMI 网络屏 · MMI 信号屏</span>
              </div>
              <div className="cab-manager__summary">
                <strong>{connectedCount}</strong><span>/3 ONLINE</span>
              </div>
              <button type="button" className="cab-manager__close" onClick={() => setDialogOpen(false)} aria-label="关闭">×</button>
            </div>

            <form className="cab-dialog__form" onSubmit={handleConnectAll}>
              <HardwareEndpointCard
                code="PLC"
                name="司机控制台"
                endpoint="plc"
                state={status.state}
                host={dialogHost}
                port={dialogPort}
                frames={status.framesReceived}
                frameDirection="RX"
                lastFrameAt={status.lastFrameAt}
                lastError={status.lastError}
                busy={busyAction !== null}
                portOptions={PLC_PORTS}
                onHostChange={setDialogHost}
                onPortChange={setDialogPort}
                onReconnect={() => reconnectEndpoint('plc')}
                onDisconnect={() => disconnectEndpoint('plc')}
              />
              <HardwareEndpointCard
                code="HMI"
                name="网络状态屏"
                endpoint="network-screen"
                state={status.networkScreen.state}
                host={dialogNetworkScreenHost}
                port={status.networkScreen.port}
                frames={status.networkScreen.framesSent}
                frameDirection="TX"
                lastFrameAt={status.networkScreen.lastFrameAt}
                lastError={status.networkScreen.lastError}
                busy={busyAction !== null}
                onHostChange={setDialogNetworkScreenHost}
                onReconnect={() => reconnectEndpoint('network-screen')}
                onDisconnect={() => disconnectEndpoint('network-screen')}
              />
              <HardwareEndpointCard
                code="MMI"
                name="信号显示屏"
                endpoint="signal-screen"
                state={status.signalScreen.state}
                host={dialogSignalScreenHost}
                port={status.signalScreen.port}
                frames={status.signalScreen.framesSent}
                frameDirection="TX"
                lastFrameAt={status.signalScreen.lastFrameAt}
                lastError={status.signalScreen.lastError}
                busy={busyAction !== null}
                onHostChange={setDialogSignalScreenHost}
                onReconnect={() => reconnectEndpoint('signal-screen')}
                onDisconnect={() => disconnectEndpoint('signal-screen')}
              />

              {actionError ? <div className="cab-manager__global-error">{actionError}</div> : null}

              <div className="cab-manager__bulk-actions">
                <button
                  type="button"
                  className="cab-dialog__btn cab-dialog__btn--cancel cab-manager__disconnect-all"
                  disabled={busyAction !== null || !hasActiveSession}
                  onClick={() => void runAction('all-disconnect', disconnectDriverCab)}
                >
                  全部断开
                </button>
                <button
                  type="submit"
                  className="cab-dialog__btn cab-dialog__btn--connect"
                  disabled={busyAction !== null || !allHostsValid}
                >
                  {busyAction === 'all-connect' ? '正在建立三路连接…' : '全部连接'}
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
