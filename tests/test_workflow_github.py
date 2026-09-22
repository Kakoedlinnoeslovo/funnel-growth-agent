from __future__ import annotations

import json
import shutil
from dataclasses import replace

import httpx
import pytest

from funnel_growth_agent.workflow_github import GitHubPublisher
from funnel_growth_agent.workflow_preview import MARKER, artifact_digest


@pytest.fixture
def publisher(settings, monkeypatch):
    monkeypatch.setattr("funnel_growth_agent.workflow_github.shutil.which", lambda _: "/bin/gh")
    return GitHubPublisher(
        replace(
            settings,
            github_publish_repo="example/landings",
            github_public_origin="https://landings.example.com",
        )
    )


def publication(tmp_path):
    return {
        "repository": "example/landings",
        "branch": "main",
        "origin": "https://landings.example.com",
        "baseSha": "base",
        "commitSha": "reviewed",
        "workspace": str(tmp_path),
        "versionMarker": "creative-landings/draft.json",
        "marker": {"variant": "main/v1_a1", "sourceHash": "source"},
    }


def test_preparation_only_stages_new_version_and_preserves_default(
    publisher, tmp_path, monkeypatch
):
    source = publisher.settings.pricing_lab_dir
    (source / "node_modules").mkdir()
    (source / "package-lock.json").write_text("{}")
    (source / ".claude/skills").mkdir(parents=True)
    (source / ".claude/skills/style.md").write_text("text-white")
    shutil.copytree(source / "funnels/v7", source / "funnels/v7_a1")
    before = (source / "funnels/site.yaml").read_bytes()
    checkout = tmp_path / "publication/repository"
    commands = []

    def command(argv, **kwargs):
        commands.append(argv)
        if argv[:2] == ["git", "clone"]:
            shutil.copytree(
                source, checkout, ignore=shutil.ignore_patterns("node_modules", "v7_a1")
            )
        if argv[:2] == ["git", "rev-parse"]:
            return "base" if argv[-1] == "FETCH_HEAD" else "reviewed"
        if argv[:2] == ["git", "diff"]:
            return "funnels/site.yaml\nfunnels/v7_a1/steps/landing.yaml"
        return ""

    def build(lab, identity, environment):
        assert environment["VITE_VERCEL_ENV"] == "production"
        assert environment["VITE_VERCEL_GIT_COMMIT_REF"] == "main"
        assert "VITE_RECRAFT_API_URL" not in environment
        assert (lab / ".claude/skills/style.md").read_text() == "text-white"
        assert (lab / "node_modules").is_symlink()
        dist = lab / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("reviewed")
        (dist / MARKER).write_text(json.dumps({**identity, "artifactHash": artifact_digest(dist)}))
        return dist

    monkeypatch.setattr("funnel_growth_agent.workflow_github.run", command)
    result = publisher.prepare(
        source,
        tmp_path / "review/lab",
        tmp_path / "publication",
        {"draftId": "draft", "revision": 2, "variant": "v7_a1"},
        {"VITE_RECRAFT_API_URL": "https://staging.example.com"},
        lambda *_: None,
        builder=build,
    )
    assert result["commitSha"] == "reviewed"
    assert (source / "funnels/site.yaml").read_bytes() == before
    assert "default_version: v7" in (checkout / "funnels/site.yaml").read_text()
    assert not any(cmd[:2] == ["git", "push"] for cmd in commands)
    assert ["npm", "test"] in commands
    assert [
        "npm",
        "run",
        "lint",
        "--",
        "--no-ignore",
        "src",
        "scripts",
        "vite.config.ts",
    ] in commands
    staged = next(cmd for cmd in commands if cmd[:2] == ["git", "add"])
    assert staged[2:] == [
        "--",
        "funnels/v7_a1",
        "funnels/site.yaml",
        f"public/{MARKER}",
        "public/creative-landings/draft.json",
    ]


