from __future__ import annotations

import copy
import json
import shutil
from types import SimpleNamespace

import pytest
import test_workflow as fixtures
from test_workflow import job

from funnel_growth_agent.blueprint import (
    baseline_blueprint,
    compose_document,
    enforce_level,
    require_renderer,
)
from funnel_growth_agent.landing_diff import ApplyError, ProducedFiles, check_landing_diff
from funnel_growth_agent.landing_patch import dump_yaml, load_yaml
from funnel_growth_agent.models import LandingProposal, PageBlueprint
from funnel_growth_agent.quality_gate import score_proposal, valid_proposal
from funnel_growth_agent.workflow import CreateDraft, Workflow
from redesign_helpers import redesign_proposal


@pytest.fixture
def workflow(settings):
    yield from fixtures.workflow.__wrapped__(settings)


def page(recipe=None):
    recipe = recipe or {
        "theme": "clean-light",
        "layout": "split",
        "body": ["gallery", "features", "steps", "proof"],
    }
    blocks = [
        {
            "id": "opening",
            "kind": "hero",
            "headline": "One idea. A family of icons.",
            "layout": recipe["layout"],
            "images": ["assets/showcase/vector-1.webp"],
        }
    ]
    for index, kind in enumerate(recipe["body"]):
        row = {
            "id": f"body-{index}",
            "kind": kind,
            "headline": kind.title(),
            "items": [
                {"title": "Start with an idea", "body": "Describe your style."},
                {"title": "Make it yours", "body": "Edit the vector output."},
            ],
        }
        if kind in {"gallery", "comparison"}:
            row["images"] = ["assets/showcase/vector-2.webp"]
        if kind == "proof":
            row["sourceSectionId"] = "logo-strip"
        blocks.append(row)
    blocks.append({"id": "closing", "kind": "cta", "headline": "Make your next set"})
    return PageBlueprint.model_validate(
        {
            "schemaVersion": 1,
            "theme": recipe["theme"],
            "artDirection": "Original artwork with restrained lime accents",
            "blocks": blocks,
        }
    )


def test_quality_gate_accepts_grounded_heavy_redesign():
    payload = redesign_proposal(media=None, layout=None, composition=None).model_dump(by_alias=True)
    payload.update(experimentType="landing_rebuild", changes=page().model_dump(by_alias=True))
    proposal = LandingProposal.model_validate(payload)
    card = score_proposal(proposal, {"previous": []})
    assert card["experiment_type_ok"]
    assert valid_proposal(proposal, card)


def test_light_medium_heavy_have_enforced_distinct_scope():
    original = page().model_dump(by_alias=True)
    edited = copy.deepcopy(original)
    edited["blocks"][0]["headline"] = "A stronger promise"
    enforce_level(original, edited, "light")
    edited["blocks"][0]["layout"] = "centered"
    with pytest.raises(ValueError, match="Light"):
        enforce_level(original, edited, "light")
    enforce_level(original, edited, "medium")
    edited["theme"] = "dark-showcase"
    with pytest.raises(ValueError, match="theme"):
        enforce_level(original, edited, "medium")
    with pytest.raises(ValueError, match="two body"):
        enforce_level(original, edited, "heavy")
    edited["blocks"][1], edited["blocks"][2] = edited["blocks"][2], edited["blocks"][1]
    enforce_level(original, edited, "heavy")
    with pytest.raises(ValueError, match="Heavy"):
        enforce_level({}, {"copy": {"hero": {"headline": ["small"]}}}, "heavy")


def test_rebuild_preserves_offer_quiz_proof_and_nonlanding_contracts(settings):
    original = load_yaml(settings.landing_path)
    original["props"]["sections"].insert(
        1,
        {
            "id": "quiz",
            "component": "quiz-hero",
            "question": "Choose",
            "choices": [{"id": "a", "label": "Icons", "value": "icons"}],
        },
    )
    conflicting = page().model_dump(by_alias=True)
    conflicting["blocks"][0]["id"] = "quiz"
    with pytest.raises(ValueError, match="reserved"):
        compose_document(original, PageBlueprint.model_validate(conflicting))
    rebuilt = compose_document(original, page())
    protected = [
        row
        for row in original["props"]["sections"]
        if row["component"] in {"quiz-hero", "logo-strip", "press-quotes", "plan-preview"}
    ]
    assert all(row in rebuilt["props"]["sections"] for row in protected)
    assert len(check_landing_diff(original, rebuilt, ProducedFiles())) > 0
    rebuilt["props"]["sections"][-2]["headline"] = "Altered offer"
    with pytest.raises(ApplyError):
        check_landing_diff(original, rebuilt, ProducedFiles())


