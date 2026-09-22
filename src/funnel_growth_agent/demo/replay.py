"""Pacing for replaying a recording on stage: fixed, readable delays per event kind instead
of the real timestamps (a live apply spends minutes encoding video)."""

from __future__ import annotations

from ..events import Event

# Seconds to wait before showing an event of this kind, at speed 1.0.
PACING: dict[str, float] = {
    "propose_started": 0.0,
    "context_ready": 0.4,
    "tool_call": 0.5,
    "tool_result": 0.9,
    "proposal": 1.2,
    "proposal_saved": 0.3,
    "propose_failed": 0.3,
    "apply_started": 0.4,
    "apply_stage": 0.45,
    "tile_started": 0.6,
    "tile_candidate": 0.8,
    "tile_judged": 1.4,
    "apply_done": 0.8,
    "apply_failed": 0.3,
    "deploy_started": 0.4,
    "deploy_stage": 0.7,
    "deploy_done": 0.8,
    "deploy_failed": 0.3,
}
DEFAULT_DELAY = 0.4


def split_phases(events: list[Event]) -> tuple[list[Event], list[Event]]:
    propose = [e for e in events if e.phase == "propose"]
    apply = [e for e in events if e.phase == "apply"]
    return propose, apply


def deploy_events(events: list[Event]) -> list[Event]:
    return [e for e in events if e.phase == "deploy"]


def schedule(
    events: list[Event], *, speed: float = 1.0, pacing: dict[str, float] | None = None
) -> list[tuple[float, Event]]:
    """(delay before, event) pairs. The first event shows at once. Speed 2 halves every wait."""
    table = pacing or PACING
    speed = max(speed, 0.01)
    out: list[tuple[float, Event]] = []
    for index, event in enumerate(events):
        delay = 0.0 if index == 0 else table.get(event.kind, DEFAULT_DELAY) / speed
        out.append((round(delay, 3), event))
    return out


def total_duration(scheduled: list[tuple[float, Event]]) -> float:
    return round(sum(delay for delay, _ in scheduled), 3)
