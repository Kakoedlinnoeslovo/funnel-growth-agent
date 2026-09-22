"""Deploy: a branch off origin/main with only the variant, a pull request, then the merge and
the Vercel build followed through GitHub's deployments API. Real git, fake `gh`."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from funnel_growth_agent.demo.events import Recorder
from funnel_growth_agent.deploy import (
    BRANCH_PREFIX,
    DeployError,
    deploy_variant,
    parse_github_repo,
)

REPO = "acme/pricing-lab"
PR_URL = f"https://github.com/{REPO}/pull/7"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


class FakeGitHub:
    """Answers the `gh` calls deploy makes; everything else is real git. Records every call."""

    def __init__(self, origin: Path) -> None:
        self.origin = origin
        self.calls: list[list[str]] = []
        self.merged_sha: str | None = None
        self.closed = False
        self.preview_state = "success"
        self.production_states = ["success"]
        self.polls = 0
        self.fail: str | None = None

    def merge_on_github(self) -> None:
        """What a person does in the browser: squash the PR branch onto main at origin."""
        branch = f"{BRANCH_PREFIX}v7_a1"
        git(self.origin, "update-ref", "refs/heads/main", f"refs/heads/{branch}")
        self.merged_sha = git(self.origin, "rev-parse", "main")

    def __call__(self, args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        if args[0] != "gh":
            return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
        if self.fail and self.fail in " ".join(args):
            return subprocess.CompletedProcess(args, 1, "", f"boom: {self.fail}")
        return subprocess.CompletedProcess(args, 0, self._gh(args, cwd), "")

    def _gh(self, args: list[str], cwd: Path) -> str:
        if args[1:3] == ["api", "user"]:
            return "roman\n"
        if args[1:3] == ["pr", "create"]:
            return f"Creating pull request…\n{PR_URL}\n"
        if args[1:3] == ["pr", "view"]:
            if "number" in args[-1] and "state" not in args[-1]:
                return json.dumps({"number": 7})
            self.polls += 1
            if self.closed:
                return json.dumps({"state": "CLOSED", "mergeCommit": None})
            if self.merged_sha:
                return json.dumps({"state": "MERGED", "mergeCommit": {"oid": self.merged_sha}})
            if self.polls >= 2:
                self.merge_on_github()
            return json.dumps({"state": "OPEN", "mergeCommit": None})
        if args[1] == "api" and "/deployments?" in args[2]:
            sha = args[2].split("sha=")[1].split("&")[0]
            env = "Production" if sha == self.merged_sha else "Preview"
            return json.dumps([{"id": 100 if env == "Preview" else 200, "environment": env}])
        if args[1] == "api" and "/statuses" in args[2]:
            if "/100/" in args[2]:
                return json.dumps(
                    [{"state": self.preview_state, "environment_url": "https://lab-abc.vercel.app"}]
                )
            state = (
                self.production_states.pop(0)
                if len(self.production_states) > 1
                else self.production_states[0]
            )
            return json.dumps(
                [
                    {
                        "state": state,
                        "environment_url": "https://lab-prod.vercel.app",
                        "log_url": "https://vercel.com/log",
                    }
                ]
            )
        raise AssertionError(f"unexpected gh call {args}")


@pytest.fixture
def lab_repo(settings, tmp_path: Path):
    """Turn the settings' lab into a clone of a bare origin whose main lacks the variant."""
    lab = settings.pricing_lab_dir
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "--quiet", "-b", "main", str(origin)], check=True)
    git(lab, "init", "--quiet", "-b", "main")
    git(lab, "config", "user.email", "test@example.com")
    git(lab, "config", "user.name", "Test")
    git(lab, "remote", "add", "origin", f"https://github.com/{REPO}.git")
    git(lab, "remote", "set-url", "--push", "origin", str(origin))
    git(lab, "config", f"url.{origin}.insteadOf", f"https://github.com/{REPO}.git")
    (lab / ".gitignore").write_text("node_modules\n", encoding="utf-8")
    git(lab, "add", "-A")
    git(lab, "commit", "--quiet", "-m", "base")
    git(lab, "push", "--quiet", "-u", "origin", "main")
    git(lab, "remote", "set-head", "origin", "main")
    # Upstream moves on: a commit the local checkout does not have.
    upstream = tmp_path / "upstream"
    git(tmp_path, "clone", "--quiet", str(origin), str(upstream))
    git(upstream, "config", "user.email", "up@example.com")
    git(upstream, "config", "user.name", "Upstream")
    (upstream / "UPSTREAM.md").write_text("newer\n", encoding="utf-8")
    git(upstream, "add", "-A")
    git(upstream, "commit", "--quiet", "-m", "upstream change")
    git(upstream, "push", "--quiet")
    # The agent applied v7_a1 locally: a folder plus two site.yaml lines, and some dirt.
    variant = lab / "funnels" / "v7_a1"
    (variant / "steps").mkdir(parents=True)
    (variant / "funnel.yaml").write_text("id: recraft-quiz-v7-a1\n", encoding="utf-8")
    (variant / "steps" / "landing.yaml").write_text(
        "hero: {ctaLabel: Vectorize}\n", encoding="utf-8"
    )
    (variant / "content").mkdir()
    (variant / "content" / "brief.md").write_text("local only\n", encoding="utf-8")
    settings.site_path.write_text(
        "default_version: v7\npublished_versions:\n  - v7\n  - v7_a1\n"
        "versions:\n  v7: Onboarding quiz\n  v7_a1: Agent hero-copy experiment v7_a1\n",
        encoding="utf-8",
    )
    (lab / "funnels" / "v7" / "funnel.yaml").write_text("id: dirty-local-edit\n", encoding="utf-8")
    return origin


