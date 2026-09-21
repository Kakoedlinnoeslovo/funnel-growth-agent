"""Structured events the propose and apply pipelines emit for a UI. Core code only ever calls
`emit(kind, data)`; whoever listens (the demo recorder, a console) stamps sequence and time.
No listener means no cost: every hook is optional."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

EventKind = Literal[
    "propose_started",
    "context_ready",
    "tool_call",
    "tool_result",
    "proposal",
    "proposal_saved",
    "propose_failed",
    "apply_started",
    "apply_stage",
    "tile_started",
    "tile_candidate",
    "tile_judged",
    "apply_done",
    "apply_failed",
]
Phase = Literal["propose", "apply"]

EmitData = Callable[[str, dict[str, Any]], None]


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seq: int
    t: float
    ts: str
    phase: Phase
    kind: EventKind
    data: dict[str, Any]


def emit_to(on_event: EmitData | None, kind: str, data: dict[str, Any]) -> None:
    if on_event is not None:
        on_event(kind, data)
