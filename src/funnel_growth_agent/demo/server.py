"""The demo console server: stdlib HTTP on 127.0.0.1, Server-Sent Events for the feed, and an
allowlisted asset route. Replay mode streams a recording with stage pacing; live mode runs the
real propose and apply and records them as it goes."""

from __future__ import annotations

import json
import mimetypes
import queue
import sys
import threading
import time
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from ..config import Settings
from ..events import Event
from .events import (
    JsonlSink,
    Recorder,
    asset_allowlist,
    asset_id,
    demo_dir,
    read_events,
    recording_path,
    referenced_paths,
    run_id_of,
)
from .replay import deploy_events, schedule, split_phases

PREVIEW_BASE = "http://localhost:5173/pm"
Mode = Literal["replay", "live"]
Status = Literal[
    "idle",
    "proposing",
    "proposed",
    "declined",
    "applying",
    "live",
    "deploying",
    "deployed",
    "failed",
]


class Broadcaster:
    def __init__(self) -> None:
        self._subscribers: list[queue.Queue[dict[str, Any]]] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        q: queue.Queue[dict[str, Any]] = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def publish(self, payload: dict[str, Any]) -> None:
        with self._lock:
            targets = list(self._subscribers)
        for q in targets:
            q.put(payload)


@dataclass
class ConsoleState:
    mode: Mode
    base_version: str
    status: Status = "idle"
    run_id: str | None = None
    variant: str | None = None
    recording: str | None = None
    speed: float = 1.0
    buffer: list[Event] = field(default_factory=list)
    preflight: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    can_apply: bool = False
    can_deploy: bool = False
    prod_url: str | None = None
    pr_url: str | None = None


def frame(event_name: str, payload: dict[str, Any], event_id: int | None = None) -> bytes:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_name}")
    lines.append("data: " + json.dumps(payload, default=str))
    return ("\n".join(lines) + "\n\n").encode("utf-8")


def gh_ready(lab: Path, timeout: float = 5.0) -> str | None:
    """The GitHub login `gh` will push and open pull requests as, or None."""
    import subprocess

    try:
        completed = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            cwd=lab if lab.is_dir() else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None if completed.returncode == 0 else None


