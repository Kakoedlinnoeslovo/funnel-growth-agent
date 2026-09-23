from __future__ import annotations

import copy
import hashlib
import json

import pytest
import test_workflow as fixtures
from test_goal_redesign import page
from test_workflow import create, job

from funnel_growth_agent.blueprint import baseline_blueprint, compose_document, require_renderer
from funnel_growth_agent.campaign import CampaignBrief, research_fingerprint, resolve_level
from funnel_growth_agent.campaign_research import research_campaign
from funnel_growth_agent.campaign_review import CampaignReview, CampaignReviewError
from funnel_growth_agent.competitor_research import ResearchConfig, relevance_context
from funnel_growth_agent.landing_diff import ProducedFiles, check_landing_diff
from funnel_growth_agent.landing_patch import ProducedMedia, dump_yaml, load_yaml
from funnel_growth_agent.models import MediaPlan, PageBlueprint
from funnel_growth_agent.research import LandingPatternRead
from funnel_growth_agent.web_assets import candidates, valid_asset
from funnel_growth_agent.workflow import CreateDraft
from redesign_helpers import FakeBrowser, FakeReader, redesign_proposal


def brief(audience="Game artists", task="Create consistent game artwork"):
    return CampaignBrief(audience=audience, promotedTask=task).model_dump(by_alias=True)


def v2_page(body=None, *, theme="clean-light", layout="collection", eyebrow="FOR GAME ARTISTS"):
    """`page()` only builds v1-legal pages, so v2-only controls are applied after the bump."""
    data = page(
        {
            "theme": theme,
            "layout": "split",
            "body": body or ["gallery", "features", "steps", "proof"],
        }
    ).model_dump(by_alias=True)
    data.update(schemaVersion=2, tokens={"accent": "#e6b750", "typography": "editorial"})
    data["blocks"] = [b for b in data["blocks"] if b["kind"] != "proof"]
    data["blocks"][0].update(layout=layout, eyebrow=eyebrow)
    return PageBlueprint.model_validate(data)


def test_auto_is_new_default_and_legacy_levels_remain_available():
    assert (
        CreateDraft(baseVersion="v8", baseHash="hash", goalPrompt="Game art").changeLevel == "auto"
    )
    draft = {"campaignBrief": brief(), "revisions": [], "readyRevision": None}
    assert resolve_level(draft, "auto", "local") == "heavy"
    assert resolve_level(draft, "medium", "campaign") == "medium"
    draft.update(revisions=[{"number": 1, "campaignBrief": brief()}], readyRevision=1)
    assert resolve_level(draft, "auto", "copy") == "light"
    assert resolve_level(draft, "auto", "local") == "medium"
    draft["campaignBrief"] = brief("Brand owners", "Create ads")
    assert resolve_level(draft, "auto", "local") == "heavy"


def test_research_identity_tracks_meaning_not_refresh_toggle():
    draft = {"campaignBrief": brief(), "research": {"refresh": False}, "analyses": []}
    first = research_fingerprint(draft)
    draft["research"]["refresh"] = True
    assert first == research_fingerprint(draft)
    draft["analyses"] = [{"analysis": {"primaryPromise": "Make brand ads"}}]
    assert first != research_fingerprint(draft)
    assert "Game artists" in relevance_context(draft)["text"]


class Search:
    def __init__(self):
        self.calls = []

    def discover(self, settings, campaign, evidence, config):
        self.calls.append(campaign)
        domain = "game.example" if "Game" in campaign["audience"] else "brands.example"
        return {
            "queries": [campaign["promotedTask"]],
            "errors": [],
            "sources": [
                {
                    "url": f"https://{domain}/use-case",
                    "name": domain,
                    "role": "competitor",
                    "reason": "Matches the campaign task and audience",
                },
                {
                    "url": "https://excluded.example/",
                    "name": "Excluded",
                    "role": "competitor",
                    "reason": "Must be excluded",
                },
            ],
        }


class InspectBrowser(FakeBrowser):
    def inspect_page(self, url):
        return {
            "url": url,
            "title": "Relevant use case",
            "text": "Observed product workflow",
            "links": [],
            "assets": [],
        }


def test_discovery_changes_with_audience_and_honors_exclusions(settings):
    search = Search()
    config = ResearchConfig(excludedDomains=["excluded.example"])
    a = research_campaign(
        settings,
        config,
        {"campaignBrief": brief()},
        provider=search,
        browser=InspectBrowser(),
        reader=FakeReader(),
    )
    b = research_campaign(
        settings,
        config,
        {"campaignBrief": brief("Brand owners", "Create ads")},
        provider=search,
        browser=InspectBrowser(),
        reader=FakeReader(),
    )
    assert a[0]["url"] == "https://game.example/use-case"
    assert b[0]["url"] == "https://brands.example/use-case"
    assert len(a) == len(b) == 1
    assert a[0]["selectionReason"] and a[0]["pageText"] and a[0]["read"]
    research_campaign(
        settings,
        config,
        {"campaignBrief": brief()},
        provider=search,
        browser=InspectBrowser(),
        reader=FakeReader(),
    )
    assert len(search.calls) == 2


