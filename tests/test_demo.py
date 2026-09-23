"""Event hooks, recording, replay pacing, synthesis and the console server."""

from __future__ import annotations

import http.client
import json
import threading
import types
from importlib import resources
from pathlib import Path

import pytest

from funnel_growth_agent.apply import apply_run
from funnel_growth_agent.demo.events import (
    JsonlSink,
    Recorder,
    asset_allowlist,
    asset_id,
    read_events,
    recording_path,
    resolve_recording,
    run_id_of,
)
from funnel_growth_agent.demo.replay import PACING, schedule, split_phases, total_duration
from funnel_growth_agent.demo.server import make_server
from funnel_growth_agent.demo.synthesize import synthesize_events, synthesize_recording
from funnel_growth_agent.events import Event
from funnel_growth_agent.proposal import propose
from redesign_helpers import ScriptedModel, fake_media_tools, redesign_proposal


def _record(settings, *, apply: bool = True) -> tuple[Recorder, str]:
    recorder = Recorder(clock=iter(range(0, 10_000)).__next__)
    saved = propose(
        settings,
        model=ScriptedModel(
            redesign_proposal(),
            tools=[("get_landing_cta_metrics", {}), ("get_current_landing", {})],
        ),
        on_event=recorder,
    )
    if apply:
        recorder.phase = "apply"
        apply_run(
            settings,
            saved.run_id,
            validate=lambda _d: None,
            media_tools=fake_media_tools(),
            on_data=recorder,
        )
    return recorder, saved.run_id


def test_propose_emits_a_paired_tool_trace_and_the_saved_proposal(settings) -> None:
    recorder, run_id = _record(settings, apply=False)
    kinds = [e.kind for e in recorder.events]
    assert kinds[:2] == ["propose_started", "context_ready"]
    assert kinds[-2:] == ["proposal", "proposal_saved"]
    calls = [e for e in recorder.events if e.kind == "tool_call"]
    results = [e for e in recorder.events if e.kind == "tool_result"]
    assert [c.data["name"] for c in calls] == ["get_landing_cta_metrics", "get_current_landing"]
    assert [c.data["id"] for c in calls] == [r.data["id"] for r in results]
    assert results[1].data["result"]["sections"][0]["id"] == "kittl-hero"
    assert recorder.events[-1].data == {
        "runId": run_id,
        "status": "proposed",
        "decision": "experiment",
    }
    assert all(e.phase == "propose" for e in recorder.events)
    assert [e.seq for e in recorder.events] == list(range(1, len(recorder.events) + 1))


def test_propose_return_value_is_identical_with_and_without_the_hook(settings) -> None:
    plain = propose(settings, model=ScriptedModel(redesign_proposal()))
    hooked = propose(settings, model=ScriptedModel(redesign_proposal()), on_event=lambda *_: None)
    assert plain.model_dump(exclude={"run_id"}) == hooked.model_dump(exclude={"run_id"})


def test_apply_emits_stages_tiles_and_done(settings) -> None:
    recorder, run_id = _record(settings)
    apply_events = [e for e in recorder.events if e.phase == "apply"]
    kinds = [e.kind for e in apply_events]
    assert kinds[0] == "apply_started" and kinds[-1] == "apply_done"
    stages = [e.data["stage"] for e in apply_events if e.kind == "apply_stage"]
    assert stages[0] == "copy" and stages[-1] == "publish"
    assert stages.count("validate") == 2
    assert kinds.count("tile_started") == 1 and kinds.count("tile_candidate") == 3
    judged = next(e for e in apply_events if e.kind == "tile_judged")
    assert judged.data["chosen"] == 1 and len(judged.data["scores"]) == 3
    assert judged.data["scores"][0]["houseStyle"] == 6 and "total" in judged.data["scores"][0]
    done = apply_events[-1].data
    assert done["variant"] == "v7_a1" and done["previewUrl"].endswith("/pm/v7_a1")
    assert done["runId"] == run_id


def test_cached_tiles_emit_the_same_event_shape_as_fresh_ones(settings) -> None:
    first, _ = _record(settings)
    fresh = [e.kind for e in first.events if e.kind.startswith("tile_")]
    again = Recorder()
    saved = propose(settings, model=ScriptedModel(redesign_proposal()), on_event=again)
    again.phase = "apply"
    apply_run(
        settings,
        saved.run_id,
        validate=lambda _d: None,
        media_tools=fake_media_tools(),
        on_data=again,
    )
    cached = [e.kind for e in again.events if e.kind.startswith("tile_")]
    assert cached == fresh


