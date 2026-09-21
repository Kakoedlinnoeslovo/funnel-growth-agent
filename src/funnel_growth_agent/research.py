"""Look at a reference landing: desktop and phone screenshots via the gstack browse CLI,
read into a structured pattern summary by Gemini. Cached per URL. Never raises into the
tool loop; a missing browser or key comes back as an `error` field."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from .config import Settings
from .creatives import LAB_HOSTS
from .models import CAMEL

DESKTOP = (1440, 900)
PHONE = (390, 844)

READ_PROMPT = (
    "These are two screenshots of the first screen of {url}: desktop (1440 wide) then phone "
    "(390 wide). Describe the landing pattern only. Return JSON with keys heroHeadline, "
    "heroSubhead, primaryCta, ctaAboveFold (boolean), heroMedia (one of video, image, none, "
    "interactive), firstScreenSections (list, top to bottom), proofElements (list), "
    "notablePatterns (list of short observations about layout, media and CTA placement), "
    "phoneDifferences (list). Do not give CRO advice. Do not invent text that is not visible."
)


class LandingPatternRead(BaseModel):
    model_config = CAMEL

    hero_headline: str | None = Field(default=None, alias="heroHeadline")
    hero_subhead: str | None = Field(default=None, alias="heroSubhead")
    primary_cta: str | None = Field(default=None, alias="primaryCta")
    cta_above_fold: bool | None = Field(default=None, alias="ctaAboveFold")
    hero_media: Literal["video", "image", "none", "interactive"] | None = Field(
        default=None, alias="heroMedia"
    )
    first_screen_sections: list[str] = Field(default_factory=list, alias="firstScreenSections")
    proof_elements: list[str] = Field(default_factory=list, alias="proofElements")
    notable_patterns: list[str] = Field(default_factory=list, alias="notablePatterns")
    phone_differences: list[str] = Field(default_factory=list, alias="phoneDifferences")


class CachedResearch(BaseModel):
    model_config = CAMEL

    url: str
    fetched_at: str = Field(alias="fetchedAt")
    model: str | None = None
    screenshots: list[str] = Field(default_factory=list)
    read: LandingPatternRead | None = None
    error: str | None = None


class Browser(Protocol):
    def available(self) -> bool: ...

    def screenshot(self, url: str, *, width: int, height: int, out: Path) -> None: ...


def find_browse_binary(settings: Settings | None = None) -> Path | None:
    candidates: list[Path | None] = []
    if settings is not None and settings.browse_bin:
        candidates.append(Path(settings.browse_bin))
    env = os.environ.get("GSTACK_BROWSE")
    if env:
        candidates.append(Path(env))
    candidates.append(Path.home() / ".claude" / "skills" / "gstack" / "browse" / "dist" / "browse")
    found = shutil.which("browse")
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    return None


class GstackBrowser:
    def __init__(self, binary: Path | None, *, timeout: float = 60.0) -> None:
        self.binary = binary
        self.timeout = timeout

    def available(self) -> bool:
        return self.binary is not None and Path(self.binary).is_file()

    def _run(self, *args: str) -> None:
        completed = subprocess.run(
            [str(self.binary), *args],
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()[-300:]
            raise RuntimeError(f"browse {' '.join(args[:1])} failed: {detail}")

    def screenshot(self, url: str, *, width: int, height: int, out: Path) -> None:
        self._run("viewport", f"{width}x{height}")
        self._run("goto", url)
        try:
            # Marketing pages hydrate after load; a blank hero screenshot reads as nothing.
            self._run("wait", "--networkidle")
        except RuntimeError:
            pass
        self._run("screenshot", "--viewport", str(out))
        if not out.is_file():
            raise RuntimeError(f"browse wrote no screenshot at {out}")


class PatternReader(Protocol):
    def read(self, url: str, shots: list[Path]) -> LandingPatternRead: ...


class GeminiPatternReader:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def read(self, url: str, shots: list[Path]) -> LandingPatternRead:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.settings.gemini_api_key)
        parts: list[Any] = [
            types.Part.from_bytes(data=shot.read_bytes(), mime_type="image/png") for shot in shots
        ]
        parts.append(types.Part.from_text(text=READ_PROMPT.format(url=url)))
        response = client.models.generate_content(
            model=self.settings.gemini_model,
            contents=parts,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        return LandingPatternRead.model_validate(json.loads(response.text or "{}"))


def research_cache_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def research_landing(
    url: str,
    settings: Settings,
    *,
    browser: Browser | None = None,
    reader: PatternReader | None = None,
    refresh: bool = False,
) -> CachedResearch:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        return CachedResearch(url=url, fetched_at=_now(), error="only https URLs can be researched")
    if host in LAB_HOSTS or host.endswith(".vercel.app"):
        return CachedResearch(
            url=url, fetched_at=_now(), error="that is the lab itself; use get_current_landing"
        )
    settings.research_dir.mkdir(parents=True, exist_ok=True)
    key = research_cache_key(url)
    cache = settings.research_dir / f"{key}.json"
    if cache.is_file() and not refresh:
        cached = CachedResearch.model_validate_json(cache.read_text(encoding="utf-8"))
        if cached.read is not None:
            return cached
    browser = browser or GstackBrowser(find_browse_binary(settings))
    if not browser.available():
        return CachedResearch(
            url=url,
            fetched_at=_now(),
            error="browse unavailable: install gstack or set GSTACK_BROWSE to the browse binary",
        )
    shots: list[Path] = []
    try:
        for tag, (width, height) in (("desktop", DESKTOP), ("phone", PHONE)):
            out = settings.research_dir / f"{key}-{tag}.png"
            browser.screenshot(url, width=width, height=height, out=out)
            shots.append(out)
    except Exception as error:
        return CachedResearch(
            url=url, fetched_at=_now(), error=f"screenshot failed: {type(error).__name__}: {error}"
        )
    if reader is None:
        if not settings.gemini_api_key:
            return CachedResearch(
                url=url,
                fetched_at=_now(),
                screenshots=[str(shot) for shot in shots],
                error="gemini unavailable: GEMINI_API_KEY is not set",
            )
        reader = GeminiPatternReader(settings)
    try:
        read = reader.read(url, shots)
    except Exception as error:
        return CachedResearch(
            url=url,
            fetched_at=_now(),
            screenshots=[str(shot) for shot in shots],
            error=f"read failed: {type(error).__name__}: {str(error)[:200]}",
        )
    record = CachedResearch(
        url=url,
        fetched_at=_now(),
        model=settings.gemini_model,
        screenshots=[str(shot) for shot in shots],
        read=read,
        error=None,
    )
    cache.write_text(record.model_dump_json(by_alias=True, indent=2), encoding="utf-8")
    return record