def test_refresh_preserves_published_commit_and_source(publisher, tmp_path, monkeypatch):
    saved = {**publication(tmp_path), "pushAttempted": True}
    commands = []

    def command(argv, **kwargs):
        commands.append(argv)
        return "reviewed" if argv[:2] == ["git", "rev-parse"] else ""

    def build(checkout, lab, identity, environment, emit, builder):
        assert checkout == tmp_path / "repository"
        assert identity["revision"] == 7
        assert environment["VITE_VERCEL_ENV"] == "production"

    monkeypatch.setattr("funnel_growth_agent.workflow_github.run", command)
    publisher.build = build
    updated = publisher.refresh(saved, tmp_path / "review", {"revision": 7}, lambda *_: None)
    assert updated["commitSha"] == saved["commitSha"]
    assert updated["marker"] == saved["marker"]
    assert updated["pushAttempted"]
    assert not any(cmd[:2] == ["git", "push"] for cmd in commands)


def test_publish_and_retry_push_only_once(publisher, tmp_path, monkeypatch):
    saved = publication(tmp_path)
    commands, events = [], []
    remote = {"head": "base"}

    def api(path):
        if "/git/ref/" in path:
            return {"object": {"sha": remote["head"]}}
        if "/statuses" in path:
            return [{"state": "success"}]
        return [{"id": 1, "environment": "Production"}]

    def command(argv, **_):
        commands.append(argv)
        if argv[:2] == ["git", "rev-parse"]:
            return "reviewed"
        if argv[:2] == ["git", "push"]:
            remote["head"] = "reviewed"
        return ""

    publisher.api = api
    publisher.verify = lambda *_: None
    monkeypatch.setattr("funnel_growth_agent.workflow_github.run", command)
    assert (
        publisher.publish(saved, tmp_path, lambda *event: events.append(event)) == saved["origin"]
    )
    assert (
        publisher.publish(saved, tmp_path, lambda *event: events.append(event)) == saved["origin"]
    )
    assert [cmd for cmd in commands if cmd[:2] == ["git", "push"]] == [
        ["git", "push", "origin", "reviewed:refs/heads/main"]
    ]
    assert saved["pushAttempted"]


def test_upstream_change_requires_new_review_without_pushing(publisher, tmp_path, monkeypatch):
    publisher.api = lambda _: {"object": {"sha": "new-upstream"}}
    monkeypatch.setattr(
        "funnel_growth_agent.workflow_github.run",
        lambda *_args, **_kwargs: pytest.fail("Must not push"),
    )
    with pytest.raises(ValueError, match="Refresh the publishing preview"):
        publisher.publish(publication(tmp_path), tmp_path, lambda *_: None)


def test_failed_deployment_keeps_existing_commit_for_retry(publisher, tmp_path, monkeypatch):
    def api(path):
        if "/git/ref/" in path:
            return {"object": {"sha": "reviewed"}}
        if "/statuses" in path:
            return [{"state": "failure"}]
        return [{"id": 1, "environment": "Production"}]

    publisher.api = api
    monkeypatch.setattr(
        "funnel_growth_agent.workflow_github.run",
        lambda *_args, **_kwargs: pytest.fail("Already pushed"),
    )
    with pytest.raises(RuntimeError, match="saved commit will be reused"):
        publisher.publish(publication(tmp_path), tmp_path, lambda *_: None)


@pytest.mark.parametrize("mode", ["correct", "protected", "different"])
def test_public_verification_requires_unauthenticated_matching_page(
    publisher, tmp_path, monkeypatch, mode
):
    saved = publication(tmp_path)
    (tmp_path / "index.html").write_text("reviewed")
    client = httpx.Client

    def respond(request):
        assert "authorization" not in request.headers
        if mode == "protected":
            return httpx.Response(302, headers={"location": "https://vercel.com/login"})
        if request.url.path.endswith(".json"):
            return httpx.Response(200, json=saved["marker"])
        return httpx.Response(200, text="reviewed" if mode == "correct" else "different")

    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs)
    )
    if mode == "correct":
        publisher.verify(saved, tmp_path)
    else:
        with pytest.raises(RuntimeError):
            publisher.verify(saved, tmp_path)
