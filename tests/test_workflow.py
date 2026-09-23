from __future__ import annotations

import copy
import http.client
import io
import json
import shutil
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from funnel_growth_agent.apply import _tree_digest, next_variant_name
from funnel_growth_agent.demo.server import make_server
from funnel_growth_agent.gemini import analyze_ranked
from funnel_growth_agent.landing_diff import ApplyError, ProducedFiles, check_landing_diff
from funnel_growth_agent.landing_patch import ProducedMedia, load_yaml, patch_redesign
from funnel_growth_agent.models import RedesignChanges
from funnel_growth_agent.workflow import SelectionReview, Workflow
from funnel_growth_agent.workflow_catalog import safe_version
from funnel_growth_agent.workflow_preview import MARKER, artifact_digest
from redesign_helpers import (
    FakeBrowser,
    FakeReader,
    ScriptedModel,
    fake_media_tools,
    redesign_proposal,
)


class Builder:
    def __init__(self):
        self.calls = []
        self.fail = False

    def __call__(self, lab, identity, environment):
        self.calls.append(identity)
        if self.fail:
            raise RuntimeError("Build failed for test")
        dist = lab / "dist"
        dist.mkdir()
        content = (lab / "funnels" / identity["variant"] / "steps/landing.yaml").read_text()
        (dist / "index.html").write_text("<!doctype html><title>Landing</title>" + content)
        (dist / "assets").mkdir()
        (dist / "assets/test.js").write_text("/* reviewed bundle */")
        (dist / MARKER).write_text(json.dumps({**identity, "artifactHash": artifact_digest(dist)}))
        return dist


class Publisher:
    def __init__(self):
        self.calls = []
        self.fail = False

    def __call__(self, settings, dist, destination):
        marker = json.loads((dist / MARKER).read_text())
        self.calls.append(marker)
        if self.fail:
            raise RuntimeError("Deployment unavailable")
        return f"https://{marker['draftId']}.vercel.app"


class CapturingModel:
    def __init__(self):
        self.calls = []

    def complete(self, messages, system, execute):
        context = json.loads(messages[0]["content"])
        self.calls.append(context)
        assert "Explicit user selection" in context["selection"][0]["reasonSelected"]
        assert "explicitly selected" in system
        actual = execute("get_top_creatives", {})["creatives"]
        assert {row["creativeId"] for row in actual} == {
            row["creativeId"] for row in context["selection"]
        }
        promise = context["selection"][0]["title"] or "Create with Recraft"
        return redesign_proposal(
            media=None,
            layout=None,
            composition=None,
            copy={
                "hero": {"headline": [promise], "ctaLabel": "Try " + promise},
                "finalCta": {"headline": promise, "ctaLabel": "Try " + promise},
            },
        )


def coherent(*_args):
    return SelectionReview(
        coherent=True, commonPromise="Editable vectors", reason="Shared vector task"
    )


@pytest.fixture
def workflow(settings):
    from funnel_growth_agent.metrics import load_latest_reports

    report, _ = load_latest_reports(settings)
    for row in report["creatives"]:
        row["adset_id"] = "set-1" if row["ad_id"] in {"ad_1", "ad_2"} else "set-2"
        row["adset_name"] = "Vector winners" if row["adset_id"] == "set-1" else "Other direction"
        row["campaign_name"] = "Paid social"
    Path(report["_path"]).write_text(json.dumps(report))
    instance = Workflow(
        settings,
        model=CapturingModel(),
        builder=Builder(),
        publisher=Publisher(),
        analyzer=lambda rows, settings, **_: analyze_ranked(rows, settings, call_model=False),
        reviewer=coherent,
        validate=lambda _: None,
        media_tools=fake_media_tools(),
        browser=FakeBrowser(),
        reader=FakeReader(),
    )
    # Most unit tests do not need a listening preview server.
    instance.previews.url = lambda dist, version: f"http://localhost:9999/pm/{version}"
    instance.previews.proxy = lambda base: base
    yield instance
    instance.close()


def test_reopened_draft_has_current_media_library_without_rewriting_history(workflow):
    created = create(workflow)
    draft = workflow.load(created["id"])
    draft["context"] = {"media": {"youtube": [{"videoId": "old-snapshot"}]}}
    workflow.save(draft)
    view = workflow.present(created["id"])
    assert len(view["mediaLibrary"]["youtube"]) > 13
    assert view["mediaLibrary"]["youtubeIndex"]["indexedAt"]
    assert workflow.load(created["id"])["context"] == draft["context"]