def test_unverified_proof_and_foreign_assets_rejected(settings):
    original = load_yaml(settings.landing_path)
    bad = page().model_dump(by_alias=True)
    bad["blocks"][0]["images"] = ["https://competitor.test/image.jpg"]
    with pytest.raises(ValueError, match="baseline asset"):
        compose_document(original, PageBlueprint.model_validate(bad))
    bad = page().model_dump(by_alias=True)
    bad["blocks"][-2]["sourceSectionId"] = "kittl-hero"
    with pytest.raises(ValueError, match="verified proof"):
        compose_document(original, PageBlueprint.model_validate(bad))


def test_existing_blueprint_can_be_a_new_light_baseline(settings):
    result = compose_document(load_yaml(settings.landing_path), page())
    dump_yaml(settings.landing_path, result)
    before = baseline_blueprint(settings.landing_path)
    after = copy.deepcopy(before)
    after["blocks"][0]["headline"] = "Sharper promise"
    enforce_level(before, after, "light")
    second = compose_document(result, PageBlueprint.model_validate(after))
    assert second["props"]["growthDesign"] == result["props"]["growthDesign"]
    assert len(second["props"]["sections"]) == len(result["props"]["sections"])


def test_renderer_must_exist_on_target_branch(settings):
    with pytest.raises(ValueError, match="Install the shared renderer"):
        require_renderer(settings.pricing_lab_dir)
    (settings.pricing_lab_dir / "funnels/growth-capabilities.json").write_text(
        json.dumps({"contract": "growth-blocks-v1", "schemaVersion": 2})
    )
    with pytest.raises(ValueError, match="Install the shared renderer"):
        require_renderer(settings.pricing_lab_dir)


class GoalModel:
    def __init__(self):
        self.calls = []

    def complete(self, messages, system, execute):
        brief = json.loads(messages[0]["content"])
        self.calls.append(brief)
        if brief.get("recipe"):
            proposal = redesign_proposal(media=None, layout=None, composition=None).model_dump(
                by_alias=True
            )
            proposal.update(
                experimentType="landing_rebuild",
                changes=page(brief["recipe"]).model_dump(by_alias=True),
            )
            return proposal
        if (brief.get("previousProposal") or {}).get("changes", {}).get("blocks"):
            proposal = redesign_proposal(media=None, layout=None, composition=None).model_dump(
                by_alias=True
            )
            changes = copy.deepcopy(brief["previousProposal"]["changes"])
            changes["blocks"][0]["headline"] = "A refined headline"
            proposal.update(experimentType="landing_rebuild", changes=changes)
            return proposal
        return redesign_proposal(media=None, layout=None, composition=None)


def create_goal_draft(workflow, level="medium"):
    workflow.model = GoalModel()
    (workflow.settings.pricing_lab_dir / "funnels/growth-capabilities.json").write_text(
        json.dumps({"contract": "growth-blocks-v1"})
    )
    catalog = workflow.catalog()
    baseline = next(b for b in catalog["baselines"] if b["version"] == "v7")
    return workflow.create(
        {
            "baseVersion": "v7",
            "baseHash": baseline["hash"],
            "reportToken": catalog["reportToken"],
            "goalPrompt": "Create a page for brand designers building consistent icon sets",
            "changeLevel": level,
            "research": {"competitors": []},
        }
    )


