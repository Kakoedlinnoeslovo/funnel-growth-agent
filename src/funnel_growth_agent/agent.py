"""Claude read-tool loop. Cap 8 calls. No write tools."""

from __future__ import annotations

import json
from typing import Any

from .config import Settings
from .creatives import get_top_creatives
from .gemini import analyze_creative
from .landing import get_current_landing
from .memory import get_previous_runs
from .metrics import get_landing_cta_metrics, load_latest_reports
from .models import CachedCreativeAnalysis, MetricsSlice, RankedCreative, parse_model_output

MAX_TOOL_CALLS = 8

TOOL_SPECS = [
    {
        "name": "get_landing_cta_metrics",
        "description": "Latest weekly landing→CTA metrics. v6 is a proxy when isProxy is true.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_current_landing",
        "description": "Structured current v7 landing sections. No media bytes.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_previous_runs",
        "description": "Last hypotheses, changes, evaluation, and learning for one experiment type.",
        "input_schema": {
            "type": "object",
            "properties": {
                "experiment_type": {"type": "string", "default": "hero_copy"},
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
]


class ToolLoop:
    def __init__(
        self,
        settings: Settings,
        *,
        ranked: list[RankedCreative] | None = None,
        analyses: list[CachedCreativeAnalysis] | None = None,
        metrics: MetricsSlice | None = None,
    ) -> None:
        self.settings = settings
        self.ranked = ranked
        self.analyses = {item.creative_id: item for item in (analyses or [])}
        self.metrics = metrics
        self.calls = 0

    def system_prompt(self) -> str:
        instructions = self.settings.instructions_path.read_text(encoding="utf-8")
        playbook = self.settings.playbook_path.read_text(encoding="utf-8")
        return instructions.strip() + "\n\n" + playbook.strip()

    def execute(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls += 1
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
                experiment_type=str(arguments.get("experiment_type") or "hero_copy"),
                limit=int(arguments.get("limit") or 5),
            )
            return {"runs": [json.loads(row.model_dump_json(by_alias=True)) for row in rows]}
        if name == "get_top_creatives":
            if self.ranked is None:
                weekly, _ = load_latest_reports(self.settings)
                self.ranked = get_top_creatives(weekly, self.settings)
            return {"creatives": [json.loads(row.model_dump_json(by_alias=True)) for row in self.ranked]}
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
            record = analyze_creative(match, self.settings, call_model=bool(self.settings.gemini_api_key))
            self.analyses[creative_id] = record
            return json.loads(record.model_dump_json(by_alias=True))
        return {"error": f"unknown tool {name}"}


def run_tool_loop(settings: Settings, tools: ToolLoop) -> Any:
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    from anthropic import Anthropic

    client = Anthropic(api_key=settings.anthropic_api_key)
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": "Propose one landing CTA experiment or no_experiment. Use tools as needed.",
        }
    ]
    for _ in range(MAX_TOOL_CALLS + 1):
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=2048,
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
                result = tools.execute(block.name, dict(block.input or {}))
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    }
                )
            messages.append({"role": "user", "content": tool_results})
            continue
        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("Model returned no JSON object")
        return parse_model_output(json.loads(text[start : end + 1]))
    raise ValueError("Model exceeded the tool-call cap without a proposal")