def create(workflow, *, base="v7", ids=None, adset=None):
    catalog = workflow.catalog()
    baseline = next(row for row in catalog["baselines"] if row["version"] == base)
    return workflow.create(
        {
            "baseVersion": base,
            "baseHash": baseline["hash"],
            "reportToken": catalog["reportToken"],
            "creativeIds": [] if adset else (ids or ["ad_1"]),
            "adsetId": adset,
        }
    )


def job(workflow, draft, action, payload=None):
    workflow.start_job(draft["id"], action, payload)
    deadline = time.monotonic() + 10
    while workflow.busy and time.monotonic() < deadline:
        time.sleep(0.01)
    assert workflow.busy is None, "operation did not finish"
    return workflow.load(draft["id"])


def test_catalog_includes_low_volume_and_nonranked_ads_with_honest_metrics(workflow):
    catalog = workflow.catalog()
    ids = {row["id"] for row in catalog["creatives"]}
    assert {"ad_tiny", "ad_go", "ad_old_window_should_not_matter"} <= ids
    tiny = next(row for row in catalog["creatives"] if row["id"] == "ad_tiny")
    assert tiny["metrics"]["leads"] is None
    assert tiny["warnings"] and not tiny["sufficient"]
    assert next(group for group in catalog["adsets"] if group["id"] == "set-1")["creativeIds"] == [
        "ad_1",
        "ad_2",
    ]
    assert "imagePath" not in tiny


def test_two_cycles_publish_then_use_new_baseline_without_losing_first(workflow):
    settings = workflow.settings
    base_before = _tree_digest(settings.pricing_lab_dir / "funnels/v7")
    original_default = load_yaml(settings.site_path)["default_version"]
    first = create(workflow, ids=["ad_tiny"])
    first = job(workflow, first, "generate")
    assert first["status"] == "ready", first["error"]
    assert first["variant"] == "v7_a1"
    assert not (settings.pricing_lab_dir / "funnels/v7_a1").exists()
    first = job(workflow, first, "publish", {"expectedRevision": 1})
    assert first["status"] == "published", first["error"]
    assert first["publicUrl"].endswith("/pm/v7_a1")
    first_bytes = _tree_digest(settings.pricing_lab_dir / "funnels/v7_a1")
    second = create(workflow, base="v7_a1", adset="set-1")
    assert second["variant"] == "v7_a2"
    assert second["adsetId"] == "set-1"
    assert {row["id"] for row in second["creatives"]} == {"ad_1", "ad_2"}
    second = job(workflow, second, "generate")
    assert second["status"] == "ready", second["error"]
    second = job(workflow, second, "publish", {"expectedRevision": 1})
    assert second["status"] == "published", second["error"]
    assert _tree_digest(settings.pricing_lab_dir / "funnels/v7") == base_before
    assert _tree_digest(settings.pricing_lab_dir / "funnels/v7_a1") == first_bytes
    assert load_yaml(settings.site_path)["default_version"] == original_default
    assert len(workflow.list_drafts()) == 2
    assert len(workflow.publisher.calls) == 2
    assert [c["selection"][0]["creativeId"] for c in workflow.model.calls] == ["ad_tiny", "ad_1"]


def test_quick_edits_and_language_revisions_keep_history_and_selection(workflow):
    draft = job(workflow, create(workflow), "generate")
    assert draft["status"] == "ready", draft["error"]
    first_dist = workflow.path(draft["id"]) / "revisions/1/lab/dist"
    first_bytes = (first_dist / "index.html").read_bytes()
    draft = job(
        workflow,
        draft,
        "revise",
        {
            "copy": {"hero": {"headline": ["A better vector."], "ctaLabel": "Vectorize now"}},
            "expectedRevision": 1,
        },
    )
    assert draft["status"] == "ready", draft["error"]
    assert draft["readyRevision"] == 2 and len(workflow.model.calls) == 1
    assert draft["revisions"][1]["proposal"]["changes"]["copy"]["hero"]["headline"] == [
        "A better vector."
    ]
    assert (first_dist / "index.html").read_bytes() == first_bytes
    draft = job(
        workflow, draft, "revise", {"instruction": "Make it more direct", "expectedRevision": 2}
    )
    assert draft["status"] == "ready", draft["error"]
    assert draft["readyRevision"] == 3
    assert workflow.model.calls[-1]["previousProposal"]["changes"]["copy"]["hero"]["headline"] == [
        "A better vector."
    ]
    assert workflow.model.calls[-1]["revisionRequest"]["instruction"] == "Make it more direct"
    assert len(draft["revisions"]) == 3
    with pytest.raises(ValueError, match="newer revision"):
        workflow.start_job(
            draft["id"], "revise", {"instruction": "Stale edit", "expectedRevision": 1}
        )


