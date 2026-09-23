from __future__ import annotations

import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import ValidationError

from funnel_growth_agent.competitor_research import (
    COMPETITORS,
    ResearchConfig,
    _api_ads,
    canonical_destination,
    research_competitors,
)
from redesign_helpers import FakeBrowser, FakeReader


class JourneyBrowser(FakeBrowser):
    def __init__(self, cards=None, *, blocked=False, cta="https://www.kittl.com/features"):
        super().__init__()
        self.pages = []
        self.full_shots = []
        self.blocked = blocked
        self.cta = cta
        self.cards = cards if cards is not None else [self.card(str(i)) for i in range(1, 6)]

    @staticmethod
    def card(ad_id, name="Kittl", destination="https://www.kittl.com/vector", active=True):
        return {
            "id": ad_id,
            "text": f"{name}\n{'Active' if active else 'Inactive'}\nVector illustration",
            "links": [{"text": "Learn more", "url": destination}],
        }

    def inspect_page(self, url):
        self.pages.append(url)
        if "facebook.com/ads/library" in url:
            if self.blocked:
                raise RuntimeError("HTTP 403")
            ad_id = (parse_qs(urlparse(url).query).get("id") or [None])[0]
            cards = [card for card in self.cards if not ad_id or card["id"] == ad_id]
            return {"url": url, "ads": cards, "links": cards[0]["links"] if ad_id and cards else []}
        if url.endswith("/features"):
            return {"url": url, "title": "Features", "links": [], "hasForm": True}
        return {
            "url": url,
            "title": "Design vectors",
            "redirectChain": [url],
            "links": [{"text": "Start for free", "url": self.cta, "y": 200}],
        }

    def full_screenshot(self, url, *, out):
        self.full_shots.append((url, out))
        out.write_bytes(b"full page screenshot")


@pytest.fixture(autouse=True)
def no_live_api(monkeypatch):
    monkeypatch.delenv("META_AD_LIBRARY_TOKEN", raising=False)


def test_configuration_defaults_and_untrusted_urls():
    config = ResearchConfig()
    assert config.competitors == ["kittl", "canva", "zeely", "runway", "luma"]
    assert config.country == "GB"
    assert "adLibraryUrls" in config.model_dump()
    assert ResearchConfig(country="us").country == "US"
    assert ResearchConfig(
        adLibraryUrls=["https://www.facebook.com/ads/library/?id=123&access_token=secret"]
    ).ad_library_urls == ["https://www.facebook.com/ads/library/?id=123"]
    for payload in (
        {"competitors": ["unknown"]},
        {"country": "ALL"},
        {"landingUrls": ["https://127.0.0.1/"]},
        {"adLibraryUrls": ["https://evil.example/ads/library/?id=1"]},
    ):
        with pytest.raises(ValidationError):
            ResearchConfig.model_validate(payload)


def test_verified_ad_journey_is_bounded_incremental_and_has_full_page(settings):
    browser, reader = JourneyBrowser(), FakeReader()
    events, results = [], []
    records = research_competitors(
        settings,
        {"competitors": ["kittl"]},
        {"theme": "vector"},
        browser=browser,
        reader=reader,
        on_event=lambda kind, data: events.append((kind, data)),
        on_result=results.append,
    )
    record = records[0]
    assert results == records
    assert record["status"] == "completed"
    assert len(record["ads"]) == 3
    assert all(ad["advertiserVerified"] for ad in record["ads"])
    assert record["sourceKind"] == "ad_destination"
    assert record["adLibraryUrl"] == "https://www.facebook.com/ads/library/?id=1"
    assert record["uniqueDestinations"] == ["https://www.kittl.com/vector"]
    assert len(record["screenshots"]) == 3
    assert len(reader.calls[0][1]) == 3
    assert record["journey"][-1]["status"] == "stopped"
    assert "Form boundary" in record["journey"][-1]["reason"]
    assert events[0][0] == "step_started"
    assert events[-1][0] == "step_completed"
    assert events[-1][1]["id"] == "competitor:kittl"


def test_24_hour_cache_refresh_and_corruption(settings):
    browser, reader = JourneyBrowser(), FakeReader()
    config = {"competitors": ["kittl"]}
    research_competitors(settings, config, browser=browser, reader=reader)
    original_calls = len(browser.pages)
    cached = research_competitors(settings, config, browser=browser, reader=reader)[0]
    assert cached["cacheHit"] and len(browser.pages) == original_calls
    research_competitors(settings, {**config, "refresh": True}, browser=browser, reader=reader)
    assert len(browser.pages) > original_calls
    path = next((settings.research_dir / "competitors").glob("*.json"))
    saved = json.loads(path.read_text())
    saved["fetchedAt"] = (settings.clock() - timedelta(hours=25)).isoformat()
    path.write_text(json.dumps(saved))
    count = len(browser.pages)
    research_competitors(settings, config, browser=browser, reader=reader)
    assert len(browser.pages) > count
    path.write_text("broken")
    assert (
        research_competitors(settings, config, browser=browser, reader=reader)[0]["status"]
        == "completed"
    )


