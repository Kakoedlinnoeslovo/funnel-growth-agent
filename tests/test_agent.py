from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from funnel_growth_agent.agent import MAX_OUTPUT_RETRIES, MAX_TOKENS, ToolLoop, run_tool_loop


def response(*blocks, stop="tool_use"):
    return SimpleNamespace(stop_reason=stop, content=list(blocks))


def submitted(payload):
    return SimpleNamespace(type="tool_use", name="submit_proposal", id="submit", input=payload)


def text_block(text):
    return SimpleNamespace(type="text", text=text)


def setup_client(monkeypatch, settings, responses):
    settings = replace(settings, anthropic_api_key="test-only")
    requests, events = [], []
    iterator = iter(responses)

    def create(**kwargs):
        requests.append(deepcopy(kwargs))
        return next(iterator)

    monkeypatch.setattr(
        "anthropic.Anthropic",
        lambda **kwargs: SimpleNamespace(messages=SimpleNamespace(create=create)),
    )
    tools = ToolLoop(settings, on_event=lambda kind, data: events.append((kind, data)))
    return settings, tools, requests, events


VALID = {"proposal": {"decision": "no_experiment", "reason": "No supported change."}}


def test_malformed_json_text_is_retried_as_schema_tool(monkeypatch, settings):
    settings, tools, requests, events = setup_client(
        monkeypatch,
        settings,
        [
            response(text_block('{"decision":"experiment", changes: {}}'), stop="end_turn"),
            response(submitted(VALID)),
        ],
    )
    output = run_tool_loop(settings, tools, brief="Use only these saved creatives.")
    assert output.decision == "no_experiment"
    assert len(requests) == 2
    assert requests[1]["tool_choice"] == {"type": "tool", "name": "submit_proposal"}
    assert requests[1]["messages"][0]["content"] == "Use only these saved creatives."
    assert [kind for kind, _ in events] == ["model_output_retry", "proposal"]
    schema = requests[0]["tools"][-1]["input_schema"]
    assert schema["required"] == ["proposal"]
    assert "LandingProposal" in schema["$defs"]


def test_validation_errors_are_returned_to_model_before_accepting(monkeypatch, settings):
    settings, tools, requests, events = setup_client(
        monkeypatch,
        settings,
        [
            response(submitted({"proposal": {"decision": "experiment"}})),
            response(submitted(VALID)),
        ],
    )
    run_tool_loop(settings, tools)
    feedback = requests[1]["messages"][-1]["content"][0]
    assert feedback["is_error"] is True
    assert "experimentType" in feedback["content"]
    assert sum(kind == "proposal" for kind, _ in events) == 1


def test_truncated_tool_is_not_executed_or_accepted(monkeypatch, settings):
    settings, tools, requests, events = setup_client(
        monkeypatch,
        settings,
        [response(submitted(VALID), stop="max_tokens"), response(submitted(VALID))],
    )
    run_tool_loop(settings, tools)
    assert requests[1]["max_tokens"] == MAX_TOKENS * 2
    assert len(requests[1]["messages"]) == 1
    assert tools.calls == 0
    assert sum(kind == "proposal" for kind, _ in events) == 1


@pytest.mark.parametrize("truncated", [False, True])
def test_bad_outputs_have_bounded_retries(monkeypatch, settings, truncated):
    bad = (
        response(submitted(VALID), stop="max_tokens")
        if truncated
        else response(submitted({"proposal": {"decision": "experiment"}}))
    )
    settings, tools, requests, events = setup_client(
        monkeypatch, settings, [bad] * (MAX_OUTPUT_RETRIES + 1)
    )
    with pytest.raises(ValueError, match="selections are saved"):
        run_tool_loop(settings, tools)
    assert len(requests) == MAX_OUTPUT_RETRIES + 1
    assert not any(kind == "proposal" for kind, _ in events)


def test_read_tool_evidence_survives_format_retry(monkeypatch, settings):
    read = SimpleNamespace(type="tool_use", name="get_current_landing", id="read", input={})
    settings, tools, requests, _ = setup_client(
        monkeypatch,
        settings,
        [
            response(read),
            response(text_block("Here is a proposal: {broken}"), stop="end_turn"),
            response(submitted(VALID)),
        ],
    )
    run_tool_loop(settings, tools)
    assert tools.calls == 1
    assert requests[2]["messages"][2]["content"][0]["tool_use_id"] == "read"
    assert "hero" in requests[2]["messages"][2]["content"][0]["content"]


def test_refusal_does_not_trigger_retries(monkeypatch, settings):
    settings, tools, requests, _ = setup_client(
        monkeypatch, settings, [response(text_block("Declined"), stop="refusal")]
    )
    with pytest.raises(ValueError, match="declined"):
        run_tool_loop(settings, tools)
    assert len(requests) == 1


def test_baseline_constraint_errors_are_corrected_before_accepting(monkeypatch, settings):
    from redesign_helpers import redesign_proposal

    invalid = redesign_proposal(media=None, layout=None, composition={"omit": ["press-quotes"]})
    valid = redesign_proposal(media=None, layout=None, composition=None)
    settings, tools, requests, events = setup_client(
        monkeypatch,
        settings,
        [
            response(submitted({"proposal": invalid.model_dump(by_alias=True)})),
            response(submitted({"proposal": valid.model_dump(by_alias=True)})),
        ],
    )
    original = settings.landing_path.read_bytes()
    result = run_tool_loop(settings, tools)
    assert result == valid
    feedback = requests[1]["messages"][-1]["content"][0]
    assert feedback["is_error"] is True
    assert "press-quotes cannot be omitted" in feedback["content"]
    assert settings.landing_path.read_bytes() == original
    assert sum(kind == "proposal" for kind, _ in events) == 1
