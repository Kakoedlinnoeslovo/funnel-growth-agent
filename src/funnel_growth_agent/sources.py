"""Ready video sources the agent may put on the landing: indexed Recraft YouTube clips and the
ranked ad creatives that have a local mp4. Claude picks from this list; it never invents ids."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import Settings
from .models import RankedCreative


def load_youtube_index(settings: Settings) -> dict[str, Any]:
    path = settings.youtube_catalog_path
    if not path.is_file():
        return {"videos": []}
    return json.loads(path.read_text(encoding="utf-8"))


def load_youtube_catalog(settings: Settings) -> list[dict[str, Any]]:
    return list(load_youtube_index(settings).get("videos") or [])


def youtube_ids(settings: Settings) -> set[str]:
    return {str(item.get("videoId")) for item in load_youtube_catalog(settings)}


def creative_live_dir(settings: Settings) -> Path:
    return settings.growth_loop_dir / "data" / "output" / "creative" / "live"


def creative_video_path(settings: Settings, creative_id: str) -> Path:
    if path := settings.creative_paths.get(creative_id, {}).get("video"):
        return Path(path)
    return creative_live_dir(settings) / f"{creative_id}.mp4"


def creative_image_path(settings: Settings, creative_id: str) -> Path:
    if path := settings.creative_paths.get(creative_id, {}).get("image"):
        return Path(path)
    return creative_live_dir(settings) / f"{creative_id}.jpg"


def media_sources(settings: Settings, ranked: list[RankedCreative] | None) -> dict[str, Any]:
    creatives = []
    for item in ranked or []:
        local = Path(item.video_path) if item.video_path else None
        if local is None or not local.is_file():
            continue
        # Short uploads still work as timestamped image references, but cannot fill a 4s hero clip.
        if item.video_duration is not None and item.video_duration < 4.1:
            continue
        creatives.append(
            {
                "creativeId": item.creative_id,
                "adName": item.ad_name,
                "title": item.title,
                "spend": item.spend,
                "clicks": item.clicks,
                "reasonSelected": item.reason_selected,
                "duration": item.video_duration,
            }
        )
    index = load_youtube_index(settings)
    return {
        "youtube": index.get("videos", []),
        "youtubeIndex": {key: value for key, value in index.items() if key != "videos"},
        "creatives": creatives,
        "rules": (
            "Pick a clip that shows the promised action within its first 3 seconds. Clips are "
            "trimmed at apply time to start+duration (4 to 30 s). videoId must come from `youtube`, "
            "creativeId from `creatives`. Title-derived topics are search hints, not proof of "
            "what appears on screen. Missing durations are unknown, not zero."
        ),
    }
