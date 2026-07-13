from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from app.adapters.binary import TcpFrameClient
from app.adapters.connection_log import ConnectionEventLog
from app.adapters.cab.mitsubishi_plc import (
    MitsubishiPlcCabInputState,
    MitsubishiPlcCabOutputFrameBuilder,
    MitsubishiPlcCabOutputState,
    MitsubishiPlcTcpClient,
)
from app.adapters.hmi import NetworkScreenFrameBuilder, NetworkScreenState
from app.adapters.mmi import SignalScreenFrameBuilder, SignalScreenState
from app.domain.control import CabControlService


class ManualControlEngine(Protocol):
    def snapshot(self) -> Any: ...

    def set_manual_mode(self, train_id: str, enabled: bool) -> dict[str, Any]: ...

    def set_manual_command(
        self,
        train_id: str,
        traction_percent: float,
        brake_percent: float,
        emergency_brake: bool = False,
    ) -> dict[str, Any]: ...


ClientFactory = Callable[[str, int, float], MitsubishiPlcTcpClient]
DisplayClientFactory = Callable[[str, int, float], TcpFrameClient]


def _default_client_factory(host: str, port: int, timeout_s: float) -> MitsubishiPlcTcpClient:
    return MitsubishiPlcTcpClient(host=host, port=port, timeout_s=timeout_s)


def _default_display_client_factory(host: str, port: int, timeout_s: float) -> TcpFrameClient:
    return TcpFrameClient(host=host, port=port, timeout_s=timeout_s)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class _DisplayEndpointRuntime:
    state: str = "DISCONNECTED"
    frames_sent: int = 0
    connected_at: str | None = None
    last_frame_at: str | None = None
    last_error: str | None = None

    def to_dict(self, host: str, port: int) -> dict[str, Any]:
        return {
            "state": self.state,
            "host": host,
            "port": port,
            "framesSent": self.frames_sent,
            "connectedAt": self.connected_at,
            "lastFrameAt": self.last_frame_at,
            "lastError": self.last_error,
        }


@dataclass(frozen=True)
class DriverCabHardwareStatus:
    state: str
    host: str
    port: int
    train_id: str
    control_state: str
    frames_received: int
    connected_at: str | None
    last_frame_at: str | None
    last_error: str | None
    last_input: dict[str, Any] | None
    last_command: dict[str, Any] | None
    plc_output: dict[str, Any]
    network_screen_host: str
    network_screen_port: int
    signal_screen_host: str
    signal_screen_port: int
    network_screen: dict[str, Any]
    signal_screen: dict[str, Any]
    logs: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "host": self.host,
            "port": self.port,
            "trainId": self.train_id,
            "controlState": self.control_state,
            "framesReceived": self.frames_received,
            "connectedAt": self.connected_at,
            "lastFrameAt": self.last_frame_at,
            "lastError": self.last_error,
            "lastInput": self.last_input,
            "lastCommand": self.last_command,
            "plcOutput": self.plc_output,
            "networkScreenHost": self.network_screen_host,
            "networkScreenPort": self.network_screen_port,
            "signalScreenHost": self.signal_screen_host,
            "signalScreenPort": self.signal_screen_port,
            "networkScreen": self.network_screen,
            "signalScreen": self.signal_screen,
            "logs": self.logs,
        }