def test_apply_failure_emits_apply_failed_then_raises(settings) -> None:
    recorder = Recorder()
    saved = propose(settings, model=ScriptedModel(redesign_proposal()), on_event=recorder)
    recorder.phase = "apply"

    def boom(_directory: Path) -> None:
        raise RuntimeError("validator exploded")

    with pytest.raises(RuntimeError):
        apply_run(
            settings, saved.run_id, validate=boom, media_tools=fake_media_tools(), on_data=recorder
        )
    assert recorder.events[-1].kind == "apply_failed"
    assert "validator exploded" in recorder.events[-1].data["error"]


def test_jsonl_sink_round_trips_and_renames(settings) -> None:
    sink = JsonlSink(settings.data_dir / "demo" / "recording-x.events.jsonl")
    recorder = Recorder(sinks=[sink])
    recorder("propose_started", {"baseVersion": "v7", "path": Path("/tmp/x")})
    recorder("proposal_saved", {"runId": "run-9", "status": "proposed", "decision": "experiment"})
    final = sink.rename(recording_path(settings, "run-9"))
    sink.close()
    events = read_events(final)
    assert [e.kind for e in events] == ["propose_started", "proposal_saved"]
    assert events[0].data["path"] == "/tmp/x"  # non-JSON values are stringified
    assert run_id_of(events) == "run-9"
    assert resolve_recording(settings, None) == final
    assert resolve_recording(settings, "run-9") == final
    with pytest.raises(FileNotFoundError):
        resolve_recording(settings, "nope")


def test_asset_allowlist_only_names_existing_files_from_the_log(settings, tmp_path: Path) -> None:
    recorder, _ = _record(settings)
    allowed = asset_allowlist(recorder.events)
    candidate = next(e for e in recorder.events if e.kind == "tile_candidate").data["path"]
    assert asset_id(candidate) in allowed and allowed[asset_id(candidate)] == Path(candidate)
    ref = next(e for e in recorder.events if e.kind == "tile_started").data["references"][0]["path"]
    assert asset_id(ref) in allowed
    ghost = Event(
        seq=99,
        t=0,
        ts="",
        phase="apply",
        kind="tile_candidate",
        data={"stem": "s", "index": 0, "path": str(tmp_path / "missing.png")},
    )
    assert asset_id(str(tmp_path / "missing.png")) not in asset_allowlist([*recorder.events, ghost])
    assert "etc/passwd" not in allowed


def test_schedule_uses_pacing_and_speed(settings) -> None:
    recorder, _ = _record(settings)
    propose_events, apply_events = split_phases(recorder.events)
    assert propose_events[-1].kind == "proposal_saved" and apply_events[0].kind == "apply_started"
    paced = schedule(propose_events)
    assert paced[0][0] == 0.0
    assert paced[1][0] == PACING["context_ready"]
    fast = schedule(propose_events, speed=2)
    assert fast[1][0] == PACING["context_ready"] / 2
    assert total_duration(schedule(recorder.events)) < 30


def test_synthesize_rebuilds_a_full_log_from_memory_and_caches(settings) -> None:
    recorder, run_id = _record(settings)
    events = synthesize_events(settings, run_id)
    kinds = [e.kind for e in events]
    assert kinds[0] == "propose_started" and events[0].data["synthesized"] is True
    assert "get_current_landing" in [e.data["name"] for e in events if e.kind == "tool_call"]
    proposal = next(e for e in events if e.kind == "proposal").data["output"]
    assert proposal["decision"] == "experiment" and proposal["changes"]["media"]["showcase"]
    assert kinds.count("tile_started") == 1 and kinds.count("tile_candidate") == 3
    assert kinds.count("tile_judged") == 1
    assert events[-1].kind == "apply_done" and events[-1].data["variant"] == "v7_a1"
    path = synthesize_recording(settings, run_id)
    assert path == recording_path(settings, run_id) and len(read_events(path)) == len(events)


def test_synthesize_stops_after_propose_for_an_unapplied_run(settings) -> None:
    _, run_id = _record(settings, apply=False)
    events = synthesize_events(settings, run_id)
    assert events[-1].kind == "proposal_saved" and all(e.phase == "propose" for e in events)


def _request(port: int, method: str, path: str) -> tuple[int, bytes, str]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path)
    response = conn.getresponse()
    body = response.read()
    content_type = response.getheader("Content-Type") or ""
    conn.close()
    return response.status, body, content_type


