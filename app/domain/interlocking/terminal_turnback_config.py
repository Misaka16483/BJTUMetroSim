"""Declarative terminal turnback arrangements for the imported Line 9 map.

The route table contains the physical interlocking routes, but it does not
label a route as a terminal turnback manoeuvre.  Keep that operational
meaning in this small, auditable configuration rather than scattering route
IDs through planning code.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TurnbackPhaseConfig:
    """One movement between a platform/turnback point and the next point."""

    direction: str
    route_ids: tuple[str, ...]


@dataclass(frozen=True)
class TerminalTurnbackConfig:
    """A configured two-or-more-phase terminal turnback arrangement."""

    terminal_id: str
    origin_platform_id: int
    final_platform_id: int
    turning_point_segment_id: int
    phases: tuple[TurnbackPhaseConfig, ...]


# The data source has no authoritative turnback flag.  These arrangements were
# verified against its route, signal, Seg, and turnout records:
#
# * GGZ: platform 1 (S13) -- route 10 --> S22, change ends, then follow the
#   signal-continuous backward chain route 13 (F9->F7, 11->10) and route 12
#   (F7->F6, 10->57) into platform 2 (S39).
# * GTG: platform 26 (S220) -- route 90 --> S213, change ends, then route 87
#   returns in the backward direction to platform 25 (S207).
DEFAULT_TERMINAL_TURNBACKS: tuple[TerminalTurnbackConfig, ...] = (
    TerminalTurnbackConfig(
        terminal_id="GGZ",
        origin_platform_id=1,
        final_platform_id=2,
        turning_point_segment_id=22,
        phases=(
            TurnbackPhaseConfig(direction="forward", route_ids=("10",)),
            TurnbackPhaseConfig(direction="backward", route_ids=("13", "12")),
        ),
    ),
    TerminalTurnbackConfig(
        terminal_id="GTG",
        origin_platform_id=26,
        final_platform_id=25,
        turning_point_segment_id=213,
        phases=(
            TurnbackPhaseConfig(direction="forward", route_ids=("90",)),
            TurnbackPhaseConfig(direction="backward", route_ids=("87",)),
        ),
    ),
)