def test_conflicting_adset_generates_one_page_with_all_creatives(workflow):
    workflow.reviewer = lambda *_: SelectionReview(
        coherent=False,
        commonPromise="",
        reason="Different product tasks",
        conflictingCreativeIds=["ad_2"],
    )
    draft = job(workflow, create(workflow, adset="set-1"), "generate")
    assert draft["status"] == "ready", draft["error"]
    assert len(draft["revisions"]) == len(workflow.model.calls) == len(workflow.builder.calls) == 1
    assert len(draft["creatives"]) == 2
    brief = workflow.model.calls[0]
    assert {row["creativeId"] for row in brief["selection"]} == {"ad_1", "ad_2"}
    assert brief["selectionReview"]["coherent"] is False


def test_mixed_destinations_generate_with_comparability_warning(workflow):
    draft = job(workflow, create(workflow, ids=["ad_1", "ad_go"]), "generate")
    assert draft["status"] == "ready", draft["error"]
    assert any(
        "Destination differs" in text for row in draft["creatives"] for text in row["warnings"]
    )
    assert len(workflow.model.calls[0]["selection"]) == 2


def test_unavailable_advisory_review_does_not_block_generation(workflow):
    def unavailable(*_):
        raise RuntimeError("Service unavailable")

    workflow.reviewer = unavailable
    draft = job(workflow, create(workflow, adset="set-1"), "generate")
    assert draft["status"] == "ready", draft["error"]
    assert any("review unavailable" in text for text in draft["warnings"])


def test_blocked_draft_recovers_with_saved_selection_and_analysis(workflow):
    draft = create(workflow, adset="set-1")
    raw = workflow.load(draft["id"])
    from funnel_growth_agent.workflow_catalog import ranked_selection

    raw["analyses"] = [
        item.model_dump(by_alias=True)
        for item in analyze_ranked(
            ranked_selection(raw["creatives"]), workflow.settings, call_model=False
        )
    ]
    raw.update(
        status="needs_selection", error="Different promises", selectionReview={"coherent": False}
    )
    workflow.save(raw)
    recovered = Workflow(workflow.settings)
    try:
        ready = recovered.load(draft["id"])
        assert ready["status"] == "draft"
        assert ready["error"] is None and ready["selectionReview"] is None
        for key in ("id", "variant", "baseHash", "creatives", "analyses", "events", "revisions"):
            assert ready[key] == raw[key]
    finally:
        recovered.close()
    workflow.analyzer = lambda *_args, **_kwargs: pytest.fail("Saved analysis must be reused")
    assert job(workflow, ready, "generate")["status"] == "ready"


def upload_image(workflow):
    from subprocess import CompletedProcess

    runner = workflow.media_tools.runner
    original_run = runner.run

    def run(argv, **kwargs):
        if Path(argv[0]).name == "ffprobe":
            return CompletedProcess(
                argv,
                0,
                json.dumps(
                    {
                        "streams": [
                            {
                                "codec_type": "video",
                                "codec_name": "png",
                                "width": 400,
                                "height": 300,
                            }
                        ],
                        "format": {"format_name": "png_pipe"},
                    }
                ),
                "",
            )
        return original_run(argv, **kwargs)

    runner.run = run
    try:
        return workflow.upload(io.BytesIO(b"fake"), filename="creative.png", length=4)
    finally:
        runner.run = original_run


def test_upload_only_without_report_preserves_unknown_metrics_and_assets(workflow):
    uploaded = upload_image(workflow)
    shutil.rmtree(workflow.settings.reports_dir)
    catalog = workflow.catalog()
    assert [row["id"] for row in catalog["creatives"]] == [uploaded["id"]]
    assert "imagePath" not in uploaded
    draft = workflow.create(
        {
            "baseVersion": "v7",
            "baseHash": catalog["baselines"][0]["hash"],
            "creativeIds": [uploaded["id"]],
        }
    )
    assert Path(workflow.load(draft["id"])["creatives"][0]["imagePath"]).is_relative_to(
        workflow.path(draft["id"])
    )
    shutil.rmtree(workflow.settings.data_dir / "uploads")
    draft = job(workflow, draft, "generate")
    assert draft["status"] == "ready", draft["error"]
    brief = workflow.model.calls[0]
    assert all(value is None for value in brief["observedMetrics"][uploaded["id"]].values())
    for key in ("spend", "clicks", "impressions", "checkouts", "payments"):
        assert brief["selection"][0][key] is None
    assert draft["revisions"][0]["proposal"]["baseline"]["source"] == "unavailable"
    workflow.builder.fail = True
    failed = job(workflow, draft, "revise", {"instruction": "More direct", "expectedRevision": 1})
    workflow.builder.fail = False
    assert job(workflow, failed, "retry")["status"] == "ready"


