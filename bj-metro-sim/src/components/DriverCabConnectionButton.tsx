import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { CSSProperties, FormEvent, ReactNode } from 'react';
import {
  clearHardwareLogs,
  connectDriverCab,
  connectDriverCabEndpoint,
  connectVision,
  disconnectDriverCab,
  disconnectDriverCabEndpoint,
  disconnectVision,
  fetchDriverCabStatus,
  fetchVisionStatus,
  type DriverCabEndpoint,
  type DriverCabHardwareStatus,
  type HardwareConnectionLog,
  type VisionHardwareStatus,
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
  logs: [],
};

const EMPTY_VISION_STATUS: VisionHardwareStatus = {
  state: 'DISCONNECTED',
  remoteHost: '18.32.115.28',
  remotePort: 8303,
  localHost: '0.0.0.0',
  localPort: 8303,
  intervalMs: 100,
  layout: 'compact',
  framesSent: 0,
  bytesSent: 0,
  lastFrameSize: 0,
  lastFrameAt: null,
  lastError: null,
  nextLiveCounter: 0,
  mapping: {
    protocolSignalCount: 77,
    mappedSignalCount: 0,
    protocolSwitchCount: 29,
    mappedSwitchCount: 0,
    unmappedSignalsDefault: 'RED',
    unmappedSwitchesDefault: 'NORMAL',
  },
  logs: [],
};

const PLC_PORTS = [8001, 8002, 8003];

type EndpointState = 'DISCONNECTED' | 'CONNECTING' | 'STARTING' | 'CONNECTED' | 'RETRYING' | 'ERROR';
type LogFilter = 'all' | 'plc' | 'networkScreen' | 'signalScreen' | 'vision';

const LOG_FILTERS: ReadonlyArray<{ key: LogFilter; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'plc', label: 'PLC' },
  { key: 'networkScreen', label: 'HMI' },
  { key: 'signalScreen', label: 'MMI' },
  { key: 'vision', label: 'VISION' },
];

const ENDPOINT_LABEL: Record<string, string> = {
  system: 'SYSTEM',
  plc: 'PLC',
  networkScreen: 'HMI',
  signalScreen: 'MMI',
  vision: 'VISION',
};

interface HardwareEndpointCardProps {
  code: string;
  name: string;
  transport: 'TCP' | 'UDP';
  endpoint: Exclude<LogFilter, 'all'>;
  state: EndpointState;
  host: string;
  port: number;
  frames: number;
  frameDirection: 'RX' | 'TX';
  lastFrameAt: string | null;
  lastError: string | null;
  busy: boolean;
  logSelected: boolean;
  portOptions?: number[];
  telemetryNote?: string;
  extraControls?: ReactNode;
  onHostChange: (host: string) => void;
  onPortChange?: (port: number) => void;
  onReconnect: () => void;
  onDisconnect: () => void;
  onShowLogs: () => void;
}

function endpointColor(state: string): string {
  if (state === 'CONNECTED') return '#30d158';
  if (state === 'CONNECTING' || state === 'STARTING' || state === 'RETRYING') return '#ff9f0a';
  if (state === 'ERROR') return '#ff453a';
  return '#636366';
}

function endpointStateLabel(state: EndpointState): string {
  if (state === 'CONNECTED') return '在线';
  if (state === 'CONNECTING' || state === 'STARTING') return '连接中';
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

function formatLogTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--:--:--';
  return date.toLocaleTimeString('zh-CN', { hour12: false });
}