def test_prompt_only_works_without_report_or_creatives(workflow):
    shutil.rmtree(workflow.settings.reports_dir)
    draft = create_goal_draft(workflow)
    assert not draft["creatives"] and draft["changeLevel"] == "medium"
    ready = job(workflow, draft, "generate")
    assert ready["status"] == "ready", ready["error"]
    assert ready["revisions"][0]["goalPrompt"] == draft["goalPrompt"]
    assert workflow.model.calls[0]["goalPrompt"] == draft["goalPrompt"]
    with pytest.raises(ValueError):
        CreateDraft(baseVersion="v7", baseHash="x")


def test_three_directions_persist_select_once_and_light_preserves_heavy(workflow):
    draft = create_goal_draft(workflow, "heavy")
    choice = job(workflow, draft, "generate")
    assert choice["status"] == "awaiting_direction", choice["error"]
    assert len(choice["directionSet"]["records"]) == 3 and not choice["readyRevision"]
    assert len(workflow.media_tools.images.calls) == 0
    assert len(workflow.model.calls) == 3
    assert Workflow(workflow.settings).load(draft["id"])["status"] == "awaiting_direction"
    with pytest.raises(ValueError, match="stale"):
        workflow.start_job(
            draft["id"],
            "select_direction",
            {"directionSetId": "old", "directionId": "results-first"},
        )
    ready = job(
        workflow,
        draft,
        "select_direction",
        {"directionSetId": choice["directionSet"]["id"], "directionId": "results-first"},
    )
    assert ready["status"] == "ready", ready["error"]
    first = ready["revisions"][0]["proposal"]["changes"]
    revised = job(
        workflow,
        ready,
        "revise",
        {"expectedRevision": 1, "instruction": "Sharpen the opening", "changeLevel": "light"},
    )
    assert revised["status"] == "ready", revised["error"]
    second = revised["revisions"][1]["proposal"]["changes"]
    assert second["theme"] == first["theme"]
    assert [(b["id"], b["kind"], b["images"]) for b in second["blocks"]] == [
        (b["id"], b["kind"], b["images"]) for b in first["blocks"]
    ]
    assert second["blocks"][0]["headline"] != first["blocks"][0]["headline"]
    assert (
        revised["revisions"][0]["changeLevel"] == "heavy"
        and revised["revisions"][1]["changeLevel"] == "light"
    )


def test_one_rejected_direction_still_offers_the_others(workflow):
    """A single direction the model cannot get past validation must not void the whole run."""
    draft = create_goal_draft(workflow, "heavy")
    model = workflow.model

    def complete(messages, system, execute):
        brief = json.loads(messages[0]["content"])
        if (brief.get("recipe") or {}).get("id") == "workflow-first":
            raise ValueError("Each direction needs a distinct composition: received ['proof']")
        return GoalModel.complete(model, messages, system, execute)

    workflow.model = SimpleNamespace(complete=complete, calls=model.calls)
    choice = job(workflow, draft, "generate")
    assert choice["status"] == "awaiting_direction", choice["error"]
    assert [r["id"] for r in choice["directionSet"]["records"]] == ["results-first", "proof-first"]
    assert "Workflow first" in choice["directionSet"]["unavailable"][0]
    failures = [e for e in choice["events"] if e["kind"] == "step_failed"]
    assert "distinct composition" in failures[-1]["data"]["error"]
    ready = job(
        workflow,
        draft,
        "select_direction",
        {"directionSetId": choice["directionSet"]["id"], "directionId": "proof-first"},
    )
    assert ready["status"] == "ready", ready["error"]


def test_a_single_surviving_direction_is_not_a_choice(workflow):
    """One option is no decision to make: fail loudly, naming every reason."""
    draft = create_goal_draft(workflow, "heavy")
    model = workflow.model

    def complete(messages, system, execute):
        brief = json.loads(messages[0]["content"])
        recipe = brief.get("recipe") or {}
        if recipe.get("id") != "proof-first":
            raise ValueError("no blueprint for " + recipe.get("id", "?"))
        return GoalModel.complete(model, messages, system, execute)

    workflow.model = SimpleNamespace(complete=complete, calls=model.calls)
    failed = job(workflow, draft, "generate")
    assert failed["status"] == "failed" and not failed.get("directionSet")
    assert "1 of 3 design directions" in failed["error"]
    assert "Results first" in failed["error"] and "Workflow first" in failed["error"]


