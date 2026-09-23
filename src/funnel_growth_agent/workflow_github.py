"""Publish version-only commits through an existing GitHub → Vercel integration.

Preparation is local and creates a fresh reviewable build from the current deployment
branch. Publishing is a separate action, uses a normal fast-forward push, and waits
for a successful deployment plus an unauthenticated verification of the public page.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .apply import _tree_digest, hard_diff_site, patch_site
from .config import Settings
from .landing_patch import dump_yaml, load_yaml
from .workflow_catalog import safe_version
from .workflow_preview import MARKER, build_preview, copy_lab


def run(argv: list[str], *, cwd: Path | None = None, timeout: int = 120) -> str:
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(
            f"{argv[0]} {argv[1]} failed: " + (result.stderr or result.stdout)[-2500:]
        )
    return result.stdout.strip()


def register_version_contracts(checkout: Path, version: Path, variant: str) -> list[str]:
    """Initialize the new version's identity and explicit phone-layout test enrollment.

    Existing versions keep their identities. The lab deliberately asserts a unique
    paywall per version and explicitly lists versions inheriting its phone-first layout.
    """
    slug = variant.replace("/", "-").replace("_", "-")
    for path in (version / "steps").glob("*.yaml"):
        step = load_yaml(path)
        if step.get("component") == "paywall":
            props = step.setdefault("props", {})
            props["paywallId"] = f"{slug}-{props.get('layout', 'classic')}"
            dump_yaml(path, step)
    changed = []
    identity_test = checkout / "src/funnel/engine/recraft-home.test.ts"
    if identity_test.is_file():
        old = identity_test.read_text()
        # Version paths allow _aN; analytic ids permit neither '/' nor '_'.
        new = old.replace("version.replace(/\\//g, '-')", "version.replace(/[/_]/g, '-')")
        if new != old:
            identity_test.write_text(new)
            changed.append(str(identity_test.relative_to(checkout)))
    mobile_test = checkout / "src/funnel/engine/recraft-quiz.test.ts"
    landing = load_yaml(version / "steps/landing.yaml")
    if (landing.get("props") or {}).get("mobile") == "phone-first" and mobile_test.is_file():
        old = mobile_test.read_text()
        match = re.search(r"const phoneFirst = new Set\(\[(.*?)\]\)", old)
        if not match:
            raise ValueError(
                "The site's mobile-layout contract changed; update its version registration before publishing."
            )
        members = re.findall(r"['\"]([^'\"]+)['\"]", match[1])
        if variant not in members:
            members.append(variant)
            new = (
                old[: match.start(1)]
                + ", ".join(f"'{item}'" for item in members)
                + old[match.end(1) :]
            )
            mobile_test.write_text(new)
            changed.append(str(mobile_test.relative_to(checkout)))
    return changed


class GitHubPublisher:
    def __init__(self, settings: Settings):
        self.settings = settings

    def config(self) -> tuple[str, str, str]:
        repo = self.settings.github_publish_repo or ""
        branch = self.settings.github_publish_branch
        origin = (self.settings.github_public_origin or "").rstrip("/")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise ValueError("Set GITHUB_PUBLISH_REPO to the connected owner/repository.")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]*", branch) or ".." in branch:
            raise ValueError("Invalid GitHub publishing branch")
        url = urlsplit(origin)
        if url.scheme != "https" or not url.hostname or url.username or url.path or url.query:
            raise ValueError("Set GITHUB_PUBLIC_ORIGIN to the existing public HTTPS site origin.")
        if not shutil.which("gh"):
            raise ValueError("GitHub CLI is required. Install gh and sign in, then retry.")
        return repo, branch, origin

    def api(self, path: str):
        return json.loads(run(["gh", "api", path]))

    def prepare(
        self,
        source: Path,
        lab: Path,
        workspace: Path,
        identity: dict,
        environment: dict[str, str],
        emit,
        builder=build_preview,
    ) -> dict:
        repo, branch, origin = self.config()
        workspace.mkdir(parents=True, exist_ok=True)
        checkout = workspace / "repository"
        emit("github_preparing", {"message": "Refreshing the preview from the current public site"})
        # Read the user's checkout as an object cache; never switch or modify its branch/index.
        run(
            [
                "git",
                "clone",
                "--shared",
                "--no-checkout",
                str(self.settings.pricing_lab_dir),
                str(checkout),
            ]
        )
        run(["git", "remote", "set-url", "origin", f"git@github.com:{repo}.git"], cwd=checkout)
        run(["git", "fetch", "--depth=1", "origin", branch], cwd=checkout, timeout=300)
        base_sha = run(["git", "rev-parse", "FETCH_HEAD"], cwd=checkout)
        run(["git", "checkout", "--detach", base_sha], cwd=checkout)
        variant = identity["variant"]
        version = safe_version(checkout / "funnels", variant)
        if version.exists():
            raise ValueError(
                f"{variant} already exists on the public site. Existing versions were preserved."
            )
        version.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source / "funnels" / variant, version)
        if (load_yaml(version / "steps/landing.yaml").get("props") or {}).get("growthDesign"):
            from .blueprint import require_renderer
            require_renderer(checkout, load_yaml(version / "steps/landing.yaml")["props"]["growthDesign"]["schemaVersion"])
        contract_paths = register_version_contracts(checkout, version, variant)
        original_site = (checkout / "funnels/site.yaml").read_bytes()
        patch_site(
            checkout / "funnels/site.yaml", variant, "Creative-matched landing", "landing_redesign"
        )
        hard_diff_site(original_site, checkout / "funnels/site.yaml", variant)
        version_marker = f"creative-landings/{identity['draftId']}.json"
        marker = {**identity, "sourceHash": _tree_digest(version)}
        public_marker = checkout / "public" / version_marker
        public_marker.parent.mkdir(parents=True, exist_ok=True)
        public_marker.write_text(json.dumps(marker, sort_keys=True))
        environment = self.production_environment(branch)
        dist = self.build(checkout, lab, identity, environment, emit, builder)
        # Vercel copies this public file verbatim; index/asset fingerprints prove the build.
        shutil.copy2(dist / MARKER, checkout / "public" / MARKER)
        paths = [
            f"funnels/{variant}",
            "funnels/site.yaml",
            f"public/{MARKER}",
            f"public/{version_marker}",
            *contract_paths,
        ]
        run(["git", "add", "--", *paths], cwd=checkout)
        changed = run(["git", "diff", "--cached", "--name-only"], cwd=checkout).splitlines()
        if any(
            not any(name == item or name.startswith(item + "/") for item in paths)
            for name in changed
        ):
            raise ValueError(
                "Publication contains files outside the new version; nothing was pushed."
            )
        run(["git", "commit", "-m", f"Publish creative landing {variant}"], cwd=checkout)
        sha = run(["git", "rev-parse", "HEAD"], cwd=checkout)
        return {
            "provider": "github",
            "repository": repo,
            "branch": branch,
            "origin": origin,
            "baseSha": base_sha,
            "commitSha": sha,
            "workspace": str(workspace),
            "versionMarker": version_marker,
            "marker": marker,
            "environment": environment,
        }

    def production_environment(self, branch: str) -> dict[str, str]:
        # A developer's .env.local often points at staging. Production previews use
        # repository defaults plus explicitly configured public deployment overrides.
        return {
            **self.settings.github_build_environment,
            "VITE_VERCEL_ENV": "production",
            "VITE_VERCEL_GIT_COMMIT_REF": branch,
        }

    def build(self, checkout, lab, identity, environment, emit, builder) -> Path:
        copy_lab(checkout, lab)
        # This checkout contains only committed project guidance. Tailwind scans
        # those tracked files too, so retain them to reproduce the remote CSS.
        if (checkout / ".claude").is_dir():
            shutil.copytree(checkout / ".claude", lab / ".claude")
        dependencies = self.settings.pricing_lab_dir / "node_modules"
        if (
            dependencies.is_dir()
            and (checkout / "package-lock.json").read_bytes()
            == (self.settings.pricing_lab_dir / "package-lock.json").read_bytes()
        ):
            (lab / "node_modules").symlink_to(dependencies.resolve(), target_is_directory=True)
        else:
            run(["npm", "ci"], cwd=lab, timeout=600)
        emit("github_checking", {"message": "Checking the new version with the current site"})
        run(["npm", "test"], cwd=lab, timeout=300)
        # The snapshot lives below this agent's Git-ignored data directory. Limit lint
        # to the lab's source paths so ancestor ignore rules cannot hide all files.
        run(
            ["npm", "run", "lint", "--", "--no-ignore", "src", "scripts", "vite.config.ts"],
            cwd=lab,
            timeout=300,
        )
        return builder(lab, identity, environment)

    def refresh(self, publication, lab, identity, emit, builder=build_preview) -> dict:
        """Re-review a saved publication without altering its source or pushing again."""
        checkout = Path(publication["workspace"]) / "repository"
        if run(["git", "rev-parse", "HEAD"], cwd=checkout) != publication["commitSha"] or run(
            ["git", "status", "--porcelain"], cwd=checkout
        ):
            raise ValueError("The saved publication changed; its source must be restored first.")
        environment = self.production_environment(publication["branch"])
        self.build(checkout, lab, identity, environment, emit, builder)
        return {**publication, "environment": environment}

    def publish(self, publication: dict, dist: Path, emit) -> str:
        repo, branch, origin = self.config()
        if (repo, branch, origin) != (
            publication["repository"],
            publication["branch"],
            publication["origin"],
        ):
            raise ValueError("Publishing settings changed. Refresh the publishing preview first.")
        checkout = Path(publication["workspace"]) / "repository"
        sha = publication["commitSha"]
        head = self.api(f"repos/{repo}/git/ref/heads/{branch}")["object"]["sha"]
        if head != sha:
            if head != publication["baseSha"]:
                comparison = (
                    self.api(f"repos/{repo}/compare/{sha}...{head}")
                    if publication.get("pushAttempted")
                    else {}
                )
                if comparison.get("status") not in {"ahead", "identical"}:
                    emit(
                        "github_not_published",
                        {"message": "The public site changed; refresh the publishing preview"},
                    )
                    raise ValueError(
                        "The public site changed since this preview. Refresh the publishing preview, review it, then publish again."
                    )
            else:
                if run(["git", "rev-parse", "HEAD"], cwd=checkout) != sha:
                    raise ValueError("The prepared publication changed; refresh its preview.")
                if run(["git", "status", "--porcelain"], cwd=checkout):
                    raise ValueError(
                        "The prepared publication has unsaved changes; refresh its preview."
                    )
                publication["pushAttempted"] = True
                emit(
                    "github_pushing", {"message": "Publishing the reviewed version through GitHub"}
                )
                # No force, no unrelated commits, no changes to the site's default version.
                run(
                    ["git", "push", "origin", f"{sha}:refs/heads/{branch}"],
                    cwd=checkout,
                    timeout=300,
                )
        emit(
            "github_deploying",
            {"message": "GitHub accepted the version; waiting for the public deployment"},
        )
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            deployments = self.api(f"repos/{repo}/deployments?sha={sha}&per_page=20")
            deployments = [
                d for d in deployments if d.get("environment", "").lower() == "production"
            ]
            for deployment in deployments[:1]:
                statuses = self.api(
                    f"repos/{repo}/deployments/{deployment['id']}/statuses?per_page=1"
                )
                if not statuses:
                    continue
                state = statuses[0]["state"]
                if state in {"failure", "error"}:
                    raise RuntimeError(
                        "GitHub saved the version, but the deployment failed. Check the repository deployment, then retry; the saved commit will be reused."
                    )
                if state == "success":
                    self.verify(publication, dist)
                    return origin
            time.sleep(5)
        raise RuntimeError(
            "GitHub saved the version; the public deployment is still pending. Retry to check it without publishing a duplicate."
        )

    def verify(self, publication: dict, dist: Path) -> None:
        origin = publication["origin"]
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            marker = client.get(f"{origin}/{publication['versionMarker']}")
            try:
                matches = marker.status_code == 200 and marker.json() == publication["marker"]
            except ValueError:
                matches = False
            if not matches:
                raise RuntimeError(
                    "The new version is not publicly available yet. Retry to verify the saved deployment."
                )
            page = client.get(origin + "/pm/" + publication["marker"]["variant"])
            if page.status_code != 200 or page.content != (dist / "index.html").read_bytes():
                raise RuntimeError(
                    "The deployed build differs from the reviewed preview. Check the site's build environment before retrying verification."
                )
