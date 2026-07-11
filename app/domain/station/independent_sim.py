"""Stateful passenger-flow simulation, deliberately independent from SimulationEngine."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.station.services import (
    DayType, DwellTimeConfig, FlowScenario, PoissonPassengerFlowGenerator,
    StationFlowConfig, StationService, TrainLoadState,
)

JsonDict = dict[str, Any]


@dataclass
class PassengerTrain:
    train_id: str
    direction: str
    station_index: int
    phase: str = "DWELL"
    remaining_sec: int = 30
    load_pax: int = 0

    @property
    def passenger_mass_kg(self) -> float:
        """Mass contribution exported for the future dynamics adapter only."""
        return self.load_pax * 65.0


class IndependentPassengerSimulation:
    """Own clock, queues and train-stop events; no dependency on the main simulation."""

    def __init__(self, stations: list[JsonDict]) -> None:
        self.stations = stations
        self.start_time_ms = 6 * 3600 * 1000
        self.reset()

    def reset(self) -> None:
        configs: list[StationFlowConfig] = []
        for index, station in enumerate(self.stations):
            code = str(station["code"])
            base = 35.0 + (index % 5) * 12.0
            configs.extend([
                StationFlowConfig(code, base, 0.08 + (index % 4) * 0.03, "UP"),
                StationFlowConfig(code, base * 0.8, 0.08 + (index % 4) * 0.03, "DOWN"),
            ])
        self.station_service = StationService(
            PoissonPassengerFlowGenerator(configs, FlowScenario(DayType.MON_THU, random_seed=42)),
            DwellTimeConfig(base_dwell_sec=30.0, door_capacity_pax_per_sec=3.0),
        )
        self.sim_time_ms = self.start_time_ms
        self.tick = 0
        self.state = "IDLE"
        self.trains = [PassengerTrain("PAX-UP-01", "UP", 0, load_pax=280), PassengerTrain("PAX-DOWN-01", "DOWN", len(self.stations) - 1, load_pax=240)]
        self.events: list[JsonDict] = []

    def start(self) -> None:
        if self.state in {"IDLE", "STOPPED"}:
            self.reset()
        self.state = "RUNNING"

    def pause(self) -> None:
        if self.state == "RUNNING": self.state = "PAUSED"

    def resume(self) -> None:
        if self.state == "PAUSED": self.state = "RUNNING"

    def stop(self) -> None:
        self.state = "STOPPED"

    def step(self, seconds: int = 1) -> None:
        if self.state != "RUNNING": return
        for _ in range(max(1, min(int(seconds), 3600))):
            self.tick += 1
            self.sim_time_ms += 1000
            self.station_service.update_arrivals(self.sim_time_ms, 1.0)
            self.trains = [self._tick_train(train) for train in self.trains]

    def _tick_train(self, train: PassengerTrain) -> PassengerTrain:
        train.remaining_sec -= 1
        if train.remaining_sec > 0: return train
        if train.phase == "RUN":
            train.station_index += 1 if train.direction == "UP" else -1
            train.phase, train.remaining_sec = "DWELL", 30
            return self._serve_stop(train)
        if (train.direction == "UP" and train.station_index == len(self.stations) - 1) or (train.direction == "DOWN" and train.station_index == 0):
            train.direction = "DOWN" if train.direction == "UP" else "UP"
        segment_index = train.station_index if train.direction == "UP" else train.station_index - 1
        train.phase, train.remaining_sec = "RUN", 70 + (max(0, segment_index) % 4) * 12
        return train

    def _serve_stop(self, train: PassengerTrain) -> PassengerTrain:
        station = self.stations[train.station_index]
        result, plan = self.station_service.process_train_stop(
            sim_time_ms=self.sim_time_ms, station_id=str(station["code"]), direction=train.direction,
            train_load=TrainLoadState(train.train_id, train.load_pax, 600),
        )
        train.load_pax = result.updated_load.onboard_pax
        train.remaining_sec = int(round(plan.estimated_dwell_sec))
        self.events.append({
            "type": "TRAIN_STOP",
            "simTimeMs": self.sim_time_ms,
            "trainId": train.train_id,
            "stationCode": station["code"],
            "direction": train.direction,
            "boarding": result.boarding,
            "alighting": result.alighting,
            "waiting": result.waiting,
            "onboardPax": train.load_pax,
            "passengerMassKg": train.passenger_mass_kg,
        })
        self.events = self.events[-100:]
        return train

    def snapshot(self) -> JsonDict:
        return {
            "source": "independent-passenger-simulation",
            "clock": {"state": self.state, "tick": self.tick, "simTimeMs": self.sim_time_ms},
            "stations": [{"code": s["code"], "name": s["name"], "direction": direction, "waitingPax": platform.waiting_pax, "leftBehindPax": platform.left_behind_pax, "platformDensity": round(platform.platform_density_pax_per_m2, 3)} for (code, direction), platform in self.station_service.platforms.items() for s in self.stations if s["code"] == code],
            # Passenger load is intentionally exposed as an integration output.  The
            # independent UI does not render it; a future dynamics adapter consumes
            # passengerMassKg to update total train mass and resistance.
            "trains": [
                {**vars(train), "passengerMassKg": train.passenger_mass_kg}
                for train in self.trains
            ],
            "events": self.events,
        }