def dev_server_up(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - localhost
            return 200 <= response.status < 500
    except Exception:  # noqa: BLE001 - any failure means "not reachable"
        return False


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        """A browser tab closing or reloading resets its socket; that is not an error."""
        error = sys.exc_info()[1]
        if isinstance(error, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)

    def __init__(
        self,
        address: tuple[str, int],
        settings: Settings,
        *,
        mode: Mode,
        events: list[Event] | None = None,
        recording: Path | None = None,
        speed: float = 1.0,
        verbose: bool = False,
        preview_base: str = PREVIEW_BASE,
    ) -> None:
        super().__init__(address, Handler)
        self.settings = settings
        self.verbose = verbose
        self.preview_base = preview_base
        self.broadcaster = Broadcaster()
        self.lock = threading.Lock()
        self.recorded = events or []
        self.recorder: Recorder | None = None
        self.sink: JsonlSink | None = None
        self._stop = threading.Event()
        base = settings.base_version
        if self.recorded:
            base = str(self.recorded[0].data.get("baseVersion") or base)
        self.state = ConsoleState(
            mode=mode,
            base_version=base,
            recording=recording.name if recording else None,
            speed=speed,
        )
        self.state.can_apply = mode == "live" or any(e.phase == "apply" for e in self.recorded)
        self.gh_login = gh_ready(settings.pricing_lab_dir) if mode == "live" else None
        self.state.can_deploy = bool(self.gh_login) or bool(deploy_events(self.recorded))
        self._assets: dict[str, Path] = {}
        self._assets_len = -1
        self.state.preflight = self.preflight()

    # ---------------------------------------------------------------- state

    def refresh_preflight(self) -> None:
        """Preflight is cheap and the lab dev server is often started after the console,
        so re-run it whenever the page (re)loads while nothing is running."""
        if self.state.status == "idle":
            self.state.preflight = self.preflight()

    def state_dict(self) -> dict[str, Any]:
        s = self.state
        return {
            "mode": s.mode,
            "status": s.status,
            "baseVersion": s.base_version,
            "previewBase": self.preview_base,
            "runId": s.run_id,
            "variant": s.variant,
            "recording": s.recording,
            "speed": s.speed,
            "canApply": s.can_apply,
            "canDeploy": s.can_deploy,
            "prodUrl": s.prod_url,
            "prUrl": s.pr_url,
            "reloadDelayMs": 0 if s.mode == "replay" else 1500,
            "preflight": s.preflight,
            "error": s.error,
            "events": len(s.buffer),
        }

    def _set_status(self, status: Status, **changes: Any) -> None:
        with self.lock:
            self.state.status = status
            for key, value in changes.items():
                setattr(self.state, key, value)
        self.broadcaster.publish({"__state__": self.state_dict()})

    def publish_event(self, event: Event) -> None:
        with self.lock:
            self.state.buffer.append(event)
        self.broadcaster.publish(event.model_dump())
        if event.kind == "proposal_saved":
            self._set_status(
                "proposed" if event.data.get("decision") == "experiment" else "declined",
                run_id=event.data.get("runId"),
            )
        elif event.kind == "propose_failed":
            self._set_status("failed", error=str(event.data.get("error")))
        elif event.kind == "apply_started":
            self._set_status("applying", variant=event.data.get("variant"))
        elif event.kind == "apply_done":
            self._set_status("live", variant=event.data.get("variant"))
        elif event.kind == "apply_failed":
            self._set_status("failed", error=str(event.data.get("error")))
        elif event.kind == "deploy_started":
            self._set_status("deploying")
        elif event.kind == "deploy_done":
            self._set_status(
                "deployed",
                prod_url=event.data.get("prodUrl"),
                pr_url=event.data.get("prUrl"),
            )
        elif event.kind == "deploy_failed":
            self._set_status("failed", error=str(event.data.get("error")))

    def buffer_snapshot(self) -> list[Event]:
        with self.lock:
            return list(self.state.buffer)

    def assets(self) -> dict[str, Path]:
        source = self.recorded if self.state.mode == "replay" else self.buffer_snapshot()
        if len(source) != self._assets_len:
            self._assets = asset_allowlist(source)
            self._assets_len = len(source)
        return self._assets

    # ------------------------------------------------------------ preflight

    def preflight(self) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        s = self.settings
        base_url = f"{self.preview_base}/{self.state.base_version}"
        checks.append({"ok": dev_server_up(base_url), "label": f"lab dev server serves {base_url}"})
        if self.state.mode == "replay":
            run_id = run_id_of(self.recorded)
            checks.append(
                {
                    "ok": bool(self.recorded),
                    "label": f"recording {self.state.recording}: {len(self.recorded)} events, run {run_id}",
                }
            )
            done = next((e for e in self.recorded if e.kind == "apply_done"), None)
            if done is not None:
                variant = str(done.data.get("variant"))
                folder = s.pricing_lab_dir / "funnels" / variant
                checks.append({"ok": folder.is_dir(), "label": f"variant folder {folder}"})
                site = s.site_path.read_text(encoding="utf-8") if s.site_path.is_file() else ""
                checks.append(
                    {"ok": f"- {variant}" in site, "label": f"{variant} listed in site.yaml"}
                )
                checks.append(
                    {
                        "ok": dev_server_up(f"{self.preview_base}/{variant}"),
                        "label": f"lab serves {self.preview_base}/{variant}",
                    }
                )
            wanted = referenced_paths(self.recorded)
            present = sum(1 for p in wanted if p.is_file())
            checks.append(
                {
                    "ok": present == len(wanted),
                    "label": f"{present}/{len(wanted)} referenced files present",
                }
            )
        else:
            checks.append({"ok": bool(s.anthropic_api_key), "label": "ANTHROPIC_API_KEY set"})
            checks.append(
                {"ok": bool(s.gemini_api_key), "label": "GEMINI_API_KEY set (tiles + judge)"}
            )
            checks.append(
                {"ok": s.landing_path.is_file(), "label": f"base landing {s.landing_path}"}
            )
            checks.append(
                {
                    "ok": bool(self.gh_login),
                    "label": f"gh signed in as {self.gh_login} (deploy opens the pull request)"
                    if self.gh_login
                    else "gh not signed in: deploy is off (`gh auth login`)",
                }
            )
        return checks

    # -------------------------------------------------------------- actions

    def start(self) -> tuple[int, dict[str, Any]]:
        if self.state.status != "idle":
            return 409, {"error": f"cannot start while {self.state.status}"}
        self._set_status("proposing")
        if self.state.mode == "replay":
            propose_events, _ = split_phases(self.recorded)
            threading.Thread(
                target=self._replay, args=(propose_events, "proposed"), daemon=True
            ).start()
        else:
            threading.Thread(target=self._live_propose, daemon=True).start()
        return 202, {"ok": True}

    def apply(self) -> tuple[int, dict[str, Any]]:
        if self.state.status != "proposed":
            return 409, {"error": f"cannot apply while {self.state.status}"}
        if not self.state.can_apply:
            return 409, {"error": "this recording has no apply phase"}
        self._set_status("applying")
        if self.state.mode == "replay":
            _, apply_events = split_phases(self.recorded)
            threading.Thread(target=self._replay, args=(apply_events, "live"), daemon=True).start()
        else:
            threading.Thread(target=self._live_apply, daemon=True).start()
        return 202, {"ok": True}

    def deploy(self) -> tuple[int, dict[str, Any]]:
        if self.state.status != "live":
            return 409, {"error": f"cannot deploy while {self.state.status}"}
        if not self.state.can_deploy:
            return 409, {"error": "deploy is off: gh is not signed in"}
        self._set_status("deploying")
        if self.state.mode == "replay":
            threading.Thread(
                target=self._replay, args=(deploy_events(self.recorded), "deployed"), daemon=True
            ).start()
        else:
            threading.Thread(target=self._live_deploy, daemon=True).start()
        return 202, {"ok": True}

    def reset(self) -> tuple[int, dict[str, Any]]:
        if self.state.mode != "replay":
            return 409, {"error": "reset is for replay mode"}
        self._stop.set()
        time.sleep(0.05)
        self._stop = threading.Event()
        with self.lock:
            self.state.buffer.clear()
            self.state.run_id = None
            self.state.variant = None
            self.state.error = None
            self.state.prod_url = None
            self.state.pr_url = None
        self._set_status("idle")
        return 200, {"ok": True}

    def _replay(self, events: list[Event], final: Status) -> None:
        stop = self._stop
        for delay, event in schedule(events, speed=self.state.speed):
            if stop.wait(delay):
                return
            self.publish_event(event)
        if self.state.status in {"proposing", "applying", "deploying"}:
            self._set_status(final)

    def _new_recorder(self) -> Recorder:
        stamp = time.strftime("%Y-%m-%dT%H%M%S")
        self.sink = JsonlSink(demo_dir(self.settings) / f"recording-{stamp}.events.jsonl")
        self.recorder = Recorder(sinks=[self.sink, self.publish_event])
        return self.recorder

    def _live_propose(self) -> None:
        from ..proposal import propose

        recorder = self._new_recorder()
        try:
            saved = propose(self.settings, on_event=recorder)
        except Exception as error:  # noqa: BLE001 - reported to the page via propose_failed
            if self.state.status != "failed":
                self._set_status("failed", error=str(error))
            return
        if self.sink is not None:
            self.sink.rename(recording_path(self.settings, saved.run_id))
            self.state.recording = self.sink.path.name

    def _live_apply(self) -> None:
        from ..apply import apply_run

        recorder = self.recorder or self._new_recorder()
        recorder.phase = "apply"
        run_id = self.state.run_id
        if not run_id:
            self._set_status("failed", error="no run id to apply")
            return
        try:
            apply_run(self.settings, run_id, on_data=recorder)
        except Exception as error:  # noqa: BLE001 - reported via apply_failed
            if self.state.status != "failed":
                self._set_status("failed", error=str(error))

    def _live_deploy(self) -> None:
        from ..deploy import deploy_variant

        recorder = self.recorder or self._new_recorder()
        recorder.phase = "deploy"
        variant = self.state.variant
        if not variant:
            self._set_status("failed", error="no variant to deploy")
            return
        try:
            deploy_variant(self.settings, variant, on_data=recorder)
        except Exception as error:  # noqa: BLE001 - reported via deploy_failed
            if self.state.status != "failed":
                self._set_status("failed", error=str(error))


class Handler(BaseHTTPRequestHandler):
    server: ConsoleServer  # type: ignore[assignment]
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        if self.server.verbose:
            super().log_message(format, *args)

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json")

    def do_GET(self) -> None:  # noqa: N802 - stdlib name
        path = urlsplit(self.path).path
        if path == "/":
            page = resources.files("funnel_growth_agent.demo").joinpath("console.html")
            self._send(200, page.read_bytes(), "text/html; charset=utf-8")
        elif path == "/state":
            self.server.refresh_preflight()
            self._json(200, self.server.state_dict())
        elif path == "/events":
            self._sse()
        elif path.startswith("/asset/"):
            self._asset(path[len("/asset/") :])
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib name
        path = urlsplit(self.path).path
        actions = {
            "/start": self.server.start,
            "/apply": self.server.apply,
            "/deploy": self.server.deploy,
            "/reset": self.server.reset,
        }
        action = actions.get(path)
        if action is None:
            self._json(404, {"error": "not found"})
            return
        code, payload = action()
        self._json(code, payload)

    def _asset(self, key: str) -> None:
        target = self.server.assets().get(key)
        if target is None or not target.is_file():
            self._json(404, {"error": "no such asset"})
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), content_type)

    def _sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = self.server.broadcaster.subscribe()
        try:
            self.wfile.write(frame("state", self.server.state_dict()))
            for event in self.server.buffer_snapshot():
                self.wfile.write(frame(event.kind, event.model_dump(), event.seq))
            self.wfile.write(b": ready\n\n")
            self.wfile.flush()
            while True:
                try:
                    payload = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                if "__state__" in payload:
                    self.wfile.write(frame("state", payload["__state__"]))
                else:
                    self.wfile.write(frame(str(payload["kind"]), payload, int(payload["seq"])))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.server.broadcaster.unsubscribe(q)