def _record() -> Recorder:
    return Recorder(clock=iter(range(0, 10_000)).__next__, phase="deploy")


def test_parse_github_repo_accepts_https_and_ssh() -> None:
    assert parse_github_repo("https://github.com/acme/lab.git") == "acme/lab"
    assert parse_github_repo("git@github.com:acme/lab.git\n") == "acme/lab"
    assert parse_github_repo("https://github.com/acme/lab/") == "acme/lab"
    with pytest.raises(DeployError):
        parse_github_repo("https://gitlab.com/acme/lab.git")


def test_deploy_opens_the_pr_from_origin_main_and_follows_the_merge(settings, lab_repo) -> None:
    gh = FakeGitHub(lab_repo)
    recorder = _record()
    validated: list[Path] = []
    result = deploy_variant(
        settings,
        "v7_a1",
        on_data=recorder,
        sh=gh,
        validate=validated.append,
        poll_interval=0,
        clock=iter(range(0, 10_000)).__next__,
        sleep=lambda _s: None,
    )

    kinds = [e.kind for e in recorder.events]
    assert kinds[0] == "deploy_started" and kinds[-1] == "deploy_done"
    stages = [e.data["stage"] for e in recorder.events if e.kind == "deploy_stage"]
    assert stages == [
        "check",
        "branch",
        "validate",
        "commit",
        "push",
        "pr",
        "preview",
        "merge",
        "merge",
        "build",
        "build",
    ]
    assert result.pr_url == PR_URL and result.pr_number == 7
    assert result.preview_url == "https://lab-abc.vercel.app/pm/v7_a1"
    assert result.prod_url == "https://lab-prod.vercel.app/pm/v7_a1"
    assert result.sha == gh.merged_sha
    done = recorder.events[-1].data
    assert done["prodUrl"] == result.prod_url and done["merged"] is True

    # The branch was validated where it was built, not in the lab checkout.
    assert len(validated) == 1 and validated[0] != settings.pricing_lab_dir / "funnels"
    assert not validated[0].exists(), "the throwaway worktree is removed afterwards"

    # Origin's main now carries the upstream change, the variant and exactly two site lines.
    files = git(lab_repo, "ls-tree", "-r", "--name-only", "main").splitlines()
    assert "UPSTREAM.md" in files
    assert "funnels/v7_a1/steps/landing.yaml" in files
    assert "funnels/v7_a1/content/brief.md" not in files
    site = git(lab_repo, "show", "main:funnels/site.yaml")
    assert "  - v7_a1\n" in site and "v7_a1: Agent hero-copy experiment v7_a1" in site
    assert "default_version: v7" in site
    base_funnel = git(lab_repo, "show", "main:funnels/v7/funnel.yaml")
    assert "dirty-local-edit" not in base_funnel, "local dirt never reaches the branch"

    # The local checkout is untouched: same branch, same dirt, no leftover worktree or branch.
    lab = settings.pricing_lab_dir
    assert git(lab, "branch", "--show-current") == "main"
    assert "dirty-local-edit" in (lab / "funnels" / "v7" / "funnel.yaml").read_text()
    assert f"{BRANCH_PREFIX}v7_a1" not in git(lab, "branch", "--list", "agent/*")
    assert "deploy-v7_a1" not in git(lab, "worktree", "list")

    create = next(c for c in gh.calls if c[:3] == ["gh", "pr", "create"])
    assert "--head" in create and create[create.index("--head") + 1] == "agent/v7_a1"
    assert not any(c[:3] == ["gh", "pr", "merge"] for c in gh.calls), "merging is never automated"


