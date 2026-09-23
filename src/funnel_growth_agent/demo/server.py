"""The demo console server: stdlib HTTP on 127.0.0.1, Server-Sent Events for the feed, and an
allowlisted asset route. Replay mode streams a recording with stage pacing; live mode runs the
real propose and apply and records them as it goes."""

from __future__ import annotations

import json
import mimetypes
import queue
import re
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
from urllib.parse import parse_qs, unquote, urlsplit

from ..config import Settings
from ..events import Event
from ..workflow_uploads import UploadError
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
        self.workflow = None
        if mode == "live":
            from ..workflow import Workflow

            self.workflow = Workflow(settings, preview_base=preview_base)

    def server_close(self) -> None:
        if getattr(self, "workflow", None) is not None:
            self.workflow.close()
        super().server_close()

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
            name = "workflow.html" if self.server.state.mode == "live" else "console.html"
            page = resources.files("funnel_growth_agent.demo").joinpath(name)
            self._send(200, page.read_bytes(), "text/html; charset=utf-8")
        elif path in {"/workflow.js", "/workflow.css", "/instrument-sans.woff2"}:
            page = resources.files("funnel_growth_agent.demo").joinpath(path[1:])
            content_type = {
                ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".woff2": "font/woff2",
            }[Path(path).suffix]
            self._send(200, page.read_bytes(), content_type)
        elif path.startswith("/api/"):
            self._workflow_api(path, "GET")
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
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if origin and origin != f"http://{host}":
            self._json(403, {"error": "Cross-origin actions are not allowed"})
            return
        if path.startswith("/api/"):
            self._workflow_api(path, "POST")
            return
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

    def _workflow_api(self, path: str, method: str) -> None:
        workflow = self.server.workflow
        if workflow is None:
            self._json(409, {"error": "Start the demo with --live to use the creative workflow"})
            return
        host = urlsplit("http://" + self.headers.get("Host", "")).hostname
        if host not in {"localhost", "127.0.0.1", self.server.server_address[0]}:
            self._json(403, {"error": "Untrusted host"})
            return
        try:
            payload = {}
            if method == "POST" and path == "/api/uploads":
                if self.headers.get("Transfer-Encoding") or not self.headers.get("Content-Length"):
                    raise UploadError("Upload requires a file size.", 411)
                length = int(self.headers["Content-Length"])
                filename = unquote(self.headers.get("X-File-Name", ""))
                previous_timeout = self.connection.gettimeout()
                self.connection.settimeout(30)
                try:
                    result = workflow.upload(self.rfile, filename=filename, length=length)
                finally:
                    self.connection.settimeout(previous_timeout)
                self._json(201, result)
                return
            if method == "POST":
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    self._json(415, {"error": "Expected application/json"})
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > 262144:
                    self._json(413, {"error": "Request is too large"})
                    return
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("Expected a JSON object")
            if path == "/api/catalog" and method == "GET":
                self._json(200, workflow.catalog())
            elif path == "/api/resolve-url" and method == "POST":
                self._json(200, workflow.resolve_url(payload.get("url", "")))
            elif path == "/api/import-url" and method == "POST":
                self._json(202, workflow.start_import(payload.get("url", "")))
            elif path == "/api/library" and method == "GET":
                self._json(200, {"pages": workflow.library_catalog(), "busy": workflow.busy})
            elif path.startswith("/api/library/") and method == "POST":
                self._json(200, workflow.edit_library(path.removeprefix("/api/library/"), payload))
            elif path == "/api/drafts":
                if method == "GET":
                    self._json(200, {"drafts": workflow.list_drafts(), "busy": workflow.busy})
                else:
                    self._json(201, workflow.create(payload))
            elif path.startswith("/api/drafts/"):
                parts = path.removeprefix("/api/drafts/").split("/")
                if len(parts) == 1 and method == "GET":
                    self._json(200, workflow.present(parts[0]))
                elif len(parts) == 2 and parts[1] == "events" and method == "GET":
                    self._workflow_sse(workflow, parts[0])
                elif len(parts) == 3 and parts[1] == "preview" and method == "GET":
                    draft = workflow.load(parts[0])
                    number = int(parts[2])
                    if (
                        number < 1
                        or number > len(draft["revisions"])
                        or draft["revisions"][number - 1]["status"] != "ready"
                    ):
                        raise ValueError("Preview revision is unavailable")
                    dist = workflow.path(parts[0]) / "revisions" / str(number) / "lab/dist"
                    location = workflow.previews.url(dist, draft["variant"])
                    if draft.get("funnelMode"):
                        from urllib.parse import urlencode

                        from ..funnel_steps import step_document

                        step, _ = step_document(
                            dist.parent / "funnels" / draft["variant"], draft["activeStepId"]
                        )
                        location += (
                            step["path"].rstrip("/") + "?" + urlencode({"_step": step["id"]})
                        )
                    self.send_response(302)
                    self.send_header("Location", location)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                elif len(parts) == 2 and parts[1] == "select_step" and method == "POST":
                    self._json(200, workflow.select_step(parts[0], payload))
                elif len(parts) == 2 and parts[1] == "messages" and method == "POST":
                    self._json(202, workflow.start_message(parts[0], payload))
                elif len(parts) == 2 and method == "POST":
                    self._json(202, workflow.start_job(parts[0], parts[1], payload))
                else:
                    self._json(404, {"error": "Unknown draft action"})
            elif path.startswith("/api/assets/") and method == "GET":
                target = workflow.asset_paths.get(path.removeprefix("/api/assets/"))
                if target is None or not target.is_file():
                    self._json(404, {"error": "Asset unavailable"})
                else:
                    self._send_asset(target)
            else:
                self._json(404, {"error": "Not found"})
        except UploadError as error:
            self.close_connection = True
            self._json(error.status, {"error": str(error)})
        except KeyError:
            self._json(404, {"error": "Draft not found"})
        except (ValueError, FileNotFoundError) as error:
            self._json(400, {"error": str(error)})
        except Exception as error:
            self._json(500, {"error": str(error)})

    def _workflow_sse(self, workflow, draft_id: str) -> None:
        query = parse_qs(urlsplit(self.path).query)
        cursor = max(0, int(self.headers.get("Last-Event-ID") or query.get("after", ["0"])[0]))
        # Validate before sending headers; the durable sequence is the stream's cursor.
        workflow.load(draft_id)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.connection.settimeout(20)
        try:
            while not workflow.closed:
                events = workflow.events_after(draft_id, cursor, wait=10)
                for event in events:
                    self.wfile.write(f"id: {event['seq']}\ndata: {json.dumps(event)}\n\n".encode())
                    cursor = event["seq"]
                if not events:
                    self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        finally:
            self.close_connection = True

    def _asset(self, key: str) -> None:
        target = self.server.assets().get(key)
        if target is None or not target.is_file():
            self._json(404, {"error": "no such asset"})
            return
        self._send_asset(target)

    def _send_asset(self, target: Path) -> None:
        """Single-range streaming lets original videos seek without reloading from zero."""
        size = target.stat().st_size
        start, end, partial = 0, size - 1, False
        requested = self.headers.get("Range")
        if requested:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
            if match and any(match.groups()):
                left, right = match.groups()
                if left:
                    start = int(left)
                    end = min(int(right), size - 1) if right else size - 1
                else:
                    start = max(0, size - int(right))
                partial = 0 <= start <= end < size
            if not partial:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        self.send_response(206 if partial else 200)
        self.send_header(
            "Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        )
        self.send_header("Content-Length", str(max(0, end - start + 1)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with target.open("rb") as handle:
                handle.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = handle.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        with self.server.lock:
            q = self.server.broadcaster.subscribe()
            snapshot = list(self.server.state.buffer)
        try:
            self.wfile.write(frame("state", self.server.state_dict()))
            for event in snapshot:
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
    preview_base: str = PREVIEW_BASE,
) -> None:
    server = make_server(
        settings,
        recording=recording,
        live=live,
        port=port,
        speed=speed,
        verbose=verbose,
        preview_base=preview_base,
    )
    host, bound = server.server_address[:2]
    url = f"http://{host}:{bound}/"
    print(f"Demo console ({server.state.mode}): {url}")
    for check in server.state.preflight:
        print(f"  {'✓' if check['ok'] else '✗'} {check['label']}")
    if live:
        print(
            "Select a baseline and creatives, generate a draft, refine it, then publish. Ctrl-C stops the server."
        )
    else:
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
