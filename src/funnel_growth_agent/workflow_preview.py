"""Build isolated pricing-lab snapshots and serve the exact reviewed artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from dotenv import dotenv_values

from .config import Settings
from .landing_diff import walk_files

MARKER = "creative-landing-version.json"


def artifact_digest(dist: Path) -> str:
    digest = hashlib.sha256()
    for relative, path in sorted(walk_files(dist).items()):
        if relative == MARKER:
            continue
        digest.update(relative.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def copy_lab(source: Path, dest: Path) -> None:
    """Copy source and content, never credentials, build artifacts or dependencies."""
    shutil.copytree(
        source,
        dest,
        ignore=shutil.ignore_patterns(
            ".git",
            ".vercel",
            ".env*",
            "node_modules",
            "dist",
            ".worktrees",
            ".claude",
            "content",
            ".DS_Store",
            "*.tsbuildinfo",
        ),
    )
    # Tailwind's scanner honors .gitignore only inside a repository boundary.
    # Without this empty boundary it scans symlinked dependencies and old builds.
    # Never copy the source repository's Git configuration or credentials.
    (dest / ".git").mkdir()
    dependencies = source / "node_modules"
    if dependencies.exists():
        (dest / "node_modules").symlink_to(dependencies.resolve(), target_is_directory=True)
    # TypeScript's incremental output belongs to this snapshot, not the shared dependency tree.
    for config in dest.glob("tsconfig*.json"):
        text = config.read_text()
        text = re.sub(
            r'("tsBuildInfoFile"\s*:\s*")[^"]+(")',
            r"\1.cache/" + config.stem + r".tsbuildinfo\2",
            text,
        )
        config.write_text(text)


def build_environment(settings: Settings) -> dict[str, str]:
    # These are public frontend values. No API credentials are copied into the snapshot.
    values: dict[str, str] = {}
    for filename in (".env", ".env.local", ".env.production", ".env.production.local"):
        values.update(
            {
                key: value
                for key, value in dotenv_values(settings.pricing_lab_dir / filename).items()
                if key.startswith("VITE_") and value is not None
            }
        )
    values.update({key: value for key, value in os.environ.items() if key.startswith("VITE_")})
    values.setdefault("VITE_RECRAFT_API_URL", "https://api.recraft.ai")
    return values


def build_preview(lab: Path, identity: dict, environment: dict[str, str]) -> Path:
    result = subprocess.run(
        ["npm", "run", "build"],
        cwd=lab,
        env={
            **{key: value for key, value in os.environ.items() if not key.startswith("VITE_")},
            **environment,
            "VERCEL_ENV": environment.get("VITE_VERCEL_ENV", "preview"),
            "VERCEL_GIT_COMMIT_REF": environment.get("VITE_VERCEL_GIT_COMMIT_REF", ""),
        },
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Landing build failed:\n" + (result.stdout + result.stderr)[-4000:])
    dist = lab / "dist"
    if not (dist / "index.html").is_file():
        raise RuntimeError("Landing build did not produce index.html")
    marker = {**identity, "artifactHash": artifact_digest(dist)}
    (dist / MARKER).write_text(json.dumps(marker, sort_keys=True))
    return dist


class PreviewHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        pass

    def end_headers(self) -> None:
        # Local review must not send advertising/analytics events or initiate checkout.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self' data: blob:; img-src 'self' data: blob: https://cdn.prod.website-files.com; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'none'; form-action 'none'; frame-src 'none'; object-src 'none'",
        )
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def translate_path(self, path: str) -> str:
        root = Path(self.directory).resolve()
        route = unquote(urlsplit(path).path)
        if route.startswith("/pm/assets/"):
            route = route.removeprefix("/pm")
        target = (root / route.lstrip("/")).resolve()
        if not target.is_relative_to(root):
            return str(root / "__missing__")
        if target.is_file():
            return str(target)
        if not Path(route).suffix:
            return str(root / "index.html")
        return str(target)

    def list_directory(self, path: str):
        self.send_error(404)
        return None


class PreviewServers:
    def __init__(self) -> None:
        self.servers: dict[str, ThreadingHTTPServer] = {}
        self.lock = threading.Lock()

    def url(self, dist: Path, version: str) -> str:
        key = str(dist.resolve())
        with self.lock:
            if key not in self.servers:
                server = ThreadingHTTPServer(
                    ("127.0.0.1", 0), partial(PreviewHandler, directory=key)
                )
                server.daemon_threads = True
                self.servers[key] = server
                threading.Thread(target=server.serve_forever, daemon=True).start()
            port = self.servers[key].server_address[1]
        return f"http://127.0.0.1:{port}/pm/{version}"

    def proxy(self, upstream: str) -> str:
        """Review the existing dev server with the same isolation as a built draft."""
        parsed = urlsplit(upstream)
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Baseline preview must use a local lab dev server")
        origin = f"{parsed.scheme}://{parsed.netloc}"
        key = "proxy:" + origin
        with self.lock:
            if key not in self.servers:
                server = ThreadingHTTPServer(
                    ("127.0.0.1", 0), partial(DevPreviewHandler, upstream=origin)
                )
                server.daemon_threads = True
                self.servers[key] = server
                threading.Thread(target=server.serve_forever, daemon=True).start()
            port = self.servers[key].server_address[1]
        return f"http://127.0.0.1:{port}{parsed.path}"

    def close(self) -> None:
        for server in self.servers.values():
            server.shutdown()
            server.server_close()


class DevPreviewHandler(PreviewHandler):
    def __init__(self, *args, upstream: str, **kwargs):
        self.upstream = upstream
        super().__init__(*args, **kwargs)

    def do_GET(self):  # noqa: N802
        import httpx

        try:
            # The destination is fixed by the local console, never by a request parameter.
            response = httpx.get(self.upstream + self.path, timeout=20, follow_redirects=False)
            self.send_response(response.status_code)
            self.send_header(
                "Content-Type", response.headers.get("content-type", "application/octet-stream")
            )
            self.send_header("Content-Length", str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)
        except httpx.HTTPError:
            self.send_error(503, "Start the pricing-lab dev server to preview a baseline")

    def do_HEAD(self):  # noqa: N802
        self.send_error(405)


def deployment_config(settings: Settings) -> dict[str, str] | None:
    if settings.vercel_project_id and settings.vercel_org_id:
        return {"projectId": settings.vercel_project_id, "orgId": settings.vercel_org_id}
    path = settings.pricing_lab_dir / ".vercel/project.json"
    if path.is_file():
        value = json.loads(path.read_text())
        if value.get("projectId") and value.get("orgId"):
            return {key: value[key] for key in ("projectId", "orgId")}
    return None


def publish_artifact(settings: Settings, dist: Path, destination: Path) -> str:
    """Vercel preview deployments get unique public URLs; never promote or alias production."""
    import httpx

    config = deployment_config(settings)
    if not config:
        raise RuntimeError(
            "Publishing needs a linked Vercel project: run vercel link in the pricing lab, or set VERCEL_PROJECT_ID and VERCEL_ORG_ID."
        )
    if not shutil.which(settings.vercel_bin):
        raise RuntimeError("Vercel CLI is unavailable. Install it and sign in, then retry Publish.")
    vercel = destination / ".vercel"
    vercel.mkdir(parents=True, exist_ok=True)
    (vercel / "project.json").write_text(json.dumps(config))
    output = vercel / "output"
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(dist, output / "static")
    (output / "config.json").write_text(
        json.dumps(
            {
                "version": 3,
                "routes": [
                    {"src": "/pm/assets/(.*)", "dest": "/assets/$1"},
                    {"handle": "filesystem"},
                    {"src": "/.*", "dest": "/index.html"},
                ],
            }
        )
    )
    result = subprocess.run(
        [settings.vercel_bin, "deploy", "--prebuilt", "--yes"],
        cwd=destination,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Public deployment failed:\n" + result.stderr[-2500:])
    urls = [
        line.strip().rstrip("/")
        for line in result.stdout.splitlines()
        if line.strip().startswith("https://")
    ]
    if not urls:
        raise RuntimeError(
            "Deployment returned no HTTPS URL. The draft is saved; retry publication."
        )
    origin = urls[-1]
    parsed = urlsplit(origin)
    if not parsed.hostname or not parsed.hostname.endswith(".vercel.app") or parsed.username:
        raise RuntimeError("Deployment returned an unexpected URL")
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        response = client.get(f"{origin}/{MARKER}")
        try:
            verified = response.status_code == 200 and response.json() == json.loads(
                (dist / MARKER).read_text()
            )
        except ValueError:
            verified = False
        if not verified:
            raise RuntimeError(
                "Deployment is not publicly accessible or its artifact could not be verified. Check Vercel Deployment Protection for this project, then retry."
            )
        page = client.get(origin + "/pm/" + json.loads((dist / MARKER).read_text())["variant"])
        if page.status_code != 200 or page.content != (dist / "index.html").read_bytes():
            raise RuntimeError("The public landing does not match the reviewed build.")
    return origin