def test_failed_discovery_uses_supplied_reference_without_fixed_competitors(settings):
    class Offline:
        def discover(self, *args):
            return {"sources": [], "queries": [], "errors": ["Search unavailable"]}

    results = research_campaign(
        settings,
        ResearchConfig(landingUrls=["https://reference.example/game"]),
        {"campaignBrief": brief()},
        provider=Offline(),
        browser=InspectBrowser(),
        reader=FakeReader(),
    )
    assert len(results) == 1 and results[0]["sourceKind"] == "reference"
    assert results[0]["warnings"] == ["Search unavailable"]


def test_scalar_visual_observations_keep_other_evidence():
    read = LandingPatternRead(
        heroHeadline="Game art",
        heroComposition="Split hero",
        typography="Large title",
        spacing=None,
    )
    assert (
        read.hero_composition == ["Split hero"]
        and read.typography == ["Large title"]
        and read.spacing == []
    )


def test_v2_composition_keeps_functional_sections_without_forcing_old_proof(settings):
    original = load_yaml(settings.landing_path)
    rebuilt = compose_document(original, v2_page())
    assert not any(s["component"] == "logo-strip" for s in rebuilt["props"]["sections"])
    assert rebuilt["props"]["growthDesign"]["tokens"]["accent"] == "#e6b750"
    check_landing_diff(original, rebuilt, ProducedFiles())
    dump_yaml(settings.landing_path, rebuilt)
    recovered = baseline_blueprint(settings.landing_path)
    assert recovered["schemaVersion"] == 2
    assert recovered["blocks"][0]["layout"] == "collection"
    assert compose_document(rebuilt, PageBlueprint.model_validate(recovered)) == rebuilt


def test_capability_negotiation_keeps_v1_and_requires_v2_for_campaign(settings):
    path = settings.pricing_lab_dir / "funnels/growth-capabilities.json"
    path.write_text(json.dumps({"contract": "growth-blocks-v1", "schemaVersion": 1}))
    require_renderer(settings.pricing_lab_dir)
    with pytest.raises(ValueError, match="growth-blocks-v2"):
        require_renderer(settings.pricing_lab_dir, 2)
    path.write_text(json.dumps({"contract": "growth-blocks-v2", "schemaVersion": 2}))
    require_renderer(settings.pricing_lab_dir, 1)
    require_renderer(settings.pricing_lab_dir, 2)


def test_only_owned_or_image_licensed_external_assets_enter_catalog(settings):
    source = {
        "url": "https://external.example/game",
        "pageText": "Game gallery",
        "sourceKind": "competitor",
        "pageAssets": [{"url": "https://cdn.example/a.png", "width": 300, "height": 300}],
    }
    assert candidates([source]) == []
    source["pageAssets"][0].update(
        licenseUrl="https://creativecommons.org/licenses/by/4.0/", credit="Artist Example"
    )
    found = candidates([source])
    assert len(found) == 1 and found[0]["credit"] == "Artist Example"
    data = b"image bytes"
    target = settings.media_cache_dir / "web/example/image.webp"
    target.parent.mkdir(parents=True)
    target.write_bytes(data)
    record = {"path": str(target), "sha256": hashlib.sha256(data).hexdigest()}
    assert valid_asset(record, settings)
    target.write_bytes(b"changed")
    assert not valid_asset(record, settings)


def test_sourced_images_preserve_attribution_and_are_not_generated(settings):
    data = v2_page().model_dump(by_alias=True)
    asset_id = "web-" + "a" * 20
    data["media"] = {"sourced": [{"group": "opening", "slot": 0, "assetId": asset_id}]}
    sourced = PageBlueprint.model_validate(data)
    image = "assets/showcase/source.webp"
    credit = {
        "image": image,
        "url": "https://recraft.ai/generate/game-assets",
        "label": "Recraft",
        "license": "Recraft campaign asset",
    }
    produced = ProducedMedia(
        showcase={("opening", 0): image},
        sourced_credits={image: credit},
        files=ProducedFiles(showcase=frozenset([image])),
    )
    rebuilt = compose_document(load_yaml(settings.landing_path), sourced, produced)
    hero = rebuilt["props"]["sections"][0]
    assert hero["generatedImages"] == [] and hero["credits"] == [credit]
    dump_yaml(settings.landing_path, rebuilt)
    recovered = PageBlueprint.model_validate(baseline_blueprint(settings.landing_path))
    assert compose_document(rebuilt, recovered)["props"]["sections"][0]["credits"] == [credit]