def test_direction_failure_retry_keeps_last_preview_and_reuses_proposal(workflow):
    draft = create_goal_draft(workflow, "heavy")
    choice = job(workflow, draft, "generate")
    workflow.builder.fail = True
    failed = job(
        workflow,
        draft,
        "select_direction",
        {"directionSetId": choice["directionSet"]["id"], "directionId": "workflow-first"},
    )
    assert failed["status"] == "failed" and failed["directionSet"]
    workflow.builder.fail = False
    ready = job(workflow, failed, "retry")
    assert ready["status"] == "ready", ready["error"]
    assert len(workflow.model.calls) == 3


def test_arbitrary_selected_proposal_cannot_bypass_direction_binding(workflow):
    draft = create_goal_draft(workflow)
    with pytest.raises(ValueError, match="cannot be supplied"):
        workflow.start_job(draft["id"], "generate", {"selectedProposal": {}})


def test_reanalysis_refreshes_legacy_evidence_without_rewriting_prior_revision(workflow):
    from test_workflow import create

    from funnel_growth_agent.models import CachedCreativeAnalysis, CreativeAnalysis
    from funnel_growth_agent.video_analysis import video_fingerprint

    draft = create(workflow)
    ready = job(workflow, draft, "generate")
    before = copy.deepcopy(ready["revisions"][0])
    folder = workflow.path(draft["id"])
    video = folder / "creative-assets/sample.mp4"
    video.parent.mkdir(exist_ok=True)
    video.write_bytes(b"first video content")
    frame = folder / "creative-assets/frame.jpg"
    frame.write_bytes(b"frame")
    ready["creatives"][0]["videoPath"] = str(video)
    ready["analyses"][0]["schema_version"] = 4
    workflow.save(ready)
    calls = []

    def analyzer(rows, settings, **kwargs):
        calls.append(kwargs)
        return [
            CachedCreativeAnalysis(
                creativeId=rows[0].creative_id,
                assetFingerprint=video_fingerprint(
                    video, settings.gemini_model, settings.analysis_schema_version
                ),
                analyzedAt="2026-09-23T00:00:00Z",
                model=settings.gemini_model,
                schema_version=5,
                analysis=CreativeAnalysis(
                    visualHook="Later demonstration",
                    primaryPromise="Consistent sets",
                    audienceIntent="Designers",
                    ctaIntent="Try it",
                    suggestedLandingTheme="Workflow",
                    videoEvidence={
                        "concept": "An editable family",
                        "narrative": "A demonstration followed by a CTA",
                        "duration": 10,
                        "method": "video_audio",
                        "audioAvailable": True,
                        "coveredIntervals": [[0, 10]],
                        "storyboard": [{"timestamp": 5, "path": str(frame), "nonblank": True}],
                    },
                ),
            )
        ]

    workflow.analyzer = analyzer
    refreshed = job(workflow, ready, "reanalyze")
    assert refreshed["status"] == "ready" and refreshed["readyRevision"] == 1
    assert calls[0]["refresh"] is True
    assert refreshed["revisions"][0] == before
    assert refreshed["analyses"][0]["schema_version"] == 5
    assert refreshed["creatives"][0]["frameSource"] == "Video storyboard · 5.0s"
    presented = workflow.present(draft["id"])["analyses"][0]["analysis"]["videoEvidence"][
        "storyboard"
    ][0]
    assert presented["imageUrl"].startswith("/api/assets/")
    video.write_bytes(b"changed content, same thumbnail")
    workflow.analyze_selection(refreshed)
    assert len(calls) == 2


def test_refreshed_evidence_keeps_unchosen_directions_choosable(workflow):
    """Reading a competitor page again must not cost three designed directions."""
    draft = create_goal_draft(workflow, "heavy")
    choice = job(workflow, draft, "generate")
    assert choice["status"] == "awaiting_direction"
    refreshed = job(workflow, draft, "research", {"competitors": ["kittl"]})
    assert refreshed["status"] == "awaiting_direction"
    ready = job(
        workflow,
        draft,
        "select_direction",
        {"directionSetId": choice["directionSet"]["id"], "directionId": "results-first"},
    )
    assert ready["status"] == "ready", ready["error"]
