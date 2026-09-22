from __future__ import annotations

import json
from dataclasses import replace
from subprocess import CompletedProcess

import httpx
import pytest

from funnel_growth_agent.workflow_preview import (
    MARKER,
    PreviewServers,
    artifact_digest,
    copy_lab,
    publish_artifact,
)


def test_isolated_copy_excludes_credentials_and_uses_local_build_cache(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / ".env.local").write_text("SECRET=not-for-preview")
    (source / ".vercel").mkdir()
    (source / ".vercel/project.json").write_text("{}")
    (source / "node_modules").mkdir()
    (source / "tsconfig.app.json").write_text(
        '{"compilerOptions":{"tsBuildInfoFile":"./node_modules/.tmp/app.tsbuildinfo"}}'
    )
    dest = tmp_path / "snapshot"
    copy_lab(source, dest)
    assert not (dest / ".env.local").exists() and not (dest / ".vercel").exists()
    assert (dest / ".git").is_dir() and not list((dest / ".git").iterdir())
    assert (dest / "node_modules").resolve() == source / "node_modules"
    assert '".cache/tsconfig.app.tsbuildinfo"' in (dest / "tsconfig.app.json").read_text()
    assert "./node_modules/.tmp" in (source / "tsconfig.app.json").read_text()


def test_prebuilt_publisher_uploads_reviewed_bytes_and_does_not_promote(
    settings, tmp_path, monkeypatch
):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<h1>Reviewed landing</h1>")
    marker = {
        "draftId": "test",
        "revision": 2,
        "variant": "v7_a1",
        "artifactHash": artifact_digest(dist),
    }
    (dist / MARKER).write_text(json.dumps(marker))
    settings = replace(settings, vercel_project_id="project", vercel_org_id="org")
    commands = []

    def run(argv, **kwargs):
        commands.append(argv)
        assert (kwargs["cwd"] / ".vercel/output/static/index.html").read_bytes() == (
            dist / "index.html"
        ).read_bytes()
        return CompletedProcess(argv, 0, "https://reviewed.vercel.app\n", "")

    monkeypatch.setattr(
        "funnel_growth_agent.workflow_preview.shutil.which", lambda _: "/bin/vercel"
    )
    monkeypatch.setattr("funnel_growth_agent.workflow_preview.subprocess.run", run)
    real_client = httpx.Client

    def response(request):
        assert not request.headers.get("authorization")
        if request.url.path == "/" + MARKER:
            return httpx.Response(200, json=marker)
        return httpx.Response(200, content=(dist / "index.html").read_bytes())

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(response), **kwargs),
    )
    assert publish_artifact(settings, dist, tmp_path / "deploy") == "https://reviewed.vercel.app"
    assert "--prebuilt" in commands[0] and "--prod" not in commands[0]
    config = json.loads((tmp_path / "deploy/.vercel/output/config.json").read_text())
    assert config["routes"][0]["src"] == "/pm/assets/(.*)"


def test_protected_deployment_is_not_reported_as_public(settings, tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("reviewed")
    (dist / MARKER).write_text(json.dumps({"variant": "v7_a1"}))
    settings = replace(settings, vercel_project_id="project", vercel_org_id="org")
    monkeypatch.setattr(
        "funnel_growth_agent.workflow_preview.shutil.which", lambda _: "/bin/vercel"
    )
    monkeypatch.setattr(
        "funnel_growth_agent.workflow_preview.subprocess.run",
        lambda argv, **_: CompletedProcess(argv, 0, "https://protected.vercel.app\n", ""),
    )
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(lambda _: httpx.Response(401)), **kwargs
        ),
    )
    with pytest.raises(RuntimeError, match="not publicly accessible"):
        publish_artifact(settings, dist, tmp_path / "deploy")


def test_static_preview_routing_and_isolation(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<h1>Preview</h1>")
    (dist / "assets/test.js").write_text("script")
    (tmp_path / "private.json").write_text("secret")
    previews = PreviewServers()
    try:
        url = previews.url(dist, "main/v1_a1")
        root = url.split("/pm/")[0]
        response = httpx.get(url)
        assert response.status_code == 200 and "Preview" in response.text
        csp = response.headers["content-security-policy"]
        assert "connect-src 'none'" in csp and "form-action 'none'" in csp
        assert "https://cdn.prod.website-files.com" in csp
        assert httpx.get(root + "/pm/assets/test.js").text == "script"
        proxy = previews.proxy(root + "/pm")
        proxied = httpx.get(proxy + "/main/v1_a1")
        assert proxied.text == response.text
        assert "connect-src 'none'" in proxied.headers["content-security-policy"]
        with pytest.raises(ValueError, match="local lab"):
            previews.proxy("https://example.com/pm")
        assert httpx.get(root + "/%2e%2e/private.json").status_code == 404
    finally:
        previews.close()
