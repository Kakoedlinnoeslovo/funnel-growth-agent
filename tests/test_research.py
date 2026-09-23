from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from funnel_growth_agent.research import (
    RESEARCH_SCHEMA_VERSION,
    LandingPatternRead,
    public_redirect_chain,
    research_cache_key,
    research_landing,
    validate_public_url,
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


def test_research_expires_after_24_hours(settings):
    browser, reader = FakeBrowser(), FakeReader()
    url = "https://www.kittl.com/"
    research_landing(url, settings, browser=browser, reader=reader)
    path = settings.research_dir / f"{research_cache_key(url)}.json"
    cached = json.loads(path.read_text())
    cached["fetchedAt"] = (settings.clock() - timedelta(hours=25)).isoformat()
    path.write_text(json.dumps(cached))
    research_landing(url, settings, browser=browser, reader=reader)
    assert len(reader.calls) == 2


def test_refresh_preserves_saved_drafts_screenshots(settings):
    browser, reader = FakeBrowser(), FakeReader()
    url = "https://www.kittl.com/"
    first = research_landing(url, settings, browser=browser, reader=reader)
    previous_paths = list(first.screenshots)
    second = research_landing(url, settings, browser=browser, reader=reader, refresh=True)
    assert set(previous_paths).isdisjoint(second.screenshots)
    assert all(path.is_file() for _, _, _, path in browser.shots)


@pytest.mark.parametrize(
    "failure",
    [None, "navigate", "capture-desktop", "capture-phone", "capture-full", "read", "primary-cta"],
)
def test_research_emits_paired_live_steps_and_reports_cache_hits(settings, failure):
    events = []
    calls = []
    url = "https://www.kittl.com/"
    destination = "https://www.kittl.com/features"

    def entered(name):
        # The running state must already be visible when the potentially slow call begins.
        kind, data = events[-1]
        assert kind == "step_started" and data["id"].startswith(name + ":")
        assert data["stage"] == "research"
        calls.append(name)
        if name == failure:
            raise OSError("injected browser or reader failure")

    class Browser(FakeBrowser):
        def inspect_page(self, target):
            entered("navigate" if target == url else "primary-cta")
            return {
                "url": target,
                "title": "Design features",
                "links": [{"text": "Start for free", "url": destination}],
            }

        def screenshot(self, target, *, width, height, out):
            entered("capture-desktop" if width == 1440 else "capture-phone")
            super().screenshot(target, width=width, height=height, out=out)

        def full_screenshot(self, target, *, out):
            entered("capture-full")
            out.write_bytes(b"full page")

    class Reader(FakeReader):
        def read(self, target, shots):
            entered("read")
            return super().read(target, shots)

    browser, reader = Browser(), Reader()
    record = research_landing(
        url,
        settings,
        browser=browser,
        reader=reader,
        include_journey=True,
        on_event=lambda kind, data: events.append((kind, data)),
    )
    assert len(events) == 2 * len(calls)
    for (start_kind, start), (end_kind, end) in zip(events[::2], events[1::2]):
        assert start_kind == "step_started"
        assert start["id"] == end["id"] and start["label"] == end["label"]
        assert end_kind == (
            "step_failed" if start["id"].split(":")[0] == failure else "step_completed"
        )
    if failure:
        assert events[-1][0] == "step_failed"
        assert "injected browser or reader failure" in events[-1][1]["error"]
        if failure == "primary-cta":
            assert record.error is None and record.journey[-1]["status"] == "blocked"
        else:
            assert record.error is not None
        return

    assert record.error is None and len(record.screenshots) == 3
    assert calls == [
        "navigate",
        "capture-desktop",
        "capture-phone",
        "capture-full",
        "read",
        "primary-cta",
    ]
    assert events[-1][1]["resolvedUrl"] == destination
    calls.clear()
    events.clear()
    cached = research_landing(
        url,
        settings,
        browser=browser,
        reader=reader,
        include_journey=True,
        on_event=lambda kind, data: events.append((kind, data)),
    )
    assert cached.cache_hit and not calls
    assert len(events) == 1 and events[0][0] == "step_completed"
    assert events[0][1]["cacheHit"] is True and events[0][1]["status"] == "cached"
    assert events[0][1]["screenshots"] == record.screenshots


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost/",
        "https://10.1.2.3/",
        "https://[::1]/",
        "https://169.254.169.254/",
        "https://user:pass@example.com/",
        "https://example.com:8443/",
        "file:///etc/passwd",
    ],
)
def test_public_url_rejects_local_and_credentialed_urls(url):
    with pytest.raises(ValueError):
        validate_public_url(url, resolve_dns=False)


def test_public_url_rejects_mixed_public_private_dns(monkeypatch):
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", (ip, 443)) for ip in ["8.8.8.8", "127.0.0.1"]],
    )
    with pytest.raises(ValueError, match="private"):
        validate_public_url("https://example.com/")


def test_redirect_destination_is_checked_before_following(monkeypatch):
    monkeypatch.setattr(
        "socket.getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("8.8.8.8", 443))]
    )
    called = []

    class Client:
        def head(self, url, **kwargs):
            called.append(url)
            return SimpleNamespace(
                status_code=302, headers={"location": "https://127.0.0.1/private"}
            )

    with pytest.raises(ValueError, match="Private"):
        public_redirect_chain("https://example.com/", client=Client())
    assert called == ["https://example.com/"]


def test_redirect_stops_before_authentication(monkeypatch):
    monkeypatch.setattr(
        "socket.getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("8.8.8.8", 443))]
    )
    called = []

    class Client:
        def head(self, url, **kwargs):
            called.append(url)
            return SimpleNamespace(status_code=302, headers={"location": "/signup"})

    with pytest.raises(ValueError, match="authentication"):
        public_redirect_chain("https://example.com/", client=Client())
    assert called == ["https://example.com/"]
