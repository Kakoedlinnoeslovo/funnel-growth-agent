"""Pydantic contracts for propose, memory, creatives, and apply."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

CAMEL = ConfigDict(extra="forbid", populate_by_name=True, ser_json_by_alias=True)

Status = Literal["proposed", "applied", "rejected", "deployed", "measuring", "evaluated"]
PRIMARY_METRIC = Literal["landing_cta_rate"]
ExperimentType = Literal["hero_copy", "composition", "hero_layout"]
EvaluateResult = Literal[
    "directionally_better",
    "directionally_worse",
    "flat",
    "insufficient_data",
]


class SectionCopy(BaseModel):
    model_config = CAMEL

    headline: list[str] | str | None = None
    subhead: str | None = None
    cta_label: str | None = Field(default=None, alias="ctaLabel")
    reassurance: str | None = None


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


class CompositionChanges(BaseModel):
    model_config = CAMEL

    order: list[str] | None = None
    omit: list[str] | None = None


class HeroLayoutChanges(BaseModel):
    model_config = CAMEL

    layout: Literal["copy-first", "video-first"] | None = None
    sticky_cta: bool | None = Field(default=None, alias="stickyCta")


class LandingProposal(BaseModel):
    """Claude output when it proposes a hero_copy experiment."""

    model_config = CAMEL

    decision: Literal["experiment"] = "experiment"
    experiment_type: Literal["hero_copy"] = Field(alias="experimentType")
    problem: str
    evidence: list[str]
    hypothesis: str
    primary_metric: PRIMARY_METRIC = Field(alias="primaryMetric")
    changes: HeroCopyChanges
    other_ideas: list[str] = Field(default_factory=list, alias="otherIdeas")


class NoExperiment(BaseModel):
    model_config = CAMEL

    decision: Literal["no_experiment"] = "no_experiment"
    reason: str


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
    creative_evidence: list[CreativeEvidence] = Field(default_factory=list, alias="creativeEvidence")
    decision: Literal["experiment", "no_experiment"]
    experiment_type: Literal["hero_copy"] | None = Field(default=None, alias="experimentType")
    problem: str | None = None
    evidence: list[str] = Field(default_factory=list)
    hypothesis: str | None = None
    primary_metric: PRIMARY_METRIC | None = Field(default=None, alias="primaryMetric")
    changes: HeroCopyChanges | None = None
    other_ideas: list[str] = Field(default_factory=list, alias="otherIdeas")
    reason: str | None = None


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
