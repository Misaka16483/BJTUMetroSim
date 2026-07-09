from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.domain.interlocking.section_occupation import SectionOccupationService
from app.domain.line.services import LineMapRepository, TrackQueryService
from app.domain.operations.member_c_demo import MemberCDemoRunner
from app.domain.operations.member_d_demo import Phase2MemberDDemoRunner
from app.domain.operations.phase0_member_d_demo import Phase0MemberDDemoRunner
from app.domain.operations.phase1_member_d_demo import Phase1MemberDDemoRunner
from app.domain.operations.phase2_member_d_full_demo import Phase2MemberDFullDemoRunner


JsonDict = dict[str, Any]

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE = ROOT / "data" / "cache" / "line_map.json"
DEFAULT_RUN_DIR = ROOT / "outputs" / "runs"
REPO_STATIONS = ROOT / "MetroDynamicsJavaDemo" / "data" / "stations.csv"
WORKSPACE_STATIONS = (
    ROOT / "external" / "BJTUMetroSim" / "MetroDynamicsJavaDemo" / "data" / "stations.csv"
)
DEFAULT_STATIONS = REPO_STATIONS if REPO_STATIONS.exists() else WORKSPACE_STATIONS

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
    def __init__(
        self,
        cache_path: Path = DEFAULT_CACHE,
        stations_path: Path = DEFAULT_STATIONS,
        run_dir: Path = DEFAULT_RUN_DIR,
    ) -> None:
        self.cache_path = cache_path
        self.stations_path = stations_path
        self.run_dir = run_dir
        self._line_map: JsonDict | None = None
        self._stations: list[JsonDict] | None = None
        self._sim_runner: MemberCDemoRunner | None = None

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

    def health(self) -> JsonDict:
        validation = self.line_map.get("validation", {})
        return {
            "ok": True,
            "service": "BJTUMetroSim Phase0 API",
            "lineId": "9",
            "cache": str(self.cache_path),
            "cacheExists": self.cache_path.exists(),
            "validationOk": validation.get("ok"),
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
            "source": "phase0-backend",
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
            platforms = sorted(platforms_by_mileage.get(mileage, []), key=lambda item: item["id"])
            lat, lng = LINE9_COORDS.get(station["code"], (0.0, 0.0))
            platform_ids = [item["id"] for item in platforms]
            segment_ids = [item["segmentId"] for item in platforms if item.get("segmentId") is not None]
            mappings.append(
                {
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
                }
            )
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
                    "stationName": self._station_name_for_seg(item["id"], platform_by_seg, station_by_platform),
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

    def sim_state(self) -> JsonDict:
        return {
            "clock": {"state": "LOADED", "simTime": "07:00:00", "tick": 0},
            "trains": [
                {
                    "id": "T0901",
                    "lineId": "9",
                    "segmentId": 13,
                    "offsetM": 0.0,
                    "speedMps": 0.0,
                    "nextStation": "郭公庄",
                    "loadRate": 0.42,
                }
            ],
            "signals": [],
            "sectionOccupancies": [],
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


class ApiHandler(BaseHTTPRequestHandler):
    service: Line9DataService

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            if path == "/api/health":
                self._send_json(self.service.health())
            elif path == "/api/lines/9/macro":
                self._send_json(self.service.macro_line())
            elif path == "/api/lines/9/stations":
                self._send_json({"lineId": "9", "stations": self.service.station_mappings()})
            elif path == "/api/lines/9/track-map":
                self._send_json(self.service.track_map())
            elif path == "/api/sim/state":
                self._send_json(self.service.sim_state())
            elif path == "/api/phase2/member-d/demo":
                self._send_json(self.service.member_d_demo())
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

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[api] {self.address_string()} - {format % args}")

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
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _serve_html_file(self, file_path: Path) -> None:
        body = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self._send_cors_headers()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def build_server(host: str, port: int, service: Line9DataService) -> ThreadingHTTPServer:
    class BoundApiHandler(ApiHandler):
        pass

    BoundApiHandler.service = service
    return ThreadingHTTPServer((host, port), BoundApiHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="BJTUMetroSim Phase 0 HTTP API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--cache", default=str(DEFAULT_CACHE))
    parser.add_argument("--stations", default=str(DEFAULT_STATIONS))
    parser.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR))
    args = parser.parse_args()

    service = Line9DataService(Path(args.cache), Path(args.stations), Path(args.run_dir))
    server = build_server(args.host, args.port, service)
    print(f"Phase 0 API listening on http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()

