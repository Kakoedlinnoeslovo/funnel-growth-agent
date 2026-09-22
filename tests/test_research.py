from __future__ import annotations

import json

from funnel_growth_agent.research import (
    RESEARCH_SCHEMA_VERSION,
    LandingPatternRead,
    research_cache_key,
    research_landing,
)
from redesign_helpers import FakeBrowser, FakeReader


def test_research_screenshots_desktop_and_phone_then_caches(settings) -> None:
    browser = FakeBrowser()
    reader = FakeReader()
    record = research_landing("https://www.kittl.com/", settings, browser=browser, reader=reader)
    assert record.error is None
    assert record.read is not None and record.read.primary_cta == "Start for free"
    assert record.read.imagery_formats == ["ugc-candid", "screenshot"]
    assert record.read.people_shown is True
    assert record.schema_version == RESEARCH_SCHEMA_VERSION
    assert [(w, h) for _u, w, h, _p in browser.shots] == [(1440, 900), (390, 844)]
    assert reader.calls[0][1] == [p for _u, _w, _h, p in browser.shots]
    key = research_cache_key("https://www.kittl.com/")
    assert (settings.research_dir / f"{key}.json").is_file()
    again = research_landing("https://www.kittl.com/", settings, browser=browser, reader=reader)
    assert again.read == record.read
    assert len(browser.shots) == 2 and len(reader.calls) == 1


def test_research_degrades_when_browser_is_missing(settings) -> None:
    record = research_landing(
        "https://www.canva.com/",
        settings,
        browser=FakeBrowser(available=False),
        reader=FakeReader(),
    )
    assert record.read is None
    assert "browse unavailable" in (record.error or "")


def test_research_refuses_http_and_the_lab_itself(settings) -> None:
    assert "https" in research_landing("http://example.com", settings, browser=FakeBrowser()).error
    lab = research_landing("https://recraft-meta.vercel.app/", settings, browser=FakeBrowser())
    assert "get_current_landing" in lab.error


def test_research_v1_cache_is_refetched(settings) -> None:
    browser, reader = FakeBrowser(), FakeReader()
    url = "https://www.kittl.com/"
    first = research_landing(url, settings, browser=browser, reader=reader)
    cache = settings.research_dir / f"{research_cache_key(url)}.json"
    data = json.loads(cache.read_text(encoding="utf-8"))
    del data["schemaVersion"]
    del data["read"]["imageryFormats"]
    cache.write_text(json.dumps(data), encoding="utf-8")
    again = research_landing(url, settings, browser=browser, reader=reader)
    assert len(reader.calls) == 2 and again.read == first.read


def test_pattern_read_normalises_imagery_fields() -> None:
    read = LandingPatternRead.model_validate(
        {
            "imageryFormats": "Screenshot\nhologram\n",
            "imageryStyle": "flat 3D renders\napp screenshot",
            "peopleShown": "false",
        }
    )
    assert read.imagery_formats == ["screenshot", "other"]
    assert read.imagery_style == ["flat 3D renders", "app screenshot"]
    assert read.people_shown is False
    assert LandingPatternRead.model_validate({}).imagery_formats == []


def test_research_without_gemini_key_returns_error_not_exception(settings) -> None:
    record = research_landing("https://www.kittl.com/", settings, browser=FakeBrowser())
    assert record.read is None and "GEMINI_API_KEY" in record.error
    assert len(record.screenshots) == 2
