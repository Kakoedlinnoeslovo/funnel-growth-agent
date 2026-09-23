from __future__ import annotations

import copy
import http.client
import json
import threading
import time

import pytest
import test_workflow as fixtures
from test_workflow import create, job

from funnel_growth_agent.demo.server import make_server
from funnel_growth_agent.workflow import Workflow
from funnel_growth_agent.workflow_chat import CHAT_SYSTEM, ChatDecision, respond


@pytest.fixture
def workflow(settings):
    yield from fixtures.workflow.__wrapped__(settings)


def turn(text="What could improve?", request_id="request_123", revision=0, level="medium", **extra):
    return {
        "text": text,
        "requestId": request_id,
        "expectedRevision": revision,
        "changeLevel": level,
        **extra,
    }


def finish(workflow, draft):
    deadline = time.monotonic() + 10
    while workflow.busy and time.monotonic() < deadline:
        time.sleep(0.01)
    assert workflow.busy is None
    return workflow.load(draft["id"])


def test_question_and_clarification_never_generate(workflow):
    draft = create(workflow)
    calls = []

    def chat(settings, context):
        calls.append(context)
        return {
            "action": "answer" if len(calls) == 1 else "clarify",
            "text": "Lead with the editable output.",
        }

    workflow.chat_model = chat
    workflow.start_message(draft["id"], turn())
    first = finish(workflow, draft)
    workflow.start_message(draft["id"], turn("Make it better", "request_234"))
    saved = finish(workflow, draft)
    assert saved["status"] == "draft" and saved["revisions"] == []
    assert [m["role"] for m in saved["conversation"]["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert first["conversation"]["messages"][-1]["action"] == "answer"
    assert saved["conversation"]["messages"][-1]["action"] == "clarify"
    assert calls[0]["landing"]["sections"]
    assert calls[1]["messages"][-2]["text"] == "Lead with the editable output."
    assert all(event["kind"] == "chat_updated" for event in saved["events"])


def test_agreed_discussion_flows_into_generation_and_revision(workflow):
    draft = create(workflow)
    calls = []

    def chat(settings, context):
        calls.append(context)
        if len(calls) == 1:
            return {
                "action": "answer",
                "text": "We will focus on brand designers.",
                "agreedBrief": "Focus on brand designers. Keep editable vector output.",
            }
        return {
            "action": "edit",
            "text": "I’ll simplify the opening for brand designers.",
            "instruction": context["agreedBrief"] + " Simplify the opening.",
        }

    workflow.chat_model = chat
    workflow.start_message(draft["id"], turn("Focus on brand designers"))
    finish(workflow, draft)
    workflow.start_message(draft["id"], turn("Create the page", "request_build"))
    built = finish(workflow, draft)
    assert built["status"] == "ready" and built["readyRevision"] == 1
    assert "brand designers" in built["revisions"][0]["input"]["instruction"]
    reply = built["conversation"]["messages"][-1]
    assert reply["operationId"] == built["operationResults"][0]["id"]
    workflow.start_message(draft["id"], turn("Simplify the hero", "request_light", 1, "light"))
    revised = finish(workflow, draft)
    assert revised["status"] == "ready" and revised["readyRevision"] == 2
    assert revised["revisions"][-1]["changeLevel"] == "light"
    assert calls[-1]["changeLevel"] == "light"
    assert revised["conversation"]["status"] == "idle"


@pytest.mark.parametrize("level", ["light", "medium", "heavy"])
def test_dispatch_preserves_selected_level(workflow, level):
    draft = create(workflow)
    dispatched = []
    workflow.chat_model = lambda *_: {
        "action": "edit",
        "text": "I’ll create it.",
        "instruction": "Create a landing for designers.",
    }

    def generate(draft, payload):
        dispatched.append(payload)
        draft["status"] = "awaiting_direction" if level == "heavy" else "draft"

    workflow.generate = generate
    workflow.start_message(draft["id"], turn("Create the landing", level=level))
    saved = finish(workflow, draft)
    assert len(dispatched) == 1 and dispatched[0]["changeLevel"] == level
    assert saved["operationResults"][0]["action"] == "generate"


def test_idempotency_while_running_completed_and_conflicting_payload(workflow):
    draft = create(workflow)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def chat(*_):
        calls.append(True)
        entered.set()
        assert release.wait(3)
        return {"action": "answer", "text": "One clear promise helps."}

    workflow.chat_model = chat
    first = workflow.start_message(draft["id"], turn())
    assert entered.wait(3)
    duplicate = workflow.start_message(draft["id"], turn())
    assert duplicate["messageId"] == first["messageId"]
    with pytest.raises(ValueError, match="different message"):
        workflow.start_message(draft["id"], turn("Different content"))
    with pytest.raises(ValueError, match="Another operation"):
        workflow.start_message(draft["id"], turn(request_id="request_second"))
    release.set()
    saved = finish(workflow, draft)
    assert workflow.start_message(draft["id"], turn())["status"] == "completed"
    assert len(calls) == 1 and len(saved["conversation"]["messages"]) == 2


def test_stale_requests_rejected_before_model(workflow):
    draft = job(workflow, create(workflow), "generate")
    workflow.chat_model = lambda *_: pytest.fail("Stale messages must not call the provider")
    with pytest.raises(ValueError, match="newer revision"):
        workflow.start_message(draft["id"], turn())
    assert workflow.load(draft["id"])["readyRevision"] == 1
    assert workflow.busy is None


def test_answer_failure_preserves_ready_preview_and_has_explicit_idempotent_retry(workflow):
    draft = job(workflow, create(workflow), "generate")
    original = copy.deepcopy(draft["revisions"])

    def fail(*_):
        raise RuntimeError("Provider timed out")

    workflow.chat_model = fail
    workflow.start_message(draft["id"], turn(revision=1))
    failed = finish(workflow, draft)
    assert failed["status"] == "ready" and failed["revisions"] == original
    assert failed["conversation"]["status"] == "failed"
    assert workflow.start_message(draft["id"], turn(revision=1))["status"] == "failed"
    workflow.chat_model = lambda *_: {"action": "answer", "text": "The workflow can be clearer."}
    workflow.start_message(draft["id"], turn(revision=1, retryFailed=True))
    retried = finish(workflow, draft)
    assert len(retried["conversation"]["messages"]) == 2
    assert retried["conversation"]["status"] == "idle"
    assert retried["revisions"] == original
    assert (
        "error" not in retried["conversation"]["messages"][-1]
        or retried["conversation"]["messages"][-1]["error"] is None
    )


def test_restart_marks_only_pending_chat_as_failed(workflow):
    draft = job(workflow, create(workflow), "generate")
    draft["conversation"] = {
        "status": "thinking",
        "messages": [{"id": "pending_reply", "role": "assistant", "status": "pending", "text": ""}],
    }
    workflow.save(draft)
    reopened = Workflow(workflow.settings)
    try:
        saved = reopened.load(draft["id"])
        assert saved["status"] == "ready" and saved["readyRevision"] == 1
        assert saved["conversation"]["status"] == "failed"
        assert "interrupted" in saved["conversation"]["messages"][0]["error"]
    finally:
        reopened.close()


def test_invalid_or_publication_model_action_cannot_run(workflow):
    draft = create(workflow)
    workflow.chat_model = lambda *_: {"action": "publish", "text": "Publishing"}
    workflow.start_message(draft["id"], turn("Publish it"))
    saved = finish(workflow, draft)
    assert saved["conversation"]["status"] == "failed"
    assert saved["status"] == "draft" and not saved["revisions"]
    assert not saved.get("publicUrl")
    with pytest.raises(ValueError, match="concrete instruction"):
        ChatDecision(action="edit", text="I’ll do it")
    assert "hypothetical" in CHAT_SYSTEM and "publishing" in CHAT_SYSTEM


def test_published_draft_allows_discussion_but_not_edits(workflow):
    draft = job(workflow, create(workflow), "generate")
    draft["status"] = "published"
    workflow.save(draft)
    workflow.chat_model = lambda *_: {"action": "answer", "text": "This is the published design."}
    workflow.start_message(draft["id"], turn(revision=1))
    assert finish(workflow, draft)["conversation"]["status"] == "idle"
    workflow.chat_model = lambda *_: {
        "action": "edit",
        "text": "Changing it",
        "instruction": "Simplify",
    }
    workflow.start_message(draft["id"], turn("Simplify", "request_edit", 1))
    saved = finish(workflow, draft)
    assert saved["status"] == "published" and len(saved["revisions"]) == 1
    assert saved["conversation"]["messages"][-1]["status"] == "failed"


def test_research_results_are_immutable_and_legacy_summary_is_preserved(workflow):
    draft = job(workflow, create(workflow), "generate")
    original = copy.deepcopy(draft["operationResults"][0])
    refreshed = job(workflow, draft, "research", {"competitors": [], "landingUrls": []})
    assert refreshed["operationResults"][0] == original
    assert refreshed["operationResults"][-1]["id"] != original["id"]
    # Simulate a pre-chat draft. First new conversation freezes its existing saved evidence.
    refreshed.pop("operationResults")
    refreshed.pop("legacyEvidence", None)
    refreshed.pop("conversation")
    workflow.save(refreshed)
    workflow.chat_model = lambda *_: {"action": "answer", "text": "Your prior work is saved."}
    workflow.start_message(draft["id"], turn(revision=1))
    saved = finish(workflow, draft)
    assert saved["legacyEvidence"]["revision"] == 1
    evidence = copy.deepcopy(saved["legacyEvidence"])
    saved["context"] = {"competitors": [{"url": "https://new.example"}]}
    workflow.save(saved)
    assert workflow.present(draft["id"])["legacyEvidence"]["context"] == evidence["context"]


def test_provider_uses_conversation_schema_and_rejects_truncated_tool(monkeypatch, settings):
    from dataclasses import replace
    from types import SimpleNamespace

    import anthropic

    calls = []

    class Client:
        def __init__(self, **kwargs):
            self.messages = self

        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(stop_reason="max_tokens", content=[])

    monkeypatch.setattr(anthropic, "Anthropic", Client)
    with pytest.raises(ValueError, match="finish its reply"):
        respond(replace(settings, anthropic_api_key="test"), {"messages": []})
    assert calls[0]["tool_choice"]["name"] == "respond_to_user"
    assert calls[0]["tools"][0]["input_schema"]["properties"]["action"]["enum"] == [
        "answer",
        "clarify",
        "edit",
        "analyze",
    ]


def test_http_messages_endpoint_and_resumable_events(workflow):
    draft = create(workflow)
    workflow.chat_model = lambda *_: {"action": "answer", "text": "Keep the CTA clear."}
    server = make_server(workflow.settings, recording=None, live=True, port=0)
    server.workflow.close()
    server.workflow = workflow
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection(*server.server_address)
        connection.request(
            "POST",
            f"/api/drafts/{draft['id']}/messages",
            json.dumps(turn()),
            {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 202, response.read()
        response.read()
        saved = finish(workflow, draft)
        cursor = saved["events"][-2]["seq"]
        rows = workflow.events_after(draft["id"], cursor)
        assert len(rows) == 1 and rows[0]["kind"] == "chat_updated"
        assert (
            workflow.present(draft["id"])["conversation"]["messages"][-1]["text"]
            == "Keep the CTA clear."
        )
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_interrupted_edit_keeps_a_terminal_operation_card(workflow):
    draft = job(workflow, create(workflow), "generate")
    original = copy.deepcopy(draft["operationResults"])
    draft["status"] = "revising"
    draft["operationId"] = "interrupted-operation"
    draft["retry"] = {
        "action": "revise",
        "payload": {"instruction": "Simplify", "expectedRevision": 1},
    }
    workflow.save(draft)
    reopened = Workflow(workflow.settings)
    try:
        saved = reopened.load(draft["id"])
        assert saved["readyRevision"] == 1
        assert saved["operationResults"][:-1] == original
        assert saved["operationResults"][-1]["id"] == "interrupted-operation"
        assert saved["operationResults"][-1]["status"] == "failed"
        assert "interrupted" in saved["operationResults"][-1]["error"]
    finally:
        reopened.close()


def test_analysis_request_runs_the_reanalysis_job(workflow):
    """Asking chat to read the creatives starts the workspace job, not a dead end."""
    draft = create(workflow)
    refreshes = []
    workflow.chat_model = lambda *_: {"action": "analyze", "text": "Reading the video now."}
    workflow.analyze_selection = lambda draft, *, refresh=False: refreshes.append(refresh)
    workflow.generate = lambda *_: pytest.fail("An analysis request must not change the page")
    workflow.start_message(draft["id"], turn("run a video analysis"))
    saved = finish(workflow, draft)
    assert refreshes == [True]
    assert saved["conversation"]["messages"][-1]["action"] == "analyze"
    assert saved["operationResults"][-1]["action"] == "reanalyze"
    assert saved["status"] == "draft" and saved["revisions"] == []
    assert "analyze" in CHAT_SYSTEM
