from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from funnel_growth_agent.cli import app
from funnel_growth_agent.config import PACKAGE_DIR
from funnel_growth_agent.sources import load_youtube_catalog, media_sources, youtube_ids
from funnel_growth_agent.youtube_index import (
    CHANNEL_ID,
    CHANNEL_URL,
    build_catalog,
    refresh_catalog,
    save_catalog,
)


def channel():
    return {
        "channel_id": CHANNEL_ID,
        "entries": [
            {
                "webpage_url": CHANNEL_URL + "/videos",
                "entries": [
                    {"id": "z0r74lakHOM", "title": "Vectorize your logo", "duration": 89},
                    {"id": "U8N6A0aiNN0", "title": "Generate & Edit Vectors", "duration": 182},
                ],
            },
            {
                "webpage_url": CHANNEL_URL + "/shorts",
                "entries": [
                    {"id": "1nevzOnWTGE", "title": "New styles"},
                    {"id": "z0r74lakHOM", "title": "Vectorize your logo"},
                ],
            },
            {
                "webpage_url": CHANNEL_URL + "/streams",
                "entries": [
                    {
                        "id": "bspnBUuPS3s",
                        "title": "Session 1",
                        "duration": 1000,
                        "live_status": "was_live",
                    },
                    {"id": "gwUbE9D6V-8", "title": "Live now", "live_status": "is_live"},
                    {"id": "ns30Qj4WTwk", "title": "Tomorrow", "live_status": "is_upcoming"},
                ],
            },
        ],
    }


def test_channel_tabs_deduplicate_preserve_notes_and_skip_unfinished_streams():
    previous = {"videos": [{"videoId": "z0r74lakHOM", "topics": ["path editing"]}]}
    result = build_catalog(channel(), previous)
    assert result["counts"] == {"video": 2, "short": 1, "stream": 1}
    first, second, short, stream = result["videos"]
    assert first["topics"] == ["path editing"]
    assert first["topicsSource"] == "curated"
    assert second["topics"] == ["vectors", "editing"]
    assert second["topicsSource"] == "title"
    assert short["durationSeconds"] is None
    assert stream["durationSeconds"] == 1000
    assert "indexedAt" in result


@pytest.mark.parametrize("case", ["wrong_channel", "missing_entry", "bad_id", "empty"])
def test_invalid_or_incomplete_results_are_rejected(case):
    raw = channel()
    if case == "wrong_channel":
        raw["channel_id"] = "other"
    elif case == "missing_entry":
        raw["entries"][0]["entries"].append(None)
    elif case == "bad_id":
        raw["entries"][0]["entries"][0]["id"] = "../../file"
    else:
        raw["entries"] = []
    with pytest.raises(ValueError):
        build_catalog(raw)


def test_smaller_or_missing_tab_refresh_keeps_last_good_snapshot(tmp_path):
    path = tmp_path / "index.json"
    full = build_catalog(channel())
    save_catalog(full, path, previous={})
    before = path.read_bytes()
    smaller = deepcopy(full)
    smaller["videos"].pop()
    with pytest.raises(ValueError, match="smaller"):
        save_catalog(smaller, path, previous=full)
    assert path.read_bytes() == before
    missing_tab = deepcopy(full)
    del missing_tab["sourceTabs"]["shorts"]
    with pytest.raises(ValueError, match="tab is missing"):
        save_catalog(missing_tab, path, previous=full)
    assert path.read_bytes() == before
    save_catalog(smaller, path, previous=full, allow_smaller=True)
    assert len(json.loads(path.read_text())["videos"]) == 3


def test_refresh_uses_all_channel_pages_without_video_downloads(tmp_path):
    path = tmp_path / "index.json"
    with patch("funnel_growth_agent.youtube_index.subprocess.run") as run:
        run.return_value.stdout = json.dumps(channel())
        refresh_catalog(path, seed=tmp_path / "absent.json")
    args = run.call_args.args[0]
    assert args[-1] == CHANNEL_URL  # root includes Videos, Shorts and Live
    assert "--flat-playlist" in args and "--skip-download" in args
    assert "--playlist-end" not in args
    assert len(json.loads(path.read_text())["videos"]) == 4
    before = path.read_bytes()
    with patch(
        "funnel_growth_agent.youtube_index.subprocess.run",
        side_effect=subprocess.TimeoutExpired("yt-dlp", 180),
    ):
        with pytest.raises(subprocess.TimeoutExpired):
            refresh_catalog(path, seed=path)
    assert path.read_bytes() == before


def test_demo_and_agent_use_saved_override_with_bundled_fallback(settings):
    bundled = load_youtube_catalog(settings)
    assert len(bundled) > 13
    assert "z0r74lakHOM" in youtube_ids(settings)
    snapshot = build_catalog(channel())
    path = settings.data_dir / "youtube_catalog.json"
    save_catalog(snapshot, path, previous={})
    assert settings.youtube_catalog_path == path
    sources = media_sources(settings, [])
    assert sources["youtube"] == snapshot["videos"]
    assert sources["youtubeIndex"]["indexedAt"] == snapshot["indexedAt"]
    assert len(youtube_ids(settings)) == 4
    path.unlink()
    assert settings.youtube_catalog_path == PACKAGE_DIR / "youtube_catalog.json"


def test_refresh_command_reports_failure_without_replacing_snapshot(tmp_path):
    path = tmp_path / "index.json"
    path.write_text('{"videos": []}')
    with patch(
        "funnel_growth_agent.youtube_index.subprocess.run",
        side_effect=subprocess.CalledProcessError(1, "yt-dlp"),
    ):
        result = CliRunner().invoke(app, ["index-youtube", "--output", str(path)])
    assert result.exit_code == 1
    assert "index unchanged" in result.output
    assert path.read_text() == '{"videos": []}'
