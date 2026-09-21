"""Recording and reading event logs. One JSON line per Event (events.py) in
<data dir>/demo/<run_id>.events.jsonl."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import Settings
from ..events import Event, Phase

Sink = Callable[[Event], None]

RECORDING_PREFIX = "recording-"


def demo_dir(settings: Settings) -> Path:
    return settings.data_dir / "demo"


class Recorder:
    """Stamps seq/t/ts/phase on (kind, data) calls and fans the Event out to every sink.
    Pass it as `on_event` to propose and as `on_data` to apply_run; flip `phase` between."""

    def __init__(
        self,
        sinks: list[Sink] | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        phase: Phase = "propose",
    ) -> None:
        self.sinks: list[Sink] = list(sinks or [])
        self.clock = clock
        self.phase: Phase = phase
        self.seq = 0
        self.started = clock()
        self.events: list[Event] = []

    def __call__(self, kind: str, data: dict[str, Any]) -> None:
        self.seq += 1
        event = Event(
            seq=self.seq,
            t=round(self.clock() - self.started, 3),
            ts=datetime.now(UTC).isoformat(timespec="seconds"),
            phase=self.phase,
            kind=kind,  # type: ignore[arg-type]
            data=json.loads(json.dumps(data, default=str)),
        )
        self.events.append(event)
        for sink in self.sinks:
            sink(event)


class JsonlSink:
    """Appends and flushes one line per event; `rename` moves the file once the run id is known."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = path.open("a", encoding="utf-8")

    def __call__(self, event: Event) -> None:
        self._handle.write(event.model_dump_json() + "\n")
        self._handle.flush()

    def rename(self, new_path: Path) -> Path:
        self._handle.close()
        new_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(self.path, new_path)
        self.path = new_path
        self._handle = new_path.open("a", encoding="utf-8")
        return new_path

    def close(self) -> None:
        self._handle.close()


def write_events(path: Path, events: list[Event]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(e.model_dump_json() + "\n" for e in events), encoding="utf-8")
    return path


def read_events(path: Path) -> list[Event]:
    events: list[Event] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(Event.model_validate_json(line))
    return events


def recording_path(settings: Settings, run_id: str) -> Path:
    return demo_dir(settings) / f"{run_id}.events.jsonl"


def list_recordings(settings: Settings) -> list[Path]:
    """Finished recordings, newest first (temporary `recording-*` files are skipped)."""
    folder = demo_dir(settings)
    if not folder.is_dir():
        return []
    paths = [p for p in folder.glob("*.events.jsonl") if not p.name.startswith(RECORDING_PREFIX)]
    return sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)


def newest_recording(settings: Settings) -> Path | None:
    paths = list_recordings(settings)
    return paths[0] if paths else None


def resolve_recording(settings: Settings, name: str | None) -> Path:
    """A run id, a file name, or a path; None means the newest recording."""
    if name is None:
        newest = newest_recording(settings)
        if newest is None:
            raise FileNotFoundError(
                f"No recordings in {demo_dir(settings)}. Run `funnel-growth demo synthesize "
                "<run_id>` for an existing run or `funnel-growth demo record` for a new one."
            )
        return newest
    for candidate in (Path(name), recording_path(settings, name), demo_dir(settings) / name):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"No recording named {name!r} in {demo_dir(settings)}")


def run_id_of(events: list[Event]) -> str | None:
    for event in events:
        if event.kind in {"proposal_saved", "apply_started", "apply_done"}:
            run_id = event.data.get("runId")
            if run_id:
                return str(run_id)
    return None


def asset_id(raw: Any) -> str:
    """`<parent dir>/<file name>`: what the console derives from any path in an event."""
    path = Path(str(raw))
    return f"{path.parent.name}/{path.name}"


def referenced_paths(events: list[Event]) -> list[Path]:
    """Every local file an event log names, in order of first mention."""
    seen: dict[str, Path] = {}

    def add(raw: Any) -> None:
        if raw:
            path = Path(str(raw))
            seen.setdefault(str(path), path)

    for event in events:
        data = event.data
        if event.kind == "context_ready":
            for row in data.get("ranked") or []:
                add(row.get("imagePath"))
        elif event.kind == "tool_result":
            result = data.get("result") or {}
            if data.get("name") == "research_landing":
                for shot in result.get("screenshots") or []:
                    add(shot)
        elif event.kind == "tile_started":
            for ref in data.get("references") or []:
                add(ref.get("path"))
        elif event.kind == "tile_candidate":
            add(data.get("path"))
        elif event.kind == "apply_stage":
            add(data.get("posterPath"))
    return list(seen.values())


def asset_allowlist(events: list[Event]) -> dict[str, Path]:
    """Files the console may serve, keyed by asset_id. Only files the log names and only
    ones that exist; nothing outside this map is ever served."""
    return {asset_id(path): path for path in referenced_paths(events) if path.is_file()}
