from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from app.adapters.vision.line9_v13 import (
    DOWN_MAINLINE_EDGES,
    LINE9_SIGNALS_V13,
    LINE9_SWITCHES_V13,
    UP_MAINLINE_EDGES,
    ProtocolEdge,
)
from app.adapters.vision.protocol import VisionFrameState, VisionTrainState


JsonDict = dict[str, Any]

SIGNAL_ASPECT_VALUES = {
    "DARK": 0x00,
    "RED": 0x01,
    "GREEN": 0x02,
    "WHITE": 0x04,
    "YELLOW": 0x10,
    "BLUE": 0x40,
    "RED_YELLOW": 0x11,
}
SWITCH_POSITION_VALUES = {"NORMAL": 0x01, "REVERSE": 0x02}


@dataclass(frozen=True)
class _SegmentPortion:
    local_start_m: float
    local_end_m: float
    global_start_m: float
    global_end_m: float

    def contains(self, offset_m: float) -> bool:
        low, high = sorted((self.local_start_m, self.local_end_m))
        return low - 1e-6 <= offset_m <= high + 1e-6

    def mileage_at(self, offset_m: float) -> float:
        span = self.local_end_m - self.local_start_m
        if abs(span) <= 1e-9:
            return self.global_start_m
        ratio = (offset_m - self.local_start_m) / span
        return self.global_start_m + ratio * (self.global_end_m - self.global_start_m)


