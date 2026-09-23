"""Refresh the offline Recraft channel catalog without downloading video files."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CHANNEL_URL = "https://www.youtube.com/@Recraftai"
CHANNEL_ID = "UCzA1s-xikIANwlEXrOwk19w"
TOPICS = {
    "vector": "vectors",
    "svg": "editable svg",
    "icon": "icons",
    "logo": "logos",
    "brand": "branding",
    "style": "styles",
    "consistent": "consistency",
    "mockup": "mockups",
    "photo": "photography",
    "edit": "editing",
    "video": "video generation",
    "3d": "3d",
    "text": "typography",
    "poster": "posters",
    "figma": "figma",
    "studio": "studio",
    "tutorial": "tutorial",
    "session": "workshop",
}


def build_catalog(raw: dict, previous: dict | None = None) -> dict:
    """Normalize yt-dlp's paginated channel tabs; retain human-written topic notes."""
    if raw.get("channel_id") != CHANNEL_ID:
        raise ValueError("The index response is not from the Recraft channel.")
    old = {row["videoId"]: row for row in (previous or {}).get("videos", [])}
    videos: dict[str, dict[str, Any]] = {}
    tabs: dict[str, int] = {}

    def visit(node: dict, kind: str = "video") -> None:
        if "entries" in node:
            url = node.get("webpage_url", "").rstrip("/")
            tab = url.rsplit("/", 1)[-1]
            if tab in {"videos", "shorts", "streams"}:
                kind = {"videos": "video", "shorts": "short", "streams": "stream"}[tab]
                tabs[tab] = len(node["entries"])
            for entry in node["entries"]:
                if not isinstance(entry, dict):
                    raise ValueError("Incomplete channel response; keeping the saved index.")
                visit(entry, kind)
            return
        video_id, title = node.get("id", ""), node.get("title", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) or not title:
            raise ValueError("Invalid video metadata; keeping the saved index.")
        if node.get("availability") in {"private", "premium_only", "subscriber_only", "needs_auth"}:
            return
        if node.get("live_status") in {"is_live", "is_upcoming", "post_live"}:
            return
        if title in {"[Private video]", "[Deleted video]"}:
            return
        if video_id in videos:
            return
        prior = old.get(video_id, {})
        curated = prior.get("topics") and prior.get("topicsSource", "curated") == "curated"
        topics = (
            prior["topics"]
            if curated
            else list(
                dict.fromkeys(topic for word, topic in TOPICS.items() if word in title.lower())
            )
        )
        videos[video_id] = {
            "videoId": video_id,
            "title": title,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "kind": kind,
            "durationSeconds": node.get("duration") or prior.get("durationSeconds"),
            "viewCount": node.get("view_count"),
            "topics": topics,
            "topicsSource": "curated" if curated else "title",
        }

    visit(raw)
    if not videos or not tabs.get("videos"):
        raise ValueError("No channel videos found; keeping the saved index.")
    return {
        "schemaVersion": 1,
        "channel": CHANNEL_URL,
        "channelId": CHANNEL_ID,
        "indexedAt": datetime.now(UTC).isoformat(),
        "source": "yt-dlp flat channel playlists (all pages)",
        "sourceTabs": tabs,
        "counts": dict(Counter(row["kind"] for row in videos.values())),
        "note": (
            "Saved public channel metadata, not transcripts or a visual review. "
            "Topics marked title are inferred from titles. Missing durations are unknown. "
            "Video files download on demand and are cached locally."
        ),
        "videos": list(videos.values()),
    }


def save_catalog(catalog: dict, path: Path, *, previous: dict, allow_smaller: bool = False) -> None:
    """Leave the last good snapshot intact on failed or unexpectedly smaller refreshes."""
    if not allow_smaller and len(catalog["videos"]) < len(previous.get("videos", [])):
        raise ValueError("The refreshed index is smaller. Review it before using --allow-smaller.")
    if not allow_smaller and set(previous.get("sourceTabs", {})) - set(catalog["sourceTabs"]):
        raise ValueError("A previously indexed tab is missing. Use --allow-smaller after review.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, suffix=".json", delete=False
    ) as output:
        temporary = Path(output.name)
        try:
            json.dump(catalog, output, indent=2, ensure_ascii=False)
            output.write("\n")
            output.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def refresh_catalog(path: Path, *, seed: Path, allow_smaller: bool = False) -> dict:
    previous_path = path if path.is_file() else seed
    previous = json.loads(previous_path.read_text()) if previous_path.is_file() else {}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "yt_dlp",
            "--ignore-config",
            "--flat-playlist",
            "--dump-single-json",
            "--skip-download",
            "--socket-timeout",
            "20",
            "--extractor-retries",
            "2",
            CHANNEL_URL,
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=True,
    )
    catalog = build_catalog(json.loads(result.stdout), previous)
    save_catalog(catalog, path, previous=previous, allow_smaller=allow_smaller)
    return catalog
