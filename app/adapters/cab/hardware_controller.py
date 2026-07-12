from __future__ import annotations

import socket
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from app.adapters.cab.mitsubishi_plc import MitsubishiPlcCabInputState, MitsubishiPlcTcpClient
from app.domain.control import CabControlService


class ManualControlEngine(Protocol):
    def set_manual_mode(self, train_id: str, enabled: bool) -> dict[str, Any]: ...

    def set_manual_command(
        self,
        train_id: str,
        traction_percent: float,
        brake_percent: float,
        emergency_brake: bool = False,
    ) -> dict[str, Any]: ...


ClientFactory = Callable[[str, int, float], MitsubishiPlcTcpClient]


def _default_client_factory(host: str, port: int, timeout_s: float) -> MitsubishiPlcTcpClient:
    return MitsubishiPlcTcpClient(host=host, port=port, timeout_s=timeout_s)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        }


class DriverCabHardwareController:
    """Owns the PLC connection and routes decoded driver input to one train."""

    def __init__(
        self,
        engine: ManualControlEngine,
        client_factory: ClientFactory = _default_client_factory,
        default_host: str = "192.168.100.123",
        default_port: int = 8001,
        train_id: str = "T0901",
        timeout_s: float = 3.0,
    ) -> None:
        self.engine = engine
        self.client_factory = client_factory
        self.default_host = default_host
        self.default_port = default_port
        self.train_id = train_id
        self.timeout_s = timeout_s
        self._control_service = CabControlService()
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._client: MitsubishiPlcTcpClient | None = None
        self._state = "DISCONNECTED"
        self._control_state = "IDLE"
        self._host = default_host
        self._port = default_port
        self._frames_received = 0
        self._connected_at: str | None = None
        self._last_frame_at: str | None = None
        self._last_error: str | None = None
        self._last_input: dict[str, Any] | None = None
        self._last_command: dict[str, Any] | None = None
        self._manual_mode_armed = False
        self._ever_armed = False

    def connect(self, host: str | None = None, port: int | None = None) -> dict[str, Any]:
        next_host = (host or self.default_host).strip()
        next_port = port if port is not None else self.default_port
        if not next_host:
            raise ValueError("host must not be empty")
        if next_port <= 0 or next_port > 65535:
            raise ValueError("port must be between 1 and 65535")
        with self._lock:
            if self._state in {"CONNECTING", "CONNECTED"}:
                return {"ok": True, "status": self._snapshot_locked().to_dict()}
            self._host = next_host
            self._port = next_port
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
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="driver-cab-plc",
                daemon=True,
            )
            self._thread.start()
            return {"ok": True, "status": self._snapshot_locked().to_dict()}

    def disconnect(self) -> dict[str, Any]:
        with self._lock:
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
            self._state = "DISCONNECTED"
            self._control_state = "IDLE"
            return {"ok": True, "status": self._snapshot_locked().to_dict()}

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {"ok": True, "status": self._snapshot_locked().to_dict()}

    def process_input_state(self, input_state: MitsubishiPlcCabInputState) -> dict[str, Any]:
        """Apply one decoded frame; public for deterministic integration tests.

        状态机:
          DISCONNECTED → 首次连入: 武装人工
          人工模式     → ATO 激活条件满足: 切 ATO
          ATO 模式     → 司机推手柄: 切回人工
        """
        # —— ATO 激活: 具备 + 激活 + 按下启动按钮 ——
        if input_state.ato_available and input_state.ato_active and input_state.ato_start_triggered:
            self._ever_armed = True
            if self._manual_mode_armed:
                self.engine.set_manual_mode(self.train_id, False)
                self._manual_mode_armed = False
            with self._lock:
                self._record_input_locked(input_state)
                self._control_state = "ACTIVE"
                self._last_error = None
                self._last_command = None
            return {"ok": True, "trainId": self.train_id, "manualMode": False, "message": "ATO_ACTIVATED"}

        # —— 司机接管: ATO 下操纵主手柄切回人工 ——
        if not self._manual_mode_armed and input_state.main_handle_code != 0:
            mode_result = self.engine.set_manual_mode(self.train_id, True)
            if mode_result.get("ok"):
                self._manual_mode_armed = True
            # fall through to send the manual command below

        # —— 首次连入: 武装人工模式 (只执行一次) ——
        if not self._ever_armed:
            self._ever_armed = True
            mode_result = self.engine.set_manual_mode(self.train_id, True)
            if not mode_result.get("ok"):
                self._ever_armed = False
                with self._lock:
                    self._control_state = "WAITING_FOR_TRAIN"
                    self._last_error = "T0901_NOT_FOUND"
                    self._record_input_locked(input_state)
                return mode_result
            self._manual_mode_armed = True
            # 武装后立刻下发一次零位指令避免残留牵引/制动
            self.engine.set_manual_command(self.train_id, 0.0, 0.0)
            with self._lock:
                self._record_input_locked(input_state)
                self._control_state = "ACTIVE"
                self._last_error = None
            return {"ok": True}

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

    def _run(self) -> None:
        client = self.client_factory(self._host, self._port, self.timeout_s)
        with self._lock:
            self._client = client
        try:
            client.connect()
            if self._stop_event.is_set():
                return
            with self._lock:
                self._state = "CONNECTED"
                self._control_state = "WAITING_FOR_TRAIN"
                self._connected_at = _utc_now_iso()
            while not self._stop_event.is_set():
                input_state = client.read_input_state(train_id=self.train_id)
                self.process_input_state(input_state)
        except (ConnectionError, OSError, RuntimeError, socket.timeout, ValueError) as exc:
            if not self._stop_event.is_set():
                self._apply_connection_loss_protection()
                with self._lock:
                    self._state = "ERROR"
                    self._control_state = "FAIL_SAFE_BRAKE" if self._manual_mode_armed else "IDLE"
                    self._last_error = str(exc)
        finally:
            client.close()
            with self._lock:
                self._client = None

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

    def _record_input_locked(self, input_state: MitsubishiPlcCabInputState) -> None:
        self._frames_received += 1
        self._last_frame_at = _utc_now_iso()
        self._last_input = {
            "speedMps": input_state.vehicle_speed_mps,
            "direction": input_state.direction,
            "handleCode": input_state.main_handle_code,
            "tractionPercent": min(input_state.traction_percent_raw, 100),
            "brakePercent": min(input_state.brake_percent_raw, 100),
            "emergencyBrake": input_state.emergency_brake_button_locked,
            "keyActive": input_state.key_switch_locked,
            "atoStart": input_state.ato_start_triggered,
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
        )
