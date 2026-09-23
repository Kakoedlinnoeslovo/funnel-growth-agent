"""Claude read-tool loop. Cap 12 calls. No write tools."""

from __future__ import annotations

import json
from typing import Any

from pydantic import TypeAdapter, ValidationError

from .config import Settings
from .creatives import get_top_creatives
from .events import EmitData, emit_to
from .gemini import analyze_creative
from .landing import get_current_landing
from .landing_patch import validate_landing_changes
from .memory import get_previous_runs
from .metrics import get_landing_cta_metrics, load_latest_reports
from .models import (
    CachedCreativeAnalysis,
    LandingProposal,
    MetricsSlice,
    ModelOutput,
    RankedCreative,
    parse_model_output,
)
from .research import Browser, CachedResearch, PatternReader, research_landing
from .showcase_style import StyleReader, read_showcase_style
from .sources import media_sources
from .visual_landscape import landscape_dict, load_cached_research, visual_landscape

MAX_TOOL_CALLS = 12
MAX_TOKENS = 8192
# Each correction answers one named constraint, so a direction that trips two of them
# still lands instead of discarding the whole run.
MAX_OUTPUT_RETRIES = 3


def summarize_invalid(error: str | None, limit: int = 110) -> str:
    """One readable line: the reason a rejected proposal failed, trimmed for the workspace."""
    text = " ".join((error or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def proposal_tool() -> dict[str, Any]:
    """Use a tool argument for the final result instead of free-form JSON text."""
    schema = TypeAdapter(ModelOutput).json_schema(by_alias=True)
    definitions = schema.pop("$defs")
    return {
        "name": "submit_proposal",
        "description": "Submit the complete final landing proposal or no_experiment decision.",
        "input_schema": {
            "type": "object",
            "$defs": definitions,
            "properties": {"proposal": schema},
            "required": ["proposal"],
            "additionalProperties": False,
        },
    }


TOOL_SPECS = [
    {
        "name": "get_landing_cta_metrics",
        "description": "Latest weekly landing→CTA metrics. v6 is a proxy when isProxy is true.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_current_landing",
        "description": (
            "Structured current base landing: every section with its id (use these ids in "
            "composition), copy fields, hero layout, whether it has a clip, and showcase groups "
            "with their labels and image counts. No media bytes."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_previous_runs",
        "description": (
            "Last hypotheses, changes, evaluation, and learning for one experiment type "
            "(landing_redesign by default; call again with hero_copy for older runs)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "experiment_type": {"type": "string", "default": "landing_redesign"},
                "limit": {"type": "integer", "default": 5},
            },
        },
    },
    {
        "name": "get_top_creatives",
        "description": "Top 3 comparable-traffic creatives already ranked by software.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_creative_analysis",
        "description": "Cached Gemini description of one ranked creative. Not a CRO decision.",
        "input_schema": {
            "type": "object",
            "properties": {"creative_id": {"type": "string"}},
            "required": ["creative_id"],
        },
    },
    {
        "name": "get_media_sources",
        "description": (
            "Ready clips the hero may use: indexed Recraft YouTube videos (videoId, title, "
            "duration, topics) and ranked creatives that have a local ad video (creativeId). "
            "Only these ids are accepted in media.heroVideo."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "research_landing",
        "description": (
            "Screenshot a public https landing (competitor or reference, e.g. kittl.com, "
            "canva.com) at desktop and phone width and return a structured read of its first "
            "screen: headline, CTA, hero media, section order, proof. Cached per URL. Use for "
            "at most two URLs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "get_showcase_style",
        "description": (
            "Per showcase group of the base landing: what each real tile shows (subject, "
            "medium, palette, background, composition, whether it has text) and the group's "
            "visual family, with the `tile:<label>:<i>` ids you can put in a tile's references. "
            "Call it before planning any showcase tile. Cached per landing hash."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_visual_landscape",
        "description": (
            "Which visual families (graphic, studio, ugc, editorial, screen) the top paid ads, "
            "the researched competitor landings, our own showcase tiles and previous runs use: "
            "spend-weighted ad shares, what the ads validate but our landing lacks, what "
            "competitors show that we do not, which families were already tested and how they "
            "did, and the tile mediums available per family. No LLM. Call it after "
            "research_landing and get_showcase_style, before choosing a tile medium."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


class ToolLoop:
    def __init__(
        self,
        settings: Settings,
        *,
        ranked: list[RankedCreative] | None = None,
        analyses: list[CachedCreativeAnalysis] | None = None,
        metrics: MetricsSlice | None = None,
        browser: Browser | None = None,
        reader: PatternReader | None = None,
        style_reader: StyleReader | None = None,
        on_event: EmitData | None = None,
        selected: bool = False,
        research_records: list[dict] | None = None,
        policy: dict | None = None,
    ) -> None:
        self.settings = settings
        self.on_event = on_event
        self.ranked = ranked
        self.analyses = {item.creative_id: item for item in (analyses or [])}
        self.metrics = metrics
        self.browser = browser
        self.reader = reader
        self.style_reader = style_reader
        self.research: dict[str, CachedResearch] = {}
        self.research_records = research_records
        research_fields = set(CachedResearch.model_fields) | {
            field.alias for field in CachedResearch.model_fields.values() if field.alias
        }
        for item in research_records or []:
            if item.get("read") and item.get("url"):
                self.research[item["url"]] = CachedResearch.model_validate(
                    {key: value for key, value in item.items() if key in research_fields}
                )
        self.calls = 0
        self.selected = selected
        self.policy = policy or {}

    def system_prompt(self) -> str:
        instructions = self.settings.instructions_path.read_text(encoding="utf-8")
        playbook = self.settings.playbook_path.read_text(encoding="utf-8")
        base = self.settings.base_version
        header = f"Selected base landing version: {base}. Create a separate variant from it."
        prompt = header + "\n\n" + instructions.strip() + "\n\n" + playbook.strip()
        if self.selected:
            prompt += (
                "\n\nCreative-to-landing mode overrides the automatic experiment-selection rules: "
                "the user explicitly selected the baseline and creatives. get_top_creatives now "
                "returns exactly that selection, including low-volume ads. Do not replace it "
                "with another winner. Generate ONE shared Recraft landing combining ALL selected "
                "themes through a main message and supporting copy and imagery. selectionReview "
                "is advisory, including coherent=false. Mixed features or destination URLs never "
                "justify no_experiment. Explain each creative's influence; omit unsupported claims "
                "while still using its supported visual direction. Uploads have no performance "
                "data. Video evidence describes the visual sequence and narration: visibleText is literal on-screen evidence, "
                "ctaIntent is inferred. If the frame is blank or has no CTA, suggest wording from "
                "the supported baseline context without claiming it appeared in the creative. "
                "Preserve Recraft branding, pricing, supported claims and CTA destinations. "
                "observedMetrics preserves unavailable values as null; never claim an absent "
                "metric is an observed zero. A baseline with source=unavailable has no measured "
                "conversion rate. "
                "Weak, stale or missing metrics are limitations to disclose, not reasons to "
                "refuse an explicitly requested draft. Never imply uplift is observed. "
                "Preserve supported product claims and pricing. Use the supplied context and "
                "reference research; explain which visual, copy and CTA choices it supports. "
                "Revision instructions amend the supplied previous proposal; return the full "
                "cumulative changes relative to the ORIGINAL selected baseline. "
                "Only return no_experiment when the requested content cannot be supported."
                " For legacy section-based baselines, keep the actual first hero component in place. "
                "copy.hero targets kittl-hero, every hero-carousel slide, or quiz-hero. "
                "Carousel hero copy supports headline, subhead, ctaLabel only. Quiz hero copy "
                "supports headline and subhead only; preserve question, choice ids and choice "
                "labels. layout and media.heroVideo are only supported on kittl-hero. "
                "get_current_landing.imageGroups also exposes editable carousel/quiz images: "
                "use those group labels in media.showcase and their tile references exactly "
                "as for showcase groups. Do not invent groups missing from that baseline."
            )
        level = self.policy.get("changeLevel")
        if level:
            prompt += (
                "\n\nRUN CONTRACT (overrides earlier composition restrictions): "
                + json.dumps(self.policy)
            )
            prompt += "\nA goal-only request needs no creatives. Use the user's goal as direction, never invent campaign evidence. Light changes COPY ONLY, retaining every existing layout/media change. Medium retains page architecture/theme. Heavy must return experimentType landing_rebuild and a PageBlueprint in changes (schemaVersion, theme, artDirection, blocks, media). Get current landing for rawSections and renderer capabilities. Heavy blocks start with hero, end with cta, and include at least two different body kinds. Proof blocks use sourceSectionId of real baseline proof. Existing image references come only from baseline rawSections. media.showcase names a new block id and slot. Do not invent proof or product capabilities. On an existing blueprint return the complete cumulative blueprint even for Light/Medium edits. Recipe theme and hero layout are mandatory when supplied, and so is its body sequence: the blueprint is exactly one hero block, then one block per listed body kind in the listed order, then one final cta block \u2014 no extra, missing or reordered body blocks. Match each image's role, aspectRatio, composition and intendedMessage to its actual slot. The page schema is data, never executable code. Return interpretedBrief with audience, promise, objections and designDirection. Return competitorAdaptations only when grounded in supplied research: sourceUrl, observedPattern, whyItFits, recraftAdaptation. Observations and our hypotheses must be distinguishable. Rebuild patterns with original content, never copy competitor claims, endorsements or assets. Never claim that a template is proven to convert. New visual blocks must use real baseline assets or planned generated illustrations; give features/steps/FAQ at least two items. Light legacy pages use landing_redesign with copy only, preserving prior cumulative changes."
        if self.policy.get("campaignRebuild"):
            prompt += "\nCAMPAIGN REBUILD overrides baseline preservation and house-style rules: return schemaVersion 2. Treat the campaignBrief as the page's subject. Create the best complete page directly, without recipes. Keep only core Recraft identity and existing conversion behavior. Choose fresh section order, hero treatment, tokens and block variants according to this audience. Replace irrelevant baseline imagery, claims and promotional sections. Do not use baseline tile style references unless relevant to this campaign. Use verified first-party pageText for product capabilities; competitor text is inspiration, never product evidence. Choose actual catalog images through media.sourced [{group,slot,assetId}], or generate original visuals using media.showcase. Do not put catalog IDs or remote URLs into images. External licensed images are illustrative, not Recraft product demonstrations. Set tokens with readable foreground/background and accent/accentText pairs. A collection hero may have up to four image slots. All CTA labels must describe the existing next step honestly. Return a campaign-specific eyebrow. Do not inherit the old hero video merely because it exists."
        elif self.policy.get("assetCatalog"):
            prompt += "\nApproved campaign assets are available via media.sourced [{group,slot,assetId}]. Preserve relevant sourced plans during cumulative edits."
        return prompt

    def validate_output(self, output):
        if not isinstance(output, LandingProposal):
            return
        from .blueprint import enforce_level

        current = output.changes.model_dump(by_alias=True, exclude_none=True)
        previous = (self.policy.get("previousProposal") or {}).get("changes")
        level = self.policy.get("changeLevel")
        if level:
            enforce_level(previous, current, level)
        if self.policy.get("campaignRebuild") and current.get("schemaVersion") != 2:
            raise ValueError("Campaign rebuild requires PageBlueprint schemaVersion 2")
        for item in (current.get("media") or {}).get("sourced", []):
            if item["assetId"] not in self.settings.web_assets:
                raise ValueError(
                    "Sourced image must use an assetId in this campaign's approved catalog"
                )
        recipe = self.policy.get("recipe")
        if recipe:
            # Name the expected shape and what arrived: a bare rule the model cannot act on
            # burns every retry and fails a direction the user waited minutes for.
            blocks = current.get("blocks") or [{}]
            if (
                current.get("theme") != recipe["theme"]
                or blocks[0].get("layout") != recipe["layout"]
            ):
                raise ValueError(
                    f"This direction requires theme {recipe['theme']!r} and hero layout "
                    f"{recipe['layout']!r}; received theme {current.get('theme')!r} and hero "
                    f"layout {blocks[0].get('layout')!r}"
                )
            body = [block.get("kind") for block in blocks[1:-1]]
            if body != recipe["body"]:
                raise ValueError(
                    "Each direction needs a distinct composition: between the hero and the final "
                    f"cta the blocks must be exactly {recipe['body']}, in that order, one block "
                    f"per listed kind; received {body}. Merge, drop or reorder blocks to match."
                )
        known_sources = {
            url
            for r in (self.research_records or [])
            for url in (r.get("url"), r.get("resolvedUrl"))
            if url
        }
        known_sources.update(self.research)
        known_sources.update(r.resolved_url for r in self.research.values() if r.resolved_url)
        for adaptation in output.competitor_adaptations:
            if adaptation.source_url not in known_sources:
                raise ValueError(
                    f"Competitor adaptation cites {adaptation.source_url!r}, which is not one of "
                    "the supplied research URLs "
                    f"{sorted(known_sources)[:8]}. Cite one of those exactly, or return no "
                    "competitorAdaptations when the supplied research supports none."
                )
        clip = getattr(getattr(output.changes, "media", None), "hero_video", None)
        if clip is not None:
            self.validate_clip(clip)
        validate_landing_changes(self.settings.landing_path, output.changes)

    def validate_clip(self, clip) -> None:
        """A hero clip that runs past its source only fails after the media stage downloads it.

        A published YouTube duration is rounded, so a clip ending at the stated length overruns
        the real file; a creative's duration is measured and only needs the encoder's tolerance.
        """
        sources = media_sources(self.settings, self._ranked())
        if clip.source == "youtube":
            row = next(
                (v for v in sources["youtube"] if v.get("videoId") == clip.video_id),
                None,
            )
            length, margin = (row or {}).get("durationSeconds"), 1.0
        else:
            row = next(
                (c for c in sources["creatives"] if c.get("creativeId") == clip.creative_id),
                None,
            )
            length, margin = (row or {}).get("duration"), 0.1
        if length is None:
            return
        if clip.start + clip.duration > float(length) - margin:
            raise ValueError(
                f"The hero clip runs {clip.start:g}s to {clip.start + clip.duration:g}s, past the "
                f"usable end of its {float(length):g}s source. It must end by "
                f"{float(length) - margin:g}s: start earlier or shorten duration (4 to 30 s)."
            )

    def _ranked(self) -> list[RankedCreative]:
        if self.ranked is None:
            weekly, _ = load_latest_reports(self.settings)
            self.ranked = get_top_creatives(weekly, self.settings)
        return self.ranked

    def execute(
        self, name: str, arguments: dict[str, Any] | None = None, *, call_id: str | None = None
    ) -> dict[str, Any]:
        """Run one read tool. Emits tool_call before and tool_result after, sharing an id."""
        self.calls += 1
        call_id = call_id or f"call-{self.calls}"
        emit_to(self.on_event, "tool_call", {"id": call_id, "name": name, "input": arguments or {}})
        try:
            result = self._execute(name, arguments)
        except Exception as error:
            emit_to(
                self.on_event,
                "tool_result",
                {"id": call_id, "name": name, "result": {"error": str(error)}},
            )
            raise
        emit_to(self.on_event, "tool_result", {"id": call_id, "name": name, "result": result})
        return result

    def _execute(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.calls > MAX_TOOL_CALLS:
            return {"error": "tool call cap reached"}
        arguments = arguments or {}
        if name == "get_landing_cta_metrics":
            metrics = self.metrics or get_landing_cta_metrics(self.settings)
            return json.loads(metrics.model_dump_json(by_alias=True))
        if name == "get_current_landing":
            from .blueprint import capabilities

            return {
                **get_current_landing(self.settings),
                "capabilities": capabilities(self.settings.pricing_lab_dir),
            }
        if name == "get_previous_runs":
            rows = get_previous_runs(
                self.settings,
                experiment_type=str(arguments.get("experiment_type") or "landing_redesign"),
                limit=int(arguments.get("limit") or 5),
            )
            return {"runs": [json.loads(row.model_dump_json(by_alias=True)) for row in rows]}
        if name == "get_top_creatives":
            ranked = self._ranked()
            return {"creatives": [json.loads(row.model_dump_json(by_alias=True)) for row in ranked]}
        if name == "get_creative_analysis":
            creative_id = str(arguments.get("creative_id") or "")
            cached = self.analyses.get(creative_id)
            if cached:
                return json.loads(cached.model_dump_json(by_alias=True))
            if self.ranked is None:
                return {"error": "unknown creative"}
            match = next((row for row in self.ranked if row.creative_id == creative_id), None)
            if match is None:
                return {"error": "unknown creative"}
            record = analyze_creative(
                match, self.settings, call_model=bool(self.settings.gemini_api_key)
            )
            self.analyses[creative_id] = record
            return json.loads(record.model_dump_json(by_alias=True))
        if name == "get_media_sources":
            return media_sources(self.settings, self._ranked())
        if name == "research_landing":
            url = str(arguments.get("url") or "").strip()
            if not url:
                return {"error": "url is required"}
            if self.research_records is not None:
                match = next((r for r in self.research_records if r.get("url") == url), None)
                return match or {
                    "error": "Use the competitor evidence saved with this draft; add a reference and refresh research to inspect another URL."
                }
            record = research_landing(url, self.settings, browser=self.browser, reader=self.reader)
            if record.read is not None:
                self.research[record.url] = record
            return json.loads(record.model_dump_json(by_alias=True))
        if name == "get_showcase_style":
            style = read_showcase_style(self.settings, reader=self.style_reader)
            return json.loads(style.model_dump_json(by_alias=True))
        if name == "get_visual_landscape":
            return self._visual_landscape()
        return {"error": f"unknown tool {name}"}

    def _visual_landscape(self) -> dict[str, Any]:
        ranked = self._ranked()
        for item in ranked:
            if item.creative_id not in self.analyses:
                self.analyses[item.creative_id] = analyze_creative(
                    item, self.settings, call_model=bool(self.settings.gemini_api_key)
                )
        research = (
            {}
            if self.research_records is not None
            else {r.url: r for r in load_cached_research(self.settings)}
        )
        research.update(self.research)
        style = read_showcase_style(self.settings, reader=self.style_reader)
        previous = get_previous_runs(self.settings, experiment_type="landing_redesign", limit=10)
        landscape = visual_landscape(
            ranked, self.analyses.values(), research.values(), style, previous
        )
        if self.settings.glam_api_key:
            landscape.notes.append(
                "image backend is glam (text only): tiles take no references, so a ugc group "
                "cannot lock the same person across slots and tile:/creative: refs are dropped."
            )
        return landscape_dict(landscape)


def run_tool_loop(settings: Settings, tools: ToolLoop, *, brief: str | None = None) -> Any:
    """Tool events come from `tools.execute`; the final parsed output is emitted as `proposal`."""
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    from anthropic import Anthropic

    client = Anthropic(api_key=settings.anthropic_api_key)
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": brief
            or ("Propose one landing redesign experiment or no_experiment. Use tools as needed."),
        }
    ]
    output_retries = 0
    max_tokens = MAX_TOKENS
    force_proposal = False
    # The reason the last submission was rejected: without it a failure is undiagnosable.
    last_invalid: str | None = None
    system = tools.system_prompt() + (
        "\n\nSubmit the final JSON object through the submit_proposal tool's proposal argument. "
        "Use the read tools first as needed. Do not write the final proposal as text. "
        "The tool schema is authoritative for field names, types and allowed values."
    )
    for request_index in range(MAX_TOOL_CALLS + MAX_OUTPUT_RETRIES + 1):
        final_only = force_proposal or tools.calls >= MAX_TOOL_CALLS
        request_info = {
            "id": f"model-{request_index}",
            "label": "Prepare the next proposal step",
            "model": settings.anthropic_model,
            "stage": "design",
        }
        emit_to(tools.on_event, "model_request_started", request_info)
        try:
            response = client.messages.create(
                model=settings.anthropic_model,
                max_tokens=max_tokens,
                system=system,
                tools=([proposal_tool()] if final_only else [*TOOL_SPECS, proposal_tool()]),
                tool_choice={"type": "tool", "name": "submit_proposal"}
                if final_only
                else {"type": "auto"},
                messages=messages,
            )
        except Exception as error:
            emit_to(tools.on_event, "model_request_failed", {**request_info, "error": str(error)})
            raise
        emit_to(
            tools.on_event,
            "model_request_completed",
            {**request_info, "stopReason": response.stop_reason},
        )
        if response.stop_reason == "refusal":
            raise ValueError("The model declined this proposal. Your saved draft is unchanged.")
        if response.stop_reason == "max_tokens":
            # A truncated tool block must never be executed or saved as a proposal.
            if output_retries >= MAX_OUTPUT_RETRIES:
                raise ValueError(
                    "The model could not finish the landing proposal. "
                    "Your selections are saved; retry generation."
                )
            output_retries += 1
            max_tokens *= 2
            emit_to(
                tools.on_event,
                "model_output_retry",
                {"message": "The proposal was cut off; retrying with more room to finish."},
            )
            continue
        if response.stop_reason == "tool_use":
            tool_results = []
            output = None
            messages.append({"role": "assistant", "content": response.content})
            for block in response.content:
                if block.type != "tool_use":
                    continue
                invalid = False
                if block.name == "submit_proposal":
                    try:
                        output = parse_model_output((block.input or {}).get("proposal"))
                        if isinstance(output, LandingProposal):
                            tools.validate_output(output)
                        result = {"accepted": True}
                    except (ValidationError, ValueError, TypeError, AttributeError) as error:
                        invalid = True
                        last_invalid = str(error)
                        result = {
                            "error": str(error),
                            "instruction": "Correct these fields and submit the complete proposal again.",
                        }
                else:
                    result = tools.execute(block.name, dict(block.input or {}), call_id=block.id)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                        "is_error": invalid,
                    }
                )
            messages.append({"role": "user", "content": tool_results})
            if output is not None and not any(item["is_error"] for item in tool_results):
                emit_to(tools.on_event, "proposal", {"output": output.model_dump(by_alias=True)})
                return output
            if not any(item["is_error"] for item in tool_results):
                continue
        else:
            # Never attempt to repair malformed JSON by dropping text or changing evidence.
            messages.append({"role": "assistant", "content": response.content})
            messages.append(
                {
                    "role": "user",
                    "content": "Submit the complete result using submit_proposal, not text or a code block.",
                }
            )
        if output_retries >= MAX_OUTPUT_RETRIES:
            # The workspace replaces an error over 240 characters with a generic line, so the
            # headline stays short; Activity keeps each rejection in full.
            raise ValueError(
                "The model returned an invalid landing proposal after automatic retries"
                + (f" ({summarize_invalid(last_invalid, 110)})" if last_invalid else "")
                + ". Your selections are saved; retry generation."
            )
        output_retries += 1
        force_proposal = True
        emit_to(
            tools.on_event,
            "model_output_retry",
            {
                "message": "Correcting the proposal format automatically; your selections are saved.",
                "reason": last_invalid,
            },
        )
    raise ValueError("Model exceeded the tool-call cap without a proposal")