class DriverCabHardwareController:
    """Owns the PLC connection and routes decoded driver input to one train."""

    def __init__(
        self,
        engine: ManualControlEngine,
        client_factory: ClientFactory = _default_client_factory,
        display_client_factory: DisplayClientFactory = _default_display_client_factory,
        default_host: str = "192.168.100.123",
        default_port: int = 8001,
        default_network_screen_host: str = "192.168.100.122",
        default_network_screen_port: int = 8888,
        default_signal_screen_host: str = "192.168.100.121",
        default_signal_screen_port: int = 9999,
        train_id: str = "T0901",
        timeout_s: float = 3.0,
        display_interval_s: float = 0.25,
        display_reconnect_interval_s: float = 1.0,
    ) -> None:
        self.engine = engine
        self.client_factory = client_factory
        self.display_client_factory = display_client_factory
        self.default_host = default_host
        self.default_port = default_port
        self.default_network_screen_host = default_network_screen_host
        self.default_network_screen_port = default_network_screen_port
        self.default_signal_screen_host = default_signal_screen_host
        self.default_signal_screen_port = default_signal_screen_port
        self.train_id = train_id
        self.timeout_s = timeout_s
        if display_interval_s <= 0:
            raise ValueError("display_interval_s must be positive")
        if display_reconnect_interval_s <= 0:
            raise ValueError("display_reconnect_interval_s must be positive")
        self.display_interval_s = display_interval_s
        self.display_reconnect_interval_s = display_reconnect_interval_s
        self._control_service = CabControlService()
        self._network_screen_builder = NetworkScreenFrameBuilder()
        self._signal_screen_builder = SignalScreenFrameBuilder()
        self._plc_output_builder = MitsubishiPlcCabOutputFrameBuilder()
        self._lock = threading.RLock()
        self._connection_log = ConnectionEventLog()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._client: MitsubishiPlcTcpClient | None = None
        self._display_threads: dict[str, threading.Thread] = {}
        self._display_clients: dict[str, TcpFrameClient] = {}
        self._display_stop_events: dict[str, threading.Event] = {}
        self._state = "DISCONNECTED"
        self._control_state = "IDLE"
        self._host = default_host
        self._port = default_port
        self._network_screen_host = default_network_screen_host
        self._network_screen_port = default_network_screen_port
        self._signal_screen_host = default_signal_screen_host
        self._signal_screen_port = default_signal_screen_port
        self._network_screen_runtime = _DisplayEndpointRuntime()
        self._signal_screen_runtime = _DisplayEndpointRuntime()
        self._frames_received = 0
        self._connected_at: str | None = None
        self._last_frame_at: str | None = None
        self._last_error: str | None = None
        self._last_input: dict[str, Any] | None = None
        self._last_command: dict[str, Any] | None = None
        self._manual_mode_armed = False
        self._ever_armed = False
        self._ato_available_sent = False
        self._ato_active_sent = False
        self._last_plc_output: MitsubishiPlcCabOutputState | None = None
        self._log("plc", "READY", "PLC 连接控制器已就绪", details={"host": self._host, "port": self._port})
        self._log(
            "networkScreen",
            "READY",
            "HMI 网络屏连接控制器已就绪",
            details={"host": self._network_screen_host, "port": self._network_screen_port},
        )
        self._log(
            "signalScreen",
            "READY",
            "MMI 信号屏连接控制器已就绪",
            details={"host": self._signal_screen_host, "port": self._signal_screen_port},
        )

    def connect(
        self,
        host: str | None = None,
        port: int | None = None,
        network_screen_host: str | None = None,
        signal_screen_host: str | None = None,
    ) -> dict[str, Any]:
        next_host = (host or self.default_host).strip()
        next_port = port if port is not None else self.default_port
        next_network_screen_host = (network_screen_host or self.default_network_screen_host).strip()
        next_signal_screen_host = (signal_screen_host or self.default_signal_screen_host).strip()
        if not next_host:
            raise ValueError("host must not be empty")
        if not next_network_screen_host:
            raise ValueError("network_screen_host must not be empty")
        if not next_signal_screen_host:
            raise ValueError("signal_screen_host must not be empty")
        if next_port <= 0 or next_port > 65535:
            raise ValueError("port must be between 1 and 65535")
        self.disconnect()
        with self._lock:
            self._host = next_host
            self._port = next_port
            self._network_screen_host = next_network_screen_host
            self._signal_screen_host = next_signal_screen_host
            self._start_plc_locked()
            self._start_display_locked("networkScreen")
            self._start_display_locked("signalScreen")
            return {"ok": True, "status": self._snapshot_locked().to_dict()}

    def disconnect(self) -> dict[str, Any]:
        self.disconnect_plc()
        self.disconnect_display("networkScreen")
        self.disconnect_display("signalScreen")
        with self._lock:
            return {"ok": True, "status": self._snapshot_locked().to_dict()}

    def connect_plc(self, host: str | None = None, port: int | None = None) -> dict[str, Any]:
        next_host = (host or self._host or self.default_host).strip()
        next_port = port if port is not None else self._port
        if not next_host:
            raise ValueError("host must not be empty")
        if next_port <= 0 or next_port > 65535:
            raise ValueError("port must be between 1 and 65535")
        self.disconnect_plc()
        with self._lock:
            self._host = next_host
            self._port = next_port
            self._start_plc_locked()
            return {"ok": True, "status": self._snapshot_locked().to_dict()}

    def disconnect_plc(self) -> dict[str, Any]:
        with self._lock:
            was_active = self._state != "DISCONNECTED" or self._thread is not None
            if was_active:
                self._log("plc", "DISCONNECT_REQUESTED", "正在断开 PLC 连接")
            self._stop_event.set()
            client = self._client
        if client is not None:
            client.close()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        if self._manual_mode_armed:
            self.engine.set_manual_mode(self.train_id, False)
        with self._lock:
            self._client = None
            self._thread = None
            self._manual_mode_armed = False
            self._ever_armed = False
            self._ato_available_sent = False
            self._ato_active_sent = False
            self._last_plc_output = None
            self._state = "DISCONNECTED"
            self._control_state = "IDLE"
            if was_active:
                self._log("plc", "DISCONNECTED", "PLC 连接已断开")
            return {"ok": True, "status": self._snapshot_locked().to_dict()}

    def connect_display(self, endpoint: str, host: str | None = None) -> dict[str, Any]:
        current_host, _ = self._display_address(endpoint)
        next_host = (host or current_host).strip()
        if not next_host:
            raise ValueError("display host must not be empty")
        self.disconnect_display(endpoint)
        with self._lock:
            if endpoint == "networkScreen":
                self._network_screen_host = next_host
            elif endpoint == "signalScreen":
                self._signal_screen_host = next_host
            else:
                raise ValueError(f"unknown display endpoint: {endpoint}")
            self._start_display_locked(endpoint)
            return {"ok": True, "status": self._snapshot_locked().to_dict()}

    def disconnect_display(self, endpoint: str) -> dict[str, Any]:
        runtime = self._display_runtime(endpoint)
        with self._lock:
            was_active = runtime.state != "DISCONNECTED" or endpoint in self._display_threads
            if was_active:
                self._log(endpoint, "DISCONNECT_REQUESTED", f"正在断开 {self._endpoint_label(endpoint)}")
            stop_event = self._display_stop_events.get(endpoint)
            if stop_event is not None:
                stop_event.set()
            client = self._display_clients.get(endpoint)
            thread = self._display_threads.get(endpoint)
        if client is not None:
            client.close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        with self._lock:
            self._display_clients.pop(endpoint, None)
            self._display_threads.pop(endpoint, None)
            self._display_stop_events.pop(endpoint, None)
            runtime.state = "DISCONNECTED"
            if was_active:
                self._log(endpoint, "DISCONNECTED", f"{self._endpoint_label(endpoint)}连接已断开")
            return {"ok": True, "status": self._snapshot_locked().to_dict()}

    def _start_plc_locked(self) -> None:
        self._state = "CONNECTING"
        self._control_state = "WAITING_FOR_CONNECTION"
        self._frames_received = 0
        self._connected_at = None
        self._last_frame_at = None
        self._last_error = None
        self._last_input = None
        self._last_command = None
        self._manual_mode_armed = False
        self._ever_armed = False
        self._ato_available_sent = False
        self._ato_active_sent = False
        self._last_plc_output = None
        self._log(
            "plc",
            "CONNECTING",
            "正在连接 PLC 司机台",
            details={"host": self._host, "port": self._port, "transport": "TCP"},
        )
        stop_event = threading.Event()
        self._stop_event = stop_event
        self._thread = threading.Thread(
            target=self._run,
            args=(stop_event,),
            name="driver-cab-plc",
            daemon=True,
        )
        self._thread.start()

    def _start_display_locked(self, endpoint: str) -> None:
        runtime = _DisplayEndpointRuntime(state="CONNECTING")
        if endpoint == "networkScreen":
            self._network_screen_runtime = runtime
        elif endpoint == "signalScreen":
            self._signal_screen_runtime = runtime
        else:
            raise ValueError(f"unknown display endpoint: {endpoint}")
        host, port = self._display_address(endpoint)
        self._log(
            endpoint,
            "CONNECTING",
            f"正在连接 {self._endpoint_label(endpoint)}",
            details={"host": host, "port": port, "transport": "TCP"},
        )
        stop_event = threading.Event()
        self._display_stop_events[endpoint] = stop_event
        thread = threading.Thread(
            target=self._run_display_endpoint,
            args=(endpoint, stop_event),
            name="driver-cab-hmi" if endpoint == "networkScreen" else "driver-cab-mmi",
            daemon=True,
        )
        self._display_threads[endpoint] = thread
        thread.start()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {"ok": True, "status": self._snapshot_locked().to_dict()}

    def clear_logs(self) -> dict[str, Any]:
        self._connection_log.clear()
        self._log("system", "LOGS_CLEARED", "设备连接日志已清空")
        return self.status()

    def process_input_state(self, input_state: MitsubishiPlcCabInputState) -> dict[str, Any]:
        """Apply one decoded frame; public for deterministic integration tests.

        状态机:
          DISCONNECTED → 首次连入: 武装人工 + 发送 ATO 可用
          人工模式     → 司机按 ATO 启动: 切 ATO + 发送 ATO 激活
          ATO 模式     → 司机推手柄: 切回人工 + 发送 ATO 复位
        """
        emergency_brake_requested = input_state.emergency_brake_requested

        # —— ATO 激活: 系统已发送 ATO 可用 → 司机按启动按钮 → 系统发送 ATO 激活 ——
        if (
            not emergency_brake_requested
            and self._ato_available_sent
            and input_state.ato_start_triggered
        ):
            self._ever_armed = True
            if self._manual_mode_armed:
                self.engine.set_manual_mode(self.train_id, False)
                self._manual_mode_armed = False
            self._ato_active_sent = True
            with self._lock:
                self._record_input_locked(input_state)
                self._control_state = "ATO_ACTIVE"
                self._last_error = None
                self._last_command = None
            return {"ok": True, "trainId": self.train_id, "manualMode": False, "message": "ATO_ACTIVATED"}

        # —— 司机接管: ATO 下操纵主手柄切回人工 ——
        if not self._manual_mode_armed and (
            input_state.main_handle_code != 0 or emergency_brake_requested
        ):
            mode_result = self.engine.set_manual_mode(self.train_id, True)
            if mode_result.get("ok"):
                self._manual_mode_armed = True
                # ATO remains available while the driver is in manual mode.
                # Clearing this bit here made every later ATO-start pulse
                # impossible to accept, especially when the first PLC frame
                # arrived with the master handle away from neutral.
                self._ato_available_sent = True
                self._ato_active_sent = False
            else:
                with self._lock:
                    self._record_input_locked(input_state)
                    self._control_state = "WAITING_FOR_TRAIN"
                    self._last_error = "T0901_NOT_FOUND"
                    self._last_command = None
                return mode_result
            # fall through to send the manual command below

        # —— ATO 持续运行: 启动按钮松开后仍保持 ATO ——
        if self._ever_armed and not self._manual_mode_armed:
            with self._lock:
                self._record_input_locked(input_state)
                self._control_state = "ATO_ACTIVE"
                self._last_error = None
                self._last_command = None
            return {
                "ok": True,
                "trainId": self.train_id,
                "manualMode": False,
                "message": "ATO_ACTIVE",
            }

        # —— 首次连入: 武装人工模式 + 发送 ATO 可用 (只执行一次) ——
        if not self._ever_armed:
            self._ever_armed = True
            if not self._manual_mode_armed:
                mode_result = self.engine.set_manual_mode(self.train_id, True)
                if not mode_result.get("ok"):
                    self._ever_armed = False
                    with self._lock:
                        self._control_state = "WAITING_FOR_TRAIN"
                        self._last_error = "T0901_NOT_FOUND"
                        self._record_input_locked(input_state)
                    return mode_result
                self._manual_mode_armed = True
                self._ato_available_sent = True
            # Apply the first valid PLC frame below instead of replacing it
            # with a synthetic neutral command.

        # —— 人工模式下发司机操纵指令 ——
        driver_input = input_state.to_driver_input()
        command = self._control_service.command_from_driver_input(driver_input)
        result = self.engine.set_manual_command(
            self.train_id,
            command.traction_percent,
            command.brake_percent,
            emergency_brake=command.emergency_brake,
        )
        with self._lock:
            self._record_input_locked(input_state)
            if result.get("ok"):
                self._control_state = "ACTIVE"
                self._last_error = None
                self._last_command = {
                    "tractionPercent": command.traction_percent,
                    "brakePercent": command.brake_percent,
                    "emergencyBrake": command.emergency_brake,
                    "handleMode": driver_input.handle_mode.value,
                }
            else:
                self._control_state = "WAITING_FOR_TRAIN"
                self._last_error = str(result.get("error", "CONTROL_REJECTED"))
                if result.get("error") == "TRAIN_NOT_FOUND":
                    self._manual_mode_armed = False
        return result

    def _run(self, stop_event: threading.Event) -> None:
        client = self.client_factory(self._host, self._port, self.timeout_s)
        with self._lock:
            self._client = client
        try:
            client.connect()
            if stop_event.is_set():
                return
            with self._lock:
                self._state = "CONNECTED"
                self._control_state = "WAITING_FOR_TRAIN"
                self._connected_at = _utc_now_iso()
                self._log(
                    "plc",
                    "CONNECTED",
                    "PLC 司机台 TCP 连接已建立",
                    details={"host": self._host, "port": self._port},
                )
            while not stop_event.is_set():
                input_state = client.read_input_state(train_id=self.train_id)
                self.process_input_state(input_state)
                self._send_plc_output(client)
        except (ConnectionError, OSError, RuntimeError, socket.timeout, ValueError) as exc:
            if not stop_event.is_set():
                self._apply_connection_loss_protection()
                with self._lock:
                    self._state = "ERROR"
                    self._control_state = "FAIL_SAFE_BRAKE" if self._manual_mode_armed else "IDLE"
                    self._last_error = str(exc)
                    self._log(
                        "plc",
                        "CONNECTION_ERROR",
                        "PLC 连接中断，已进入保护状态",
                        level="ERROR",
                        details={"error": str(exc)},
                    )
        finally:
            client.close()
            with self._lock:
                if self._client is client:
                    self._client = None

    def _run_display_endpoint(self, endpoint: str, stop_event: threading.Event) -> None:
        runtime = self._display_runtime(endpoint)
        first_attempt = True
        while not stop_event.is_set():
            host, port = self._display_address(endpoint)
            client = self.display_client_factory(host, port, self.timeout_s)
            with self._lock:
                runtime.state = "CONNECTING" if first_attempt else "RETRYING"
                self._display_clients[endpoint] = client
                if not first_attempt:
                    self._log(
                        endpoint,
                        "RETRYING",
                        f"正在重新连接 {self._endpoint_label(endpoint)}",
                        level="WARN",
                        details={"host": host, "port": port},
                    )
            try:
                client.connect()
                with self._lock:
                    runtime.state = "CONNECTED"
                    runtime.connected_at = _utc_now_iso()
                    runtime.last_error = None
                    self._log(
                        endpoint,
                        "CONNECTED",
                        f"{self._endpoint_label(endpoint)} TCP 连接已建立",
                        details={"host": host, "port": port},
                    )
                previous_speed: float | None = None
                previous_sample_at: float | None = None
                while not stop_event.is_set():
                    train = self._display_train_snapshot()
                    sample_at = time.monotonic()
                    speed_mps = float(train.get("speedMps", 0.0))
                    acceleration = 0.0
                    if previous_speed is not None and previous_sample_at is not None:
                        elapsed = sample_at - previous_sample_at
                        if elapsed > 0:
                            acceleration = max(-5.0, min(5.0, (speed_mps - previous_speed) / elapsed))
                    frame = self._build_display_frame(endpoint, train, acceleration)
                    client.send_frame(frame)
                    previous_speed = speed_mps
                    previous_sample_at = sample_at
                    with self._lock:
                        runtime.frames_sent += 1
                        runtime.last_frame_at = _utc_now_iso()
                        if runtime.frames_sent == 1:
                            self._log(
                                endpoint,
                                "FIRST_FRAME_SENT",
                                f"{self._endpoint_label(endpoint)}已发送首帧",
                                details={"bytes": len(frame)},
                            )
                    if stop_event.wait(self.display_interval_s):
                        break
            except (ConnectionError, OSError, RuntimeError, socket.timeout, ValueError) as exc:
                if not stop_event.is_set():
                    with self._lock:
                        runtime.state = "RETRYING"
                        runtime.last_error = str(exc)
                        self._log(
                            endpoint,
                            "CONNECTION_ERROR",
                            f"{self._endpoint_label(endpoint)}连接异常",
                            level="ERROR",
                            details={"error": str(exc), "host": host, "port": port},
                        )
            finally:
                client.close()
                with self._lock:
                    if self._display_clients.get(endpoint) is client:
                        self._display_clients.pop(endpoint, None)
            first_attempt = False
            if stop_event.wait(self.display_reconnect_interval_s):
                break
        with self._lock:
            runtime.state = "DISCONNECTED"

    def _display_runtime(self, endpoint: str) -> _DisplayEndpointRuntime:
        if endpoint == "networkScreen":
            return self._network_screen_runtime
        if endpoint == "signalScreen":
            return self._signal_screen_runtime
        raise ValueError(f"unknown display endpoint: {endpoint}")

    def _display_address(self, endpoint: str) -> tuple[str, int]:
        with self._lock:
            if endpoint == "networkScreen":
                return self._network_screen_host, self._network_screen_port
            if endpoint == "signalScreen":
                return self._signal_screen_host, self._signal_screen_port
        raise ValueError(f"unknown display endpoint: {endpoint}")

    def _display_train_snapshot(self) -> dict[str, Any]:
        snapshot = self.engine.snapshot()
        if snapshot is None:
            return {}
        return next(
            (train for train in snapshot.trains if train.get("trainId") == self.train_id),
            {},
        )

    def _build_display_frame(
        self,
        endpoint: str,
        train: dict[str, Any],
        acceleration_mps2: float,
    ) -> bytes:
        common = self._display_common_values(train)
        if endpoint == "networkScreen":
            door_word = 0 if train.get("doorState", "CLOSED") == "CLOSED" else 0x11111111
            brake_active = common["brake_percent"] > 0
            stop_state = 0x10 if brake_active else 0x11
            state = NetworkScreenState(
                curr_station_id=common["current_station_id"],
                next_station_id=common["next_station_id"],
                end_station_id=common["end_station_id"],
                speed_mps=common["speed_mps"],
                acceleration_mps2=acceleration_mps2,
                power_pull=_clamp_u16(float(train.get("tractionForceN", 0.0)) / 1000.0),
                net_pressure=_clamp_u16(train.get("pantographVoltageV", 1500.0)),
                speed_limit=common["speed_limit_kmh"],
                level_pos=common["level_position"],
                run_mode=1 if common["operation_mode"] == "ATO" else 0,
                master_voltage=_clamp_u16(train.get("pantographVoltageV", 0.0)),
                run_dir=1 if common["direction"] == "UP" else 2,
                driver_room_state=1,
                door_states=[door_word] * 6,
                elect_stop_forces=[_clamp_u16(float(train.get("electricBrakeForceN", 0.0)) / 6000.0)] * 6,
                brake_pressures=[_clamp_u16(common["brake_percent"] * 10.0)] * 6,
                usage_rates=[_clamp_u8(float(train.get("loadFactor", 0.0)) * 100.0)] * 6,
                line_voltages=[_clamp_u16(train.get("pantographVoltageV", 0.0))] * 6,
                stop_states=[stop_state] * 6,
                train_no=common["train_no"],
            )
            return self._network_screen_builder.build(state)
        if endpoint == "signalScreen":
            state = SignalScreenState(
                curr_station_id=common["current_station_id"],
                next_station_id=common["next_station_id"],
                end_station_id=common["end_station_id"],
                speed_mps=common["speed_mps"],
                acceleration_mps2=acceleration_mps2,
                speed_limit=common["speed_limit_kmh"],
                mode=1 if common["operation_mode"] == "ATO" else 3,
                pull_state=1 if common["traction_percent"] > 0 else 0,
                brake_state=1 if common["brake_percent"] > 0 else 0,
                urgency_stop_state=1 if common["emergency_brake"] else 0,
                train_no=common["train_no"],
                next_station_distance_m=max(0.0, float(train.get("distanceToNextM", 0.0))),
            )
            return self._signal_screen_builder.build(state)
        raise ValueError(f"unknown display endpoint: {endpoint}")

    def _display_common_values(self, train: dict[str, Any]) -> dict[str, Any]:
        if not train:
            return {
                "current_station_id": 0,
                "next_station_id": 0,
                "end_station_id": 0,
                "speed_mps": 0.0,
                "speed_limit_kmh": 0,
                "level_position": 0,
                "operation_mode": "ATO",
                "direction": "UP",
                "traction_percent": 0.0,
                "brake_percent": 0.0,
                "emergency_brake": False,
                "train_no": _numeric_train_no(self.train_id),
            }
        current_station_id = max(1, min(16, int(train.get("stationIndex", 0)) + 1))
        direction = str(train.get("direction", "UP"))
        if direction == "DOWN":
            next_station_id = max(1, current_station_id - 1)
            end_station_id = 1
        else:
            next_station_id = min(16, current_station_id + 1)
            end_station_id = 13
        traction_percent = max(0.0, min(100.0, float(train.get("tractionPercent", 0.0))))
        brake_percent = max(0.0, min(100.0, float(train.get("brakePercent", 0.0))))
        last_command = self._last_command or {}
        emergency_brake = bool(last_command.get("emergencyBrake", False))
        level_position = 3 if emergency_brake else 2 if brake_percent > 0 else 1 if traction_percent > 0 else 0
        return {
            "current_station_id": current_station_id,
            "next_station_id": next_station_id,
            "end_station_id": end_station_id,
            "speed_mps": max(0.0, float(train.get("speedMps", 0.0))),
            "speed_limit_kmh": _clamp_u16(float(train.get("localSpeedLimitMps", 0.0)) * 3.6),
            "level_position": level_position,
            "operation_mode": str(train.get("operationMode", "ATO")),
            "direction": direction,
            "traction_percent": traction_percent,
            "brake_percent": brake_percent,
            "emergency_brake": emergency_brake,
            "train_no": _numeric_train_no(str(train.get("trainId", self.train_id))),
        }

    def _apply_connection_loss_protection(self) -> None:
        if not self._manual_mode_armed:
            return
        result = self.engine.set_manual_command(
            self.train_id,
            0.0,
            100.0,
            emergency_brake=True,
        )
        if result.get("ok"):
            with self._lock:
                self._last_command = {
                    "tractionPercent": 0.0,
                    "brakePercent": 100.0,
                    "emergencyBrake": True,
                    "handleMode": "CONNECTION_LOSS",
                }

    def _send_plc_output(self, client: MitsubishiPlcTcpClient) -> None:
        train = self._display_train_snapshot()
        output = MitsubishiPlcCabOutputState(
            ato_available=self._ato_available_sent,
            ato_active=self._ato_active_sent,
            doors_closed_light=train.get("doorState", "CLOSED") == "CLOSED" if train else False,
            vehicle_speed_cmps=_clamp_u16(max(0.0, float(train.get("speedMps", 0.0))) * 100.0),
        )
        if self._last_plc_output == output:
            return
        client.send_output_state(output, builder=self._plc_output_builder)
        self._last_plc_output = output

    def _record_input_locked(self, input_state: MitsubishiPlcCabInputState) -> None:
        self._frames_received += 1
        self._last_frame_at = _utc_now_iso()
        if self._frames_received == 1:
            self._log(
                "plc",
                "FIRST_FRAME_RECEIVED",
                "PLC 已接收首帧驾驶数据",
                details={"trainId": self.train_id},
            )
        self._last_input = {
            "speedMps": input_state.vehicle_speed_mps,
            "direction": input_state.direction,
            "handleCode": input_state.main_handle_code,
            "tractionPercent": min(input_state.traction_percent_raw, 100),
            "brakePercent": min(input_state.brake_percent_raw, 100),
            "emergencyBrake": input_state.emergency_brake_requested,
            "keyActive": input_state.key_switch_locked,
            "atoStart": input_state.ato_start_triggered,
            "atoAvailableEcho": input_state.ato_available,
            "atoActiveEcho": input_state.ato_active,
        }

    def _snapshot_locked(self) -> DriverCabHardwareStatus:
        return DriverCabHardwareStatus(
            state=self._state,
            host=self._host,
            port=self._port,
            train_id=self.train_id,
            control_state=self._control_state,
            frames_received=self._frames_received,
            connected_at=self._connected_at,
            last_frame_at=self._last_frame_at,
            last_error=self._last_error,
            last_input=self._last_input,
            last_command=self._last_command,
            plc_output={
                "atoAvailable": self._ato_available_sent,
                "atoActive": self._ato_active_sent,
                "frameLength": (
                    self._plc_output_builder.speed_extension_frame_size_bytes
                    if self._last_plc_output is not None
                    and self._last_plc_output.vehicle_speed_cmps is not None
                    else self._plc_output_builder.strict_frame_size_bytes
                ),
                "speedCmps": (
                    self._last_plc_output.vehicle_speed_cmps
                    if self._last_plc_output is not None
                    else None
                ),
            },
            network_screen_host=self._network_screen_host,
            network_screen_port=self._network_screen_port,
            signal_screen_host=self._signal_screen_host,
            signal_screen_port=self._signal_screen_port,
            network_screen=self._network_screen_runtime.to_dict(
                self._network_screen_host,
                self._network_screen_port,
            ),
            signal_screen=self._signal_screen_runtime.to_dict(
                self._signal_screen_host,
                self._signal_screen_port,
            ),
            logs=self._connection_log.snapshot(),
        )

    def _log(
        self,
        endpoint: str,
        event: str,
        message: str,
        *,
        level: str = "INFO",
        details: dict[str, Any] | None = None,
    ) -> None:
        self._connection_log.append(
            endpoint,
            event,
            message,
            level=level,
            details=details,
        )

    @staticmethod
    def _endpoint_label(endpoint: str) -> str:
        return {
            "networkScreen": "HMI 网络屏",
            "signalScreen": "MMI 信号屏",
        }.get(endpoint, endpoint)


def _clamp_u8(value: Any) -> int:
    return max(0, min(0xFF, int(round(float(value)))))


def _clamp_u16(value: Any) -> int:
    return max(0, min(0xFFFF, int(round(float(value)))))


def _numeric_train_no(train_id: str) -> int:
    digits = "".join(character for character in train_id if character.isdigit())
    return _clamp_u16(int(digits) if digits else 0)