def test_same_creative_content_uses_cache_across_drafts(settings):
    browser, reader = JourneyBrowser(), FakeReader()
    first = {
        "creatives": [
            {
                "id": "ad-1",
                "name": "Vector ad",
                "body": "Vector illustrations",
                "imagePath": "/drafts/first/creative.png",
                "warnings": ["old"],
            }
        ],
        "analyses": [
            {
                "creativeId": "ad-1",
                "analyzedAt": "yesterday",
                "analysis": {"primaryPromise": "Draw vectors"},
            }
        ],
    }
    second = {
        "creatives": [
            {
                "id": "ad-1",
                "name": "Vector ad",
                "body": "Vector illustrations",
                "imagePath": "/drafts/second/creative.png",
                "warnings": ["new"],
            }
        ],
        "analyses": [
            {
                "creativeId": "ad-1",
                "analyzedAt": "today",
                "analysis": {"primaryPromise": "Draw vectors"},
            }
        ],
    }
    config = {"competitors": ["kittl"]}
    research_competitors(settings, config, first, browser=browser, reader=reader)
    count = len(browser.pages)
    cached = research_competitors(settings, config, second, browser=browser, reader=reader)[0]
    assert cached["cacheHit"]
    assert len(browser.pages) == count


def test_blocked_ads_never_become_fabricated_homepage_evidence(settings):
    browser = JourneyBrowser(blocked=True)
    result = research_competitors(
        settings, {"competitors": ["kittl", "canva"]}, browser=browser, reader=FakeReader()
    )
    assert all(record["status"] == "blocked" for record in result)
    assert all(not record["read"] and not record["ads"] for record in result)
    assert not browser.shots
    assert all("facebook.com" in url for url in browser.pages)


def test_unverified_inactive_and_unrelated_advertisers_are_not_used(settings):
    cards = [
        JourneyBrowser.card("1", name="Someone else"),
        JourneyBrowser.card("2", active=False),
        JourneyBrowser.card("3", destination="https://kittl.com.attacker.example/vector"),
    ]
    browser = JourneyBrowser(cards)
    result = research_competitors(
        settings, {"competitors": ["kittl"]}, browser=browser, reader=FakeReader()
    )[0]
    assert result["status"] == "blocked"
    assert [ad["id"] for ad in result["ads"]] == ["3"]
    assert result["ads"][0]["advertiserVerified"] is False
    assert not browser.shots


def test_explicit_reference_stays_distinct_from_ad_evidence(settings):
    records = research_competitors(
        settings,
        {"competitors": [], "landingUrls": ["https://www.kittl.com/vector"]},
        browser=JourneyBrowser(),
        reader=FakeReader(),
    )
    assert records[0]["sourceKind"] == "reference"
    assert records[0]["ads"] == []
    assert records[0]["provenance"] == [
        {"kind": "user_reference", "url": "https://www.kittl.com/vector"}
    ]


def test_auth_cta_is_recorded_without_visiting(settings):
    browser = JourneyBrowser(cta="https://www.kittl.com/signup")
    record = research_competitors(
        settings, {"competitors": ["kittl"]}, browser=browser, reader=FakeReader()
    )[0]
    assert record["journey"][-1]["url"] == browser.cta
    assert record["journey"][-1]["status"] == "stopped"
    assert browser.cta not in browser.pages


def test_existing_screenshot_only_fake_does_not_use_network(settings, monkeypatch):
    monkeypatch.setenv("META_AD_LIBRARY_TOKEN", "should-not-be-used")
    monkeypatch.setattr(
        "funnel_growth_agent.competitor_research._api_ads",
        lambda *args: pytest.fail("Injected offline browser must stay offline"),
    )
    results = research_competitors(settings, None, browser=FakeBrowser(), reader=FakeReader())
    assert len(results) == 5
    assert all(result["status"] == "blocked" for result in results)


def test_tracking_dedup_preserves_meaningful_query():
    assert (
        canonical_destination("https://www.kittl.com/vector?utm_source=fb&fbclid=abc&plan=pro#hero")
        == "https://www.kittl.com/vector?plan=pro"
    )


