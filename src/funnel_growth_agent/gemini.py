"""Gemini describe/extract for the already-ranked top creatives."""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .models import CachedCreativeAnalysis, CreativeAnalysis, RankedCreative

ANALYSIS_SCHEMA_VERSION = 1

ModelCall = Callable[[RankedCreative, Path | None], CreativeAnalysis]


def asset_fingerprint(path: Path | None) -> str:
    if path is None or not path.is_file():
        return "missing"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    stat = path.stat()
    return f"{digest}:{stat.st_mtime_ns}"


def _cache_path(settings: Settings, creative_id: str) -> Path:
    settings.analysis_dir.mkdir(parents=True, exist_ok=True)
    return settings.analysis_dir / f"{creative_id}.json"


def _fallback(creative: RankedCreative) -> CreativeAnalysis:
    visible = [text for text in (creative.title, creative.body, creative.ad_name) if text]
    promise = creative.title or creative.body or creative.ad_name or "Unknown ad promise"
    return CreativeAnalysis(
        visual_hook="Asset unavailable; using ad title and body only.",
        primary_promise=promise,
        audience_intent="Inferred from ad copy only.",
        cta_intent=creative.title or "unspecified",
        visible_text=visible,
        product_claims=[],
        suggested_landing_theme=promise,
    )


def _gemini_call(creative: RankedCreative, asset: Path | None, settings: Settings) -> CreativeAnalysis:
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.gemini_api_key)
    parts: list[Any] = [
        types.Part.from_text(
            text=(
                "Describe this paid social creative. Extract visible text, the primary "
                "promise, audience intent, CTA intent, and a suggested landing theme. "
                "Do not decide whether to run an experiment. Do not pick a winner. "
                "Do not give CRO advice.\n\n"
                f"ad_name: {creative.ad_name}\n"
                f"title: {creative.title}\n"
                f"body: {creative.body}\n"
                f"link_url: {creative.link_url}\n"
                "Return JSON with keys visualHook, primaryPromise, audienceIntent, "
                "ctaIntent, visibleText, productClaims, suggestedLandingTheme."
            )
        )
    ]
    if asset and asset.is_file():
        mime = "image/jpeg"
        suffix = asset.suffix.lower()
        if suffix == ".png":
            mime = "image/png"
        elif suffix == ".webp":
            mime = "image/webp"
        elif suffix in {".mp4", ".webm", ".mov"}:
            mime = "video/mp4" if suffix == ".mp4" else f"video/{suffix.lstrip('.')}"
        parts.insert(0, types.Part.from_bytes(data=asset.read_bytes(), mime_type=mime))
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=parts,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    return CreativeAnalysis.model_validate(json.loads(response.text or "{}"))


def analyze_creative(
    creative: RankedCreative,
    settings: Settings,
    *,
    call_model: ModelCall | None | bool = True,
    refresh: bool = False,
) -> CachedCreativeAnalysis:
    asset = None
    for candidate in (creative.image_path, creative.video_path):
        if candidate:
            path = Path(candidate)
            if path.is_file():
                asset = path
                break
    fingerprint = asset_fingerprint(asset)
    cache_file = _cache_path(settings, creative.creative_id)
    if not refresh and cache_file.is_file():
        cached = CachedCreativeAnalysis.model_validate_json(cache_file.read_text(encoding="utf-8"))
        if (
            cached.asset_fingerprint == fingerprint
            and cached.schema_version == settings.analysis_schema_version
        ):
            return cached

    analysis: CreativeAnalysis
    model_name = "title-body-fallback"
    runner: ModelCall | None
    if call_model is True:

        def runner(item: RankedCreative, path: Path | None) -> CreativeAnalysis:
            return _gemini_call(item, path, settings)

    elif call_model is False or call_model is None:
        runner = None
    else:
        runner = call_model

    if runner is None or (asset is None and call_model is not True):
        analysis = _fallback(creative)
    else:
        try:
            analysis = runner(creative, asset)
            model_name = settings.gemini_model
        except Exception as error:
            # Loud, not fatal: a silent fallback hid a retired Gemini model for days.
            print(
                f"warning: creative {creative.creative_id} analysis fell back to title/body: "
                f"{type(error).__name__}: {str(error)[:200]}",
                file=sys.stderr,
            )
            analysis = _fallback(creative)
            model_name = "title-body-fallback"

    record = CachedCreativeAnalysis(
        creative_id=creative.creative_id,
        asset_fingerprint=fingerprint,
        analyzed_at=datetime.now(UTC).isoformat(timespec="seconds"),
        model=model_name,
        schema_version=settings.analysis_schema_version,
        analysis=analysis,
    )
    cache_file.write_text(record.model_dump_json(by_alias=True, indent=2), encoding="utf-8")
    return record


def analyze_ranked(
    creatives: list[RankedCreative],
    settings: Settings,
    *,
    refresh: bool = False,
    call_model: ModelCall | None | bool = True,
) -> list[CachedCreativeAnalysis]:
    return [
        analyze_creative(item, settings, call_model=call_model, refresh=refresh) for item in creatives
    ]