def test_uploads_mix_with_report_creatives_and_keep_report_validation(workflow):
    uploaded = upload_image(workflow)
    catalog = workflow.catalog()
    request = {
        "baseVersion": "v7",
        "baseHash": catalog["baselines"][0]["hash"],
        "creativeIds": [uploaded["id"], "ad_1"],
    }
    with pytest.raises(ValueError, match="weekly report changed"):
        workflow.create(request)
    request["reportToken"] = catalog["reportToken"]
    draft = job(workflow, workflow.create(request), "generate")
    assert draft["status"] == "ready", draft["error"]
    assert {row["id"] for row in draft["creatives"]} == {uploaded["id"], "ad_1"}
    assert len(workflow.model.calls[0]["selection"]) == 2


def test_failed_revision_preserves_reviewed_preview_and_can_retry(workflow):
    draft = job(workflow, create(workflow), "generate")
    workflow.builder.fail = True
    draft = job(workflow, draft, "revise", {"instruction": "More direct", "expectedRevision": 1})
    assert draft["status"] == "failed" and draft["readyRevision"] == 1
    assert draft["revisions"][-1]["status"] == "failed"
    assert workflow.present(draft["id"])["proposal"] == draft["revisions"][0]["proposal"]
    saved_changes = draft["revisions"][-1]["proposal"]["changes"]
    calls = len(workflow.model.calls)
    workflow.builder.fail = False
    draft = job(workflow, draft, "retry")
    assert draft["status"] == "ready" and draft["readyRevision"] == 3
    assert draft["retry"] is None
    assert len(workflow.model.calls) == calls
    assert draft["revisions"][-1]["proposal"]["changes"] == saved_changes


def test_invalid_saved_proposal_is_sent_back_for_correction_on_retry(workflow):
    workflow.builder.fail = True
    draft = job(workflow, create(workflow), "generate")
    failed = draft["revisions"][-1]
    failed["proposal"]["changes"]["composition"] = {"omit": ["press-quotes"]}
    failed["error"] = "press-quotes cannot be omitted"
    workflow.save(draft)
    workflow.builder.fail = False
    draft = job(workflow, draft, "retry")
    assert draft["status"] == "ready", draft["error"]
    assert len(workflow.model.calls) == 2
    repair = workflow.model.calls[-1]["failedAttempt"]
    assert repair["proposal"] == failed["proposal"]
    assert repair["error"] == failed["error"]
    assert "media plans exactly unchanged" in repair["instruction"]


def test_publication_retry_uses_reviewed_build_and_preserves_local_default(workflow):
    draft = job(workflow, create(workflow), "generate")
    original = workflow.settings.site_path.read_bytes()
    workflow.publisher.fail = True
    draft = job(workflow, draft, "publish", {"expectedRevision": 1})
    assert draft["status"] == "failed" and draft["readyRevision"] == 1
    assert workflow.settings.site_path.read_bytes() == original
    workflow.publisher.fail = False
    draft = job(workflow, draft, "retry")
    assert draft["status"] == "published", draft["error"]
    assert len(workflow.model.calls) == len(workflow.builder.calls) == 1
    assert workflow.publisher.calls[0] == workflow.publisher.calls[1]
    with pytest.raises(ValueError, match="immutable"):
        workflow.start_job(
            draft["id"], "revise", {"instruction": "Change it", "expectedRevision": 1}
        )