function HardwareEndpointCard({
  code,
  name,
  transport,
  state,
  host,
  port,
  frames,
  frameDirection,
  lastFrameAt,
  lastError,
  busy,
  logSelected,
  portOptions,
  telemetryNote,
  extraControls,
  onHostChange,
  onPortChange,
  onReconnect,
  onDisconnect,
  onShowLogs,
}: HardwareEndpointCardProps) {
  const color = endpointColor(state);
  return (
    <section
      className={`cab-manager-card cab-manager-card--${state.toLowerCase()} ${logSelected ? 'cab-manager-card--log-selected' : ''}`}
      style={{ '--endpoint-state': color } as CSSProperties}
    >
      <div className="cab-manager-card__identity">
        <span className="cab-manager-card__code">{code}</span>
        <div>
          <span className="cab-manager-card__name">{name}</span>
          <span className="cab-manager-card__transport">{transport} · {port}</span>
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
        {onPortChange ? (
          <label className="cab-dialog__field cab-manager-card__port-field">
            <span className="cab-dialog__label">端口</span>
            {portOptions ? (
              <select
                className="cab-dialog__input cab-manager-card__port-select"
                value={port}
                onChange={(event) => onPortChange(Number(event.target.value))}
              >
                {portOptions.map((option) => <option key={option} value={option}>{option}</option>)}
              </select>
            ) : (
              <input
                className="cab-dialog__input"
                type="number"
                min={1}
                max={65535}
                value={port}
                onChange={(event) => onPortChange(Number(event.target.value))}
              />
            )}
          </label>
        ) : null}
      </div>

      <div className="cab-manager-card__telemetry">
        <span>{frameDirection} <b>{frames.toLocaleString()}</b> 帧</span>
        <span>{telemetryNote ?? formatFrameTime(lastFrameAt)}</span>
      </div>

      {extraControls ? <div className="cab-manager-card__options">{extraControls}</div> : null}

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
          className="cab-manager-card__action cab-manager-card__action--log"
          onClick={onShowLogs}
        >
          {logSelected ? '正在查看' : '查看日志'}
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

interface ConnectionLogPanelProps {
  logs: HardwareConnectionLog[];
  filter: LogFilter;
  autoFollow: boolean;
  clearing: boolean;
  onFilterChange: (filter: LogFilter) => void;
  onAutoFollowChange: (enabled: boolean) => void;
  onClear: () => void;
}

function ConnectionLogPanel({
  logs,
  filter,
  autoFollow,
  clearing,
  onFilterChange,
  onAutoFollowChange,
  onClear,
}: ConnectionLogPanelProps) {
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const visibleLogs = useMemo(
    () => (filter === 'all' ? logs : logs.filter((entry) => entry.endpoint === filter)),
    [filter, logs],
  );

  useEffect(() => {
    const viewport = viewportRef.current;
    if (autoFollow && viewport) viewport.scrollTop = viewport.scrollHeight;
  }, [autoFollow, visibleLogs.length]);

  return (
    <section className="hardware-log-console" aria-label="设备连接日志">
      <header className="hardware-log-console__header">
        <div>
          <span className="hardware-log-console__eyebrow">CONNECTION EVENT STREAM</span>
          <strong>设备连接日志</strong>
        </div>
        <div className="hardware-log-console__tools">
          <label className="hardware-log-console__follow">
            <input
              type="checkbox"
              checked={autoFollow}
              onChange={(event) => onAutoFollowChange(event.target.checked)}
            />
            自动跟随
          </label>
          <button type="button" disabled={clearing} onClick={onClear}>
            {clearing ? '清空中…' : '清空日志'}
          </button>
        </div>
      </header>

      <nav className="hardware-log-console__filters" aria-label="日志设备筛选">
        {LOG_FILTERS.map((item) => {
          const count = item.key === 'all'
            ? logs.length
            : logs.filter((entry) => entry.endpoint === item.key).length;
          return (
            <button
              key={item.key}
              type="button"
              className={filter === item.key ? 'is-active' : ''}
              onClick={() => onFilterChange(item.key)}
            >
              {item.label}<span>{count}</span>
            </button>
          );
        })}
      </nav>

      <div ref={viewportRef} className="hardware-log-console__viewport" role="log" aria-live="polite">
        {visibleLogs.length === 0 ? (
          <div className="hardware-log-console__empty">
            <span>NO EVENTS</span>
            当前筛选范围内还没有连接事件
          </div>
        ) : visibleLogs.map((entry) => (
          <details
            key={`${entry.endpoint}-${entry.timestamp}-${entry.sequence}`}
            className={`hardware-log-row hardware-log-row--${entry.level.toLowerCase()}`}
          >
            <summary>
              <time>{formatLogTime(entry.timestamp)}</time>
              <span className="hardware-log-row__endpoint">{ENDPOINT_LABEL[entry.endpoint] ?? entry.endpoint}</span>
              <span className="hardware-log-row__event">{entry.event}</span>
              <span className="hardware-log-row__message">{entry.message}</span>
              <span className="hardware-log-row__level">{entry.level}</span>
            </summary>
            <pre>{JSON.stringify(entry.details, null, 2)}</pre>
          </details>
        ))}
      </div>
    </section>
  );
}

function statusLabel(connectedCount: number, busy: boolean): string {
  if (connectedCount === 4) return '四路设备已连接';
  if (connectedCount > 0) return `${connectedCount}/4 路在线`;
  if (busy) return '设备连接中';
  return '管理硬件连接';
}

function statusDetail(status: DriverCabHardwareStatus, vision: VisionHardwareStatus): string {
  if (status.state === 'CONNECTED' && status.controlState === 'ATO_ACTIVE') {
    return `PLC · ATO运行 · ${status.framesReceived} RX`;
  }
  if (vision.state === 'CONNECTED') {
    return `VISION · ${vision.framesSent.toLocaleString()} TX`;
  }
  if (status.state === 'CONNECTED' && status.controlState === 'ACTIVE') {
    const command = status.lastCommand;
    if (!command) return `T0901 · ${status.framesReceived} RX`;
    if (command.emergencyBrake) return 'PLC · 紧急制动';
    return `PLC · T${command.tractionPercent.toFixed(0)} B${command.brakePercent.toFixed(0)}`;
  }
  if (status.state === 'ERROR') return 'PLC 连接故障';
  return 'PLC · HMI · MMI · VIS';
}

export default function DriverCabConnectionButton() {
  const [status, setStatus] = useState<DriverCabHardwareStatus>(EMPTY_STATUS);
  const [visionStatus, setVisionStatus] = useState<VisionHardwareStatus>(EMPTY_VISION_STATUS);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [logFilter, setLogFilter] = useState<LogFilter>('all');
  const [autoFollowLogs, setAutoFollowLogs] = useState(true);
  const timerRef = useRef<number | null>(null);

  const [dialogHost, setDialogHost] = useState('192.168.100.123');
  const [dialogPort, setDialogPort] = useState(8001);
  const [dialogNetworkScreenHost, setDialogNetworkScreenHost] = useState('192.168.100.122');
  const [dialogSignalScreenHost, setDialogSignalScreenHost] = useState('192.168.100.121');
  const [dialogVisionHost, setDialogVisionHost] = useState('18.32.115.28');
  const [dialogVisionPort, setDialogVisionPort] = useState(8303);
  const [dialogVisionLocalPort, setDialogVisionLocalPort] = useState(8303);

  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const [cabResponse, visionResponse] = await Promise.all([
          fetchDriverCabStatus(),
          fetchVisionStatus(),
        ]);
        if (active) {
          if (cabResponse.ok) setStatus(cabResponse.status);
          if (visionResponse.ok) setVisionStatus(visionResponse.status);
        }
      } catch {
        // 主状态栏负责显示后端可用性，管理器保留最近一次设备状态和日志。
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

  const logs = useMemo(
    () => [...status.logs, ...visionStatus.logs].toSorted((left, right) => {
      const timestampOrder = Date.parse(left.timestamp) - Date.parse(right.timestamp);
      return timestampOrder || left.sequence - right.sequence;
    }),
    [status.logs, visionStatus.logs],
  );

  const openManager = useCallback(() => {
    setDialogHost(status.host || '192.168.100.123');
    setDialogPort(status.port);
    setDialogNetworkScreenHost(status.networkScreen.host || status.networkScreenHost);
    setDialogSignalScreenHost(status.signalScreen.host || status.signalScreenHost);
    setDialogVisionHost(visionStatus.remoteHost || '18.32.115.28');
    setDialogVisionPort(visionStatus.remotePort);
    setDialogVisionLocalPort(visionStatus.localPort);
    setActionError(null);
    setDialogOpen(true);
  }, [status, visionStatus]);

  const runAction = useCallback(async (action: string, request: () => Promise<void>) => {
    if (busyAction) return;
    setBusyAction(action);
    setActionError(null);
    try {
      await request();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '硬件操作失败');
    } finally {
      setBusyAction(null);
    }
  }, [busyAction]);

  const handleConnectAll = useCallback((event: FormEvent) => {
    event.preventDefault();
    void runAction('all-connect', async () => {
      const [cabResponse, visionResponse] = await Promise.all([
        connectDriverCab(dialogHost, dialogPort, dialogNetworkScreenHost, dialogSignalScreenHost),
        connectVision({
          remoteHost: dialogVisionHost,
          remotePort: dialogVisionPort,
          localHost: '0.0.0.0',
          localPort: dialogVisionLocalPort,
          intervalMs: 100,
          layout: 'compact',
          primaryTrainId: 'T0901',
        }),
      ]);
      if (cabResponse.ok) setStatus(cabResponse.status);
      if (visionResponse.ok) setVisionStatus(visionResponse.status);
    });
  }, [
    dialogHost,
    dialogNetworkScreenHost,
    dialogPort,
    dialogSignalScreenHost,
    dialogVisionHost,
    dialogVisionLocalPort,
    dialogVisionPort,
    runAction,
  ]);

  const reconnectEndpoint = useCallback((endpoint: DriverCabEndpoint) => {
    const host = endpoint === 'plc'
      ? dialogHost
      : endpoint === 'network-screen'
        ? dialogNetworkScreenHost
        : dialogSignalScreenHost;
    const port = endpoint === 'plc' ? dialogPort : undefined;
    void runAction(`${endpoint}-connect`, async () => {
      const response = await connectDriverCabEndpoint(endpoint, host, port);
      if (response.ok) setStatus(response.status);
    });
  }, [dialogHost, dialogNetworkScreenHost, dialogPort, dialogSignalScreenHost, runAction]);

  const disconnectEndpoint = useCallback((endpoint: DriverCabEndpoint) => {
    void runAction(`${endpoint}-disconnect`, async () => {
      const response = await disconnectDriverCabEndpoint(endpoint);
      if (response.ok) setStatus(response.status);
    });
  }, [runAction]);

  const reconnectVision = useCallback(() => {
    void runAction('vision-connect', async () => {
      const response = await connectVision({
        remoteHost: dialogVisionHost,
        remotePort: dialogVisionPort,
        localHost: '0.0.0.0',
        localPort: dialogVisionLocalPort,
        intervalMs: 100,
        layout: 'compact',
        primaryTrainId: 'T0901',
      });
      if (response.ok) setVisionStatus(response.status);
    });
  }, [dialogVisionHost, dialogVisionLocalPort, dialogVisionPort, runAction]);

  const handleDisconnectVision = useCallback(() => {
    void runAction('vision-disconnect', async () => {
      const response = await disconnectVision();
      if (response.ok) setVisionStatus(response.status);
    });
  }, [runAction]);

  const handleDisconnectAll = useCallback(() => {
    void runAction('all-disconnect', async () => {
      const [cabResponse, visionResponse] = await Promise.all([
        disconnectDriverCab(),
        disconnectVision(),
      ]);
      if (cabResponse.ok) setStatus(cabResponse.status);
      if (visionResponse.ok) setVisionStatus(visionResponse.status);
    });
  }, [runAction]);

  const handleClearLogs = useCallback(() => {
    void runAction('logs-clear', async () => {
      const response = await clearHardwareLogs();
      if (response.ok) {
        setStatus(response.driverCab);
        setVisionStatus(response.vision);
      }
    });
  }, [runAction]);

  const handleOverlayClick = useCallback((event: React.MouseEvent) => {
    if ((event.target as HTMLElement).classList.contains('cab-dialog-overlay')) setDialogOpen(false);
  }, []);

  const connectedCount = [
    status.state === 'CONNECTED',
    status.networkScreen.state === 'CONNECTED',
    status.signalScreen.state === 'CONNECTED',
    visionStatus.state === 'CONNECTED',
  ].filter(Boolean).length;
  const connectionBusy = [
    status.state,
    status.networkScreen.state,
    status.signalScreen.state,
    visionStatus.state,
  ].some((state) => state === 'CONNECTING' || state === 'STARTING' || state === 'RETRYING');
  const hasActiveSession = [
    status.state,
    status.networkScreen.state,
    status.signalScreen.state,
    visionStatus.state,
  ].some((state) => state !== 'DISCONNECTED');
  const color = connectedCount === 4 ? '#30d158' : connectedCount > 0 ? '#ff9f0a' : endpointColor(status.state);
  const endpointStates = [
    { label: 'PLC', state: status.state },
    { label: 'HMI', state: status.networkScreen.state },
    { label: 'MMI', state: status.signalScreen.state },
    { label: 'VIS', state: visionStatus.state },
  ];
  const allHostsValid = Boolean(
    dialogHost.trim()
    && dialogNetworkScreenHost.trim()
    && dialogSignalScreenHost.trim()
    && dialogVisionHost.trim()
    && dialogVisionPort > 0
    && dialogVisionPort <= 65535
    && dialogVisionLocalPort >= 0
    && dialogVisionLocalPort <= 65535,
  );

  return (
    <>
      <button
        type="button"
        onClick={openManager}
        className={`driver-cab-link ${connectedCount > 0 ? 'driver-cab-link--active' : ''}`}
        style={{ '--cab-state': color } as CSSProperties}
        title="打开设备连接管理与连接日志"
        aria-label="打开设备连接管理与连接日志"
        aria-live="polite"
      >
        <span className="driver-cab-link__socket" aria-hidden="true">
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none">
            <path d="M5 2.5v3M11 2.5v3M4 5.5h8v2.2A4 4 0 0 1 8 11.7v1.8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
            <path d="M6.2 13.5h3.6" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
          </svg>
        </span>
        <span className="driver-cab-link__copy">
          <span className="driver-cab-link__label">{statusLabel(connectedCount, connectionBusy)}</span>
          <span className="driver-cab-link__detail">{statusDetail(status, visionStatus)}</span>
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

      {dialogOpen ? createPortal(
        <div className="cab-dialog-overlay" onClick={handleOverlayClick}>
          <div className="cab-dialog cab-manager glass-elevated" role="dialog" aria-modal="true" aria-labelledby="cab-manager-title">
            <div className="cab-dialog__header cab-manager__header">
              <div>
                <span className="cab-dialog__title" id="cab-manager-title">设备连接与链路日志</span>
                <span className="cab-dialog__subtitle">PLC 控制台 · HMI 网络屏 · MMI 信号屏 · VISION 视景控制机</span>
              </div>
              <div className="cab-manager__summary">
                <strong>{connectedCount}</strong><span>/4 ONLINE</span>
              </div>
              <button type="button" className="cab-manager__close" onClick={() => setDialogOpen(false)} aria-label="关闭">×</button>
            </div>

            <form className="cab-dialog__form" onSubmit={handleConnectAll}>
              <HardwareEndpointCard
                code="PLC"
                name="司机控制台"
                transport="TCP"
                endpoint="plc"
                state={status.state}
                host={dialogHost}
                port={dialogPort}
                frames={status.framesReceived}
                frameDirection="RX"
                lastFrameAt={status.lastFrameAt}
                lastError={status.lastError}
                busy={busyAction !== null}
                logSelected={logFilter === 'plc'}
                portOptions={PLC_PORTS}
                onHostChange={setDialogHost}
                onPortChange={setDialogPort}
                onReconnect={() => reconnectEndpoint('plc')}
                onDisconnect={() => disconnectEndpoint('plc')}
                onShowLogs={() => setLogFilter('plc')}
              />
              <HardwareEndpointCard
                code="HMI"
                name="网络状态屏"
                transport="TCP"
                endpoint="networkScreen"
                state={status.networkScreen.state}
                host={dialogNetworkScreenHost}
                port={status.networkScreen.port}
                frames={status.networkScreen.framesSent}
                frameDirection="TX"
                lastFrameAt={status.networkScreen.lastFrameAt}
                lastError={status.networkScreen.lastError}
                busy={busyAction !== null}
                logSelected={logFilter === 'networkScreen'}
                onHostChange={setDialogNetworkScreenHost}
                onReconnect={() => reconnectEndpoint('network-screen')}
                onDisconnect={() => disconnectEndpoint('network-screen')}
                onShowLogs={() => setLogFilter('networkScreen')}
              />
              <HardwareEndpointCard
                code="MMI"
                name="信号显示屏"
                transport="TCP"
                endpoint="signalScreen"
                state={status.signalScreen.state}
                host={dialogSignalScreenHost}
                port={status.signalScreen.port}
                frames={status.signalScreen.framesSent}
                frameDirection="TX"
                lastFrameAt={status.signalScreen.lastFrameAt}
                lastError={status.signalScreen.lastError}
                busy={busyAction !== null}
                logSelected={logFilter === 'signalScreen'}
                onHostChange={setDialogSignalScreenHost}
                onReconnect={() => reconnectEndpoint('signal-screen')}
                onDisconnect={() => disconnectEndpoint('signal-screen')}
                onShowLogs={() => setLogFilter('signalScreen')}
              />
              <HardwareEndpointCard
                code="VIS"
                name="三维视景控制机"
                transport="UDP"
                endpoint="vision"
                state={visionStatus.state}
                host={dialogVisionHost}
                port={dialogVisionPort}
                frames={visionStatus.framesSent}
                frameDirection="TX"
                lastFrameAt={visionStatus.lastFrameAt}
                lastError={visionStatus.lastError}
                busy={busyAction !== null}
                logSelected={logFilter === 'vision'}
                telemetryNote={`${visionStatus.lastFrameSize || 0} B · ${visionStatus.mapping.mappedSignalCount}/${visionStatus.mapping.protocolSignalCount} SIG`}
                onHostChange={setDialogVisionHost}
                onPortChange={setDialogVisionPort}
                onReconnect={reconnectVision}
                onDisconnect={handleDisconnectVision}
                onShowLogs={() => setLogFilter('vision')}
                extraControls={(
                  <>
                    <label className="cab-dialog__field">
                      <span className="cab-dialog__label">源端口</span>
                      <input
                        className="cab-dialog__input"
                        type="number"
                        min={0}
                        max={65535}
                        value={dialogVisionLocalPort}
                        onChange={(event) => setDialogVisionLocalPort(Number(event.target.value))}
                      />
                    </label>
                    <div className="cab-dialog__field">
                      <span className="cab-dialog__label">数据包</span>
                      <output className="cab-dialog__input cab-manager-card__port-select">
                        现场帧 · 154B 基准
                      </output>
                    </div>
                  </>
                )}
              />

              <ConnectionLogPanel
                logs={logs}
                filter={logFilter}
                autoFollow={autoFollowLogs}
                clearing={busyAction === 'logs-clear'}
                onFilterChange={setLogFilter}
                onAutoFollowChange={setAutoFollowLogs}
                onClear={handleClearLogs}
              />

              {actionError ? <div className="cab-manager__global-error">{actionError}</div> : null}

              <div className="cab-manager__bulk-actions">
                <button
                  type="button"
                  className="cab-dialog__btn cab-dialog__btn--cancel cab-manager__disconnect-all"
                  disabled={busyAction !== null || !hasActiveSession}
                  onClick={handleDisconnectAll}
                >
                  全部断开
                </button>
                <button
                  type="submit"
                  className="cab-dialog__btn cab-dialog__btn--connect"
                  disabled={busyAction !== null || !allHostsValid}
                >
                  {busyAction === 'all-connect' ? '正在建立四路连接…' : '全部连接'}
                </button>
              </div>
            </form>
          </div>
        </div>,
        document.body,
      ) : null}
    </>
  );
}
