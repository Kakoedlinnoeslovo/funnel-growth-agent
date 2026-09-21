"""Ready video sources the agent may put on the landing: curated Recraft YouTube clips and the
ranked ad creatives that have a local mp4. Claude picks from this list; it never invents ids."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import Settings
from .models import RankedCreative


def load_youtube_catalog(settings: Settings) -> list[dict[str, Any]]:
    path = settings.youtube_catalog_path
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("videos") or [])


def youtube_ids(settings: Settings) -> set[str]:
    return {str(item.get("videoId")) for item in load_youtube_catalog(settings)}


def creative_live_dir(settings: Settings) -> Path:
    return settings.growth_loop_dir / "data" / "output" / "creative" / "live"


def creative_video_path(settings: Settings, creative_id: str) -> Path:
    return creative_live_dir(settings) / f"{creative_id}.mp4"


def creative_image_path(settings: Settings, creative_id: str) -> Path:
    return creative_live_dir(settings) / f"{creative_id}.jpg"


def media_sources(settings: Settings, ranked: list[RankedCreative] | None) -> dict[str, Any]:
    creatives = []
    for item in ranked or []:
        local = Path(item.video_path) if item.video_path else None
        if local is None or not local.is_file():
            continue
        creatives.append(
            {
                "creativeId": item.creative_id,
                "adName": item.ad_name,
                "title": item.title,
                "spend": item.spend,
                "clicks": item.clicks,
                "reasonSelected": item.reason_selected,
            }
        )
    return {
        "youtube": load_youtube_catalog(settings),
        "creatives": creatives,
        "rules": (
            "Pick a clip that shows the promised action within its first 3 seconds. Clips are "
            "trimmed at apply time to start+duration (4 to 30 s). videoId must come from `youtube`, "
            "creativeId from `creatives`."
        ),
    }
