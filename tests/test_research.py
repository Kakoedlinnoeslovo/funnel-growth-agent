from __future__ import annotations

from funnel_growth_agent.research import research_cache_key, research_landing
from redesign_helpers import FakeBrowser, FakeReader


def test_research_screenshots_desktop_and_phone_then_caches(settings) -> None:
    browser = FakeBrowser()
    reader = FakeReader()
    record = research_landing("https://www.kittl.com/", settings, browser=browser, reader=reader)
    assert record.error is None
    assert record.read is not None and record.read.primary_cta == "Start for free"
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


def test_research_without_gemini_key_returns_error_not_exception(settings) -> None:
    record = research_landing("https://www.kittl.com/", settings, browser=FakeBrowser())
    assert record.read is None and "GEMINI_API_KEY" in record.error
    assert len(record.screenshots) == 2
