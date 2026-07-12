"""Phase 1 API Server — 接入仿真引擎 + WebSocket 推送."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.adapters.cab import DriverCabHardwareController
from app.core.engine import SimulationEngine
from app.domain.line.services import LineMapRepository, LineScope, TrackQueryService
from app.domain.power.line9_topology import load_line9_power_network
from app.domain.power.experiments import PowerExperimentRegistry
from app.domain.operations.member_c_demo import MemberCDemoRunner
from app.domain.operations.member_d_demo import Phase2MemberDDemoRunner
from app.domain.operations.phase0_member_d_demo import Phase0MemberDDemoRunner
from app.domain.operations.phase1_member_d_demo import Phase1MemberDDemoRunner
from app.domain.operations.phase2_member_d_full_demo import Phase2MemberDFullDemoRunner
from app.domain.station.independent_sim import IndependentPassengerSimulation
from app.infra.recorder import RunRecorder


JsonDict = dict[str, Any]

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE = ROOT / "data" / "cache" / "line_map.json"
DEFAULT_RUN_DIR = ROOT / "outputs" / "runs"
REPO_STATIONS = ROOT / "data" / "line9" / "stations.csv"
WORKSPACE_STATIONS = (
    ROOT / "external" / "BJTUMetroSim" / "MetroDynamicsJavaDemo" / "data" / "stations.csv"
)
DEFAULT_STATIONS = REPO_STATIONS if REPO_STATIONS.exists() else WORKSPACE_STATIONS
DEFAULT_SCENARIO = ROOT / "data" / "scenarios" / "line9_interactive.json"
DEFAULT_MAINLINE_SCOPE = ROOT / "data" / "scenarios" / "line9_mainline_scope.json"
DEFAULT_POWER_TOPOLOGY = ROOT / "data" / "scenarios" / "line9_power_topology.json"

LINE9_COLOR = "#8FC31F"
LINE9_COORDS: dict[str, tuple[float, float]] = {
    "GGZ": (39.814322, 116.301889),
    "FSP": (39.825233, 116.297176),
    "KYL": (39.832480, 116.297432),
    "FTN": (39.840444, 116.296748),
    "FTD": (39.855111, 116.293857),
    "QLZ": (39.866773, 116.294292),
    "LLQ": (39.880239, 116.302808),
    "LLE": (39.886886, 116.315142),
    "BWR": (39.894706, 116.321218),
    "JBG": (39.907422, 116.323380),
    "BDZ": (39.923818, 116.325762),
    "BQS": (39.933021, 116.325680),
    "GTG": (39.943114, 116.325190),
}


class Line9DataService:
    """静态 9号线数据服务（保持不变）."""

    def __init__(
        self,
        cache_path: Path = DEFAULT_CACHE,
        stations_path: Path = DEFAULT_STATIONS,
        run_dir: Path = DEFAULT_RUN_DIR,
        mainline_scope_path: Path = DEFAULT_MAINLINE_SCOPE,
    ) -> None:
        self.cache_path = cache_path
        self.stations_path = stations_path
        self.run_dir = run_dir
        self.mainline_scope_path = mainline_scope_path
        self._line_map: JsonDict | None = None
        self._stations: list[JsonDict] | None = None
        self._mainline_scope: LineScope | None = None
        self._sim_runner: MemberCDemoRunner | None = None
        self._power_topology: JsonDict | None = None
        self._passenger_sim: IndependentPassengerSimulation | None = None

    @property
    def passenger_sim(self) -> IndependentPassengerSimulation:
        if self._passenger_sim is None:
            self._passenger_sim = IndependentPassengerSimulation(self.stations)
        return self._passenger_sim

    @property
    def line_map(self) -> JsonDict:
        if self._line_map is None:
            self._line_map = LineMapRepository(self.cache_path).load()
        return self._line_map

    @property
    def stations(self) -> list[JsonDict]:
        if self._stations is None:
            self._stations = self._load_station_catalog()
        return self._stations

    @property
    def mainline_scope(self) -> LineScope:
        if self._mainline_scope is None:
            self._mainline_scope = LineScope.load(self.mainline_scope_path)
        return self._mainline_scope

    def health(self) -> JsonDict:
        validation = self.line_map.get("validation", {})
        return {
            "ok": True,
            "service": "BJTUMetroSim Phase1 API",
            "lineId": "9",
            "cache": str(self.cache_path),
            "cacheExists": self.cache_path.exists(),
            "validationOk": validation.get("ok"),
            "mainlineScopeId": self.mainline_scope.scope_id,
            "mainlineSegmentCount": len(self.mainline_scope.segment_ids),
            "generatedAt": datetime.now(timezone.utc).isoformat(),
        }

    def macro_line(self) -> JsonDict:
        station_mappings = self.station_mappings()
        stations = [
            {
                "name": item["stationName"],
                "lat": item["lat"],
                "lng": item["lng"],
                "code": item["stationCode"],
                "mileageM": item["mileageM"],
                "platformIds": item["platformIds"],
                "platformSegmentIds": item["platformSegmentIds"],
            }
            for item in station_mappings
        ]
        coordinates = [[[station["lat"], station["lng"]] for station in stations]]
        return {
            "id": "9",
            "name": "9号线",
            "color": LINE9_COLOR,
            "coordinates": coordinates,
            "stations": stations,
            "source": "phase1-backend",
        }

    def station_mappings(self) -> list[JsonDict]:
        platforms_by_mileage: dict[float, list[JsonDict]] = {}
        for platform in self.line_map.get("platforms", []):
            mileage = platform.get("mileageM")
            if mileage is None or mileage < 100:
                continue
            platforms_by_mileage.setdefault(round(float(mileage), 3), []).append(platform)
        mappings: list[JsonDict] = []
        for station in self.stations:
            mileage = round(float(station["mileageM"]), 3)
            platforms = sorted(
                platforms_by_mileage.get(mileage, []), key=lambda item: item["id"]
            )
            lat, lng = LINE9_COORDS.get(station["code"], (0.0, 0.0))
            platform_ids = [item["id"] for item in platforms]
            segment_ids = [
                item["segmentId"]
                for item in platforms
                if item.get("segmentId") is not None
            ]
            mappings.append({
                "lineId": "9",
                "stationId": station["id"],
                "stationCode": station["code"],
                "stationName": station["name"],
                "mileageM": station["mileageM"],
                "speedLimitToNextKmh": station["speedLimitToNextKmh"],
                "dwellSeconds": station["dwellSeconds"],
                "lat": lat,
                "lng": lng,
                "platformIds": platform_ids,
                "platformSegmentIds": segment_ids,
                "platforms": [
                    {
                        "id": item["id"],
                        "segmentId": item["segmentId"],
                        "direction": item.get("direction"),
                        "mileageM": item.get("mileageM"),
                    }
                    for item in platforms
                ],
            })
        return mappings

    def track_map(self) -> JsonDict:
        station_mappings = self.station_mappings()
        station_by_platform = {
            platform_id: station
            for station in station_mappings
            for platform_id in station["platformIds"]
        }
        platform_by_seg = {
            platform["segmentId"]: platform
            for platform in self.line_map.get("platforms", [])
            if platform.get("segmentId") is not None and platform.get("mileageM", 0) >= 100
        }
        return {
            "lineId": "9",
            "name": "9号线轨道级视图",
            "scope": {
                "activeForSimulation": self.mainline_scope.scope_id,
                "mainlineSegmentCount": len(self.mainline_scope.segment_ids),
                "fullMapRetained": True,
                "segmentIds": sorted(self.mainline_scope.segment_ids),
            },
            "lengthM": self.stations[-1]["mileageM"] - self.stations[0]["mileageM"],
            "counts": {
                "segments": len(self.line_map.get("segments", [])),
                "signals": len(self.line_map.get("signals", [])),
                "platforms": len(self.line_map.get("platforms", [])),
                "balises": len(self.line_map.get("balises", [])),
                "speedRestrictions": len(self.line_map.get("speedRestrictions", [])),
                "gradients": len(self.line_map.get("gradients", [])),
                "routes": len(self.line_map.get("routes", [])),
                "axleSections": len(self.line_map.get("axleSections", [])),
                "logicalSections": len(self.line_map.get("logicalSections", [])),
            },
            "stations": station_mappings,
            "segments": [
                {
                    "id": item["id"],
                    "lengthM": item.get("lengthM"),
                    "startEndpointId": item.get("startEndpointId"),
                    "endEndpointId": item.get("endEndpointId"),
                    "nextSegmentIds": [
                        next_id
                        for next_id in [
                            item.get("startForwardSegId"),
                            item.get("startDivergingSegId"),
                            item.get("endForwardSegId"),
                            item.get("endDivergingSegId"),
                        ]
                        if next_id is not None
                    ],
                    "ciAreaId": item.get("ciAreaId"),
                    "zcAreaId": item.get("zcAreaId"),
                    "stationName": self._station_name_for_seg(
                        item["id"], platform_by_seg, station_by_platform
                    ),
                }
                for item in self.line_map.get("segments", [])
            ],
            "platforms": self._pick_fields(
                self.line_map.get("platforms", []),
                ["id", "mileageM", "segmentId", "direction", "clearPassengerFlag"],
            ),
            "signals": self._pick_fields(
                self.line_map.get("signals", []),
                ["id", "name", "type", "segmentId", "offsetM", "direction", "aspectInfo"],
            ),
            "speedRestrictions": self._pick_fields(
                self.line_map.get("speedRestrictions", []),
                ["id", "segmentId", "startOffsetM", "endOffsetM", "speedLimitMps"],
            ),
            "gradients": self._pick_fields(
                self.line_map.get("gradients", []),
                ["id", "startSegmentId", "startOffsetM", "endSegmentId", "endOffsetM", "slopePermille"],
            ),
        }

    def power_topology(self) -> JsonDict:
        if self._power_topology is None:
            self._power_topology = load_line9_power_network(DEFAULT_POWER_TOPOLOGY).topology_dict()
        return self._power_topology

    def segment_context(self, seg_id: int) -> JsonDict:
        service = TrackQueryService(self.line_map)
        return {
            "segment": service.get_segment(seg_id),
            "nextSegments": service.get_next_segments(seg_id),
            "speedLimit": service.get_speed_limit(seg_id, 0.0),
            "gradient": service.get_gradient(seg_id, 0.0),
            "nearestPlatform": service.get_nearest_platform(seg_id, 0.0),
            "nextSignal": service.get_next_signal(seg_id, 0.0),
        }

    def member_d_demo(self) -> JsonDict:
        db_path = self.run_dir / "phase2_member_d_demo.sqlite"
        summary = Phase2MemberDDemoRunner(db_path).run()
        return {
            "ok": True,
            "lineId": "9",
            "phase": 2,
            "module": "member-d",
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
        }

    def member_d_phase0_demo(self) -> JsonDict:
        db_path = self.run_dir / "phase0_member_d_demo.sqlite"
        return Phase0MemberDDemoRunner(db_path).run()

    def member_d_phase1_demo(self) -> JsonDict:
        db_path = self.run_dir / "phase1_member_d_demo.sqlite"
        return Phase1MemberDDemoRunner(db_path).run()

    def member_d_phase2_full_demo(self) -> JsonDict:
        db_path = self.run_dir / "phase2_member_d_full_demo.sqlite"
        return Phase2MemberDFullDemoRunner(db_path).run()

    @property
    def sim_runner(self) -> MemberCDemoRunner:
        if self._sim_runner is None:
            self._sim_runner = MemberCDemoRunner(self.cache_path)
        return self._sim_runner

    def member_c_state(self) -> JsonDict:
        return self.sim_runner.state_snapshot()

    def member_c_step(self) -> JsonDict:
        self.sim_runner.step()
        return self.sim_runner.state_snapshot()

    def member_c_reset(self) -> JsonDict:
        self._sim_runner = MemberCDemoRunner(self.cache_path)
        return self.sim_runner.state_snapshot()

    def _load_station_catalog(self) -> list[JsonDict]:
        with self.stations_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        return [
            {
                "id": int(row["id"]),
                "code": row["code"],
                "name": row["name"],
                "mileageM": float(row["mileage_m"]),
                "speedLimitToNextKmh": int(row["speed_limit_to_next_kmh"]),
                "dwellSeconds": int(row["dwell_s"]),
            }
            for row in rows
        ]

    @staticmethod
    def _pick_fields(items: list[JsonDict], fields: list[str]) -> list[JsonDict]:
        return [{field: item.get(field) for field in fields} for item in items]

    @staticmethod
    def _station_name_for_seg(
        seg_id: int,
        platform_by_seg: dict[int, JsonDict],
        station_by_platform: dict[int, JsonDict],
    ) -> str | None:
        platform = platform_by_seg.get(seg_id)
        if not platform:
            return None
        station = station_by_platform.get(platform["id"])
        return station["stationName"] if station else None


# ═══════════════════════════════════════════════════════════════
#  WebSocket Server (port 8001)
# ═══════════════════════════════════════════════════════════════

class WebSocketBroadcaster:
    """WebSocket 推送服务器 — 引擎每个 tick 向所有客户端广播状态."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self._clients: set = set()
        self._server = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            import websockets
        except ImportError:
            print("[ws] websockets 未安装，跳过 WebSocket 服务")
            return

        async def handler(websocket):
            self._clients.add(websocket)
            try:
                async for _ in websocket:
                    pass  # 客户端发来的消息忽略
            finally:
                self._clients.discard(websocket)

        async def serve():
            self._server = await websockets.serve(handler, self.host, self.port)
            print(f"[ws] WebSocket server started on ws://{self.host}:{self.port}")
            await self._server.wait_closed()

        try:
            asyncio.run(serve())
        except RuntimeError:
            # asyncio.run() 在已有事件循环时会报错
            pass

    async def broadcast(self, message: str) -> None:
        if not self._clients:
            return
        try:
            import websockets
        except ImportError:
            return
        tasks = [client.send(message) for client in list(self._clients)]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def broadcast_sync(self, message: str) -> None:
        """同步包装，供 HTTP handler 调用."""
        try:
            asyncio.run(self.broadcast(message))
        except RuntimeError:
            pass