def test_duplicate_generated_and_sourced_slots_rejected():
    with pytest.raises(ValueError, match="repeat"):
        MediaPlan.model_validate(
            {"sourced": [{"group": "hero", "slot": 0, "assetId": "web-" + "a" * 20}] * 2}
        )


class AutoModel:
    """Edits the previous page in place, except for a campaign rebuild over an existing page:
    a new campaign earns a materially different composition, which is what heavy requires."""

    def complete(self, messages, system, execute):
        context = json.loads(messages[0]["content"])
        prior = (context.get("previousProposal") or {}).get("changes")
        rebuild = bool(context.get("campaignRebuild"))
        data = (
            copy.deepcopy(prior) if prior and not rebuild else v2_page().model_dump(by_alias=True)
        )
        if prior and rebuild:
            # A different campaign means a different page: new treatment and new body sections.
            data = v2_page(
                ["comparison", "faq"],
                theme="warm-editorial",
                layout="immersive",
                eyebrow="FOR BRAND OWNERS",
            ).model_dump(by_alias=True)
            data["tokens"] = {"accent": "#2f6f4f", "typography": "display"}
            data["blocks"][0]["headline"] = "Ads your brand can ship today"
        else:
            data["blocks"][0]["headline"] = (
                "Your next game world starts here" if not prior else "Make game art"
            )
        proposal = redesign_proposal(media=None, layout=None, composition=None).model_dump(
            by_alias=True
        )
        proposal.update(experimentType="landing_rebuild", changes=data)
        return proposal


def passing_review(*args, **kwargs):
    return {
        key: True
        for key in [
            "audienceClear",
            "mediaRelevant",
            "narrativeRelevant",
            "claimsSupported",
            "ctaTruthful",
            "substantialChange",
            "presentationUsable",
        ]
    }


@pytest.fixture
def workflow(settings):
    yield from fixtures.workflow.__wrapped__(settings)


def test_auto_builds_directly_and_local_edit_keeps_campaign(workflow):
    (workflow.settings.pricing_lab_dir / "funnels/growth-capabilities.json").write_text(
        json.dumps({"contract": "growth-blocks-v2", "schemaVersion": 2})
    )
    workflow.campaign_interpreter = lambda *_: {"campaignBrief": brief(), "editScope": "campaign"}
    workflow.search_provider = Search()
    workflow.asset_builder = lambda *args, **kwargs: {}
    workflow.campaign_reviewer = passing_review
    workflow.preview_capture = lambda *args, **kwargs: ([], [])
    workflow.model = AutoModel()
    draft = create(workflow)
    ready = job(
        workflow,
        draft,
        "generate",
        {"instruction": "Create the game campaign", "expectedRevision": 0, "changeLevel": "auto"},
    )
    assert ready["status"] == "ready", ready["error"]
    assert ready["changeLevel"] == "auto" and ready["revisions"][0]["changeLevel"] == "heavy"
    assert not ready.get("directionSet")
    first_context = copy.deepcopy(ready["context"])
    edited = job(
        workflow,
        ready,
        "revise",
        {
            "instruction": "Shorten the headline",
            "expectedRevision": 1,
            "changeLevel": "auto",
            "campaignBrief": brief(),
            "editScope": "copy",
        },
    )
    assert edited["status"] == "ready", edited["error"]
    assert edited["revisions"][-1]["changeLevel"] == "light"
    assert edited["context"] == first_context


def test_campaign_review_does_not_hardcode_pass():
    review = passing_review()
    review.update(mediaRelevant=False, issues=["Hero still shows unrelated vectorization demo"])
    with pytest.raises(CampaignReviewError, match="unrelated"):
        CampaignReview.model_validate(review).require_pass()


def campaign_workflow(workflow, search):
    """A workflow wired for campaign builds: real research plumbing, stubbed model and renderer."""
    (workflow.settings.pricing_lab_dir / "funnels/growth-capabilities.json").write_text(
        json.dumps({"contract": "growth-blocks-v2", "schemaVersion": 2})
    )
    workflow.search_provider = search
    workflow.asset_builder = lambda *args, **kwargs: {}
    workflow.campaign_reviewer = passing_review
    workflow.preview_capture = lambda *args, **kwargs: ([], [])
    workflow.model = AutoModel()
    return workflow


