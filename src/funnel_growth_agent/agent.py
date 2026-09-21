"""Claude read-tool loop. Cap 12 calls. No write tools."""

from __future__ import annotations

import json
from typing import Any

from .config import Settings
from .creatives import get_top_creatives
from .events import EmitData, emit_to
from .gemini import analyze_creative
from .landing import get_current_landing
from .memory import get_previous_runs
from .metrics import get_landing_cta_metrics, load_latest_reports
from .models import CachedCreativeAnalysis, MetricsSlice, RankedCreative, parse_model_output
from .research import Browser, PatternReader, research_landing
from .showcase_style import StyleReader, read_showcase_style
from .sources import media_sources

MAX_TOOL_CALLS = 12
MAX_TOKENS = 4096

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
            "Ready clips the hero may use: curated Recraft YouTube videos (videoId, title, "
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
    ) -> None:
        self.settings = settings
        self.on_event = on_event
        self.ranked = ranked
        self.analyses = {item.creative_id: item for item in (analyses or [])}
        self.metrics = metrics
        self.browser = browser
        self.reader = reader
        self.style_reader = style_reader
        self.calls = 0

    def system_prompt(self) -> str:
        instructions = self.settings.instructions_path.read_text(encoding="utf-8")
        playbook = self.settings.playbook_path.read_text(encoding="utf-8")
        base = self.settings.base_version
        header = f"Base landing version: {base} (the funnel `/` serves; variants are copies of it)."
        return header + "\n\n" + instructions.strip() + "\n\n" + playbook.strip()

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
        result = self._execute(name, arguments)
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
            return get_current_landing(self.settings)
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
            record = research_landing(url, self.settings, browser=self.browser, reader=self.reader)
            return json.loads(record.model_dump_json(by_alias=True))
        if name == "get_showcase_style":
            style = read_showcase_style(self.settings, reader=self.style_reader)
            return json.loads(style.model_dump_json(by_alias=True))
        return {"error": f"unknown tool {name}"}


def run_tool_loop(settings: Settings, tools: ToolLoop) -> Any:
    """Tool events come from `tools.execute`; the final parsed output is emitted as `proposal`."""
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    from anthropic import Anthropic

    client = Anthropic(api_key=settings.anthropic_api_key)
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": (
                "Propose one landing redesign experiment or no_experiment. Use tools as needed."
            ),
        }
    ]
    for _ in range(MAX_TOOL_CALLS + 1):
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=MAX_TOKENS,
            system=tools.system_prompt(),
            tools=TOOL_SPECS,
            messages=messages,
        )
        if response.stop_reason == "tool_use":
            tool_results = []
            messages.append({"role": "assistant", "content": response.content})
            for block in response.content:
                if block.type != "tool_use":
                    continue
                result = tools.execute(block.name, dict(block.input or {}), call_id=block.id)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    }
                )
            messages.append({"role": "user", "content": tool_results})
            continue
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("Model returned no JSON object")
        output = parse_model_output(json.loads(text[start : end + 1]))
        emit_to(tools.on_event, "proposal", {"output": output.model_dump(by_alias=True)})
        return output
    raise ValueError("Model exceeded the tool-call cap without a proposal")
