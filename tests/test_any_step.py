import json
import re
import time
from subprocess import CompletedProcess

import pytest
import test_workflow as fixtures

from funnel_growth_agent.funnel_steps import (
    StepDesign,
    StepProposal,
    apply_proposal,
    canonical_url,
    copy_fields,
    copy_placeholders,
    step_document,
    tree_hash,
)
from funnel_growth_agent.landing_patch import dump_yaml, load_yaml
from funnel_growth_agent.workflow import Workflow
from funnel_growth_agent.workflow_chat import ChatRequest

workflow = fixtures.workflow


@pytest.fixture
def funnel(workflow, monkeypatch):
    folder = workflow.settings.pricing_lab_dir / "funnels/v7"
    manifest = load_yaml(folder / "funnel.yaml")
    manifest["steps"] += ["quiz", "plan", "success"]
    dump_yaml(folder / "funnel.yaml", manifest)
    for name, component, path, props in [
        (
            "quiz",
            "quiz-question",
            "/quiz",
            {"headline": "Your project", "choices": [{"id": "vector", "label": "Vector"}]},
        ),
        (
            "plan",
            "paywall",
            "/plan",
            {
                "title": "Choose a plan",
                "paywallId": "locked-v7",
                "prices": [{"id": "price_real", "amount": 999}],
            },
        ),
        ("success", "success", "/checkout/success", {}),
    ]:
        dump_yaml(
            folder / f"steps/{name}.yaml",
            {
                "id": name,
                "component": component,
                "title": name.title(),
                "path": path,
                "props": props,
            },
        )
    (workflow.settings.pricing_lab_dir / "funnels/growth-capabilities.json").write_text(
        json.dumps({"contracts": ["funnel-steps-v1"]})
    )
    monkeypatch.setattr(workflow, "snapshot_products", lambda *_: None)
    monkeypatch.setattr(
        workflow,
        "research_step",
        lambda draft: workflow.step_state(draft).update(context={"competitors": []}),
    )
    monkeypatch.setattr(
        workflow,
        "refresh_step_metrics",
        lambda draft: workflow.step_state(draft).update(
            metrics={
                "source": "posthog",
                "version": "v7",
                "stepId": draft["activeStepId"],
                "counts": {"viewed": 10},
            }
        ),
    )
    workflow.step_model = lambda _, ctx: StepProposal(
        hypothesis="Clearer purpose",
        copyPatch={next(iter(ctx["editableCopy"])): "Improved " + ctx["step"]["id"]},
    )
    return workflow


def create(funnel, step="plan"):
    baseline = next(b for b in funnel.catalog()["baselines"] if b["version"] == "v7")
    return funnel.create(
        {
            "baseVersion": "v7",
            "baseHash": baseline["funnelHash"],
            "stepId": step,
            "goalPrompt": "Improve progression",
        }
    )


def job(funnel, draft, action="generate", **kwargs):
    return fixtures.job(
        funnel,
        draft,
        action,
        {
            "stepId": draft["activeStepId"],
            "expectedRevision": draft.get("readyRevision") or 0,
            **kwargs,
        },
    )


def test_cumulative_revisions_survive_restart_publish_and_failure(funnel):
    draft = create(funnel)
    draft = job(funnel, draft)
    assert draft["status"] == "ready", draft["error"]
    view = funnel.select_step(draft["id"], {"stepId": "quiz", "expectedRevision": 1})
    draft = job(funnel, view, "revise")
    assert draft["status"] == "ready", draft["error"]
    lab, version = funnel.step_source(draft)
    folder = lab / "funnels" / version
    assert step_document(folder, "plan")[1]["props"]["title"] == "Improved plan"
    assert step_document(folder, "quiz")[1]["props"]["headline"] == "Improved quiz"
    assert step_document(folder, "plan")[1]["props"]["prices"] == [
        {"id": "price_real", "amount": 999}
    ]
    second_hash = tree_hash(folder)
    funnel.builder.fail = True
    failed = job(funnel, draft, "revise", instruction="Try a change")
    assert failed["status"] == "failed" and failed["readyRevision"] == 2
    assert tree_hash(folder) == second_hash
    funnel.builder.fail = False
    reopened = Workflow(funnel.settings, builder=funnel.builder, publisher=funnel.publisher)
    reopened.previews.url = funnel.previews.url
    reopened.previews.proxy = funnel.previews.proxy
    assert reopened.present(draft["id"])["steps"][1]["changed"]
    published = fixtures.job(reopened, failed, "publish", {"expectedRevision": 2})
    assert published["status"] == "published", published["error"]
    assert load_yaml(funnel.settings.site_path)["default_version"] == "v7"
    assert tree_hash(funnel.settings.pricing_lab_dir / "funnels" / version) == second_hash
    reopened.close()