def test_campaign_agreed_in_conversation_refreshes_research_on_the_later_build(workflow):
    # The audience is settled in one turn and built in a later one, so the brief arrives on the
    # payload and no interpreter runs. Research collected for the first campaign must not be
    # reused for the second one just because a context is already saved.
    search = Search()
    campaign_workflow(workflow, search)
    workflow.campaign_interpreter = lambda *_: pytest.fail("A supplied brief needs no interpreter")
    draft = create(workflow)
    first = job(
        workflow,
        draft,
        "generate",
        {
            "instruction": "Create it",
            "expectedRevision": 0,
            "changeLevel": "auto",
            "campaignBrief": brief(),
            "editScope": "campaign",
        },
    )
    assert first["status"] == "ready", first["error"]
    assert "https://game.example/use-case" in [
        row["url"] for row in first["context"]["competitors"]
    ]
    assert len(search.calls) == 1
    second = job(
        workflow,
        first,
        "revise",
        {
            "instruction": "Now build it for brand owners running ad campaigns",
            "expectedRevision": 1,
            "changeLevel": "auto",
            "campaignBrief": brief("Brand owners", "Create ads"),
            "editScope": "campaign",
        },
    )
    assert second["status"] == "ready", second["error"]
    assert len(search.calls) == 2, "a new audience must invalidate the saved research"
    inspected = [row["url"] for row in second["context"]["competitors"]]
    assert "https://brands.example/use-case" in inspected
    assert "https://game.example/use-case" not in inspected
    assert second["context"]["campaignFingerprint"] != first["context"]["campaignFingerprint"]
    assert second["revisions"][-1]["changeLevel"] == "heavy"


def test_pinned_competitors_reach_discovery_and_exclusions_still_win(settings):
    class Recording:
        def __init__(self):
            self.config = None

        def discover(self, settings, campaign, evidence, config):
            self.config = config
            return {
                "queries": [],
                "errors": [],
                "sources": [
                    {
                        "url": "https://pinned.example/game-art",
                        "name": "Pinned",
                        "role": "competitor",
                        "reason": "The user pinned this product for the campaign",
                    },
                    {
                        "url": "https://dropped.example/game-art",
                        "name": "Dropped",
                        "role": "competitor",
                        "reason": "Ranked highly but excluded by the user",
                    },
                ],
            }

    provider = Recording()
    results = research_campaign(
        settings,
        ResearchConfig(competitors=["pinned.example"], excludedDomains=["dropped.example"]),
        {"campaignBrief": brief()},
        provider=provider,
        browser=InspectBrowser(),
        reader=FakeReader(),
    )
    assert provider.config.competitors == ["pinned.example"]
    # An exclusion outranks the ranking a provider returns, even for a selected competitor.
    assert [row["url"] for row in results] == ["https://pinned.example/game-art"]
    assert results[0]["sourceKind"] == "competitor"


def test_detailed_inspection_stops_at_three_competitors_but_keeps_other_roles(settings):
    class Many:
        def discover(self, *args):
            return {
                "queries": [],
                "errors": [],
                "sources": [
                    *(
                        {
                            "url": f"https://c{index}.example/use-case",
                            "name": f"c{index}",
                            "role": "competitor",
                            "reason": "A direct alternative for this audience and task",
                        }
                        for index in range(5)
                    ),
                    {
                        "url": "https://recraft.ai/generate/game-assets",
                        "name": "Recraft",
                        "role": "first_party",
                        "reason": "First-party evidence of the promoted capability",
                    },
                    {
                        "url": "https://reference.example/layout",
                        "name": "Reference",
                        "role": "adjacent",
                        "reason": "A workflow reference, not an alternative product",
                    },
                ],
            }

    results = research_campaign(
        settings,
        ResearchConfig(),
        {"campaignBrief": brief()},
        provider=Many(),
        browser=InspectBrowser(),
        reader=FakeReader(),
    )
    kinds = [row["sourceKind"] for row in results]
    assert kinds.count("competitor") == 3, "detailed inspection is capped at three competitors"
    # A first-party page and an adjacent reference are not alternatives and do not use the cap.
    assert "first_party" in kinds and "adjacent" in kinds


def test_directions_are_stale_once_the_campaign_changed(workflow):
    from test_goal_redesign import create_goal_draft

    draft = create_goal_draft(workflow, "heavy")
    choice = job(workflow, draft, "generate")
    assert choice["status"] == "awaiting_direction", choice["error"]
    stored = workflow.load(draft["id"])
    stored["campaignBrief"] = brief("Brand owners", "Create ads")
    workflow.save(stored)
    # Directions were designed for the previous campaign, so selecting one must not build it.
    with pytest.raises(ValueError, match="stale"):
        workflow.start_job(
            draft["id"],
            "select_direction",
            {
                "directionSetId": choice["directionSet"]["id"],
                "directionId": "results-first",
            },
        )