def make_server(
    settings: Settings,
    *,
    recording: Path | None,
    live: bool,
    port: int = 8765,
    host: str = "127.0.0.1",
    speed: float = 1.0,
    verbose: bool = False,
    preview_base: str = PREVIEW_BASE,
) -> ConsoleServer:
    events = read_events(recording) if recording is not None and not live else []
    return ConsoleServer(
        (host, port),
        settings,
        mode="live" if live else "replay",
        events=events,
        recording=recording,
        speed=speed,
        verbose=verbose,
        preview_base=preview_base,
    )


def serve(
    settings: Settings,
    *,
    recording: Path | None,
    live: bool,
    port: int = 8765,
    speed: float = 1.0,
    open_browser: bool = False,
    verbose: bool = False,
) -> None:
    server = make_server(
        settings, recording=recording, live=live, port=port, speed=speed, verbose=verbose
    )
    host, bound = server.server_address[:2]
    url = f"http://{host}:{bound}/"
    print(f"Demo console ({server.state.mode}): {url}")
    for check in server.state.preflight:
        print(f"  {'✓' if check['ok'] else '✗'} {check['label']}")
    print("Press R to run the agent, A to apply, F for fullscreen. Ctrl-C stops the server.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


__all__ = ["ConsoleServer", "asset_id", "make_server", "serve"]
