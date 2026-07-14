"""Interlocking subsystem — Member C Phase 2.

SectionOccupationService
    Tracks axle / logical section occupancy derived from train positions.

RouteCatalog
    Immutable catalogue of all routes with pre-computed conflict table
    and switch requirements.

SwitchLockService
    Switch position, locking and health state.

InterlockingRuleEngine
    Stateless pre-condition checks for route locking.

RouteService (next)
    Route request, locking, release and lifecycle management.

SignalAspectResolver (next)
    Signal aspect derivation from route / occupancy / switch state.
"""

from app.domain.interlocking.models import (
    AxleSectionDef,
    LogicalSectionDef,
    RouteDef,
    RouteRequest,
    RouteResult,
    RouteState,
    SectionOccupation,
    SwitchDef,
    SwitchState,
)
from app.domain.interlocking.route_catalog import RouteCatalog
from app.domain.interlocking.route_chain_planner import (
    OperationIntent,
    RouteChainPlan,
    RouteChainPlanner,
    TurnbackPhase,
    TurnbackPlan,
)
from app.domain.interlocking.route_service import RouteService
from app.domain.interlocking.rule_engine import InterlockingRuleEngine, RouteCheckResult
from app.domain.interlocking.section_occupation import SectionOccupationService
from app.domain.interlocking.signal_resolver import SignalAspectResolver
from app.domain.interlocking.switch_lock import SwitchLockService
from app.domain.interlocking.train_track_trace import TrainTrackTrace

__all__ = [
    "AxleSectionDef",
    "InterlockingRuleEngine",
    "LogicalSectionDef",
    "RouteCatalog",
    "OperationIntent",
    "RouteChainPlan",
    "RouteChainPlanner",
    "TurnbackPhase",
    "TurnbackPlan",
    "RouteCheckResult",
    "RouteDef",
    "RouteRequest",
    "RouteResult",
    "RouteService",
    "RouteState",
    "SectionOccupation",
    "SectionOccupationService",
    "SignalAspectResolver",
    "SwitchDef",
    "SwitchLockService",
    "SwitchState",
    "TrainTrackTrace",
]
