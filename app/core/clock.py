from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable


TickCallback = Callable[[int, float], None]


class ClockState(str, Enum):
    IDLE = "IDLE"
    LOADED = "LOADED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


@dataclass
class SimulationClock:
    tick_seconds: float = 1.0
    state: ClockState = ClockState.IDLE
    current_tick: int = 0
    sim_time_seconds: float = 0.0

    def load(self) -> None:
        if self.state not in {ClockState.IDLE, ClockState.STOPPED}:
            raise RuntimeError(f"cannot load from {self.state.value}")
        self.current_tick = 0
        self.sim_time_seconds = 0.0
        self.state = ClockState.LOADED

    def start(self) -> None:
        if self.state not in {ClockState.LOADED, ClockState.PAUSED, ClockState.STOPPED}:
            raise RuntimeError(f"cannot start from {self.state.value}")
        self.state = ClockState.RUNNING

    def pause(self) -> None:
        if self.state != ClockState.RUNNING:
            raise RuntimeError(f"cannot pause from {self.state.value}")
        self.state = ClockState.PAUSED

    def resume(self) -> None:
        if self.state != ClockState.PAUSED:
            raise RuntimeError(f"cannot resume from {self.state.value}")
        self.state = ClockState.RUNNING

    def stop(self) -> None:
        if self.state not in {ClockState.RUNNING, ClockState.PAUSED, ClockState.LOADED}:
            raise RuntimeError(f"cannot stop from {self.state.value}")
        self.state = ClockState.STOPPED

    def step(self, callbacks: list[TickCallback] | None = None) -> int:
        if self.state != ClockState.RUNNING:
            raise RuntimeError(f"cannot step from {self.state.value}")
        self.current_tick += 1
        self.sim_time_seconds = self.current_tick * self.tick_seconds
        for callback in callbacks or []:
            callback(self.current_tick, self.sim_time_seconds)
        return self.current_tick

    def run_for_ticks(
        self,
        ticks: int,
        callbacks: list[TickCallback] | None = None,
    ) -> None:
        for _ in range(ticks):
            self.step(callbacks)

