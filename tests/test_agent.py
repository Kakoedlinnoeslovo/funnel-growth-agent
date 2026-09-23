from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from funnel_growth_agent.agent import MAX_OUTPUT_RETRIES, MAX_TOKENS, ToolLoop, run_tool_loop
from funnel_growth_agent.models import HeroVideoPlan, parse_model_output


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
    assert [kind for kind, _ in events] == [
        "model_request_started",
        "model_request_completed",
        "model_output_retry",
        "model_request_started",
        "model_request_completed",
        "proposal",
    ]
    assert events[0][1]["id"] == events[1][1]["id"]
    assert events[3][1]["id"] == events[4][1]["id"] != events[0][1]["id"]
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


def test_rejection_reason_reaches_the_retry_events_and_the_final_error(monkeypatch, settings):
    """A failed generation has to say which rule the proposal broke, not just that it failed."""
    bad = response(submitted({"proposal": {"decision": "experiment"}}))
    settings, tools, _requests, events = setup_client(
        monkeypatch, settings, [bad] * (MAX_OUTPUT_RETRIES + 1)
    )
    with pytest.raises(ValueError, match="experimentType") as failure:
        run_tool_loop(settings, tools)
    message = str(failure.value)
    assert len(message) <= 240 and message.endswith("retry generation.")
    reasons = [data["reason"] for kind, data in events if kind == "model_output_retry"]
    assert reasons and all("experimentType" in reason for reason in reasons)


def test_recipe_rejections_name_the_expected_composition(settings):
    recipe = {"theme": "dark-showcase", "layout": "media-first", "body": ["steps", "gallery"]}
    tools = ToolLoop(settings, policy={"changeLevel": "heavy", "recipe": recipe})
    blueprint = {
        "schemaVersion": 1,
        "theme": "dark-showcase",
        "artDirection": "Dark, high-contrast product story",
        "blocks": [
            {"id": "hero", "kind": "hero", "headline": "Ship 3D assets", "layout": "media-first"},
            {
                "id": "steps",
                "kind": "steps",
                "headline": "How it works",
                "items": [
                    {"title": "Upload a reference", "body": "Drop in the clip or image."},
                    {"title": "Generate the asset", "body": "Export it straight into the scene."},
                ],
            },
            {"id": "proof", "kind": "proof", "headline": "Proof", "sourceSectionId": "logo-strip"},
            {"id": "gallery", "kind": "gallery", "headline": "Made with Recraft"},
            {"id": "cta", "kind": "cta", "headline": "Start free"},
        ],
    }
    output = parse_model_output(
        {
            "decision": "experiment",
            "experimentType": "landing_rebuild",
            "problem": "The page buries the workflow.",
            "evidence": ["The creative leads with the workflow."],
            "hypothesis": "A workflow-first page explains the product faster.",
            "primaryMetric": "landing_cta_rate",
            "changes": blueprint,
        }
    )
    with pytest.raises(ValueError) as failure:
        tools.validate_output(output)
    message = str(failure.value)
    assert "['steps', 'gallery']" in message and "'proof'" in message
    assert "received" in message


def test_hero_clip_must_end_before_a_rounded_source_length(settings, monkeypatch):
    """YouTube reports a rounded length, so a clip ending at it overruns the real file."""
    tools = ToolLoop(settings, ranked=[])
    monkeypatch.setattr(
        "funnel_growth_agent.agent.media_sources",
        lambda *_: {
            "youtube": [{"videoId": "abcdefghijk", "durationSeconds": 30}],
            "creatives": [{"creativeId": "ad_1", "duration": 14.5}],
        },
    )
    plan = HeroVideoPlan(
        source="youtube", videoId="abcdefghijk", start=2, duration=28, label="Editor walkthrough"
    )
    with pytest.raises(ValueError, match="must end by 29s"):
        tools.validate_clip(plan)
    tools.validate_clip(plan.model_copy(update={"duration": 27}))
    creative = HeroVideoPlan(
        source="creative", creativeId="ad_1", start=0, duration=14.5, label="Uploaded ad clip"
    )
    with pytest.raises(ValueError, match="usable end"):
        tools.validate_clip(creative)
    tools.validate_clip(creative.model_copy(update={"duration": 14}))
    unknown = HeroVideoPlan(
        source="creative", creativeId="unknown_ad", start=0, duration=14, label="Unlisted clip"
    )
    tools.validate_clip(unknown)  # an id the media stage will reject stays its decision