@pytest.mark.parametrize("refresh", [False, True])
def test_github_publication_requires_refreshed_review_and_retries_same_version(workflow, refresh):
    from funnel_growth_agent.workflow_preview import copy_lab

    class GitHub:
        prepared = 0
        published = []
        fail = True

        def prepare(self, source, lab, workspace, identity, environment, emit, builder):
            self.prepared += 1
            copy_lab(source, lab)
            builder(lab, identity, environment)
            self.previous_lab = lab
            return {"commitSha": "reviewed", "workspace": str(workspace)}

        def refresh(self, publication, lab, identity, emit, builder):
            copy_lab(self.previous_lab, lab)
            builder(lab, identity, {})
            return dict(publication)

        def publish(self, publication, dist, emit):
            self.published.append(publication["commitSha"])
            publication["pushAttempted"] = True
            emit("github_deploying", {"message": "Deployment pending"})
            if self.fail:
                raise RuntimeError("Deployment pending")
            return "https://public.example.com"

    workflow.settings.github_publish_repo = "example/landings"
    workflow.settings.github_public_origin = "https://public.example.com"
    workflow.github = GitHub()
    draft = job(workflow, create(workflow), "generate")
    draft = job(workflow, draft, "publish", {"expectedRevision": 1})
    assert draft["status"] == "ready" and draft["readyRevision"] == 2
    assert not workflow.github.published
    assert workflow.present(draft["id"])["revisions"][-1]["publicationPrepared"]
    assert "publication" not in workflow.present(draft["id"])["revisions"][-1]
    draft = job(workflow, draft, "publish", {"expectedRevision": 2})
    assert draft["status"] == "failed" and draft["githubPublicationPending"]
    with pytest.raises(ValueError, match="sent to GitHub"):
        workflow.start_job(draft["id"], "revise", {"expectedRevision": 2, "instruction": "Edit"})
    workflow.github.fail = False
    if refresh:
        previous = draft["revisions"][-1].copy()
        draft = job(workflow, draft, "prepare_publish", {"expectedRevision": 2})
        assert draft["status"] == "ready" and draft["readyRevision"] == 3
        assert draft["revisions"][1] == previous
        assert workflow.github.published == ["reviewed"]
        draft = job(workflow, draft, "publish", {"expectedRevision": 3})
    else:
        draft = job(workflow, draft, "retry")
    assert draft["status"] == "published"
    assert draft["publicUrl"] == "https://public.example.com/pm/v7_a1"
    assert workflow.github.prepared == 1
    assert workflow.github.published == ["reviewed", "reviewed"]


def test_publication_rejects_changed_artifacts(workflow):
    draft = job(workflow, create(workflow), "generate")
    bundle = workflow.path(draft["id"]) / "revisions/1/lab/dist/assets/test.js"
    bundle.write_text("tampered bundle")
    draft = job(workflow, draft, "publish", {"expectedRevision": 1})
    assert draft["status"] == "failed" and "build changed" in draft["error"]
    assert not workflow.publisher.calls


def test_registration_retry_does_not_redeploy(workflow, monkeypatch):
    draft = job(workflow, create(workflow), "generate")
    original = workflow.install_version

    def fail(*_):
        raise OSError("Lab temporarily unavailable")

    monkeypatch.setattr(workflow, "install_version", fail)
    draft = job(workflow, draft, "publish", {"expectedRevision": 1})
    assert draft["status"] == "failed" and draft["publicUrl"]
    with pytest.raises(ValueError, match="already public"):
        workflow.start_job(draft["id"], "revise", {"instruction": "edit", "expectedRevision": 1})
    monkeypatch.setattr(workflow, "install_version", original)
    draft = job(workflow, draft, "retry")
    assert draft["status"] == "published"
    assert len(workflow.publisher.calls) == 1


def test_generated_media_survives_quick_edits_and_reused_baselines(workflow):
    workflow.model = ScriptedModel(redesign_proposal(composition=None))
    draft = job(workflow, create(workflow), "generate")
    assert draft["status"] == "ready", draft["error"]
    calls = len(workflow.media_tools.images.calls)
    draft = job(
        workflow,
        draft,
        "revise",
        {"copy": {"hero": {"ctaLabel": "Edit my vector"}}, "expectedRevision": 1},
    )
    assert draft["status"] == "ready", draft["error"]
    assert len(workflow.media_tools.images.calls) == calls
    draft = job(workflow, draft, "publish", {"expectedRevision": 2})
    assert draft["status"] == "published", draft["error"]
    second = job(workflow, create(workflow, base=draft["variant"]), "generate")
    assert second["status"] == "ready", second["error"]


def test_busy_operations_cannot_overlap(workflow):
    release, started = threading.Event(), threading.Event()
    builder = workflow.builder

    def slow(*args):
        started.set()
        assert release.wait(5)
        return builder(*args)

    workflow.builder = slow
    draft = create(workflow)
    workflow.start_job(draft["id"], "generate")
    assert started.wait(5)
    try:
        with pytest.raises(ValueError, match="Another operation"):
            workflow.start_job(draft["id"], "generate")
        with pytest.raises(ValueError, match="Another operation"):
            create(workflow)
    finally:
        release.set()
    deadline = time.monotonic() + 5
    while workflow.busy and time.monotonic() < deadline:
        time.sleep(0.01)
    assert workflow.load(draft["id"])["status"] == "ready"