def test_stale_step_and_revision_requests_are_rejected(funnel):
    draft = create(funnel)
    with pytest.raises(ValueError, match="selected step"):
        funnel.start_job(draft["id"], "generate", {"stepId": "quiz", "expectedRevision": 0})
    draft = job(funnel, draft)
    with pytest.raises(ValueError, match="newer revision"):
        funnel.start_job(draft["id"], "revise", {"stepId": "plan", "expectedRevision": 0})
    assert funnel.busy is None


def test_all_step_changes_invalidate_the_whole_funnel_hash(funnel):
    baseline = next(b for b in funnel.catalog()["baselines"] if b["version"] == "v7")
    path = funnel.settings.pricing_lab_dir / "funnels/v7/steps/quiz.yaml"
    path.write_text(path.read_text() + "\n# edited\n")
    with pytest.raises(ValueError, match="baseline changed"):
        funnel.create({"baseVersion": "v7", "baseHash": baseline["funnelHash"], "stepId": "plan"})


def test_chat_scoped_to_step(funnel):
    draft = create(funnel)
    draft["conversation"]["messages"] = [
        {"role": "user", "text": "Paywall only", "status": "completed", "stepId": "plan"},
        {"role": "user", "text": "Quiz only", "status": "completed", "stepId": "quiz"},
    ]
    ctx = funnel.chat_context(
        draft, ChatRequest(text="Why?", requestId="request-123", expectedRevision=0, stepId="plan")
    )
    assert [m["text"] for m in ctx["messages"]] == ["Paywall only"]
    assert ctx["landing"]["step"]["id"] == "plan"


def test_heavy_directions_bind_to_revision_and_step(funnel):
    funnel.step_model = lambda _, ctx: StepProposal(
        hypothesis=ctx["direction"],
        design=StepDesign(
            blocks=[
                {"id": "title", "kind": "hero", "heading": ctx["direction"]},
                {"id": "interaction", "kind": "native"},
            ]
        ),
    )
    draft = job(funnel, create(funnel), changeLevel="heavy")
    assert draft["status"] == "awaiting_direction", draft["error"]
    assert len(draft["directionSet"]["records"]) == 3 and draft["readyRevision"] is None
    selected = job(
        funnel,
        draft,
        "select_direction",
        directionSetId=draft["directionSet"]["id"],
        directionId="2",
    )
    assert selected["status"] == "ready", selected["error"]
    assert selected["revisions"][0]["proposal"]["hypothesis"] == "Editorial story"


def test_protected_contracts_and_mandatory_interaction():
    doc = {
        "id": "plan",
        "component": "paywall",
        "props": {"title": "Plans", "paywallId": "real", "plans": {"title": "Real offer"}},
    }
    assert copy_fields(doc) == {"props/title": "Plans"}
    for key in ["id", "props/paywallId", "props/plans/title"]:
        with pytest.raises(ValueError, match="Unsupported copy"):
            apply_proposal(doc, StepProposal(hypothesis="x", copyPatch={key: "fake"}), "light")
    for blocks in [
        [{"id": "hero", "kind": "hero"}],
        [{"id": "a", "kind": "native"}, {"id": "b", "kind": "native"}],
        [{"id": "a", "kind": "native"}, {"id": "b", "kind": "button", "action": "continue"}],
    ]:
        with pytest.raises(ValueError):
            apply_proposal(
                doc, StepProposal(hypothesis="x", design=StepDesign(blocks=blocks)), "heavy"
            )


def test_light_preserves_composition_and_list_copy():
    doc = {
        "component": "landing",
        "props": {"sections": [{"component": "kittl-hero", "headline": ["Original"]}]},
    }
    assert apply_proposal(
        doc, StepProposal(hypothesis="x", copyPatch={"props/sections/0/headline/0": "New"}), "light"
    )["props"]["sections"][0]["headline"] == ["New"]
    with pytest.raises(ValueError, match="Light edits"):
        apply_proposal(
            doc,
            StepProposal(
                hypothesis="x",
                design=StepDesign(blocks=[{"id": "go", "kind": "button", "action": "continue"}]),
            ),
            "light",
        )


