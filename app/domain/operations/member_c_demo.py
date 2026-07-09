"""Member C visual demo runner — Phase 2 section occupancy.

Drives a handful of trains along the line and calls
SectionOccupationService each tick so the frontend can
visualise track topology, train positions and axle-section states.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.domain.interlocking.route_catalog import RouteCatalog
from app.domain.interlocking.section_occupation import SectionOccupationService
from app.domain.line.services import LineMapRepository, TrackQueryService
from app.domain.signal.models import TrainState

JsonDict = dict[str, Any]


@dataclass
class DemoTrain:
    train_id: str
    seg_id: int
    offset_m: float
    direction: str = "FORWARD"  # "FORWARD" | "BACKWARD"
    speed_mps: float = 10.0
    length_m: float = 120.0
    position_m: float = 0.0
    color: str = "#e74c3c"


class MemberCDemoRunner:
    """Drives a few trains along the 9号线 upstream direction."""

    def __init__(self, cache_path: str | Path) -> None:
        line_map = LineMapRepository(cache_path).load()
        self.track = TrackQueryService(line_map)
        self.section_occ = SectionOccupationService(line_map)
        self.catalog = RouteCatalog(line_map)

        # Build ordered segment chain for the upstream direction.
        self._seg_chain: list[int] = self._build_mainline_chain(line_map)

        # Pick interesting segments that have route / signal coverage.
        self._station_segs = self._find_station_segments(line_map)

        self.tick: int = 0
        self.sim_time_ms: int = 0

        # ── scenario: 3 trains at staggered positions ──
        n = len(self._seg_chain)
        if n < 3:
            self.trains: list[DemoTrain] = []
            return

        self.trains: list[DemoTrain] = [
            DemoTrain(
                train_id="T0901",
                seg_id=self._seg_chain[0],
                offset_m=30.0,
                speed_mps=14.0,
                color="#e74c3c",
            ),
            DemoTrain(
                train_id="T0902",
                seg_id=self._seg_chain[min(n // 3, n - 1)],
                offset_m=20.0,
                speed_mps=11.0,
                color="#3498db",
            ),
            DemoTrain(
                train_id="T0903",
                seg_id=self._seg_chain[min(2 * n // 3, n - 1)],
                offset_m=60.0,
                speed_mps=9.0,
                color="#2ecc71",
            ),
        ]

        # Initialise position_m
        for t in self.trains:
            t.position_m = self._guess_position_m(t.seg_id, t.offset_m)

    # ── tick interface ────────────────────────────────────────────────

    def step(self, dt_sec: float = 0.5) -> None:
        """Advance the simulation by one tick."""
        self.tick += 1
        self.sim_time_ms += int(dt_sec * 1000)

        for t in self.trains:
            distance_m = t.speed_mps * dt_sec
            self._advance_train(t, distance_m)

        train_states = self._to_train_states()
        self.section_occ.update(train_states, self.track)

    def state_snapshot(self) -> JsonDict:
        """Return current simulation state for the frontend."""
        occ_snapshot = self.section_occ.snapshot()

        # Build segment list with topology links for rendering
        segments: list[JsonDict] = []
        for i, seg_id in enumerate(self._seg_chain):
            seg = self.track.get_segment(seg_id)
            if seg is None:
                continue
            segments.append({
                "id": seg_id,
                "lengthM": seg.get("lengthM", 0),
                "stationName": self._station_name_for_seg(seg_id),
                "prevSegId": self._seg_chain[i - 1] if i > 0 else None,
                "nextSegId": self._seg_chain[i + 1] if i + 1 < len(self._seg_chain) else None,
                "hasSwitch": bool(seg.get("endDivergingSegId") or seg.get("startDivergingSegId")),
            })

        # axle-section → seg mapping for frontend coloring
        axle_sections: list[JsonDict] = []
        for sid in sorted(self.section_occ.axle_section_ids, key=lambda x: int(x)):
            axle_def = self.section_occ._axle_defs.get(sid)
            if axle_def is None:
                continue
            axle_sections.append({
                "sectionId": sid,
                "name": axle_def.name,
                "segmentIds": sorted(axle_def.segment_ids),
                "occupied": self.section_occ.is_occupied(sid),
            })

        # per-train seg→color mapping for visualisation
        seg_train_colors: dict[int, str] = {}
        states = self._to_train_states()
        for train_obj, tstate in zip(self.trains, states):
            covered = self.section_occ._segments_covered_by_train(tstate, self.track)
            for sid in covered:
                if sid not in seg_train_colors:
                    seg_train_colors[sid] = train_obj.color
                else:
                    seg_train_colors[sid] = "#f39c12"  # orange = contested

        return {
            "tick": self.tick,
            "simTimeMs": self.sim_time_ms,
            "trains": [
                {
                    "id": t.train_id,
                    "segId": t.seg_id,
                    "offsetM": round(t.offset_m, 2),
                    "speedMps": t.speed_mps,
                    "direction": t.direction,
                    "lengthM": t.length_m,
                    "color": t.color,
                }
                for t in self.trains
            ],
            "segments": segments,
            "axleSections": axle_sections,
            "segTrainColors": seg_train_colors,
            "sectionOccupancies": [
                occ
                for occ in occ_snapshot
                if occ.get("occupied")
            ],
            "occupiedCount": sum(1 for o in occ_snapshot if o.get("occupied")),
            "totalAxleSections": len(self.section_occ.axle_section_ids),
        }

    # ── internal -------------------------------------------------------

    def _advance_train(self, train: DemoTrain, distance_m: float) -> None:
        """Move *train* forward *distance_m* along the upstream chain."""
        seg = self.track.get_segment(train.seg_id)
        if seg is None:
            return
        seg_len = float(seg.get("lengthM", 100))

        if train.direction == "FORWARD":
            new_offset = train.offset_m + distance_m
        else:
            new_offset = train.offset_m - distance_m

        # Cross segment boundaries
        while True:
            if train.direction == "FORWARD" and new_offset > seg_len:
                next_id = seg.get("endForwardSegId")
            elif train.direction == "BACKWARD" and new_offset < 0:
                next_id = seg.get("startForwardSegId")
            else:
                break  # still within this segment

            if next_id is None:
                # End of mainline → reverse direction smoothly (no teleport)
                train.direction = "BACKWARD"
                # Stay on the same segment, start moving backward
                train.offset_m = seg_len if train.offset_m > seg_len else train.offset_m
                new_offset = max(0, train.offset_m - abs(distance_m))
                break

            # Move to next segment
            if train.direction == "FORWARD":
                overflow = new_offset - seg_len
            else:
                overflow = -new_offset

            train.seg_id = int(next_id)
            seg = self.track.get_segment(train.seg_id)
            if seg is None:
                break
            seg_len = float(seg.get("lengthM", 100))
            train.offset_m = overflow if train.direction == "FORWARD" else seg_len - overflow
            new_offset = train.offset_m
            continue

        if 0 <= new_offset <= seg_len:
            train.offset_m = new_offset

        train.position_m = self._guess_position_m(train.seg_id, train.offset_m)

    def _guess_position_m(self, seg_id: int, offset_m: float) -> float:
        """Approximate cumulative position from the start of the upstream chain."""
        total = 0.0
        for sid in self._seg_chain:
            if sid == seg_id:
                return total + offset_m
            seg = self.track.get_segment(sid)
            if seg is not None:
                total += float(seg.get("lengthM", 0))
        return offset_m

    def _to_train_states(self) -> list[TrainState]:
        return [
            TrainState(
                train_id=t.train_id,
                sim_time_ms=self.sim_time_ms,
                seg_id=t.seg_id,
                offset_m=t.offset_m,
                position_m=t.position_m,
                speed_mps=t.speed_mps,
                direction=t.direction,
                length_m=t.length_m,
            )
            for t in self.trains
        ]

    def _build_mainline_chain(self, line_map: JsonDict) -> list[int]:
        """Follow endForwardSegId from a root segment to build a linear chain.

        Only follows the main forward path — no diverging branches.
        """
        segments = line_map.get("segments", [])
        if not segments:
            return []

        seg_by_id: dict[int, dict] = {}
        for seg in segments:
            sid = seg.get("id")
            if sid is not None:
                seg_by_id[int(sid)] = seg

        # Root = segment no other seg points to via endForwardSegId
        has_predecessor: set[int] = set()
        for seg in seg_by_id.values():
            nxt = seg.get("endForwardSegId")
            if nxt is not None:
                has_predecessor.add(int(nxt))

        roots = sorted(sid for sid in seg_by_id if sid not in has_predecessor)
        if not roots:
            roots = [min(seg_by_id)]

        # Walk forward
        chain: list[int] = []
        visited: set[int] = set()
        seg_id = roots[0]
        while seg_id is not None and seg_id not in visited:
            visited.add(seg_id)
            chain.append(seg_id)
            seg = seg_by_id.get(seg_id)
            next_id = seg.get("endForwardSegId") if seg else None
            seg_id = int(next_id) if next_id is not None else None
        return chain

    def _seg_at_index(self, idx: int) -> int:
        if not self._seg_chain:
            return 5
        return self._seg_chain[idx % len(self._seg_chain)]

    def _find_station_segments(self, line_map: JsonDict) -> list[int]:
        platforms = line_map.get("platforms", [])
        return [int(p["segmentId"]) for p in platforms if p.get("segmentId") is not None]

    def _station_name_for_seg(self, seg_id: int) -> str | None:
        # Quick mapping: check if this seg is a platform seg
        for p in self.track.platforms_by_seg.get(seg_id, []):
            return f"站台{p.get('id', '')}"
        return None
