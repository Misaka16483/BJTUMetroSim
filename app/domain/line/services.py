from __future__ import annotations

import json
from pathlib import Path
from typing import Any


JsonDict = dict[str, Any]


class LineMapRepository:
    def __init__(self, cache_path: str | Path) -> None:
        self.cache_path = Path(cache_path)

    def load(self) -> JsonDict:
        with self.cache_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def save(self, line_map: JsonDict) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self.cache_path.open("w", encoding="utf-8") as handle:
            json.dump(line_map, handle, ensure_ascii=False, indent=2)


class TrackQueryService:
    def __init__(self, line_map: JsonDict) -> None:
        self.line_map = line_map
        self.segments = {
            int(item["id"]): item for item in line_map.get("segments", []) if item.get("id") is not None
        }
        self.signals_by_seg = self._group_by_seg(line_map.get("signals", []), "segmentId")
        self.platforms_by_seg = self._group_by_seg(line_map.get("platforms", []), "segmentId")
        self.speed_by_seg = self._group_by_seg(line_map.get("speedRestrictions", []), "segmentId")
        self.gradients = line_map.get("gradients", [])

    @staticmethod
    def _group_by_seg(items: list[JsonDict], key: str) -> dict[int, list[JsonDict]]:
        grouped: dict[int, list[JsonDict]] = {}
        for item in items:
            seg_id = item.get(key)
            if seg_id is None:
                continue
            grouped.setdefault(int(seg_id), []).append(item)
        return grouped

    def get_segment(self, seg_id: int) -> JsonDict | None:
        return self.segments.get(int(seg_id))

    def get_next_segments(self, seg_id: int, direction: str = "forward") -> list[JsonDict]:
        segment = self.get_segment(seg_id)
        if not segment:
            return []
        keys = (
            ["endForwardSegId", "endDivergingSegId"]
            if direction == "forward"
            else ["startForwardSegId", "startDivergingSegId"]
        )
        next_ids = [segment.get(key) for key in keys]
        return [self.segments[int(next_id)] for next_id in next_ids if next_id in self.segments]

    def get_speed_limit(self, seg_id: int, offset_m: float) -> JsonDict | None:
        candidates = self.speed_by_seg.get(int(seg_id), [])
        covering = [
            item
            for item in candidates
            if self._offset_in_range(offset_m, item.get("startOffsetM"), item.get("endOffsetM"))
        ]
        if covering:
            return min(covering, key=lambda item: item.get("speedLimitMps") or 9999)
        if candidates:
            return min(
                candidates,
                key=lambda item: abs((item.get("startOffsetM") or 0.0) - offset_m),
            )
        return None

    def get_gradient(self, seg_id: int, offset_m: float) -> JsonDict | None:
        matches = [
            item
            for item in self.gradients
            if item.get("startSegmentId") == int(seg_id) or item.get("endSegmentId") == int(seg_id)
        ]
        if not matches:
            return None
        return min(
            matches,
            key=lambda item: abs((item.get("startOffsetM") or item.get("endOffsetM") or 0.0) - offset_m),
        )

    def get_nearest_platform(
        self,
        seg_id: int,
        offset_m: float,
        direction: str = "forward",
    ) -> JsonDict | None:
        platforms = self.platforms_by_seg.get(int(seg_id), [])
        if not platforms:
            return None
        if direction == "forward":
            ahead = [item for item in platforms if (item.get("offsetM") or 0.0) >= offset_m]
        else:
            ahead = [item for item in platforms if (item.get("offsetM") or 0.0) <= offset_m]
        candidates = ahead or platforms
        return min(candidates, key=lambda item: abs((item.get("offsetM") or 0.0) - offset_m))

    def get_next_signal(
        self,
        seg_id: int,
        offset_m: float,
        direction: str = "forward",
    ) -> JsonDict | None:
        signals = self.signals_by_seg.get(int(seg_id), [])
        if not signals:
            return None
        if direction == "forward":
            candidates = [item for item in signals if (item.get("offsetM") or 0.0) >= offset_m]
        else:
            candidates = [item for item in signals if (item.get("offsetM") or 0.0) <= offset_m]
        if not candidates:
            return None
        reverse = direction != "forward"
        return sorted(candidates, key=lambda item: item.get("offsetM") or 0.0, reverse=reverse)[0]

    @staticmethod
    def _offset_in_range(offset: float, start: float | None, end: float | None) -> bool:
        if start is None and end is None:
            return True
        if start is None:
            return offset <= float(end)
        if end is None:
            return offset >= float(start)
        low, high = sorted([float(start), float(end)])
        return low <= offset <= high

