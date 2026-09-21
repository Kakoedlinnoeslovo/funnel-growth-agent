"""Pydantic contracts for propose, memory, creatives, and apply."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

CAMEL = ConfigDict(extra="forbid", populate_by_name=True, ser_json_by_alias=True)

Status = Literal["proposed", "applied", "rejected", "deployed", "measuring", "evaluated"]
PRIMARY_METRIC = Literal["landing_cta_rate"]
ExperimentType = Literal["hero_copy", "landing_redesign"]
HeroLayout = Literal["copy-first", "video-first"]
VideoAspect = Literal["16:9", "4:5", "1:1"]
ImageAspect = Literal["16:9", "4:3", "1:1"]
GlamRoute = Literal["nano_banana_2_t2i__falai", "nano_banana_pro_t2i__falai"]

# Composition rules the apply allowlist enforces; listed here so prompt, models and apply agree.
MOVABLE_COMPONENTS = frozenset(
    {
        "logo-strip",
        "showcase",
        "inline-cta",
        "style-switcher",
        "press-quotes",
        "video-cta",
        "plan-preview",
    }
)
OMITTABLE_COMPONENTS = frozenset(
    {"logo-strip", "showcase", "style-switcher", "inline-cta", "video-cta"}
)
MAX_OMIT = 2
YOUTUBE_ID_PATTERN = r"^[A-Za-z0-9_-]{11}$"
CREATIVE_ID_PATTERN = r"^[A-Za-z0-9_-]{1,40}$"
SECTION_ID_PATTERN = r"^[a-z][a-z0-9-]*$"
EvaluateResult = Literal[
    "directionally_better",
    "directionally_worse",
    "flat",
    "insufficient_data",
]


def _reject_empty_text(value: Any) -> Any:
    if value == "" or value == []:
        raise ValueError("copy fields must not be empty")
    if isinstance(value, list) and any(item == "" for item in value):
        raise ValueError("headline lines must not be empty")
    return value


class SectionCopy(BaseModel):
    model_config = CAMEL

    headline: list[str] | str | None = None
    subhead: str | None = None
    cta_label: str | None = Field(default=None, alias="ctaLabel")
    reassurance: str | None = None

    @field_validator("headline", "subhead", "cta_label", "reassurance", mode="before")
    @classmethod
    def _reject_empty(cls, value: Any) -> Any:
        return _reject_empty_text(value)

    def is_empty(self) -> bool:
        return not self.model_dump(exclude_none=True)


class HeroCopyChanges(BaseModel):
    """MVP 0 apply surface: hero + video-cta + final-cta text only."""

    model_config = CAMEL

    headline: list[str] | str | None = None
    subhead: str | None = None
    cta_label: str | None = Field(default=None, alias="ctaLabel")
    reassurance: str | None = None
    video_cta: SectionCopy | None = Field(default=None, alias="videoCta")
    final_cta: SectionCopy | None = Field(default=None, alias="finalCta")

    @model_validator(mode="before")
    @classmethod
    def _flatten_nested_sections(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        for nested_key, dest in (
            ("hero", None),
            ("video-cta", "videoCta"),
            ("final-cta", "finalCta"),
        ):
            nested = data.pop(nested_key, None)
            if nested is None:
                continue
            if dest is None:
                if not isinstance(nested, dict):
                    raise ValueError("hero must be an object with copy fields")
                for key, item in nested.items():
                    data.setdefault(key, item)
            elif dest not in data:
                data[dest] = nested
        return data

    @field_validator("headline", "subhead", "cta_label", "reassurance", mode="before")
    @classmethod
    def _reject_empty(cls, value: Any) -> Any:
        if value == "":
            raise ValueError("copy fields must not be empty")
        return value


class InlineCtaCopy(BaseModel):
    """inline-cta sections carry a string headline and a button label only."""

    model_config = CAMEL

    headline: str | None = None
    cta_label: str | None = Field(default=None, alias="ctaLabel")

    @field_validator("headline", "cta_label", mode="before")
    @classmethod
    def _reject_empty(cls, value: Any) -> Any:
        return _reject_empty_text(value)

    def is_empty(self) -> bool:
        return not self.model_dump(exclude_none=True)


class CopyChanges(BaseModel):
    """Copy per section. `inlineCta` applies to every inline-cta section on the page."""

    model_config = CAMEL

    hero: SectionCopy | None = None
    video_cta: SectionCopy | None = Field(default=None, alias="videoCta")
    final_cta: SectionCopy | None = Field(default=None, alias="finalCta")
    inline_cta: InlineCtaCopy | None = Field(default=None, alias="inlineCta")

    @model_validator(mode="before")
    @classmethod
    def _accept_kebab_keys(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        pairs = (("video-cta", "videoCta"), ("final-cta", "finalCta"), ("inline-cta", "inlineCta"))
        for kebab, camel in pairs:
            if kebab in data and camel not in data:
                data[camel] = data.pop(kebab)
        return data

    @model_validator(mode="after")
    def _headline_shapes(self) -> CopyChanges:
        # The lab schema: hero and video-cta headlines are lines, final-cta is one string.
        for section in (self.hero, self.video_cta):
            if section is not None and isinstance(section.headline, str):
                section.headline = [section.headline]
        if self.final_cta is not None and isinstance(self.final_cta.headline, list):
            self.final_cta.headline = " ".join(self.final_cta.headline)
        return self

    def is_empty(self) -> bool:
        parts = (self.hero, self.video_cta, self.final_cta, self.inline_cta)
        return all(part is None or part.is_empty() for part in parts)


class HeroLayoutChanges(BaseModel):
    model_config = CAMEL

    layout: HeroLayout | None = None
    sticky_cta: bool | None = Field(default=None, alias="stickyCta")

    def is_empty(self) -> bool:
        return self.layout is None and self.sticky_cta is None


class CompositionChanges(BaseModel):
    """Section order after the hero, and sections to drop. Ids come from get_current_landing."""

    model_config = CAMEL

    order: list[str] | None = None
    omit: list[str] | None = None

    @field_validator("order", "omit")
    @classmethod
    def _valid_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        for item in value:
            if not re.match(SECTION_ID_PATTERN, item):
                raise ValueError(f"invalid section id {item!r}")
        if len(set(value)) != len(value):
            raise ValueError("section ids repeat")
        if "kittl-hero" in value:
            raise ValueError("kittl-hero always stays first and cannot be moved or omitted")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> CompositionChanges:
        if self.omit and len(self.omit) > MAX_OMIT:
            raise ValueError(f"at most {MAX_OMIT} sections may be omitted")
        overlap = set(self.order or []) & set(self.omit or [])
        if overlap:
            raise ValueError(f"sections both ordered and omitted: {sorted(overlap)}")
        return self

    def is_empty(self) -> bool:
        return not self.order and not self.omit


class HeroVideoPlan(BaseModel):
    """Which ready clip becomes the hero video. Files are produced at apply time."""

    model_config = CAMEL

    source: Literal["youtube", "creative"]
    video_id: str | None = Field(default=None, alias="videoId", pattern=YOUTUBE_ID_PATTERN)
    creative_id: str | None = Field(default=None, alias="creativeId", pattern=CREATIVE_ID_PATTERN)
    start: float = Field(default=0, ge=0)
    duration: float = Field(default=12, ge=4, le=30)
    label: str = Field(min_length=8, max_length=200)
    aspect: VideoAspect = "16:9"

    @model_validator(mode="after")
    def _source_id(self) -> HeroVideoPlan:
        if self.source == "youtube" and not self.video_id:
            raise ValueError("youtube source needs videoId")
        if self.source == "creative" and not self.creative_id:
            raise ValueError("creative source needs creativeId")
        return self

    @property
    def source_id(self) -> str:
        return str(self.video_id if self.source == "youtube" else self.creative_id)

    @property
    def stem(self) -> str:
        key = self.source_id.lower().replace("_", "-")
        start = int(round(self.start * 10))
        duration = int(round(self.duration * 10))
        return f"hero-{self.source}-{key}-{start}-{duration}"


TileMedium = Literal["flat-vector", "lettering", "photo", "illustration", "3d-icons", "mockup"]
TileModel = Literal["nano_banana_pro", "nano_banana_2"]
ROUTE_TO_MODEL = {
    "nano_banana_2_t2i__falai": "nano_banana_2",
    "nano_banana_pro_t2i__falai": "nano_banana_pro",
}
REFERENCE_PATTERN = re.compile(r"^(creative:[A-Za-z0-9_-]{1,40}|tile:[^:]{1,80}:[0-3]|previous)$")
COLOUR_PATTERN = re.compile(r"^(#[0-9a-fA-F]{6}|[A-Za-z][A-Za-z -]{1,30})$")
LETTERING_TEXT_RE = re.compile(r'"([^"]{1,40})"')
MAX_STYLE_REFS = 3


class ShowcaseImagePlan(BaseModel):
    """One generated tile: replaces images[slot] of the showcase group with that label.

    Claude writes a brief plus medium, palette, background and references; the house-style
    prompt builder turns that into the image prompt. `prompt` is a raw override kept for
    old rows."""

    model_config = CAMEL

    group: str = Field(min_length=1, max_length=80)
    slot: int = Field(ge=0, le=3)
    brief: str | None = Field(default=None, min_length=20, max_length=400)
    medium: TileMedium | None = None
    palette: list[str] = Field(default_factory=list, max_length=3)
    background: str | None = Field(default=None, max_length=120)
    references: list[str] = Field(default_factory=list, max_length=MAX_STYLE_REFS)
    tile_model: TileModel = Field(default="nano_banana_pro", alias="model")
    prompt: str | None = Field(default=None, min_length=20, max_length=1200)

    @model_validator(mode="before")
    @classmethod
    def _legacy_keys(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        data.pop("aspectRatio", None)
        data.pop("aspect_ratio", None)
        route = data.pop("route", None)
        if route is not None and "model" not in data and "tile_model" not in data:
            if route not in ROUTE_TO_MODEL:
                raise ValueError(f"unknown route {route!r}")
            data["model"] = ROUTE_TO_MODEL[route]
        return data

    @field_validator("palette")
    @classmethod
    def _colours(cls, value: list[str]) -> list[str]:
        for item in value:
            if not COLOUR_PATTERN.match(item.strip()):
                raise ValueError(f"palette entry {item!r} is not a hex colour or a colour name")
        return [item.strip() for item in value]

    @field_validator("references")
    @classmethod
    def _reference_ids(cls, value: list[str]) -> list[str]:
        for item in value:
            if not REFERENCE_PATTERN.match(item):
                raise ValueError(
                    f"reference {item!r} must be creative:<id>, tile:<group label>:<0-3> or previous"
                )
        if len(set(value)) != len(value):
            raise ValueError("references repeat")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> ShowcaseImagePlan:
        if (self.brief is None) == (self.prompt is None):
            raise ValueError("a showcase tile needs exactly one of brief or prompt")
        if self.brief is not None:
            if self.medium is None:
                raise ValueError("a brief needs a medium")
            if not 2 <= len(self.palette) <= 3:
                raise ValueError("a brief needs a palette of 2 or 3 colours")
            if self.medium == "lettering" and self.lettering_text is None:
                raise ValueError('a lettering brief must put the exact words in "double quotes"')
        if self.references and self.tile_model == "nano_banana_2":
            raise ValueError("nano_banana_2 takes no references; use nano_banana_pro")
        return self

    @property
    def lettering_text(self) -> str | None:
        if self.brief is None:
            return None
        match = LETTERING_TEXT_RE.search(self.brief)
        return match.group(1) if match else None


class MediaPlan(BaseModel):
    model_config = CAMEL

    hero_video: HeroVideoPlan | None = Field(default=None, alias="heroVideo")
    showcase: list[ShowcaseImagePlan] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _unique_slots(self) -> MediaPlan:
        seen = {(item.group, item.slot) for item in self.showcase}
        if len(seen) != len(self.showcase):
            raise ValueError("showcase plans repeat a (group, slot)")
        slots_by_group: dict[str, list[int]] = {}
        for item in self.showcase:
            slots_by_group.setdefault(item.group, []).append(item.slot)
        for item in self.showcase:
            if "previous" in item.references and not any(
                slot < item.slot for slot in slots_by_group[item.group]
            ):
                raise ValueError(
                    f"showcase {item.group!r} slot {item.slot}: 'previous' needs an earlier "
                    "slot of the same group in this plan"
                )
        return self

    def is_empty(self) -> bool:
        return self.hero_video is None and not self.showcase


class RedesignChanges(BaseModel):
    """One hypothesis expressed across copy, hero layout, section order, and media."""

    model_config = CAMEL

    # `copy` would shadow BaseModel.copy, so the attribute is copy_changes; the JSON key stays `copy`.
    copy_changes: CopyChanges | None = Field(default=None, alias="copy")
    layout: HeroLayoutChanges | None = None
    composition: CompositionChanges | None = None
    media: MediaPlan | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> RedesignChanges:
        groups = (self.copy_changes, self.layout, self.composition, self.media)
        if all(group is None or group.is_empty() for group in groups):
            raise ValueError("landing_redesign changes are empty")
        return self


Changes = HeroCopyChanges | RedesignChanges


def coerce_changes(experiment_type: Any, raw: Any) -> Any:
    """Pick the changes model from experimentType; both models accept `{}` so no smart union."""
    if raw is None:
        return None
    if experiment_type == "landing_redesign":
        return raw if isinstance(raw, RedesignChanges) else RedesignChanges.model_validate(raw)
    if experiment_type == "hero_copy":
        return raw if isinstance(raw, HeroCopyChanges) else HeroCopyChanges.model_validate(raw)
    return raw


def _pick_changes(value: Any) -> Any:
    if isinstance(value, dict) and value.get("changes") is not None:
        data = dict(value)
        kind = data.get("experimentType", data.get("experiment_type"))
        data["changes"] = coerce_changes(kind, data["changes"])
        return data
    return value


class LandingProposal(BaseModel):
    """Claude output when it proposes an experiment."""

    model_config = CAMEL

    decision: Literal["experiment"] = "experiment"
    experiment_type: ExperimentType = Field(alias="experimentType")
    problem: str
    evidence: list[str]
    hypothesis: str
    primary_metric: PRIMARY_METRIC = Field(alias="primaryMetric")
    changes: Changes
    other_ideas: list[str] = Field(default_factory=list, alias="otherIdeas")

    @model_validator(mode="before")
    @classmethod
    def _changes_by_type(cls, value: Any) -> Any:
        return _pick_changes(value)


class NoExperiment(BaseModel):
    model_config = CAMEL

    decision: Literal["no_experiment"] = "no_experiment"
    reason: str
    # The model often still lists what it would try next; keep it instead of failing.
    other_ideas: list[str] = Field(default_factory=list, alias="otherIdeas")


ModelOutput = Annotated[LandingProposal | NoExperiment, Field(discriminator="decision")]
_output_adapter: TypeAdapter[LandingProposal | NoExperiment] = TypeAdapter(ModelOutput)


def parse_model_output(data: Any) -> LandingProposal | NoExperiment:
    return _output_adapter.validate_python(data)


class Baseline(BaseModel):
    model_config = CAMEL

    source: str = "growth-loop"
    funnel_id: str = Field(alias="funnelId")
    version: str
    funnel_version: str | None = Field(default=None, alias="funnelVersion")
    is_proxy: bool = Field(alias="isProxy")
    period: str | None = None
    landing_people: int = Field(alias="landingPeople")
    cta_people: int = Field(alias="ctaPeople")
    cta_rate: float = Field(alias="ctaRate")


class CreativeEvidence(BaseModel):
    model_config = CAMEL

    creative_id: str = Field(alias="creativeId")
    reason_selected: str = Field(alias="reasonSelected")
    primary_promise: str | None = Field(default=None, alias="primaryPromise")
    cta_intent: str | None = Field(default=None, alias="ctaIntent")


class RankedCreative(BaseModel):
    model_config = CAMEL

    creative_id: str = Field(alias="creativeId")
    ad_name: str = Field(default="", alias="adName")
    title: str = ""
    body: str = ""
    link_url: str | None = Field(default=None, alias="linkUrl")
    spend: float = 0
    impressions: int = 0
    clicks: int = 0
    ctr: float | None = None
    cpc: float | None = None
    checkouts: int = 0
    payments: int = 0
    image_path: str | None = Field(default=None, alias="imagePath")
    video_path: str | None = Field(default=None, alias="videoPath")
    reason_selected: str = Field(alias="reasonSelected")


class CreativeAnalysis(BaseModel):
    model_config = CAMEL

    visual_hook: str = Field(alias="visualHook")
    primary_promise: str = Field(alias="primaryPromise")
    audience_intent: str = Field(alias="audienceIntent")
    cta_intent: str = Field(alias="ctaIntent")
    visible_text: list[str] = Field(default_factory=list, alias="visibleText")
    product_claims: list[str] = Field(default_factory=list, alias="productClaims")
    suggested_landing_theme: str = Field(alias="suggestedLandingTheme")
    # Schema v2: what a designer could lift from the ad into a landing tile.
    palette: list[str] = Field(default_factory=list)
    visual_elements: list[str] = Field(default_factory=list, alias="visualElements")
    composition: str | None = None
    medium: str | None = None

    @field_validator("visible_text", "product_claims", "palette", "visual_elements", mode="before")
    @classmethod
    def _lines_to_list(cls, value: Any) -> Any:
        # Gemini sometimes returns one newline-joined string instead of a list.
        if isinstance(value, str):
            return [line.strip() for line in value.splitlines() if line.strip()]
        return value


class CachedCreativeAnalysis(BaseModel):
    model_config = CAMEL

    creative_id: str = Field(alias="creativeId")
    asset_fingerprint: str = Field(alias="assetFingerprint")
    analyzed_at: str = Field(alias="analyzedAt")
    model: str
    schema_version: int
    analysis: CreativeAnalysis


class MetricsSlice(BaseModel):
    model_config = CAMEL

    generated_at: str = Field(alias="generatedAt")
    age_hours: float = Field(alias="ageHours")
    fresh: bool
    report_kind: str = Field(alias="reportKind")
    report_path: str = Field(alias="reportPath")
    baseline: Baseline
    daily: Baseline | None = None


class Evaluation(BaseModel):
    model_config = CAMEL

    variant: str
    landing_people: int = Field(alias="landingPeople")
    cta_people: int = Field(alias="ctaPeople")
    cta_rate: float | None = Field(default=None, alias="ctaRate")
    baseline_proxy_rate: float | None = Field(default=None, alias="baselineProxyRate")
    causal: Literal[False] = False
    result: EvaluateResult


class PreviousRun(BaseModel):
    model_config = CAMEL

    run_id: str = Field(alias="runId")
    experiment_type: str | None = Field(default=None, alias="experimentType")
    hypothesis: str | None = None
    changes: dict[str, Any] | None = None
    result: str | None = None
    evaluation: Evaluation | None = None
    learning: str | None = None
    status: Status


class SavedProposal(BaseModel):
    model_config = CAMEL

    run_id: str = Field(alias="runId")
    base_version: str = Field(alias="baseVersion")
    base_landing_hash: str = Field(alias="baseLandingHash")
    baseline: Baseline
    creative_evidence: list[CreativeEvidence] = Field(
        default_factory=list, alias="creativeEvidence"
    )
    decision: Literal["experiment", "no_experiment"]
    experiment_type: ExperimentType | None = Field(default=None, alias="experimentType")
    problem: str | None = None
    evidence: list[str] = Field(default_factory=list)
    hypothesis: str | None = None
    primary_metric: PRIMARY_METRIC | None = Field(default=None, alias="primaryMetric")
    changes: Changes | None = None
    other_ideas: list[str] = Field(default_factory=list, alias="otherIdeas")
    reason: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _changes_by_type(cls, value: Any) -> Any:
        return _pick_changes(value)


LENIENT = ConfigDict(extra="ignore", populate_by_name=True, ser_json_by_alias=True)


class TileStyleRead(BaseModel):
    """Gemini's read of one real showcase tile, keyed by the id Claude can reference."""

    model_config = LENIENT

    reference_id: str = Field(default="", alias="referenceId")
    subject: str = ""
    medium: str | None = None
    palette: list[str] = Field(default_factory=list)
    background: str | None = None
    composition: str | None = None
    has_text: bool = Field(default=False, alias="hasText")


