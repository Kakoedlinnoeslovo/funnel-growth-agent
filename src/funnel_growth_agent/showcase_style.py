"""What the real showcase tiles of the base landing look like, read by Gemini once per
landing hash, so Claude can write briefs that sit next to their neighbours and can name the
`tile:<label>:<i>` ids as references. Never raises into the tool loop."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .config import Settings
from .landing import landing_hash
from .media import base_showcase_groups, image_mime
from .models import CachedShowcaseStyle, GroupStyle, TileStyleRead

STYLE_PROMPT = (
    'These are the {n} tiles of the "{label}" gallery ({caption}) on our landing page, in '
    "order. For each tile return subject (one phrase), medium (photo, flat vector, 3d, "
    "lettering, illustration, mockup...), palette (2-3 hex colours, dominant first), "
    "background (one phrase), composition (one sentence), hasText (boolean). Then family: one "
    'sentence describing what visually unites them. Return JSON only: {{"tiles":[...],'
    '"family":"..."}}. Do not give advice.'
)


class StyleReader(Protocol):
    def read(self, label: str, caption: str | None, images: list[Path]) -> GroupStyle: ...


class GeminiStyleReader:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def read(self, label: str, caption: str | None, images: list[Path]) -> GroupStyle:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.settings.gemini_api_key)
        parts: list[Any] = [
            types.Part.from_bytes(data=image.read_bytes(), mime_type=image_mime(image))
            for image in images
        ]
        parts.append(
            types.Part.from_text(
                text=STYLE_PROMPT.format(n=len(images), label=label, caption=caption or "")
            )
        )
        response = client.models.generate_content(
            model=self.settings.gemini_model,
            contents=parts,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        data = json.loads(response.text or "{}")
        tiles = [TileStyleRead.model_validate(item) for item in data.get("tiles") or []]
        return GroupStyle(label=label, caption=caption, family=data.get("family"), tiles=tiles)


def style_cache_path(settings: Settings) -> Path:
    digest = landing_hash(settings.landing_path)[:8]
    return settings.research_dir / f"showcase-{settings.base_version}-{digest}.json"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def read_showcase_style(
    settings: Settings,
    *,
    reader: StyleReader | None = None,
    refresh: bool = False,
) -> CachedShowcaseStyle:
    digest = landing_hash(settings.landing_path)
    settings.research_dir.mkdir(parents=True, exist_ok=True)
    cache = style_cache_path(settings)
    if cache.is_file() and not refresh:
        cached = CachedShowcaseStyle.model_validate_json(cache.read_text(encoding="utf-8"))
        if cached.error is None:
            return cached
    groups = base_showcase_groups(settings, settings.base_version)
    if not groups:
        return CachedShowcaseStyle(
            base_version=settings.base_version,
            landing_hash=digest,
            read_at=_now(),
            error="the base landing has no showcase section",
        )
    if reader is None:
        if not settings.gemini_api_key:
            return CachedShowcaseStyle(
                base_version=settings.base_version,
                landing_hash=digest,
                read_at=_now(),
                error="gemini unavailable: GEMINI_API_KEY is not set",
            )
        reader = GeminiStyleReader(settings)
    funnel_dir = settings.pricing_lab_dir / "funnels" / settings.base_version
    out: list[GroupStyle] = []
    for label, (caption, rels) in groups.items():
        images = [funnel_dir / rel for rel in rels]
        missing = [str(path) for path in images if not path.is_file()]
        if missing:
            return CachedShowcaseStyle(
                base_version=settings.base_version,
                landing_hash=digest,
                read_at=_now(),
                error=f"showcase images missing on disk: {missing[:2]}",
            )
        try:
            group = reader.read(label, caption, images)
        except Exception as error:
            return CachedShowcaseStyle(
                base_version=settings.base_version,
                landing_hash=digest,
                read_at=_now(),
                error=f"read failed for {label!r}: {type(error).__name__}: {str(error)[:200]}",
            )
        group.label = label
        group.caption = caption
        for index, tile in enumerate(group.tiles):
            tile.reference_id = f"tile:{label}:{index}"
        out.append(group)
    record = CachedShowcaseStyle(
        base_version=settings.base_version,
        landing_hash=digest,
        read_at=_now(),
        model=settings.gemini_model,
        groups=out,
        error=None,
    )
    cache.write_text(record.model_dump_json(by_alias=True, indent=2), encoding="utf-8")
    return record