def test_pasted_library_link_can_supply_observed_ad(settings):
    browser = JourneyBrowser()
    result = research_competitors(
        settings,
        {"competitors": ["kittl"], "adLibraryUrls": ["https://www.facebook.com/ads/library/?id=2"]},
        browser=browser,
        reader=FakeReader(),
    )[0]
    assert result["status"] == "completed"
    assert result["ads"][0]["id"] == "2"
    assert browser.pages[0] == "https://www.facebook.com/ads/library/?id=2"


def test_library_api_uses_separate_header_token_and_never_persists_it(monkeypatch):
    monkeypatch.setenv("META_AD_LIBRARY_TOKEN", "library-secret")
    monkeypatch.setenv("META_ADS_TOKEN", "owned-account-secret")
    calls = []

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return type(
                "Response",
                (),
                {
                    "status_code": 200,
                    "json": lambda self: {
                        "data": [
                            {
                                "id": "123",
                                "page_id": "456",
                                "page_name": "Kittl",
                                "ad_creative_bodies": ["Vector art"],
                                "ad_snapshot_url": "https://www.facebook.com/ads/archive/render_ad/?id=123&access_token=library-secret",
                            },
                            {"id": "999", "page_name": "Impostor", "ad_creative_bodies": ["Kittl"]},
                        ]
                    },
                },
            )()

    monkeypatch.setattr("funnel_growth_agent.competitor_research.httpx.Client", Client)
    ads = _api_ads(COMPETITORS["kittl"], "GB")
    assert len(ads) == 1
    assert ads[0]["adLibraryUrl"] == "https://www.facebook.com/ads/library/?id=123"
    assert calls[0][1]["headers"] == {"Authorization": "Bearer library-secret"}
    assert "access_token" not in calls[0][1]["params"]
    assert "secret" not in json.dumps(ads)


def test_api_failure_falls_back_to_browser(settings, monkeypatch):
    def unavailable(*args):
        raise RuntimeError("Library access denied")

    monkeypatch.setattr("funnel_growth_agent.competitor_research._api_ads", unavailable)
    record = research_competitors(
        settings, {"competitors": ["kittl"]}, browser=JourneyBrowser(), reader=FakeReader()
    )[0]
    assert record["status"] == "completed"
    assert "API unavailable" in record["warnings"][0]


def test_model_failure_keeps_observed_journey_and_screenshots(settings):
    class BrokenReader:
        def read(self, *args):
            raise RuntimeError("Model unavailable")

    record = research_competitors(
        settings, {"competitors": ["kittl"]}, browser=JourneyBrowser(), reader=BrokenReader()
    )[0]
    assert record["status"] == "partial"
    assert record["read"] is None
    assert len(record["screenshots"]) == 3
    assert record["journey"][0]["kind"] == "landing"
    assert record["provenance"][0]["adId"] == "1"


def test_inactive_snapshot_overrides_api_active_filter(settings, monkeypatch):
    monkeypatch.setattr(
        "funnel_growth_agent.competitor_research._api_ads",
        lambda *args: [
            {
                "id": "1",
                "pageName": "Kittl",
                "pageId": "123",
                "activeStatus": "active",
                "source": "meta_ads_library_api",
                "adLibraryUrl": "https://www.facebook.com/ads/library/?id=1",
            }
        ],
    )

    class InactiveBrowser(JourneyBrowser):
        def inspect_page(self, url):
            page = super().inspect_page(url)
            if "facebook.com" in url:
                page["ads"] = [{**card, "text": "Kittl\nInactive"} for card in page.get("ads", [])]
            return page

    browser = InactiveBrowser()
    result = research_competitors(
        settings, {"competitors": ["kittl"]}, browser=browser, reader=FakeReader()
    )[0]
    assert result["status"] == "blocked"
    assert result["ads"][0]["activeStatus"] == "inactive"
    assert not browser.shots


def test_api_snapshot_never_borrows_another_ads_destination(settings, monkeypatch):
    monkeypatch.setattr(
        "funnel_growth_agent.competitor_research._api_ads",
        lambda *args: [
            {
                "id": "1",
                "pageName": "Kittl",
                "pageId": "123",
                "activeStatus": "active",
                "source": "meta_ads_library_api",
                "adLibraryUrl": "https://www.facebook.com/ads/library/?id=1",
            }
        ],
    )

    class MixedPageBrowser(JourneyBrowser):
        def inspect_page(self, url):
            if "facebook.com" in url:
                return {"url": url, "ads": [self.card("2")], "links": self.card("2")["links"]}
            return super().inspect_page(url)

    browser = MixedPageBrowser()
    result = research_competitors(
        settings, {"competitors": ["kittl"]}, browser=browser, reader=FakeReader()
    )[0]
    assert result["status"] == "blocked"
    assert "destinationUrl" not in result["ads"][0]
    assert not browser.shots