# ═══════════════════════════════════════════════════════════════
#  HTTP Router
# ═══════════════════════════════════════════════════════════════

class ApiHandler(BaseHTTPRequestHandler):
    service: Line9DataService
    engine: SimulationEngine | None = None
    ws_broadcaster: WebSocketBroadcaster | None = None
    experiment_registry: PowerExperimentRegistry | None = None
    cab_hardware_controller: DriverCabHardwareController | None = None
    cab_hardware_lock = threading.RLock()

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            if path == "/api/health":
                self._send_json(self._health())
            elif path == "/api/lines/9/macro":
                self._send_json(self.service.macro_line())
            elif path == "/api/lines/9/stations":
                self._send_json({"lineId": "9", "stations": self.service.station_mappings()})
            elif path == "/api/lines/9/track-map":
                self._send_json(self.service.track_map())
            elif path == "/api/lines/9/power-topology":
                self._send_json(self.service.power_topology())
            elif path == "/api/sim/state":
                self._send_json(self._sim_state())
            elif path == "/api/sim/interlocking/state":
                snap = self.engine.snapshot() if self.engine else None
                self._send_json(snap.interlocking if snap else {
                    "mode": "UNAVAILABLE", "routes": [], "sections": [], "switches": [], "signals": []
                })
            elif path == "/api/sim/dispatch/state":
                snap = self.engine.snapshot() if self.engine else None
                self._send_json(snap.dispatch_runtime if snap else {
                    "registeredTrainCount": 0, "departureCount": 0, "recentDepartures": []
                })
            elif path == "/api/hardware/driver-cab/status":
                controller = self._driver_cab_controller()
                if controller is None:
                    self._send_json({"ok": False, "error": "ENGINE_NOT_INITIALIZED"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    self._send_json(controller.status())
            elif match := re.fullmatch(r"/api/sim/passenger-history/([^/]+)", path):
                if self.engine is None:
                    self._send_json({"ok": False, "error": "ENGINE_NOT_INITIALIZED"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    query = parse_qs(parsed.query)
                    since = query.get("sinceSimTimeMs", [None])[0]
                    self._send_json(self.engine.station_passenger_history(
                        match.group(1),
                        int(since) if since is not None else None,
                    ))
            elif path == "/api/passenger-sim/state":
                self._send_json(self.service.passenger_sim.snapshot())
            elif path == "/api/sim/power/state":
                self._send_json(self._sim_power_state())
            elif path == "/api/sim/power/commands":
                self._send_json({"ok": True, "data": self.engine.power_command_status() if self.engine else []})
            elif match := re.fullmatch(r"/api/sim/power/commands/([^/]+)", path):
                commands = self.engine.power_command_status(match.group(1)) if self.engine else []
                if commands:
                    self._send_json({"ok": True, "data": commands[0]})
                else:
                    self._send_json({"ok": False, "error": "POWER_COMMAND_NOT_FOUND"}, HTTPStatus.NOT_FOUND)
            elif path == "/api/sim/speed-profile":
                self._send_json(self._speed_profile())
            elif path == "/api/sim/run/export":
                if self.engine is None:
                    self._send_json({"ok": False, "error": "ENGINE_NOT_INITIALIZED"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    self._send_json({"ok": True, "data": self.engine.export_current_run()})
            elif path == "/api/power/experiments":
                self._send_json({"ok": True, "data": self._power_experiment_registry().list()})
            elif match := re.fullmatch(r"/api/power/experiments/([^/]+)/trials", path):
                try:
                    result = self._power_experiment_registry().get(match.group(1), include_trials=True)
                    self._send_json({"ok": True, "data": result["trials"]})
                except KeyError:
                    self._send_json({"ok": False, "error": "POWER_EXPERIMENT_NOT_FOUND"}, HTTPStatus.NOT_FOUND)
            elif match := re.fullmatch(r"/api/power/experiments/([^/]+)", path):
                try:
                    result = self._power_experiment_registry().get(match.group(1), include_trials=False)
                    self._send_json({"ok": True, "data": result})
                except KeyError:
                    self._send_json({"ok": False, "error": "POWER_EXPERIMENT_NOT_FOUND"}, HTTPStatus.NOT_FOUND)
            elif path == "/api/phase0/member-d/demo":
                self._send_json(self.service.member_d_phase0_demo())
            elif path == "/api/phase1/member-d/demo":
                self._send_json(self.service.member_d_phase1_demo())

            elif path == "/api/phase2/member-d/demo":
                self._send_json(self.service.member_d_demo())
            elif path == "/api/phase2/member-d/full-demo":
                self._send_json(self.service.member_d_phase2_full_demo())
            elif path == "/api/phase2/member-c/demo":
                self._serve_html_file(ROOT / "member-c-demo.html")
            elif path == "/api/phase2/member-c/state":
                self._send_json(self.service.member_c_state())
            elif path == "/api/phase2/member-c/step":
                self._send_json(self.service.member_c_step())
            elif path == "/api/phase2/member-c/reset":
                self._send_json(self.service.member_c_reset())
            elif match := re.fullmatch(r"/api/track/segments/(\d+)/context", path):
                self._send_json(self.service.segment_context(int(match.group(1))))
            else:
                self._send_json({"error": "not found", "path": path}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        try:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            if path.startswith("/api/passenger-sim/"):
                payload = self._read_json_body()
                sim = self.service.passenger_sim
                action = path.rsplit("/", 1)[-1]
                if action == "start": sim.start()
                elif action == "pause": sim.pause()
                elif action == "resume": sim.resume()
                elif action == "stop": sim.stop()
                elif action == "step": sim.step(int(payload.get("seconds", 1)))
                else:
                    self._send_json({"ok": False, "error": "UNKNOWN_PASSENGER_SIM_ACTION"}, HTTPStatus.NOT_FOUND)
                    return
                self._send_json({"ok": True, "action": action, "state": sim.snapshot()})
                return

            if path in {"/api/sim/start", "/api/sim/pause", "/api/sim/resume", "/api/sim/stop", "/api/sim/speed"}:
                if not self.engine:
                    self._send_json(
                        {"ok": False, "error": "ENGINE_NOT_INITIALIZED"},
                        HTTPStatus.SERVICE_UNAVAILABLE,
                    )
                    return

            if path == "/api/sim/start":
                start_result = self.engine.start()
                snap = self.engine.snapshot()
                self._send_json({
                    "ok": True,
                    "action": "start",
                    "result": start_result,
                    "clockState": self.engine.clock.state.value,
                    "initializationSteps": [
                        "LOAD_SCENARIO", "RESET_STATE", "CREATE_RECORDER_RUN",
                        "INITIALIZE_POWER_NETWORK", "START_TICK_THREAD",
                    ],
                    "simTimeMs": snap.sim_time_ms if snap else int(self.engine.clock.sim_time_seconds * 1000),
                })
            elif path == "/api/sim/pause":
                self.engine.pause()
                self._send_json({"ok": True, "action": "pause"})
            elif path == "/api/sim/resume":
                self.engine.resume()
                self._send_json({"ok": True, "action": "resume"})
            elif path == "/api/sim/stop":
                self.engine.stop()
                self._send_json({"ok": True, "action": "stop"})
            elif path == "/api/sim/speed":
                payload = self._read_json_body()
                try:
                    multiplier = self.engine.set_speed_multiplier(int(payload.get("multiplier", 1)))
                    self._send_json({"ok": True, "speedMultiplier": multiplier})
                except (TypeError, ValueError) as exc:
                    self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            elif path == "/api/sim/train/add":
                self._send_json(self._add_train())
            elif path == "/api/sim/train/remove":
                self._send_json(self._remove_train())
            elif path == "/api/sim/train/vehicle-config":
                self._send_json(self._set_train_vehicle_config())
            elif path == "/api/sim/train/manual-mode":
                self._send_json(self._set_train_manual_mode())
            elif path == "/api/sim/train/manual-command":
                self._send_json(self._send_train_manual_command())
            elif path == "/api/sim/vehicle-config":
                payload = self._read_json_body()
                vcfg = self.engine.set_vehicle_config(payload)
                self._send_json({"ok": True, "vehicleConfig": vcfg.to_dict()})
            elif path == "/api/sim/manual-mode":
                payload = self._read_json_body()
                enabled = bool(payload.get("enabled", False))
                train_id = str(payload.get("trainId", self.engine.trains[0].train_id if self.engine.trains else "T0901"))
                self._send_json(self._set_manual_mode_from_frontend(train_id, enabled))
            elif path == "/api/sim/manual-command":
                payload = self._read_json_body()
                train_id = str(payload.get("trainId", self.engine.trains[0].train_id if self.engine.trains else "T0901"))
                self._send_json(self.engine.set_manual_command(
                    train_id,
                    float(payload.get("tractionPercent", 0)),
                    float(payload.get("brakePercent", 0)),
                ))
            elif path == "/api/hardware/driver-cab/connect":
                controller = self._driver_cab_controller()
                if controller is None:
                    self._send_json({"ok": False, "error": "ENGINE_NOT_INITIALIZED"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    payload = self._read_json_body()
                    try:
                        result = controller.connect(
                            host=str(payload["host"]) if payload.get("host") is not None else None,
                            port=int(payload["port"]) if payload.get("port") is not None else None,
                            network_screen_host=(
                                str(payload["networkScreenHost"])
                                if payload.get("networkScreenHost") is not None
                                else None
                            ),
                            signal_screen_host=(
                                str(payload["signalScreenHost"])
                                if payload.get("signalScreenHost") is not None
                                else None
                            ),
                        )
                        self._send_json(result, HTTPStatus.ACCEPTED)
                    except (TypeError, ValueError) as exc:
                        self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            elif path == "/api/hardware/driver-cab/disconnect":
                controller = self._driver_cab_controller()
                if controller is None:
                    self._send_json({"ok": False, "error": "ENGINE_NOT_INITIALIZED"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    self._send_json(controller.disconnect())
            elif path == "/api/hardware/driver-cab/plc/connect":
                controller = self._driver_cab_controller()
                if controller is None:
                    self._send_json({"ok": False, "error": "ENGINE_NOT_INITIALIZED"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    payload = self._read_json_body()
                    try:
                        self._send_json(controller.connect_plc(
                            host=str(payload["host"]) if payload.get("host") is not None else None,
                            port=int(payload["port"]) if payload.get("port") is not None else None,
                        ), HTTPStatus.ACCEPTED)
                    except (TypeError, ValueError) as exc:
                        self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            elif path == "/api/hardware/driver-cab/plc/disconnect":
                controller = self._driver_cab_controller()
                if controller is None:
                    self._send_json({"ok": False, "error": "ENGINE_NOT_INITIALIZED"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    self._send_json(controller.disconnect_plc())
            elif path in {
                "/api/hardware/driver-cab/network-screen/connect",
                "/api/hardware/driver-cab/signal-screen/connect",
            }:
                controller = self._driver_cab_controller()
                if controller is None:
                    self._send_json({"ok": False, "error": "ENGINE_NOT_INITIALIZED"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    payload = self._read_json_body()
                    endpoint = "networkScreen" if "/network-screen/" in path else "signalScreen"
                    try:
                        self._send_json(controller.connect_display(
                            endpoint,
                            host=str(payload["host"]) if payload.get("host") is not None else None,
                        ), HTTPStatus.ACCEPTED)
                    except (TypeError, ValueError) as exc:
                        self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            elif path in {
                "/api/hardware/driver-cab/network-screen/disconnect",
                "/api/hardware/driver-cab/signal-screen/disconnect",
            }:
                controller = self._driver_cab_controller()
                if controller is None:
                    self._send_json({"ok": False, "error": "ENGINE_NOT_INITIALIZED"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    endpoint = "networkScreen" if "/network-screen/" in path else "signalScreen"
                    self._send_json(controller.disconnect_display(endpoint))
            elif path == "/api/sim/power/faults":
                payload = self._read_json_body()
                self._send_json(self._apply_power_fault(payload))
            elif path == "/api/sim/power/reset":
                self._send_json(self._reset_power_network())
            elif path == "/api/sim/power/commands":
                payload = self._read_json_body()
                self._send_json(self._queue_power_command(payload))
            elif path == "/api/sim/power/commands/replay":
                payload = self._read_json_body()
                self._send_json(self._replay_power_commands(payload))
            elif path == "/api/power/experiments":
                payload = self._read_json_body()
                try:
                    result = self._power_experiment_registry().create(payload)
                    self._send_json({"ok": True, "data": result}, HTTPStatus.CREATED)
                except ValueError as exc:
                    self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            elif path == "/api/power/experiments/batch":
                payload = self._read_json_body()
                requests = payload.get("experiments", [])
                if not isinstance(requests, list) or not requests:
                    self._send_json({"ok": False, "error": "POWER_EXPERIMENT_BATCH_REQUIRED"}, HTTPStatus.BAD_REQUEST)
                else:
                    try:
                        results = [self._power_experiment_registry().create(item) for item in requests]
                        self._send_json(
                            {"ok": True, "data": {"count": len(results), "experiments": results}},
                            HTTPStatus.CREATED,
                        )
                    except ValueError as exc:
                        self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            elif match := re.fullmatch(r"/api/sim/power/switches/([^/]+)/operate", path):
                payload = self._read_json_body()
                self._send_json(self._operate_power_switch(match.group(1), payload))
            else:
                self._send_json({"error": "not found", "path": path}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[api] {self.address_string()} - {format % args}")

    def _health(self) -> JsonDict:
        base = self.service.health()
        if self.engine:
            snap = self.engine.snapshot()
            base["simEngine"] = "attached"
            base["clockState"] = self.engine.clock.state.value
            base["simTimeStr"] = snap.sim_time_str if snap else "--:--:--"
            base["activeTrains"] = len([t for t in (snap.trains if snap else []) if t.get("phase") != "IDLE"])
        else:
            base["simEngine"] = "not_attached"
        return base

    def _sim_state(self) -> JsonDict:
        if self.engine is None:
            return {
                "clock": {"state": "IDLE", "simTime": "--:--:--", "tick": 0},
                "trains": [],
                "stations": self.service.station_mappings(),
                "source": "static",
            }
        snap = self.engine.snapshot()
        if snap is None:
            return {"clock": {"state": "STOPPED", "simTime": "--:--:--", "tick": 0}, "trains": [], "source": "static"}
        return {
            "clock": {
                "state": snap.clock_state,
                "simTime": snap.sim_time_str,
                "tick": snap.tick,
                "simTimeMs": snap.sim_time_ms,
                "speedMultiplier": snap.speed_multiplier,
            },
            "trains": snap.trains,
            "stations": snap.stations,
            "power": snap.power,
            "powerNetwork": snap.power_network,
            "dispatchDecisions": snap.dispatch_decisions,
            "dispatchRuntime": snap.dispatch_runtime,
            "interlocking": snap.interlocking,
            "kpi": snap.kpi,
            "source": "simulation-engine",
        }

    def _sim_power_state(self) -> JsonDict:
        if self.engine is None:
            return {
                "simTimeMs": 0,
                "substations": [],
                "feeders": [],
                "trainVoltages": [],
                "regen": {"generatedKw": 0, "absorbedKw": 0, "feedbackKw": 0, "wastedKw": 0},
                "lossesKw": 0,
                "alerts": [],
                "source": "static",
            }
        snap = self.engine.snapshot()
        if snap is None or not snap.power_network:
            return {
                "simTimeMs": 0,
                "substations": [],
                "feeders": [],
                "trainVoltages": [],
                "regen": {"generatedKw": 0, "absorbedKw": 0, "feedbackKw": 0, "wastedKw": 0},
                "lossesKw": 0,
                "alerts": [],
                "source": "simulation-engine",
            }
        return snap.power_network

    def _apply_power_fault(self, payload: JsonDict) -> JsonDict:
        if self.engine is None or self.engine.power_service.network is None:
            return {"ok": False, "error": "POWER_NETWORK_NOT_INITIALIZED"}
        fault_type = str(payload.get("faultType", "SUBSTATION_OUTAGE"))
        target_id = str(payload.get("targetId", ""))
        command_by_fault = {
            "SUBSTATION_OUTAGE": ("SUBSTATION_OUTAGE", {"targetId": target_id, "bigBilateral": str(payload.get("mode", "N_MINUS_1_BIG_BILATERAL")) == "N_MINUS_1_BIG_BILATERAL"}),
            "FEEDER_OPEN": ("SET_FEEDER_STATUS", {"feederId": target_id, "status": "OPEN"}),
            "CONTACT_RAIL_DEENERGIZED": ("SET_CONTACT_SECTION_STATUS", {"sectionId": target_id, "status": "DEENERGIZED"}),
        }
        if fault_type not in command_by_fault or not target_id:
            return {"ok": False, "error": "UNSUPPORTED_POWER_FAULT"}
        command_type, command_payload = command_by_fault[fault_type]
        result = self.engine.queue_power_command(command_type, command_payload)
        return {"ok": True, "data": {"faultId": f"PF-{target_id}", **result}}

    def _reset_power_network(self) -> JsonDict:
        if self.engine is None:
            return {"ok": False, "error": "ENGINE_NOT_INITIALIZED"}
        return {"ok": True, "data": self.engine.queue_power_command("RESET_NETWORK", {})}

    def _operate_power_switch(self, switch_id: str, payload: JsonDict) -> JsonDict:
        if self.engine is None or self.engine.power_service.network is None:
            return {"ok": False, "error": "POWER_NETWORK_NOT_INITIALIZED"}
        state = str(payload.get("state", "")).upper()
        if state not in {"OPEN", "CLOSED"}:
            return {"ok": False, "error": "INVALID_SWITCH_STATE"}
        result = self.engine.queue_power_command(
            "OPERATE_SWITCH",
            {"switchId": switch_id, "state": state},
        )
        switch = self.engine.power_service.network.switches[switch_id]
        return {
            "ok": True,
            "data": {
                "switchId": switch.switch_id,
                "switchType": switch.switch_type,
                "mileageM": switch.mileage_m,
                "fromNodeId": switch.from_node_id,
                "toNodeId": switch.to_node_id,
                "normalState": switch.normal_state,
                "currentState": switch.current_state,
                "remoteControllable": switch.remote_controllable,
                **result,
            },
        }

    def _queue_power_command(self, payload: JsonDict) -> JsonDict:
        if self.engine is None or self.engine.power_service.network is None:
            return {"ok": False, "error": "POWER_NETWORK_NOT_INITIALIZED"}
        command_type = str(payload.get("commandType", ""))
        command_payload = payload.get("payload", {})
        if not command_type or not isinstance(command_payload, dict):
            return {"ok": False, "error": "INVALID_POWER_COMMAND"}
        try:
            return {"ok": True, "data": self.engine.queue_power_command(command_type, command_payload)}
        except (KeyError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def _replay_power_commands(self, payload: JsonDict) -> JsonDict:
        if self.engine is None:
            return {"ok": False, "error": "ENGINE_NOT_INITIALIZED"}
        records = payload.get("commands")
        if records is None and payload.get("runId") is not None and self.engine.recorder is not None:
            records = self.engine.recorder.replay_power_commands(int(payload["runId"]))
        if not isinstance(records, list):
            return {"ok": False, "error": "POWER_COMMAND_REPLAY_DATA_REQUIRED"}
        queued = self.engine.replay_power_commands(
            records,
            base_sim_time_ms=int(payload["baseSimTimeMs"]) if payload.get("baseSimTimeMs") is not None else None,
        )
        return {"ok": True, "data": {"queuedCount": len(queued), "commands": queued}}

    def _read_json_body(self) -> JsonDict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _driver_cab_controller(self) -> DriverCabHardwareController | None:
        if self.engine is None:
            return None
        with ApiHandler.cab_hardware_lock:
            controller = ApiHandler.cab_hardware_controller
            if controller is None or controller.engine is not self.engine:
                if controller is not None:
                    controller.disconnect()
                controller = DriverCabHardwareController(self.engine)
                ApiHandler.cab_hardware_controller = controller
            return controller

    @classmethod
    def _power_experiment_registry(cls) -> PowerExperimentRegistry:
        if cls.experiment_registry is None:
            cls.experiment_registry = PowerExperimentRegistry(
                DEFAULT_POWER_TOPOLOGY,
                ROOT / "outputs" / "power_experiments.sqlite",
            )
        return cls.experiment_registry

    def _add_train(self) -> JsonDict:
        if self.engine is None:
            return {"ok": False, "error": "ENGINE_NOT_INITIALIZED"}
        payload = self._read_json_body()
        return self.engine.add_train(payload)

    def _remove_train(self) -> JsonDict:
        if self.engine is None:
            return {"ok": False, "error": "ENGINE_NOT_INITIALIZED"}
        payload = self._read_json_body()
        return self.engine.remove_train(str(payload.get("trainId", "")))

    def _set_train_vehicle_config(self) -> JsonDict:
        if self.engine is None:
            return {"ok": False, "error": "ENGINE_NOT_INITIALIZED"}
        payload = self._read_json_body()
        train_id = str(payload.get("trainId", ""))
        if not train_id:
            return {"ok": False, "error": "MISSING_TRAIN_ID"}
        vcfg = self.engine.set_train_vehicle_config(train_id, payload)
        return {"ok": True, "vehicleConfig": vcfg.to_dict()}

    def _set_train_manual_mode(self) -> JsonDict:
        if self.engine is None:
            return {"ok": False, "error": "ENGINE_NOT_INITIALIZED"}
        payload = self._read_json_body()
        train_id = str(payload.get("trainId", ""))
        if not train_id:
            return {"ok": False, "error": "MISSING_TRAIN_ID"}
        return self._set_manual_mode_from_frontend(train_id, bool(payload.get("enabled", False)))

    def _set_manual_mode_from_frontend(self, train_id: str, enabled: bool) -> JsonDict:
        """Only the PLC driver cab may change driving mode while connected."""
        if self.engine is None:
            return {"ok": False, "error": "ENGINE_NOT_INITIALIZED"}
        controller = self._driver_cab_controller()
        if controller is not None:
            status = controller.status().get("status", {})
            if status.get("state") == "CONNECTED" and status.get("trainId") == train_id:
                return {
                    "ok": False,
                    "error": "DRIVER_CAB_MODE_CONTROL_EXCLUSIVE",
                    "message": "司机台已连接，驾驶模式只能由司机台切换",
                    "trainId": train_id,
                }
        return self.engine.set_manual_mode(train_id, enabled)

    def _send_train_manual_command(self) -> JsonDict:
        if self.engine is None:
            return {"ok": False, "error": "ENGINE_NOT_INITIALIZED"}
        payload = self._read_json_body()
        train_id = str(payload.get("trainId", ""))
        if not train_id:
            return {"ok": False, "error": "MISSING_TRAIN_ID"}
        return self.engine.set_manual_command(
            train_id,
            float(payload.get("tractionPercent", 0)),
            float(payload.get("brakePercent", 0)),
        )

    def _speed_profile(self) -> JsonDict:
        if self.engine is None:
            return {"profiles": {}, "profileMeta": {}, "source": "unavailable"}
        profiles: dict[str, Any] = {}
        profile_meta: dict[str, Any] = {}
        for train in self.engine.trains:
            profiles[train.train_id] = self.engine.export_speed_profile(train.train_id)
            profile_meta[train.train_id] = self.engine.export_speed_profile_meta(train.train_id)
        return {"profiles": profiles, "profileMeta": profile_meta, "source": "simulation-engine"}

    def _send_json(self, payload: JsonDict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _serve_html_file(self, file_path: Path) -> None:
        body = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self._send_cors_headers()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ═══════════════════════════════════════════════════════════════
#  Server Builder
# ═══════════════════════════════════════════════════════════════

def build_server(host: str, port: int, service: Line9DataService) -> ThreadingHTTPServer:
    class BoundApiHandler(ApiHandler):
        pass

    BoundApiHandler.service = service
    return ThreadingHTTPServer((host, port), BoundApiHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="BJTUMetroSim Phase 1 HTTP API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--cache", default=str(DEFAULT_CACHE))
    parser.add_argument("--stations", default=str(DEFAULT_STATIONS))
    parser.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR))
    parser.add_argument("--scenario", default=str(DEFAULT_SCENARIO))
    parser.add_argument("--ws-port", type=int, default=8001)
    args = parser.parse_args()

    service = Line9DataService(Path(args.cache), Path(args.stations), Path(args.run_dir))

    # ── 初始化仿真引擎 ──
    scenario_path = Path(args.scenario)
    recorder = RunRecorder(Path(args.run_dir) / "phase1_engine.sqlite")
    if scenario_path.exists() and Path(args.cache).exists():
        try:
            engine = SimulationEngine.load_from_files(
                scenario_path=scenario_path,
                line_map_path=Path(args.cache),
                stations_csv_path=Path(args.stations),
                recorder=recorder,
            )
            engine.load()
            print(f"[engine] 场景加载成功: {scenario_path.name}")
            print(f"[engine]   {len(engine.trains)} 列车, {len(engine._station_list)} 站")
            print(f"[engine]   recorder: {recorder.db_path}")

            # 注入到 handler 类
            ApiHandler.engine = engine
        except Exception as exc:
            print(f"[engine] 初始化失败: {exc}")

    # ── WebSocket ──
    ws = WebSocketBroadcaster(args.host, args.ws_port)
    ws.start()
    ApiHandler.ws_broadcaster = ws

    # ── HTTP ──
    server = build_server(args.host, args.port, service)
    print(f"Phase 1 API listening on http://{args.host}:{args.port}")
    print(f"WebSocket on ws://{args.host}:{args.ws_port}")
    print("Available endpoints:")
    print("  GET  /api/health")
    print("  GET  /api/lines/9/macro")
    print("  GET  /api/lines/9/track-map")
    print("  GET  /api/sim/state")
    print("  GET  /api/sim/interlocking/state")
    print("  GET  /api/sim/dispatch/state")
    print("  POST /api/sim/start")
    print("  POST /api/sim/pause")
    print("  POST /api/sim/resume")
    print("  POST /api/sim/stop")
    try:
        server.serve_forever()
    finally:
        if ApiHandler.cab_hardware_controller:
            ApiHandler.cab_hardware_controller.disconnect()
        if ApiHandler.engine:
            ApiHandler.engine.stop()


if __name__ == "__main__":
    main()
