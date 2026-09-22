"""Which visual families the paid ads, the researched competitor landings, our own showcase
tiles and previous runs use. Pure: no LLM, no network; everything comes from caches the
other tools already filled. The agent reads this before choosing a tile medium, so the
visual family of a tile set is an explicit, evidence-backed hypothesis."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field

from .config import Settings
from .models import (
    CAMEL,
    CachedCreativeAnalysis,
    CachedShowcaseStyle,
    PreviousRun,
    RankedCreative,
    VisualFamily,
)
from .research import RESEARCH_SCHEMA_VERSION, CachedResearch
from .tile_prompt import MEDIUM_STYLES, family_of_medium

FAMILIES: tuple[VisualFamily, ...] = ("graphic", "studio", "ugc", "editorial", "screen")

# Formats with one obvious family. testimonial / before-after / other resolve by camera feel.
FORMAT_FAMILY: dict[str, VisualFamily] = {
    "ugc-selfie": "ugc",
    "ugc-candid": "ugc",
    "unboxing": "ugc",
    "screenshot": "screen",
    "studio-product": "studio",
    "lifestyle": "editorial",
    "editorial": "editorial",
    "illustration": "graphic",
    "lettering": "graphic",
    "collage": "graphic",
}
CAMERA_FAMILY: dict[str, VisualFamily] = {
    "phone": "ugc",
    "studio": "studio",
    "graphic": "graphic",
}
MEDIUM_KEYWORDS: tuple[tuple[VisualFamily, tuple[str, ...]], ...] = (
    ("ugc", ("selfie", "candid", "phone", "ugc", "unboxing", "handheld")),
    ("screen", ("screenshot", "screen", "ui", "interface")),
    ("editorial", ("lifestyle", "editorial", "magazine")),
    ("graphic", ("vector", "flat", "illustration", "3d", "lettering", "type", "cartoon", "icon")),
    ("studio", ("photo", "mockup", "product", "studio", "render")),
)
SHARE_THRESHOLD = 0.25


def family_of_format(fmt: str | None, camera_feel: str | None = None) -> VisualFamily | None:
    if not fmt:
        return None
    family = FORMAT_FAMILY.get(fmt)
    if family:
        return family
    by_camera = CAMERA_FAMILY.get(camera_feel or "")
    if by_camera:
        return by_camera
    if fmt == "testimonial":
        return "ugc"
    return None


def classify_medium_text(text: str | None) -> VisualFamily | None:
    """Keyword fallback for style reads that predate the `format` field."""
    if not text:
        return None
    lowered = text.lower()
    for family, keywords in MEDIUM_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return family
    return None


class TestedFamily(BaseModel):
    model_config = CAMEL

    run_id: str = Field(alias="runId")
    status: str
    result: str | None = None
    learning: str | None = None
    mediums: list[str] = Field(default_factory=list)


class FamilyEvidence(BaseModel):
    model_config = CAMEL

    family: VisualFamily
    ad_share: float = Field(default=0.0, alias="adShare")
    ad_creative_ids: list[str] = Field(default_factory=list, alias="adCreativeIds")
    competitor_urls: list[str] = Field(default_factory=list, alias="competitorUrls")
    landing_tiles: int = Field(default=0, alias="landingTiles")
    tested: list[TestedFamily] = Field(default_factory=list)


class MediumCard(BaseModel):
    model_config = CAMEL

    medium: str
    family: VisualFamily
    people: bool
    allows_text: bool = Field(alias="allowsText")
    one_line: str = Field(alias="oneLine")


class VisualLandscape(BaseModel):
    model_config = CAMEL

    families: list[FamilyEvidence]
    ads_validate_landing_lacks: list[VisualFamily] = Field(
        default_factory=list, alias="adsValidateLandingLacks"
    )
    competitors_use_we_lack: list[VisualFamily] = Field(
        default_factory=list, alias="competitorsUseWeLack"
    )
    blocked_without_new_evidence: list[VisualFamily] = Field(
        default_factory=list, alias="blockedWithoutNewEvidence"
    )
    untested: list[VisualFamily] = Field(default_factory=list)
    mediums: list[MediumCard] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def _weights(ranked: list[RankedCreative]) -> dict[str, float]:
    if any((item.spend or 0) > 0 for item in ranked):
        return {item.creative_id: max(item.spend or 0, 0.0) for item in ranked}
    if any((item.clicks or 0) > 0 for item in ranked):
        return {item.creative_id: float(max(item.clicks or 0, 0)) for item in ranked}
    return {item.creative_id: 1.0 for item in ranked}


def families_tested(previous: Iterable[PreviousRun]) -> dict[VisualFamily, list[TestedFamily]]:
    """Newest run first per family, from the tile mediums each run's proposal carried."""
    out: dict[VisualFamily, list[TestedFamily]] = {family: [] for family in FAMILIES}
    for run in previous:
        media = ((run.changes or {}).get("media") or {}) if isinstance(run.changes, dict) else {}
        showcase = media.get("showcase") or []
        by_family: dict[VisualFamily, list[str]] = {}
        for tile in showcase:
            medium = tile.get("medium") if isinstance(tile, dict) else None
            family = family_of_medium(medium)
            if family is None:
                continue
            by_family.setdefault(family, [])
            if medium not in by_family[family]:
                by_family[family].append(medium)
        for family, mediums in by_family.items():
            out[family].append(
                TestedFamily(
                    run_id=run.run_id,
                    status=run.status,
                    result=run.result,
                    learning=run.learning,
                    mediums=mediums,
                )
            )
    return out