def test_selection_is_pinned_even_when_report_changes(workflow):
    draft = create(workflow, ids=["ad_tiny"])
    from funnel_growth_agent.metrics import load_latest_reports

    report, _ = load_latest_reports(workflow.settings)
    report["creatives"] = []
    Path(report["_path"]).write_text(json.dumps(report))
    draft = job(workflow, draft, "generate")
    assert draft["status"] == "ready", draft["error"]
    assert workflow.model.calls[-1]["selection"][0]["creativeId"] == "ad_tiny"


def test_catalog_staleness_and_paths_are_checked(workflow):
    catalog = workflow.catalog()
    request = {
        "baseVersion": "v7",
        "baseHash": "stale",
        "reportToken": catalog["reportToken"],
        "creativeIds": ["ad_1"],
    }
    with pytest.raises(ValueError, match="baseline changed"):
        workflow.create(request)
    request["baseHash"] = catalog["baselines"][0]["hash"]
    request["reportToken"] = "stale"
    with pytest.raises(ValueError, match="weekly report changed"):
        workflow.create(request)
    for bad in ("../v7", "/etc/passwd", "v7_a1_a1", "family/deeper/v1"):
        with pytest.raises(ValueError):
            safe_version(workflow.settings.pricing_lab_dir / "funnels", bad)
    with pytest.raises(ValueError):
        workflow.load("../../other")


def test_family_baseline_and_variant_naming(workflow):
    root = workflow.settings.pricing_lab_dir / "funnels"
    (root / "main").mkdir()
    shutil.copytree(root / "v7", root / "main/v1")
    draft = job(workflow, create(workflow, base="main/v1"), "generate")
    assert draft["status"] == "ready", draft["error"]
    assert draft["variant"] == "main/v1_a1"
    assert "/" not in draft["revisions"][0]["proposal"]["runId"]
    assert next_variant_name(replace(workflow.settings, base_version="main/v1_a1")) == "main/v1_a1"


def test_interrupted_draft_is_recoverable(workflow):
    draft = create(workflow)
    raw = workflow.load(draft["id"])
    raw.update(status="generating", retry={"action": "generate", "payload": {}})
    workflow.save(raw)
    recovered = Workflow(workflow.settings)
    assert recovered.load(draft["id"])["status"] == "failed"
    assert recovered.load(draft["id"])["retry"]["action"] == "generate"
    recovered.close()


@pytest.mark.parametrize("kind", ["hero-carousel", "quiz-hero"])
def test_carousel_and_quiz_edit_copy_images_but_preserve_structure(tmp_path, kind):
    hero = {"component": kind}
    if kind == "hero-carousel":
        hero["slides"] = [
            {"headline": ["Old"], "subhead": "Old sub", "ctaLabel": "Go", "image": "old"}
        ]
    else:
        hero.update(
            headline="Old",
            subhead="Old sub",
            question="Choose",
            answerKey="create",
            choices=[{"id": "vector", "label": "Vectors", "image": "old"}],
        )
    doc = {"component": "landing", "props": {"sections": [hero]}}
    from ruamel.yaml import YAML

    path = tmp_path / "landing.yaml"
    with path.open("w") as handle:
        YAML().dump(doc, handle)
    image = "assets/showcase/gen-example.webp"
    files = ProducedFiles(showcase=frozenset({image}))
    changes = RedesignChanges.model_validate(
        {"copy": {"hero": {"headline": ["New promise"], "subhead": "New sub"}}}
    )
    patch_redesign(path, changes, ProducedMedia(showcase={(kind, 0): image}, files=files))
    updated = YAML(typ="safe").load(path.read_text())
    assert check_landing_diff(doc, updated, files)
    illegal = copy.deepcopy(updated)
    if kind == "quiz-hero":
        illegal["props"]["sections"][0]["choices"][0]["id"] = "changed"
    else:
        illegal["props"]["sections"][0]["slides"].append(copy.deepcopy(hero["slides"][0]))
    with pytest.raises(ApplyError):
        check_landing_diff(doc, illegal, files)