def test_no_wait_stops_once_the_pr_is_open(settings, lab_repo) -> None:
    gh = FakeGitHub(lab_repo)
    recorder = _record()
    result = deploy_variant(
        settings,
        "v7_a1",
        on_data=recorder,
        sh=gh,
        validate=lambda _d: None,
        wait_for_merge=False,
        poll_interval=0,
        sleep=lambda _s: None,
    )
    assert result.sha is None and result.prod_url is None and result.pr_url == PR_URL
    assert recorder.events[-1].data["merged"] is False
    assert gh.merged_sha is None and git(lab_repo, "ls-remote", "--heads", ".", "agent/v7_a1")


def test_failed_production_build_emits_deploy_failed_and_raises(settings, lab_repo) -> None:
    gh = FakeGitHub(lab_repo)
    gh.production_states = ["failure"]
    recorder = _record()
    with pytest.raises(DeployError, match="failure"):
        deploy_variant(
            settings,
            "v7_a1",
            on_data=recorder,
            sh=gh,
            validate=lambda _d: None,
            poll_interval=0,
            clock=iter(range(0, 10_000)).__next__,
            sleep=lambda _s: None,
        )
    assert recorder.events[-1].kind == "deploy_failed"
    assert "deploy-v7_a1" not in git(settings.pricing_lab_dir, "worktree", "list")


def test_closed_pr_and_gh_errors_fail_cleanly(settings, lab_repo) -> None:
    gh = FakeGitHub(lab_repo)
    gh.closed = True
    with pytest.raises(DeployError, match="closed without merging"):
        deploy_variant(
            settings,
            "v7_a1",
            sh=gh,
            validate=lambda _d: None,
            poll_interval=0,
            sleep=lambda _s: None,
        )
    # A second attempt refuses because origin already has the branch from the first.
    with pytest.raises(DeployError, match="already has agent/v7_a1"):
        deploy_variant(settings, "v7_a1", sh=FakeGitHub(lab_repo), validate=lambda _d: None)


def test_refuses_unapplied_or_base_versions(settings, lab_repo) -> None:
    with pytest.raises(DeployError, match="not an experiment arm"):
        deploy_variant(settings, "v7", sh=FakeGitHub(lab_repo))
    with pytest.raises(DeployError, match="no variant folder"):
        deploy_variant(settings, "v7_a9", sh=FakeGitHub(lab_repo))
    (settings.pricing_lab_dir / "funnels" / "v7_a2").mkdir()
    with pytest.raises(DeployError, match="not published"):
        deploy_variant(settings, "v7_a2", sh=FakeGitHub(lab_repo))


def test_validation_failure_leaves_nothing_behind(settings, lab_repo) -> None:
    gh = FakeGitHub(lab_repo)

    def bad(_d: Path) -> None:
        raise RuntimeError("schema broke")

    with pytest.raises(RuntimeError, match="schema broke"):
        deploy_variant(settings, "v7_a1", sh=gh, validate=bad)
    assert not git(lab_repo, "ls-remote", "--heads", ".", "agent/v7_a1"), "nothing was pushed"
    assert not any(c[:2] == ["gh", "pr"] for c in gh.calls)
    assert "deploy-v7_a1" not in git(settings.pricing_lab_dir, "worktree", "list")


def test_default_validator_runs_inside_the_worktree_with_the_labs_node_modules(
    settings, lab_repo, monkeypatch
) -> None:
    """origin/main may carry schema changes the local checkout lacks, so the branch must be
    validated by its own scripts, not the lab's."""
    import funnel_growth_agent.deploy as deploy_module

    (settings.pricing_lab_dir / "node_modules").mkdir()
    runs: list[tuple[Path, Path, bool]] = []

    real_run = subprocess.run

    def fake_npm(args, cwd, env=None, **kw):
        if args[0] != "npm":  # git keeps running for real
            return real_run(args, cwd=cwd, env=env, **kw)
        assert args == ["npm", "run", "funnel:validate"]
        modules = Path(cwd) / "node_modules"
        runs.append((Path(cwd), Path(env["FUNNEL_DIRECTORY"]), modules.is_symlink()))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(deploy_module.subprocess, "run", fake_npm)
    deploy_variant(settings, "v7_a1", sh=FakeGitHub(lab_repo), wait_for_merge=False)
    assert len(runs) == 1
    cwd, funnels, linked = runs[0]
    assert cwd != settings.pricing_lab_dir and funnels == cwd / "funnels" and linked
    assert not cwd.exists(), "the worktree (and its node_modules link) are gone afterwards"
