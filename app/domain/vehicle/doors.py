from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DoorSide(str, Enum):
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    BOTH = "BOTH"
    NONE = "NONE"


class DoorUnitStatus(str, Enum):
    CLOSED_LOCKED = "CLOSED_LOCKED"
    OPENING = "OPENING"
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    FAULT = "FAULT"
    OBSTRUCTED = "OBSTRUCTED"
    ISOLATED = "ISOLATED"
    EMERGENCY_UNLOCKED = "EMERGENCY_UNLOCKED"


_PROTOCOL_STATUS = {
    DoorUnitStatus.CLOSED_LOCKED: 0,
    DoorUnitStatus.OPENING: 1,
    DoorUnitStatus.OPEN: 1,
    # The hardware protocol has no transitional state. A closing door must
    # remain "open" on the wire until its closed-and-locked proof is present.
    DoorUnitStatus.CLOSING: 1,
    DoorUnitStatus.FAULT: 2,
    DoorUnitStatus.OBSTRUCTED: 3,
    DoorUnitStatus.ISOLATED: 4,
    DoorUnitStatus.EMERGENCY_UNLOCKED: 5,
}


@dataclass
class DoorUnit:
    door_index: int
    side: DoorSide
    status: DoorUnitStatus = DoorUnitStatus.CLOSED_LOCKED

    def to_dict(self) -> dict[str, Any]:
        return {
            "doorIndex": self.door_index,
            "side": self.side.value,
            "status": self.status.value,
            "protocolCode": _PROTOCOL_STATUS[self.status],
        }


@dataclass
class CarDoorState:
    car_index: int
    doors: list[DoorUnit] = field(default_factory=list)

    @classmethod
    def create(cls, car_index: int, doors_per_car: int = 8) -> CarDoorState:
        if doors_per_car != 8:
            raise ValueError("Line 9 hardware protocol requires 8 door units per car")
        doors = [
            DoorUnit(index, DoorSide.LEFT if index < 4 else DoorSide.RIGHT)
            for index in range(doors_per_car)
        ]
        return cls(car_index=car_index, doors=doors)

    def protocol_word(self) -> int:
        word = 0
        for door in self.doors:
            word |= (_PROTOCOL_STATUS[door.status] & 0xF) << (door.door_index * 4)
        return word

    def to_dict(self) -> dict[str, Any]:
        return {
            "carIndex": self.car_index,
            "doors": [door.to_dict() for door in self.doors],
            "protocolWord": self.protocol_word(),
        }


@dataclass
class TrainDoorSystem:
    cars: list[CarDoorState] = field(default_factory=list)
    control_mode: str = "AUTO"
    permitted_side: DoorSide = DoorSide.NONE
    active_side: DoorSide = DoorSide.NONE
    transition_remaining_sec: float = 0.0
    last_command_source: str | None = None
    last_rejection_reason: str | None = None

    @classmethod
    def line9_default(cls) -> TrainDoorSystem:
        return cls(cars=[CarDoorState.create(index) for index in range(6)])

    @property
    def all_closed_and_locked(self) -> bool:
        safe = {DoorUnitStatus.CLOSED_LOCKED, DoorUnitStatus.ISOLATED}
        return all(door.status in safe for car in self.cars for door in car.doors)

    @property
    def any_open(self) -> bool:
        return any(
            door.status in {
                DoorUnitStatus.OPENING,
                DoorUnitStatus.OPEN,
                DoorUnitStatus.CLOSING,
                DoorUnitStatus.OBSTRUCTED,
                DoorUnitStatus.EMERGENCY_UNLOCKED,
            }
            for car in self.cars
            for door in car.doors
        )

    @property
    def aggregate_state(self) -> str:
        statuses = {door.status for car in self.cars for door in car.doors}
        for status in (
            DoorUnitStatus.EMERGENCY_UNLOCKED,
            DoorUnitStatus.FAULT,
            DoorUnitStatus.OBSTRUCTED,
            DoorUnitStatus.OPENING,
            DoorUnitStatus.OPEN,
            DoorUnitStatus.CLOSING,
        ):
            if status in statuses:
                return status.value
        return "CLOSED"

    def set_permission(self, side: str | DoorSide) -> None:
        self.permitted_side = side if isinstance(side, DoorSide) else DoorSide(str(side))

    def request_open(self, side: str | DoorSide, source: str, transition_sec: float = 1.0) -> bool:
        requested = side if isinstance(side, DoorSide) else DoorSide(str(side))
        if requested not in {DoorSide.LEFT, DoorSide.RIGHT}:
            self.last_rejection_reason = "INVALID_DOOR_SIDE"
            return False
        allowed = self.permitted_side in {requested, DoorSide.BOTH}
        if not allowed:
            self.last_rejection_reason = "DOOR_SIDE_NOT_PERMITTED"
            return False
        changed = False
        for car in self.cars:
            for door in car.doors:
                # A driver may cancel a close command while still at a platform.
                # CLOSING therefore remains a valid target for reopening.
                if door.side == requested and door.status in {
                    DoorUnitStatus.CLOSED_LOCKED,
                    DoorUnitStatus.CLOSING,
                }:
                    door.status = DoorUnitStatus.OPENING
                    changed = True
        if not changed and self.active_side != requested:
            self.last_rejection_reason = "DOORS_NOT_AVAILABLE"
            return False
        self.active_side = requested
        self.transition_remaining_sec = max(0.0, transition_sec)
        self.last_command_source = source
        self.last_rejection_reason = None
        return True

    def request_close(self, source: str, transition_sec: float = 1.0) -> bool:
        changed = False
        for car in self.cars:
            for door in car.doors:
                if door.status in {DoorUnitStatus.OPENING, DoorUnitStatus.OPEN}:
                    door.status = DoorUnitStatus.CLOSING
                    changed = True
        if not changed:
            if self.all_closed_and_locked:
                self.last_rejection_reason = None
                return True
            self.last_rejection_reason = "DOORS_CANNOT_CLOSE"
            return False
        self.transition_remaining_sec = max(0.0, transition_sec)
        self.last_command_source = source
        self.last_rejection_reason = None
        return True

    def advance(self, dt_sec: float) -> None:
        if self.transition_remaining_sec <= 0.0:
            return
        self.transition_remaining_sec = max(0.0, self.transition_remaining_sec - max(0.0, dt_sec))
        if self.transition_remaining_sec > 0.0:
            return
        for car in self.cars:
            for door in car.doors:
                if door.status == DoorUnitStatus.OPENING:
                    door.status = DoorUnitStatus.OPEN
                elif door.status == DoorUnitStatus.CLOSING:
                    door.status = DoorUnitStatus.CLOSED_LOCKED
        if self.all_closed_and_locked:
            self.active_side = DoorSide.NONE

    def to_dict(self) -> dict[str, Any]:
        return {
            "carCount": len(self.cars),
            "doorsPerCar": 8,
            "controlMode": self.control_mode,
            "permittedSide": self.permitted_side.value,
            "activeSide": self.active_side.value,
            "aggregateState": self.aggregate_state,
            "allClosedAndLocked": self.all_closed_and_locked,
            "anyDoorOpen": self.any_open,
            "tractionInterlockActive": not self.all_closed_and_locked,
            "transitionRemainingSec": round(self.transition_remaining_sec, 2),
            "lastCommandSource": self.last_command_source,
            "lastRejectionReason": self.last_rejection_reason,
            "cars": [car.to_dict() for car in self.cars],
        }
