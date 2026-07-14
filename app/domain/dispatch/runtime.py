"""Runtime departure observation and headway calculation for ATS dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.dispatch.services import RuleBasedDispatchService
from app.domain.dispatch.timetable import TrainService


@dataclass(frozen=True)
class DepartureRecord:
    train_id: str
    station_index: int
    station_id: str
    direction: str
    sim_time_s: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "trainId": self.train_id,
            "stationIndex": self.station_index,
            "stationId": self.station_id,
            "direction": self.direction,
            "simTimeS": round(self.sim_time_s, 3),
        }


class DispatchRuntimeCoordinator:
    """Turns real train phase transitions into ATS departure/headway state."""

    def __init__(self, service: RuleBasedDispatchService) -> None:
        self.service = service
        self._previous: dict[str, tuple[str, int, str]] = {}
        self._departures: list[DepartureRecord] = []

    def reset(self) -> None:
        self._previous.clear()
        self._departures.clear()

    def register_train(self, train: Any, service: TrainService | None = None) -> None:
        self.service.register_train(train.train_id, service)
        self._previous[train.train_id] = (
            str(train.phase),
            int(train.station_index),
            str(train.direction),
        )

    def assign_service(self, train_id: str, service: TrainService) -> None:
        state = self.service._train_states.get(train_id)
        if state is not None:
            state.service = service

    def unregister_train(self, train_id: str) -> None:
        self.service.unregister_train(train_id)
        self._previous.pop(train_id, None)

    def observe(self, trains: list[Any], sim_time_s: float) -> list[DepartureRecord]:
        active_ids = {str(train.train_id) for train in trains}
        for train_id in set(self._previous) - active_ids:
            self.unregister_train(train_id)

        new_records: list[DepartureRecord] = []
        for train in trains:
            train_id = str(train.train_id)
            current = (str(train.phase), int(train.station_index), str(train.direction))
            previous = self._previous.get(train_id)
            if previous is None:
                self.register_train(train)
                previous = current
            self.service.update_train_position(train_id, int(train.station_index), str(train.direction))
            departed = (
                previous[0] in {"DWELLING", "IDLE"}
                and current[0] not in {"DWELLING", "IDLE"}
                and previous[1] == current[1]
                and previous[2] == current[2]
            )
            if departed:
                record = DepartureRecord(
                    train_id=train_id,
                    station_index=int(train.station_index),
                    station_id=str(train.current_station_code),
                    direction=str(train.direction),
                    sim_time_s=sim_time_s,
                )
                self._departures.append(record)
                self._departures = self._departures[-500:]
                self.service.record_departure(train_id, sim_time_s, int(train.station_index))
                new_records.append(record)
            self._previous[train_id] = current
        return new_records

    def headways_for(
        self,
        train_id: str,
        station_index: int,
        direction: str,
        sim_time_s: float,
    ) -> tuple[float | None, float | None]:
        """Return elapsed departure gap to the front train and known rear gap."""
        same_location = [
            item for item in self._departures
            if item.station_index == station_index
            and item.direction == direction
            and item.train_id != train_id
        ]
        prior = [item for item in same_location if item.sim_time_s <= sim_time_s]
        front = sim_time_s - prior[-1].sim_time_s if prior else None

        own = next(
            (
                item for item in reversed(self._departures)
                if item.train_id == train_id
                and item.station_index == station_index
                and item.direction == direction
            ),
            None,
        )
        rear_candidates = [
            item for item in same_location
            if own is not None and item.sim_time_s > own.sim_time_s
        ]
        rear = rear_candidates[0].sim_time_s - own.sim_time_s if rear_candidates and own else None
        return front, rear

    def snapshot(self) -> dict[str, Any]:
        return {
            "registeredTrainCount": len(self._previous),
            "departureCount": len(self._departures),
            "recentDepartures": [item.to_dict() for item in self._departures[-30:]],
        }