def visual_landscape(
    ranked: list[RankedCreative],
    analyses: Iterable[CachedCreativeAnalysis],
    research: Iterable[CachedResearch],
    style: CachedShowcaseStyle | None,
    previous: Iterable[PreviousRun],
) -> VisualLandscape:
    by_id = {item.creative_id: item.analysis for item in analyses}
    weights = _weights(ranked)
    total = sum(weights.values()) or 1.0
    ad_weight: dict[VisualFamily, float] = {family: 0.0 for family in FAMILIES}
    ad_ids: dict[VisualFamily, list[str]] = {family: [] for family in FAMILIES}
    unknown_weight = 0.0
    for item in ranked:
        analysis = by_id.get(item.creative_id)
        family = family_of_format(analysis.format, analysis.camera_feel) if analysis else None
        if family is None:
            unknown_weight += weights.get(item.creative_id, 0.0)
            continue
        ad_weight[family] += weights.get(item.creative_id, 0.0)
        ad_ids[family].append(item.creative_id)

    competitor_urls: dict[VisualFamily, list[str]] = {family: [] for family in FAMILIES}
    stale_research: list[str] = []
    for record in research:
        if record.read is None:
            continue
        if record.schema_version < RESEARCH_SCHEMA_VERSION:
            stale_research.append(record.url)
        seen: set[VisualFamily] = set()
        for fmt in record.read.imagery_formats:
            family = family_of_format(fmt, None)
            if family and family not in seen:
                seen.add(family)
                competitor_urls[family].append(record.url)

    landing_tiles: dict[VisualFamily, int] = {family: 0 for family in FAMILIES}
    for group in style.groups if style else []:
        for tile in group.tiles:
            family = family_of_format(tile.format, None) or classify_medium_text(tile.medium)
            if family:
                landing_tiles[family] += 1

    tested = families_tested(list(previous))
    families = [
        FamilyEvidence(
            family=family,
            ad_share=round(ad_weight[family] / total, 3),
            ad_creative_ids=ad_ids[family],
            competitor_urls=competitor_urls[family],
            landing_tiles=landing_tiles[family],
            tested=tested[family],
        )
        for family in FAMILIES
    ]
    has_traffic = any((item.spend or 0) > 0 or (item.clicks or 0) > 0 for item in ranked)
    ads_validate = [
        f.family
        for f in families
        if has_traffic and f.ad_share >= SHARE_THRESHOLD and f.landing_tiles == 0
    ]
    competitors = [f.family for f in families if f.competitor_urls and f.landing_tiles == 0]
    blocked = [
        f.family for f in families if f.tested and f.tested[0].result == "directionally_worse"
    ]
    untested = [f.family for f in families if not f.tested]

    notes: list[str] = []
    if not has_traffic:
        notes.append(
            "No measured spend or clicks: shares describe creative counts, not performance. "
            "Use these selected creatives as design direction, not validated traffic evidence."
        )
    elif any(item.spend is None and item.clicks is None for item in ranked):
        notes.append(
            "Uploaded creatives have no measured traffic and add no weight to performance shares; "
            "their creative IDs and analyses still inform the page's design."
        )
    screenshot_share = (
        sum(
            weights.get(item.creative_id, 0.0)
            for item in ranked
            if (by_id.get(item.creative_id) and by_id[item.creative_id].format == "screenshot")
        )
        / total
    )
    if screenshot_share >= SHARE_THRESHOLD:
        notes.append(
            f"screenshot ads carry {screenshot_share:.0%} of the weight: the device-screen "
            "medium shows only finished artwork on a real device, never drawn app UI; carry "
            "the screenshot promise with a real product clip in heroVideo when it matters."
        )
    if unknown_weight > 0:
        notes.append(
            f"{unknown_weight / total:.0%} of the ad weight has no format read yet "
            "(analysis older than schema v3 or title/body fallback)."
        )
    if style is None or style.error:
        notes.append("no showcase style read: landing tile counts are zero, not measured.")
    if stale_research:
        notes.append(
            f"{len(stale_research)} competitor read(s) predate imagery formats and count for "
            "nothing here; call research_landing on them again to refresh: "
            + ", ".join(stale_research[:3])
        )
    mediums = [
        MediumCard(
            medium=name,
            family=item.family,
            people=item.people,
            allows_text=item.allows_text,
            one_line=item.one_line,
        )
        for name, item in MEDIUM_STYLES.items()
    ]
    return VisualLandscape(
        families=families,
        ads_validate_landing_lacks=ads_validate,
        competitors_use_we_lack=competitors,
        blocked_without_new_evidence=blocked,
        untested=untested,
        mediums=mediums,
        notes=notes,
    )


def load_cached_research(settings: Settings) -> list[CachedResearch]:
    """Every competitor read on disk with a usable result (showcase-* files are style reads)."""
    out: list[CachedResearch] = []
    if not settings.research_dir.is_dir():
        return out
    for path in sorted(settings.research_dir.glob("*.json")):
        if path.name.startswith("showcase-"):
            continue
        try:
            cached = CachedResearch.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - skip files this version cannot read
            continue
        if cached.read is not None:
            out.append(cached)
    return out


def landscape_dict(landscape: VisualLandscape) -> dict[str, Any]:
    return json.loads(landscape.model_dump_json(by_alias=True))


__all__ = [
    "FAMILIES",
    "FamilyEvidence",
    "MediumCard",
    "TestedFamily",
    "VisualLandscape",
    "classify_medium_text",
    "family_of_format",
    "landscape_dict",
    "load_cached_research",
    "families_tested",
    "visual_landscape",
]