def _read_sse_until(port: int, kind: str, limit: float = 10.0) -> list[dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=limit)
    conn.request("GET", "/events")
    response = conn.getresponse()
    seen: list[dict] = []
    current: dict = {}
    while True:
        raw = response.fp.readline()
        if not raw:
            break
        line = raw.decode("utf-8").rstrip("\n")
        if line.startswith("event: "):
            current = {"event": line[7:]}
        elif line.startswith("data: "):
            current["data"] = json.loads(line[6:])
        elif line == "" and current:
            seen.append(current)
            if current.get("event") == kind:
                break
            current = {}
    conn.close()
    return seen


def test_console_server_replays_a_recording_and_serves_only_allowlisted_assets(settings) -> None:
    _, run_id = _record(settings)
    recording = synthesize_recording(settings, run_id)
    server = make_server(
        settings,
        recording=recording,
        live=False,
        port=0,
        speed=1000,
        preview_base="http://127.0.0.1:9/pm",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        status, body, content_type = _request(port, "GET", "/")
        assert status == 200 and b"<title>funnel-growth console</title>" in body
        status, body, _ = _request(port, "GET", "/state")
        state = json.loads(body)
        assert state["mode"] == "replay" and state["status"] == "idle" and state["canApply"]
        assert state["baseVersion"] == "v7"
        assert any("recording" in c["label"] and c["ok"] for c in state["preflight"])

        status, body, _ = _request(port, "POST", "/apply")
        assert status == 409
        status, body, _ = _request(port, "POST", "/start")
        assert status == 202
        seen = _read_sse_until(port, "proposal_saved")
        assert seen[0]["event"] == "state"
        assert [s["event"] for s in seen if s["event"] == "tool_call"]
        status, body, _ = _request(port, "GET", "/state")
        assert json.loads(body)["status"] == "proposed"

        status, body, _ = _request(port, "POST", "/start")
        assert status == 409
        status, body, _ = _request(port, "POST", "/apply")
        assert status == 202
        seen = _read_sse_until(port, "apply_done")
        assert any(s["event"] == "tile_judged" for s in seen)
        status, body, _ = _request(port, "GET", "/state")
        assert json.loads(body)["status"] == "live" and json.loads(body)["variant"] == "v7_a1"
        assert not json.loads(body)["canDeploy"], "this recording has no deploy phase"
        status, body, _ = _request(port, "POST", "/deploy")
        assert status == 409

        candidate = next(s for s in seen if s["event"] == "tile_candidate")["data"]["data"]["path"]
        status, body, content_type = _request(port, "GET", "/asset/" + asset_id(candidate))
        assert status == 200 and content_type.startswith("image/png") and body
        status, _, _ = _request(port, "GET", "/asset/tiles/other.png")
        assert status == 404
        status, _, _ = _request(port, "GET", "/asset/../../etc/passwd")
        assert status == 404

        status, _, _ = _request(port, "POST", "/reset")
        assert status == 200
        status, body, _ = _request(port, "GET", "/state")
        assert json.loads(body)["status"] == "idle" and json.loads(body)["events"] == 0
    finally:
        server.shutdown()
        server.server_close()


def test_console_html_is_packaged_and_never_injects_model_text() -> None:
    page = resources.files("funnel_growth_agent.demo").joinpath("console.html").read_text()
    assert "--lime:#C6F135" in page and "/events" in page and "/apply" in page
    assert "/deploy" in page and "autodeploy" in page
    assert "innerHTML" not in page and "insertAdjacentHTML" not in page


def test_console_deploy_runs_after_apply_and_reports_the_pull_request(
    settings, monkeypatch
) -> None:
    """Live mode: D is only enabled once a variant is live and gh is signed in; the deploy
    events flow through the same recorder and land the console on `deployed`."""
    from funnel_growth_agent.demo import server as server_module

    monkeypatch.setattr(server_module, "gh_ready", lambda lab, timeout=5.0: "roman")
    monkeypatch.setattr(server_module, "dev_server_up", lambda url, timeout=1.5: True)

    def fake_deploy(settings, variant, *, on_data=None, **_kw):
        on_data("deploy_started", {"variant": variant, "branch": f"agent/{variant}"})
        on_data("deploy_stage", {"variant": variant, "stage": "pr", "message": "#7 open"})
        on_data(
            "deploy_stage",
            {"variant": variant, "stage": "merge", "message": "waiting for someone to merge #7"},
        )
        on_data(
            "deploy_done",
            {
                "variant": variant,
                "prUrl": "https://github.com/acme/lab/pull/7",
                "prodUrl": "https://lab.vercel.app/pm/" + variant,
                "merged": True,
            },
        )

    import funnel_growth_agent.deploy as deploy_module

    monkeypatch.setattr(deploy_module, "deploy_variant", fake_deploy)
    server = make_server(settings, recording=None, live=True, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        _, body, _ = _request(port, "GET", "/state")
        state = json.loads(body)
        assert state["canDeploy"] and any(
            "gh signed in as roman" in c["label"] for c in state["preflight"]
        )
        status, _, _ = _request(port, "POST", "/deploy")
        assert status == 409, "nothing is live yet"
        # Skip propose/apply: put the console where apply_done leaves it.
        server.publish_event(
            Event(
                seq=1,
                t=0,
                ts="now",
                phase="apply",
                kind="apply_done",
                data={"runId": "r", "variant": "v7_a1", "previewUrl": "x"},
            )
        )
        assert server.state.status == "live"
        status, _, _ = _request(port, "POST", "/deploy")
        assert status == 202
        seen = _read_sse_until(port, "deploy_done")
        stages = [s["data"]["data"]["stage"] for s in seen if s["event"] == "deploy_stage"]
        assert stages == ["pr", "merge"]
        _, body, _ = _request(port, "GET", "/state")
        state = json.loads(body)
        assert state["status"] == "deployed"
        assert state["prodUrl"] == "https://lab.vercel.app/pm/v7_a1"
        assert state["prUrl"] == "https://github.com/acme/lab/pull/7"
        assert server.recorder.events[-1].phase == "deploy"
    finally:
        server.shutdown()
        server.server_close()


def test_console_preflight_rechecks_the_lab_dev_server_on_state_requests(
    settings, monkeypatch
) -> None:
    """The lab's `npm run dev` is usually started after the console, so the ✗ shown at boot
    must clear once the page reloads and the dev server is up."""
    from funnel_growth_agent.demo import server as server_module

    up = {"value": False}
    monkeypatch.setattr(server_module, "dev_server_up", lambda url, timeout=1.5: up["value"])
    _, run_id = _record(settings)
    recording = synthesize_recording(settings, run_id)
    server = make_server(settings, recording=recording, live=False, port=0, speed=1000)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        lab = lambda state: next(c for c in state["preflight"] if "lab dev server" in c["label"])  # noqa: E731
        assert not lab(server.state_dict())["ok"]
        up["value"] = True
        _, body, _ = _request(port, "GET", "/state")
        assert lab(json.loads(body))["ok"]
        server.state.status = "proposing"
        up["value"] = False
        _, body, _ = _request(port, "GET", "/state")
        assert lab(json.loads(body))["ok"], "preflight is frozen while a run is in progress"
    finally:
        server.shutdown()
        server.server_close()


def test_preview_base_reaches_the_server_from_the_command_line(settings, monkeypatch) -> None:
    """The documented setup points the workspace at a lab dev server on a chosen port.
    That only works if --preview-base survives the whole call chain."""
    from typer.testing import CliRunner

    from funnel_growth_agent import cli
    from funnel_growth_agent.demo import server as server_module

    seen: dict[str, object] = {}

    class StubServer:
        server_address = ("127.0.0.1", 0)

        def __init__(self) -> None:
            self.state = types.SimpleNamespace(mode="live", preflight=[])

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            seen["closed"] = True

    def stub_make_server(_settings, **kwargs):
        seen.update(kwargs)
        return StubServer()

    monkeypatch.setattr(server_module, "make_server", stub_make_server)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    result = CliRunner().invoke(
        cli.app,
        ["demo", "--live", "--preview-base", "http://127.0.0.1:5187/pm", "--port", "8817"],
    )

    assert result.exit_code == 0, result.output
    assert seen["preview_base"] == "http://127.0.0.1:5187/pm"
    assert seen["port"] == 8817
    assert seen["live"] is True
    assert seen["closed"] is True


def test_preview_base_defaults_when_the_flag_is_absent(settings, monkeypatch) -> None:
    from typer.testing import CliRunner

    from funnel_growth_agent import cli
    from funnel_growth_agent.demo import server as server_module

    seen: dict[str, object] = {}

    class StubServer:
        server_address = ("127.0.0.1", 0)
        state = types.SimpleNamespace(mode="live", preflight=[])

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            pass

    monkeypatch.setattr(
        server_module, "make_server", lambda _s, **kwargs: (seen.update(kwargs), StubServer())[1]
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    result = CliRunner().invoke(cli.app, ["demo", "--live"])
    assert result.exit_code == 0, result.output
    assert seen["preview_base"] == server_module.PREVIEW_BASE