def test_url_resolution_and_ambiguous_route(funnel):
    result = funnel.resolve_url("http://localhost:5173/pm/v7/plan?utm_source=test")
    assert result["matches"][0]["stepId"] == "plan"
    assert (
        canonical_url("https://Example.com/plan/?utm_campaign=a&tier=pro#offer")
        == "https://example.com/plan?tier=pro#offer"
    )
    path = funnel.settings.pricing_lab_dir / "funnels/v7/steps/quiz.yaml"
    doc = load_yaml(path)
    doc["path"] = "/plan"
    dump_yaml(path, doc)
    assert len(funnel.resolve_url("http://localhost:5173/pm/v7/plan")["matches"]) == 2
    assert funnel.resolve_url("https://unknown.example/pm/v7/plan")["kind"] == "unknown"


def test_metrics_refresh_failure_keeps_exact_snapshot(funnel, monkeypatch):
    draft = funnel.load(create(funnel)["id"])
    state = funnel.step_state(draft)
    state["metrics"] = {
        "source": "posthog",
        "version": "v7",
        "stepId": "plan",
        "counts": {"viewed": 123},
    }
    monkeypatch.setattr(
        "funnel_growth_agent.workflow_steps.subprocess.run",
        lambda *_args, **_kw: CompletedProcess([], 1, "", ""),
    )
    Workflow.refresh_step_metrics(funnel, draft)
    assert state["metrics"]["counts"]["viewed"] == 123 and state["metrics"]["stale"]


def test_import_is_durable_deduplicated_and_inert(funnel, monkeypatch):
    from funnel_growth_agent import workflow_library as library

    class Browser:
        def inspect_page(self, url):
            return {"url": url, "title": "Public page", "text": "Choose me", "images": []}

        def screenshot(self, url, *, width, height, out):
            out.write_bytes(b"png")

    funnel.browser = Browser()
    monkeypatch.setattr(library, "validate_public_url", lambda _: None)
    monkeypatch.setattr(
        library,
        "replicate",
        lambda *_: StepDesign(
            blocks=[{"id": "title", "kind": "hero", "heading": "Foreign $9 offer"}]
        ),
    )
    result = funnel.start_import("https://example.com/pricing?utm_source=test")
    deadline = time.monotonic() + 2
    while funnel.busy and time.monotonic() < deadline:
        time.sleep(0.01)
    assert funnel.library_catalog()[0]["status"] == "ready"
    assert funnel.start_import("https://example.com/pricing")["page"]["id"] == result["page"]["id"]
    draft = create(funnel)
    replaced = job(funnel, draft, "replace_step", libraryId=result["page"]["id"])
    assert replaced["status"] == "ready", replaced["error"]
    lab, version = funnel.step_source(replaced)
    doc = step_document(lab / "funnels" / version, "plan")[1]
    assert "$9" not in json.dumps(doc) and doc["props"]["prices"][0]["amount"] == 999
    assert sum(b["kind"] == "native" for b in doc["design"]["blocks"]) == 1


def test_failed_capture_can_retry_and_does_not_reserve_lock(funnel, monkeypatch):
    from funnel_growth_agent import workflow_library as library

    monkeypatch.setattr(library, "validate_public_url", lambda _: None)

    class Browser:
        def inspect_page(self, _):
            raise ValueError("Capture blocked")

    funnel.browser = Browser()
    result = funnel.start_import("https://example.com/pricing")
    deadline = time.monotonic() + 2
    while funnel.busy and time.monotonic() < deadline:
        time.sleep(0.01)
    assert funnel.library_catalog()[0]["error"] == "Capture blocked"
    assert funnel.job_lock.acquire(blocking=False)
    funnel.job_lock.release()
    assert result["page"]["id"]


def test_parallel_catalog_and_worker_yaml_reads(funnel):
    from concurrent.futures import ThreadPoolExecutor

    folder = funnel.settings.pricing_lab_dir / "funnels/v7"

    def read(_):
        return step_document(folder, "plan")[1]["props"]["paywallId"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(read, range(80))) == ["locked-v7"] * 80