def test_http_workflow_api_and_origin_protection(workflow):
    server = make_server(
        workflow.settings, recording=None, live=True, port=0, preview_base="http://127.0.0.1:9/pm"
    )
    server.workflow = workflow
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    def request(method, path, payload=None, origin=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        conn.request(
            method, path, body=json.dumps(payload) if payload is not None else None, headers=headers
        )
        response = conn.getresponse()
        code, body = response.status, response.read()
        conn.close()
        return code, body

    try:
        code, page = request("GET", "/")
        assert code == 200 and b"Message your growth agent" in page
        code, body = request("GET", "/api/catalog")
        catalog = json.loads(body)
        assert code == 200 and len(catalog["adsets"]) == 2
        payload = {
            "baseVersion": "v7",
            "baseHash": catalog["baselines"][0]["hash"],
            "reportToken": catalog["reportToken"],
            "creativeIds": ["ad_tiny"],
        }
        assert request("POST", "/api/drafts", payload, "https://other.example")[0] == 403
        code, body = request("POST", "/api/drafts", payload)
        assert code == 201
        draft = json.loads(body)
        assert request("POST", f"/api/drafts/{draft['id']}/generate", {})[0] == 202
        deadline = time.monotonic() + 10
        while workflow.busy and time.monotonic() < deadline:
            time.sleep(0.01)
        code, body = request("GET", f"/api/drafts/{draft['id']}")
        assert code == 200 and json.loads(body)["status"] == "ready", body
        assert request("GET", "/api/assets/../../etc/passwd")[0] == 404
    finally:
        server.shutdown()
        server.server_close()


def test_durable_events_resume_without_duplicates_and_stay_in_their_draft(workflow):
    first = create(workflow)
    second = create(workflow)
    first = job(workflow, first, "generate")
    events = workflow.events_after(first["id"])
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert len({e["operationId"] for e in events}) == 1
    assert events[0]["kind"] == "operation_started"
    assert events[-1]["kind"] == "operation_completed"
    assert events[-1]["data"]["busy"] is None
    assert events[-1]["data"]["readyRevision"] == 1
    assert all(e["stage"] != "publish" for e in events)
    assert workflow.events_after(first["id"], events[2]["seq"]) == events[3:]
    assert workflow.events_after(second["id"]) == []
    restored = Workflow(workflow.settings)
    try:
        assert restored.events_after(first["id"], events[-2]["seq"]) == events[-1:]
    finally:
        restored.close()


def test_legacy_events_get_stable_cursors_and_interruption_is_terminal(workflow):
    draft = workflow.load(create(workflow)["id"])
    draft["events"] = [{"kind": "analyzing", "data": {}, "at": "2026-01-01T00:00:00Z"}]
    draft["status"] = "researching"
    draft["retry"] = {"action": "research", "payload": {}}
    workflow.save(draft)
    restored = Workflow(workflow.settings)
    try:
        events = restored.events_after(draft["id"])
        assert [e["seq"] for e in events] == [1, 2]
        assert events[0]["operationId"] == "legacy"
        assert events[-1]["kind"] == "operation_failed"
        assert events[-1]["data"]["busy"] is None
    finally:
        restored.close()


def test_structured_edits_preserve_copy_and_reject_conflicting_modes(workflow):
    draft = job(workflow, create(workflow), "generate")
    previous = draft["revisions"][0]["proposal"]["changes"]
    calls = len(workflow.model.calls)
    draft = job(
        workflow,
        draft,
        "revise",
        {"expectedRevision": 1, "changes": {"layout": {"layout": "copy-first"}}},
    )
    assert draft["status"] == "ready", draft["error"]
    assert draft["revisions"][-1]["proposal"]["changes"]["copy"] == previous["copy"]
    assert len(workflow.model.calls) == calls
    with pytest.raises(ValueError, match="separately"):
        workflow.start_job(
            draft["id"],
            "revise",
            {
                "expectedRevision": 2,
                "instruction": "change it",
                "changes": {"layout": {"stickyCta": True}},
            },
        )
    assert workflow.busy is None
    editing = workflow.present(draft["id"])["editing"]
    assert "copy-first" in {p["id"] for p in editing["presets"]}
    assert "visual-first" not in {p["id"] for p in editing["presets"]}
    assert editing["sections"][0]["layoutOptions"] == ["copy-first"]
    assert editing["sections"][0]["copyKey"] == "hero"


def test_failed_revision_terminates_event_stream_and_keeps_preview(workflow):
    draft = job(workflow, create(workflow), "generate")
    workflow.builder.fail = True
    draft = job(
        workflow,
        draft,
        "revise",
        {"expectedRevision": 1, "copy": {"hero": {"ctaLabel": "Keep creating"}}},
    )
    view = workflow.present(draft["id"])
    assert view["status"] == "failed" and view["previewUrl"]
    assert view["readyRevision"] == 1
    assert view["events"][-1]["kind"] == "operation_failed"
    assert view["events"][-1]["data"]["busy"] is None
    assert len({e["operationId"] for e in view["events"]}) == 2


def test_freeform_revision_preserves_unspecified_earlier_changes(workflow):
    draft = job(workflow, create(workflow), "generate")
    draft = job(
        workflow,
        draft,
        "revise",
        {"expectedRevision": 1, "copy": {"hero": {"reassurance": "Your earlier edit"}}},
    )
    workflow.model = ScriptedModel(
        redesign_proposal(
            media=None,
            layout=None,
            composition=None,
            copy={"hero": {"headline": ["A fresh opening"]}},
        )
    )
    draft = job(
        workflow,
        draft,
        "revise",
        {"expectedRevision": 2, "instruction": "Change only the headline"},
    )
    assert draft["status"] == "ready", draft.get("error")
    copy_changes = draft["revisions"][-1]["proposal"]["changes"]["copy"]
    assert copy_changes["hero"]["headline"] == ["A fresh opening"]
    assert copy_changes["hero"]["reassurance"] == "Your earlier edit"
    assert copy_changes["finalCta"]["ctaLabel"]


def test_artifact_projection_allows_only_media_under_owned_roots(workflow, tmp_path):
    image = workflow.settings.data_dir / "research" / "desktop.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"png")
    secret = workflow.settings.data_dir / "key.txt"
    secret.write_text("not-an-artifact")
    other = tmp_path / "outside.png"
    other.write_bytes(b"not-in-data")
    data = workflow.present_artifacts(
        {"screenshots": [str(image), str(secret), str(other)], "path": str(image)}
    )
    assert data["screenshots"][0].startswith("/api/assets/")
    assert data["screenshots"][1:] == [None, None]
    assert data["imageUrl"] == data["screenshots"][0]
    draft = workflow.load(create(workflow)["id"])
    draft["context"] = {"competitors": [{"screenshots": [str(image)]}]}
    workflow.save(draft)
    restored = Workflow(workflow.settings)
    try:
        key = data["imageUrl"].removeprefix("/api/assets/")
        assert restored.asset_paths[key] == image
    finally:
        restored.close()


def test_draft_sse_respects_last_event_id(workflow):
    draft = job(workflow, create(workflow), "generate")
    last = draft["events"][-1]
    server = make_server(
        workflow.settings, recording=None, live=True, port=0, preview_base="http://127.0.0.1:9/pm"
    )
    server.workflow = workflow
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
    try:
        conn.request(
            "GET",
            f"/api/drafts/{draft['id']}/events?after=0",
            headers={"Last-Event-ID": str(last["seq"] - 1)},
        )
        response = conn.getresponse()
        assert response.status == 200
        assert response.readline().decode().strip() == f"id: {last['seq']}"
        assert json.loads(
            response.readline().decode().removeprefix("data: ")
        ) == workflow.present_event(last)
    finally:
        conn.close()
        workflow.close()
        server.shutdown()
        server.server_close()


def test_audience_projection_keeps_sources_separate_and_missing_values_null():
    from funnel_growth_agent.workflow_audience import audience_for_selection

    report = {
        "creatives": [{"ad_id": "a", "payments": 0, "payers": 2, "spend_usd": 9}],
        "creative_audience": {
            "schema_version": 1,
            "breakdowns": {
                "country": {
                    "rows": [
                        {"ad_id": "a", "meta_purchases": 4},
                        {"ad_id": "b", "meta_purchases": 99},
                    ]
                }
            },
            "creative_funnels": [{"ad_id": "b"}],
        },
    }
    view = audience_for_selection(
        report,
        [{"id": "a", "name": "Selected"}, {"id": "upload", "name": "Uploaded", "source": "upload"}],
    )
    assert view["status"] == "observed"
    assert view["outcomes"][0]["posthogPayments"] == 0
    assert view["outcomes"][0]["warehousePayers"] == 2
    assert view["outcomes"][1]["posthogPayments"] is None
    assert view["breakdowns"]["country"]["rows"] == [{"ad_id": "a", "meta_purchases": 4}]
    assert view["creative_funnels"] == []
    assert len(report["creative_audience"]["breakdowns"]["country"]["rows"]) == 2
