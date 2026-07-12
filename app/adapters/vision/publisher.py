from __future__ import annotations

from datetime import datetime, timezone
import socket
import threading
import time
from typing import Any, Callable

from app.adapters.vision.mapper import VisionSnapshotMapper
from app.adapters.vision.protocol import COMPACT_LAYOUT, VisionFrameBuilder


class UdpDatagramSender:
    """Bound UDP sender so the laboratory peer can validate the source port."""

    def __init__(
        self,
        remote_host: str,
        remote_port: int,
        local_host: str = "0.0.0.0",
        local_port: int = 8302,
    ) -> None:
        if not remote_host:
            raise ValueError("remote_host must not be empty")
        _validate_port("remote_port", remote_port, allow_zero=False)
        _validate_port("local_port", local_port, allow_zero=True)
        self.remote_host = remote_host
        self.remote_port = remote_port
        self.local_host = local_host
        self.local_port = local_port
        self._socket: socket.socket | None = None

    def open(self) -> None:
        if self._socket is not None:
            return
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            udp_socket.bind((self.local_host, self.local_port))
        except Exception:
            udp_socket.close()
            raise
        self._socket = udp_socket

    def close(self) -> None:
        if self._socket is None:
            return
        self._socket.close()
        self._socket = None

    def send(self, frame: bytes) -> int:
        self.open()
        if self._socket is None:
            raise RuntimeError("UDP socket is not open")
        return self._socket.sendto(frame, (self.remote_host, self.remote_port))


SenderFactory = Callable[..., UdpDatagramSender]


class VisionUdpPublisher:
    """Publish the engine's latest snapshot to the vision controller every 100 ms."""

    def __init__(
        self,
        engine: Any,
        *,
        remote_host: str = "18.32.115.28",
        remote_port: int = 8303,
        local_host: str = "0.0.0.0",
        local_port: int = 8302,
        interval_s: float = 0.1,
        layout: str = COMPACT_LAYOUT,
        primary_train_id: str | None = None,
        signal_source_map: dict[str, int | str] | None = None,
        switch_source_map: dict[str, int | str] | None = None,
        sender_factory: SenderFactory | None = None,
    ) -> None:
        if interval_s <= 0:
            raise ValueError("interval_s must be positive")
        _validate_port("remote_port", remote_port, allow_zero=False)
        _validate_port("local_port", local_port, allow_zero=True)
        self.engine = engine
        self.remote_host = remote_host
        self.remote_port = remote_port
        self.local_host = local_host
        self.local_port = local_port
        self.interval_s = interval_s
        self.builder = VisionFrameBuilder(layout)
        self.mapper = VisionSnapshotMapper(
            engine,
            primary_train_id=primary_train_id,
            signal_source_map=signal_source_map,
            switch_source_map=switch_source_map,
        )
        self._sender_factory = sender_factory or UdpDatagramSender
        self._sender: UdpDatagramSender | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._state = "DISCONNECTED"
        self._live_counter = 0
        self._frames_sent = 0
        self._bytes_sent = 0
        self._last_frame_size = 0
        self._last_frame_at: str | None = None
        self._last_error: str | None = None

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self.status()
            self._stop_event.clear()
            self._state = "STARTING"
            self._last_error = None
            self._thread = threading.Thread(target=self._run, name="vision-udp-publisher", daemon=True)
            self._thread.start()
            return self.status()

    def stop(self) -> dict[str, Any]:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self.interval_s * 3.0))
        with self._lock:
            self._close_sender_locked()
            self._thread = None
            self._state = "DISCONNECTED"
            return self.status()

    connect = start
    disconnect = stop

    def send_once(self) -> bytes:
        snapshot = self.engine.snapshot()
        if snapshot is None:
            raise RuntimeError("simulation snapshot is not available")
        with self._lock:
            counter = self._live_counter
        state = self.mapper.build_state(snapshot, counter)
        frame = self.builder.build(state)
        with self._lock:
            sender = self._sender
            if sender is None:
                sender = self._sender_factory(
                    remote_host=self.remote_host,
                    remote_port=self.remote_port,
                    local_host=self.local_host,
                    local_port=self.local_port,
                )
                self._sender = sender
            sent = sender.send(frame)
            if sent != len(frame):
                raise OSError(f"partial UDP datagram send: {sent}/{len(frame)} bytes")
            self._frames_sent += 1
            self._bytes_sent += sent
            self._last_frame_size = sent
            self._last_frame_at = _utc_now_iso()
            self._last_error = None
            self._state = "CONNECTED"
            self._live_counter = 0 if counter >= 2**31 - 1 else counter + 1
        return frame

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "ok": True,
                "status": {
                    "state": self._state,
                    "remoteHost": self.remote_host,
                    "remotePort": self.remote_port,
                    "localHost": self.local_host,
                    "localPort": self.local_port,
                    "intervalMs": round(self.interval_s * 1000.0),
                    "layout": self.builder.layout,
                    "framesSent": self._frames_sent,
                    "bytesSent": self._bytes_sent,
                    "lastFrameSize": self._last_frame_size,
                    "lastFrameAt": self._last_frame_at,
                    "lastError": self._last_error,
                    "nextLiveCounter": self._live_counter,
                    "mapping": self.mapper.mapping_report(),
                },
            }

    def _run(self) -> None:
        next_send = time.monotonic()
        while not self._stop_event.is_set():
            try:
                self.send_once()
            except (OSError, RuntimeError, ValueError) as exc:
                with self._lock:
                    self._last_error = str(exc)
                    self._state = "RETRYING"
                    self._close_sender_locked()
            next_send += self.interval_s
            wait_s = max(0.0, next_send - time.monotonic())
            if wait_s == 0.0:
                next_send = time.monotonic()
            if self._stop_event.wait(wait_s):
                break

    def _close_sender_locked(self) -> None:
        if self._sender is not None:
            self._sender.close()
            self._sender = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_port(name: str, port: int, *, allow_zero: bool) -> None:
    low = 0 if allow_zero else 1
    if not isinstance(port, int) or not low <= port <= 65535:
        raise ValueError(f"{name} must be between {low} and 65535")