def test_step_relevance_preserves_goal_and_role():
    from funnel_growth_agent.competitor_research import relevance_context

    result = relevance_context(
        {
            "selectedStep": {
                "goal": "Increase payment progression",
                "step": {
                    "title": "Your plan",
                    "component": "paywall",
                    "primaryMetric": "step_payment_rate",
                },
            },
            "secrets": "excluded",
        }
    )
    assert "paywall" in result["text"] and "Increase payment progression" in result["text"]
    assert "excluded" not in result["text"]


def test_paywall_cta_copy_is_editable_but_keeps_its_catalog_placeholders():
    """The paywall CTA is the copy a payment goal reaches for first. It is editable, but its
    {plan}/{price} slots are filled from the real catalog, so a rewrite cannot drop or invent
    one and state a price the funnel does not charge."""
    doc = {
        "id": "plan",
        "component": "paywall",
        "props": {
            "selectCta": {
                "annually": "Start {plan} — {price}/mo billed annually",
                "monthly": "Start {plan} — {price}/mo",
            },
            "selectLabel": "Choose a plan",
            "prices": {"pro": "$16"},
        },
    }
    fields = copy_fields(doc)
    assert fields["props/selectCta/annually"] == "Start {plan} — {price}/mo billed annually"
    assert fields["props/selectCta/monthly"] == "Start {plan} — {price}/mo"
    assert fields["props/selectLabel"] == "Choose a plan"
    assert "props/prices/pro" not in fields, "the offer itself stays protected"

    applied = apply_proposal(
        doc,
        StepProposal(
            hypothesis="x",
            copyPatch={
                "props/selectCta/annually": "Start {plan} — billed annually at {price}/mo today"
            },
        ),
        "light",
    )
    assert (
        applied["props"]["selectCta"]["annually"]
        == "Start {plan} — billed annually at {price}/mo today"
    )
    assert applied["props"]["selectCta"]["monthly"] == "Start {plan} — {price}/mo"

    # The rejection names what went wrong, so the repair pass can act on it.
    for rewritten, reason in [
        ("Start Pro — $16/mo billed annually", "dropped {plan} {price}"),
        ("Start {plan} — billed annually", "dropped {price}"),
        ("Start {plan} — {price}/mo · {annual_total} today", "invented {annual_total}"),
    ]:
        with pytest.raises(ValueError, match=re.escape(reason)):
            apply_proposal(
                doc,
                StepProposal(
                    hypothesis="x", copyPatch={"props/selectCta/annually": rewritten}
                ),
                "light",
            )

    assert copy_placeholders(doc) == {
        "props/selectCta/annually": ["{plan}", "{price}"],
        "props/selectCta/monthly": ["{plan}", "{price}"],
    }, "the model is handed the legal slots, not left to guess them"


def test_a_rejected_proposal_is_repaired_once_then_stands_rejected(workflow):
    """A proposal the validator refuses is a recoverable model mistake: the reason goes back
    once. A model that ignores the repair still fails, so validation stays authoritative."""
    document = {
        "id": "plan",
        "component": "paywall",
        "props": {"selectCta": {"annually": "Start {plan} — {price}/mo"}},
    }
    context = {"document": document, "assets": [], "step": {"title": "Your plan"}}

    asks = []

    def repairing(_settings, ask):
        asks.append(ask)
        bad = StepProposal(
            hypothesis="x", copyPatch={"props/selectCta/annually": "Start Pro — $16/mo"}
        )
        good = StepProposal(
            hypothesis="x", copyPatch={"props/selectCta/annually": "Get {plan} for {price}/mo"}
        )
        return bad if len(asks) == 1 else good

    workflow.step_model = repairing
    proposal = workflow.step_proposal(context, "light")
    assert proposal.copyPatch == {"props/selectCta/annually": "Get {plan} for {price}/mo"}
    assert len(asks) == 2
    assert "repair" not in asks[0]
    assert "dropped {plan} {price}" in asks[1]["repair"]
    assert document["props"]["selectCta"]["annually"] == "Start {plan} — {price}/mo", (
        "the dry run must not mutate the real step document"
    )

    stubborn = []

    def unrepentant(_settings, ask):
        stubborn.append(ask)
        return StepProposal(
            hypothesis="x", copyPatch={"props/selectCta/annually": "Start Pro — $16/mo"}
        )

    workflow.step_model = unrepentant
    with pytest.raises(ValueError, match=re.escape("dropped {plan} {price}")):
        workflow.step_proposal(context, "light")
    assert len(stubborn) == 2, "exactly one repair attempt, not an unbounded loop"