class GroupStyle(BaseModel):
    model_config = LENIENT

    label: str
    caption: str | None = None
    family: str | None = None
    tiles: list[TileStyleRead] = Field(default_factory=list)


class CachedShowcaseStyle(BaseModel):
    model_config = LENIENT

    base_version: str = Field(alias="baseVersion")
    landing_hash: str = Field(alias="landingHash")
    read_at: str = Field(alias="readAt")
    model: str | None = None
    groups: list[GroupStyle] = Field(default_factory=list)
    error: str | None = None


class CandidateScore(BaseModel):
    model_config = LENIENT

    index: int
    house_style: int = Field(default=0, ge=0, le=10, alias="houseStyle")
    subject_clarity: int = Field(default=0, ge=0, le=10, alias="subjectClarity")
    cleanliness: int = Field(default=0, ge=0, le=10)
    crop_safety: int = Field(default=0, ge=0, le=10, alias="cropSafety")
    palette_adherence: int = Field(default=0, ge=0, le=10, alias="paletteAdherence")
    notes: str = ""
    total: float = 0.0


class JudgeResult(BaseModel):
    model_config = LENIENT

    stem: str
    model: str | None = None
    scores: list[CandidateScore] = Field(default_factory=list)
    chosen: int = 0
    reason: str = ""
    fallback: bool = False


class MemoryRow(BaseModel):
    model_config = CAMEL

    run_id: str = Field(alias="runId")
    created_at: str = Field(alias="createdAt")
    status: Status
    base: str
    variant: str | None = None
    proposal: dict[str, Any]
    deployed_at: str | None = Field(default=None, alias="deployedAt")
    evaluation: Evaluation | None = None
    learning: str | None = None
