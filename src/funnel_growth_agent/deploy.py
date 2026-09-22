"""Publish an applied variant: a branch off origin/main carrying only `funnels/<variant>` and
its two site.yaml lines, validated, pushed and opened as a pull request. Merging stays a human
click on GitHub; this module waits for it and then follows the Vercel production deployment.

The lab checkout the agent writes into is never touched: the branch is built in a throwaway
git worktree, so a dirty or stale local main cannot leak into the commit. `default_version`
is never written, so deploying publishes a URL and nothing else."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

from .config import Settings
from .events import EmitData, emit_to
from .landing_patch import dump_yaml, load_yaml

__all__ = [
    "BRANCH_PREFIX",
    "STAGES",
    "DeployError",
    "DeployResult",
    "Shell",
    "deploy_variant",
    "parse_github_repo",
]

Shell = Callable[[list[str], Path], "subprocess.CompletedProcess[str]"]
Validate = Callable[[Path], None]

STAGES = ["check", "branch", "validate", "commit", "push", "pr", "preview", "merge", "build"]
BRANCH_PREFIX = "agent/"


class DeployError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeployResult:
    variant: str
    branch: str
    pr_url: str
    pr_number: int | None
    preview_url: str | None
    sha: str | None
    deployment_url: str | None
    prod_url: str | None


def default_shell(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)


def parse_github_repo(remote_url: str) -> str:
    """`owner/repo` from an https or ssh GitHub remote."""
    match = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", remote_url.strip())
    if not match:
        raise DeployError(f"origin is not a GitHub remote: {remote_url.strip() or '(none)'}")
    return f"{match.group(1)}/{match.group(2)}"


def _run(sh: Shell, args: list[str], cwd: Path, *, what: str) -> str:
    completed = sh(args, cwd)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise DeployError(f"{what} failed: {detail or 'no output'}")
    return (completed.stdout or "").strip()


def _gh_json(sh: Shell, args: list[str], cwd: Path, *, what: str) -> Any:
    out = _run(sh, args, cwd, what=what)
    try:
        return json.loads(out) if out else None
    except json.JSONDecodeError as error:
        raise DeployError(f"{what}: unreadable response: {out[:200]}") from error


def _description(site_path: Path, variant: str) -> str:
    doc = load_yaml(site_path)
    published = [str(v) for v in (doc.get("published_versions") or [])]
    description = (doc.get("versions") or {}).get(variant)
    if variant not in published or not description:
        raise DeployError(f"{variant} is not published in {site_path}; run apply first")
    return str(description)


def _patch_target_site(site_path: Path, variant: str, description: str) -> None:
    from .apply import _append_keeping_trailing_comment

    doc = load_yaml(site_path)
    published = doc.get("published_versions")
    if published is None:
        doc["published_versions"] = [variant]
    elif variant in published:
        raise DeployError(f"{variant} is already in published_versions on origin")
    else:
        _append_keeping_trailing_comment(published, variant)
    versions = doc.get("versions")
    if versions is None:
        doc["versions"] = {}
        versions = doc["versions"]
    versions[variant] = description
    dump_yaml(site_path, doc)


def _default_branch(sh: Shell, lab: Path) -> str:
    completed = sh(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], lab)
    if completed.returncode == 0 and completed.stdout.strip():
        return completed.stdout.strip().split("/", 1)[-1]
    return "main"


def _deployment(
    sh: Shell, cwd: Path, repo: str, sha: str, environment: str
) -> dict[str, Any] | None:
    rows = _gh_json(
        sh,
        ["gh", "api", f"repos/{repo}/deployments?sha={sha}&per_page=20"],
        cwd,
        what="gh api deployments",
    )
    for row in rows or []:
        if str(row.get("environment", "")).lower() == environment:
            return row
    return None


def _deployment_status(sh: Shell, cwd: Path, repo: str, deployment_id: Any) -> dict[str, Any]:
    rows = _gh_json(
        sh,
        ["gh", "api", f"repos/{repo}/deployments/{deployment_id}/statuses?per_page=1"],
        cwd,
        what="gh api deployment statuses",
    )
    return rows[0] if isinstance(rows, list) and rows else {}


def _validate_on_branch(lab: Path, funnels_dir: Path) -> None:
    """Run the lab's validator inside the worktree, so the branch is checked by its own schema
    code (origin/main may know keys the local checkout does not yet). The worktree has no
    node_modules of its own; the lab's are linked in for the run."""
    worktree = funnels_dir.parent
    modules = worktree / "node_modules"
    if not modules.exists() and (lab / "node_modules").is_dir():
        modules.symlink_to(lab / "node_modules", target_is_directory=True)
    completed = subprocess.run(
        ["npm", "run", "funnel:validate"],
        cwd=worktree,
        env={**os.environ, "FUNNEL_DIRECTORY": str(funnels_dir)},
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise DeployError(
            "funnel:validate failed on the branch\n"
            + (completed.stdout or "")
            + (completed.stderr or "")
        )


def _cleanup(sh: Shell, lab: Path, branch: str) -> None:
    """Drop the throwaway worktree and its local branch; origin keeps what was pushed."""
    listing = sh(["git", "worktree", "list", "--porcelain"], lab)
    if listing.returncode == 0:
        path: str | None = None
        for line in listing.stdout.splitlines():
            if line.startswith("worktree "):
                path = line[len("worktree ") :]
            elif line == f"branch refs/heads/{branch}" and path:
                sh(["git", "worktree", "remove", "--force", path], lab)
    sh(["git", "worktree", "prune"], lab)
    sh(["git", "branch", "-D", branch], lab)


class _Watch:
    """Polling with an injectable clock so tests never sleep."""

    def __init__(
        self,
        poll_interval: float,
        clock: Callable[[], float],
        sleep: Callable[[float], None],
    ) -> None:
        self.poll_interval = poll_interval
        self.clock = clock
        self.sleep = sleep

    def until(self, probe: Callable[[], Any], *, timeout: float, what: str) -> Any:
        deadline = self.clock() + timeout
        while True:
            found = probe()
            if found is not None:
                return found
            if self.clock() >= deadline:
                raise DeployError(f"gave up {what} after {int(timeout)}s")
            self.sleep(self.poll_interval)


def deploy_variant(
    settings: Settings,
    variant: str,
    *,
    on_data: EmitData | None = None,
    sh: Shell | None = None,
    validate: Validate | None = None,
    wait_for_merge: bool = True,
    poll_interval: float = 10.0,
    build_timeout: float = 600.0,
    merge_timeout: float = 3600.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> DeployResult:
    """Open the pull request for `funnels/<variant>`, then (unless `wait_for_merge` is off)
    wait for a person to merge it and for Vercel to deploy production.

    Emits deploy_started, one deploy_stage per finished step (`preview`, `merge` and `build`
    repeat while waiting), then deploy_done or deploy_failed."""
    sh = sh or default_shell
    lab = settings.pricing_lab_dir
    branch = f"{BRANCH_PREFIX}{variant}"
    watch = _Watch(poll_interval, clock, sleep)

    def emit(stage: str, message: str) -> None:
        emit_to(on_data, "deploy_stage", {"variant": variant, "stage": stage, "message": message})

    emit_to(on_data, "deploy_started", {"variant": variant, "branch": branch})
    try:
        result = _deploy(
            settings,
            variant,
            branch,
            sh=sh,
            validate=validate or (lambda funnels: _validate_on_branch(lab, funnels)),
            emit=emit,
            watch=watch,
            wait_for_merge=wait_for_merge,
            build_timeout=build_timeout,
            merge_timeout=merge_timeout,
        )
    except Exception as error:
        emit_to(on_data, "deploy_failed", {"variant": variant, "error": str(error)})
        raise
    finally:
        _cleanup(sh, lab, branch)
    emit_to(
        on_data,
        "deploy_done",
        {
            "variant": variant,
            "branch": branch,
            "prUrl": result.pr_url,
            "prNumber": result.pr_number,
            "previewUrl": result.preview_url,
            "sha": result.sha,
            "deploymentUrl": result.deployment_url,
            "prodUrl": result.prod_url,
            "merged": result.sha is not None,
        },
    )
    return result


def _deploy(
    settings: Settings,
    variant: str,
    branch: str,
    *,
    sh: Shell,
    validate: Validate,
    emit: Callable[[str, str], None],
    watch: _Watch,
    wait_for_merge: bool,
    build_timeout: float,
    merge_timeout: float,
) -> DeployResult:
    lab = settings.pricing_lab_dir
    source = lab / "funnels" / variant
    if not source.is_dir():
        raise DeployError(f"no variant folder {source}")
    if variant == settings.base_version or "_a" not in variant:
        raise DeployError(f"{variant} is not an experiment arm; only agent variants are deployed")
    description = _description(settings.site_path, variant)
    remote = _run(sh, ["git", "config", "--get", "remote.origin.url"], lab, what="git remote")
    repo = parse_github_repo(remote)
    login = _run(sh, ["gh", "api", "user", "--jq", ".login"], lab, what="gh auth")
    emit("check", f"{repo} as {login}")

    _run(sh, ["git", "fetch", "--quiet", "origin"], lab, what="git fetch")
    base = _default_branch(sh, lab)
    if _run(sh, ["git", "ls-remote", "--heads", "origin", branch], lab, what="git ls-remote"):
        raise DeployError(f"origin already has {branch}; a pull request for it may be open")
    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    worktree = Path(mkdtemp(prefix=f"deploy-{variant}-", dir=str(settings.tmp_dir)))
    worktree.rmdir()  # git worktree add creates it
    _run(
        sh,
        ["git", "worktree", "add", "--quiet", "-b", branch, str(worktree), f"origin/{base}"],
        lab,
        what="git worktree add",
    )
    target = worktree / "funnels" / variant
    if target.exists():
        raise DeployError(f"origin/{base} already has funnels/{variant}")
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("content"))
    _patch_target_site(worktree / "funnels" / "site.yaml", variant, description)
    emit("branch", f"{branch} from origin/{base}")

    validate(worktree / "funnels")
    emit("validate", "funnel:validate passed on the branch")

    _run(
        sh,
        ["git", "add", "--", f"funnels/{variant}", "funnels/site.yaml"],
        worktree,
        what="git add",
    )
    _run(
        sh,
        [
            "git",
            "commit",
            "--quiet",
            "-m",
            f"Publish agent experiment {variant}",
            "-m",
            f"{variant}: {description}",
        ],
        worktree,
        what="git commit",
    )
    short = _run(sh, ["git", "rev-parse", "--short", "HEAD"], worktree, what="git rev-parse")
    head = _run(sh, ["git", "rev-parse", "HEAD"], worktree, what="git rev-parse")
    emit("commit", f"{short} · funnels/{variant} + 2 lines in site.yaml")

    _run(sh, ["git", "push", "--quiet", "-u", "origin", branch], worktree, what="git push")
    emit("push", f"origin/{branch}")

    body = (
        f"{description}\n\n"
        f"Opened by `funnel-growth deploy`. Adds `funnels/{variant}` and lists it in "
        "`published_versions`; `default_version` is unchanged, so nothing is served differently "
        "until traffic is pointed at the new URL."
    )
    pr_url = _run(
        sh,
        [
            "gh",
            "pr",
            "create",
            "--base",
            base,
            "--head",
            branch,
            "--title",
            f"Publish agent experiment {variant}",
            "--body",
            body,
        ],
        worktree,
        what="gh pr create",
    ).splitlines()[-1]
    pr_info = (
        _gh_json(sh, ["gh", "pr", "view", pr_url, "--json", "number"], lab, what="gh pr view") or {}
    )
    number = pr_info.get("number")
    emit("pr", f"#{number} open: {pr_url}")

    preview_url: str | None = None
    preview = _deployment(sh, lab, repo, head, "preview")
    if preview is not None:
        status = watch.until(
            lambda: _settled_status(sh, lab, repo, preview.get("id")),
            timeout=build_timeout,
            what="waiting for the preview build",
        )
        preview_url = _landing_url(status.get("environment_url"), None, variant)
        emit("preview", preview_url or "preview built")
    else:
        emit("preview", "no Vercel preview for this branch")

    if not wait_for_merge:
        return DeployResult(variant, branch, pr_url, number, preview_url, None, None, None)

    emit("merge", f"waiting for someone to merge #{number}")

    def merged() -> str | None:
        info = (
            _gh_json(
                sh,
                ["gh", "pr", "view", pr_url, "--json", "state,mergeCommit"],
                lab,
                what="gh pr view",
            )
            or {}
        )
        state = str(info.get("state") or "")
        if state == "CLOSED":
            raise DeployError(f"#{number} was closed without merging")
        if state == "MERGED":
            return str((info.get("mergeCommit") or {}).get("oid") or "")
        return None

    sha = watch.until(merged, timeout=merge_timeout, what=f"waiting for #{number} to merge")
    emit("merge", f"#{number} merged as {sha[:7]}")

    deployment = watch.until(
        lambda: _deployment(sh, lab, repo, sha, "production"),
        timeout=build_timeout,
        what="waiting for Vercel to pick up the merge",
    )
    emit("build", "Vercel is building production")
    status = watch.until(
        lambda: _settled_status(sh, lab, repo, deployment.get("id")),
        timeout=build_timeout,
        what="waiting for the production build",
    )
    deployment_url = status.get("environment_url") or None
    prod_url = _landing_url(deployment_url, settings.prod_landing_base, variant)
    emit("build", f"live: {prod_url}")
    return DeployResult(variant, branch, pr_url, number, preview_url, sha, deployment_url, prod_url)


def _settled_status(sh: Shell, cwd: Path, repo: str, deployment_id: Any) -> dict[str, Any] | None:
    status = _deployment_status(sh, cwd, repo, deployment_id)
    state = str(status.get("state") or "")
    if state == "success":
        return status
    if state in {"failure", "error"}:
        raise DeployError(f"Vercel deployment {state}: {status.get('log_url') or ''}".strip())
    return None


def _landing_url(host: Any, base: str | None, variant: str) -> str | None:
    """`<base>/<variant>`: the configured production base, else the deployment host's `/pm`."""
    if base:
        return f"{base.rstrip('/')}/{variant}"
    if host:
        return f"{str(host).rstrip('/')}/pm/{variant}"
    return None