class VisionSnapshotMapper:
    """Translate an engine ``TickSnapshot`` into the legacy vision fields.

    The current electronic map uses different signal/switch identifiers from
    the legacy 9号线 vision document. We therefore match them by mainline
    mileage and direction. Unmapped or unavailable signals fail safe to RED;
    unmapped switches use NORMAL. Explicit protocol-id mappings can override
    the automatic result after laboratory calibration.
    """

    def __init__(
        self,
        engine: Any,
        *,
        primary_train_id: str | None = None,
        signal_source_map: dict[str, int | str] | None = None,
        switch_source_map: dict[str, int | str] | None = None,
        signal_match_tolerance_m: float = 180.0,
        switch_match_tolerance_m: float = 250.0,
    ) -> None:
        self.engine = engine
        self.primary_train_id = primary_train_id
        portions = self._build_mainline_portions()
        auto_signals = self._match_signals(portions, signal_match_tolerance_m)
        auto_switches = self._match_switches(portions, switch_match_tolerance_m)
        self.signal_source_map = {**auto_signals, **(signal_source_map or {})}
        self.switch_source_map = {**auto_switches, **(switch_source_map or {})}

    def mapping_report(self) -> JsonDict:
        return {
            "protocolSignalCount": len(LINE9_SIGNALS_V13),
            "mappedSignalCount": sum(
                1 for item in LINE9_SIGNALS_V13 if item.protocol_id in self.signal_source_map
            ),
            "protocolSwitchCount": len(LINE9_SWITCHES_V13),
            "mappedSwitchCount": sum(
                1 for item in LINE9_SWITCHES_V13 if item.protocol_id in self.switch_source_map
            ),
            "unmappedSignalsDefault": "RED",
            "unmappedSwitchesDefault": "NORMAL",
        }

    def build_state(self, snapshot: Any, live_counter: int) -> VisionFrameState:
        trains = list(_snapshot_value(snapshot, "trains", []))
        interlocking = dict(_snapshot_value(snapshot, "interlocking", {}) or {})
        primary = self._select_primary_train(trains)
        signal_by_id = {
            str(item.get("signalId")): str(item.get("aspect", "RED")).upper()
            for item in interlocking.get("signals", [])
        }
        switch_by_id = {
            str(item.get("switchId")): str(item.get("actualPosition", "NORMAL")).upper()
            for item in interlocking.get("switches", [])
        }
        signal_states = tuple(
            SIGNAL_ASPECT_VALUES.get(
                signal_by_id.get(str(self.signal_source_map.get(item.protocol_id)), "RED"),
                SIGNAL_ASPECT_VALUES["RED"],
            )
            for item in LINE9_SIGNALS_V13
        )
        switch_states = tuple(
            SWITCH_POSITION_VALUES.get(
                switch_by_id.get(str(self.switch_source_map.get(item.protocol_id)), "NORMAL"),
                SWITCH_POSITION_VALUES["NORMAL"],
            )
            for item in LINE9_SWITCHES_V13
        )
        primary_fields = self._train_fields(primary)
        other_trains = tuple(
            self._other_train_fields(train)
            for train in trains
            if train is not primary and str(train.get("phase", "IDLE")) != "IDLE"
        )
        traction = float(primary.get("tractionPercent", 0.0)) if primary else 0.0
        brake = float(primary.get("brakePercent", 0.0)) if primary else 0.0
        if traction > 0.5:
            run_state = 0x11
            acceleration_percent = round(traction)
        elif brake > 0.5:
            run_state = 0x12
            acceleration_percent = -round(brake)
        else:
            run_state = 0x13
            acceleration_percent = 0
        return VisionFrameState(
            live_counter=live_counter,
            signal_states=signal_states,
            switch_states=switch_states,
            speed_mmps=_clamp(round(float(primary.get("speedMps", 0.0)) * 1000.0), 0, 33333) if primary else 0,
            dwell_time_s=_clamp(round(float(primary.get("dwellRemainingSec", 0.0))), 0, 32767) if primary else 0,
            run_state=run_state,
            acceleration_percent=_clamp(acceleration_percent, -100, 100),
            section_distance_mm=primary_fields.section_distance_mm,
            edge_id=primary_fields.edge_id,
            direction=primary_fields.direction,
            other_trains=other_trains,
        )

    def _select_primary_train(self, trains: list[JsonDict]) -> JsonDict | None:
        if self.primary_train_id:
            match = next((item for item in trains if str(item.get("trainId")) == self.primary_train_id), None)
            if match is not None:
                return match
        return next((item for item in trains if str(item.get("phase", "IDLE")) != "IDLE"), trains[0] if trains else None)

    @staticmethod
    def _train_fields(train: JsonDict | None) -> VisionTrainState:
        if not train:
            return VisionTrainState()
        direction_name = str(train.get("direction", "UP")).upper()
        direction = 1 if direction_name == "UP" else -1
        mileage_m = float(train.get("headMileageM", 0.0))
        edge = _edge_for_mileage(mileage_m, UP_MAINLINE_EDGES if direction > 0 else DOWN_MAINLINE_EDGES)
        bounded_mileage = min(max(mileage_m, edge.begin_m), edge.end_m)
        return VisionTrainState(
            section_distance_mm=_clamp(round((bounded_mileage - edge.begin_m) * 1000.0), 0, 2**31 - 1),
            edge_id=edge.edge_id,
            direction=direction,
            speed_cmps=_clamp(round(float(train.get("speedMps", 0.0)) * 100.0), 0, 32767),
        )

    @classmethod
    def _other_train_fields(cls, train: JsonDict) -> VisionTrainState:
        return cls._train_fields(train)

    def _build_mainline_portions(self) -> dict[int, list[_SegmentPortion]]:
        portions: dict[int, list[_SegmentPortion]] = {}
        station_list = getattr(self.engine, "_station_list", [])
        planner = getattr(self.engine, "_path_plan_for_station_pair", None)
        if not station_list or not callable(planner):
            return portions
        for origin_idx in range(len(station_list) - 1):
            destination_idx = origin_idx + 1
            plan = planner(origin_idx, destination_idx)
            if plan is None or plan.total_length_m <= 0:
                continue
            origin_mileage = float(station_list[origin_idx]["mileageM"])
            destination_mileage = float(station_list[destination_idx]["mileageM"])
            interval_m = destination_mileage - origin_mileage
            for constraint in plan.constraints:
                portions.setdefault(int(constraint.segment_id), []).append(
                    _SegmentPortion(
                        local_start_m=float(constraint.start_offset_m),
                        local_end_m=float(constraint.end_offset_m),
                        global_start_m=origin_mileage + interval_m * constraint.path_start_m / plan.total_length_m,
                        global_end_m=origin_mileage + interval_m * constraint.path_end_m / plan.total_length_m,
                    )
                )
        return portions

    def _match_signals(
        self,
        portions: dict[int, list[_SegmentPortion]],
        tolerance_m: float,
    ) -> dict[str, int]:
        candidates: list[tuple[int, float, str]] = []
        for signal in getattr(self.engine, "line_map", {}).get("signals", []):
            segment_id = signal.get("segmentId")
            if segment_id is None:
                continue
            offset_m = float(signal.get("offsetM", 0.0))
            portion = next((item for item in portions.get(int(segment_id), []) if item.contains(offset_m)), None)
            if portion is None:
                continue
            direction = {"0x55": "Forward", "0xaa": "Reverse"}.get(str(signal.get("direction", "")).lower())
            if direction is None:
                continue
            candidates.append((int(signal["id"]), portion.mileage_at(offset_m), direction))
        pair_distances = sorted(
            (
                (abs(protocol.mileage_m - mileage), protocol.protocol_id, source_id)
                for protocol in LINE9_SIGNALS_V13
                for source_id, mileage, direction in candidates
                if protocol.direction == direction and abs(protocol.mileage_m - mileage) <= tolerance_m
            ),
            key=lambda item: item[0],
        )
        return _assign_unique(pair_distances)

    def _match_switches(
        self,
        portions: dict[int, list[_SegmentPortion]],
        tolerance_m: float,
    ) -> dict[str, int]:
        candidates: list[tuple[int, float]] = []
        for switch in getattr(self.engine, "line_map", {}).get("switches", []):
            segment_id = switch.get("frogSegId")
            if segment_id is None or not portions.get(int(segment_id)):
                continue
            portion = portions[int(segment_id)][0]
            candidates.append((int(switch["id"]), (portion.global_start_m + portion.global_end_m) / 2.0))
        pair_distances = sorted(
            (
                (abs(protocol.mileage_m - mileage), protocol.protocol_id, source_id)
                for protocol in LINE9_SWITCHES_V13
                for source_id, mileage in candidates
                if abs(protocol.mileage_m - mileage) <= tolerance_m
            ),
            key=lambda item: item[0],
        )
        return _assign_unique(pair_distances)


def _assign_unique(pair_distances: Iterable[tuple[float, str, int]]) -> dict[str, int]:
    result: dict[str, int] = {}
    used_sources: set[int] = set()
    for _, protocol_id, source_id in pair_distances:
        if protocol_id in result or source_id in used_sources:
            continue
        result[protocol_id] = source_id
        used_sources.add(source_id)
    return result


def _edge_for_mileage(mileage_m: float, edges: tuple[ProtocolEdge, ...]) -> ProtocolEdge:
    for edge in edges:
        if edge.begin_m - 1e-6 <= mileage_m <= edge.end_m + 1e-6:
            return edge
    return min(
        edges,
        key=lambda edge: min(abs(mileage_m - edge.begin_m), abs(mileage_m - edge.end_m)),
    )


def _snapshot_value(snapshot: Any, name: str, default: Any) -> Any:
    if isinstance(snapshot, dict):
        return snapshot.get(name, default)
    return getattr(snapshot, name, default)


def _clamp(value: int, low: int, high: int) -> int:
    return min(max(int(value), low), high)
