"""Bounded, attributable competitor-ad research. Public observations, never invented links.

The Ads Library token is intentionally separate from an owned-account Marketing API token.
Unavailable discovery stays visible; manually supplied landings are reference evidence only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import Settings
from .research import (
    GstackBrowser,
    _now,
    cache_is_fresh,
    find_browse_binary,
    research_landing,
    validate_public_url,
)

COMPETITORS = {
    "kittl": {"name": "Kittl", "aliases": ["Kittl"], "domains": ["kittl.com"]},
    "canva": {"name": "Canva", "aliases": ["Canva"], "domains": ["canva.com"]},
    "zeely": {"name": "Zeely", "aliases": ["Zeely", "Zeely AI"], "domains": ["zeely.ai"]},
    "runway": {
        "name": "Runway",
        "aliases": ["Runway", "Runway AI", "RunwayML"],
        "domains": ["runwayml.com"],
    },
    "luma": {"name": "Luma AI", "aliases": ["Luma AI", "Luma"], "domains": ["lumalabs.ai"]},
}
SCHEMA_VERSION = 1
MAX_ADS = 3


def _library_url(url: str) -> str:
    validate_public_url(url, resolve_dns=False)
    parsed = urlparse(url)
    if (parsed.hostname or "").lower() not in {"facebook.com", "www.facebook.com"}:
        raise ValueError("Ad links must be public Facebook Ads Library URLs")
    if parsed.path.rstrip("/") != "/ads/library":
        raise ValueError("Ad links must be public Facebook Ads Library URLs")
    # Never retain access tokens or tracking identifiers pasted with a URL.
    allowed = {
        k: v[-1]
        for k, v in parse_qs(parsed.query).items()
        if k in {"id", "view_all_page_id", "country", "q", "active_status", "ad_type"}
    }
    return "https://www.facebook.com/ads/library/?" + urlencode(allowed)


class ResearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)

    competitors: list[str] = Field(default_factory=lambda: list(COMPETITORS), max_length=5)
    country: str = "GB"
    ad_library_urls: list[str] = Field(default_factory=list, alias="adLibraryUrls", max_length=15)
    landing_urls: list[str] = Field(default_factory=list, alias="landingUrls", max_length=5)
    refresh: bool = False

    @field_validator("competitors")
    @classmethod
    def known_competitors(cls, values: list[str]) -> list[str]:
        values = list(dict.fromkeys(value.strip().lower() for value in values))
        if any(value not in COMPETITORS for value in values):
            raise ValueError("Unknown competitor identifier")
        return values

    @field_validator("country")
    @classmethod
    def country_code(cls, value: str) -> str:
        value = value.strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", value):
            raise ValueError("Use a two-letter country code")
        return value

    @field_validator("ad_library_urls")
    @classmethod
    def ad_links(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(_library_url(value.strip()) for value in values))

    @field_validator("landing_urls")
    @classmethod
    def reference_links(cls, values: list[str]) -> list[str]:
        return list(
            dict.fromkeys(validate_public_url(value.strip(), resolve_dns=False) for value in values)
        )


def _emit(callback: Any, kind: str, **data: Any) -> None:
    if callback:
        callback(kind, data)


def _official_url(url: str, spec: dict) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in spec["domains"])


def canonical_destination(url: str) -> str:
    parsed = urlparse(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in {"fbclid", "gclid", "msclkid", "_ga", "_gl"}
    ]
    return urlunparse(parsed._replace(query=urlencode(sorted(query)), fragment=""))


def _outbound_links(page: dict) -> list[str]:
    result: list[str] = []
    for link in page.get("links") or []:
        url = str(link.get("url") or "")
        parsed = urlparse(url)
        if parsed.hostname in {"l.facebook.com", "lm.facebook.com", "www.facebook.com"}:
            url = (parse_qs(parsed.query).get("u") or [url])[0]
            parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host in {"facebook.com", "instagram.com", "meta.com"} or host.endswith(
            (".facebook.com", ".instagram.com", ".meta.com")
        ):
            continue
        try:
            validate_public_url(url, resolve_dns=False)
        except ValueError:
            continue
        if url not in result:
            result.append(url)
    return result


def _advertiser_matches(name: str, spec: dict) -> bool:
    def normal(value: str) -> str:
        return re.sub(r"\s+", " ", value.strip()).casefold()

    return normal(name) in {normal(alias) for alias in spec["aliases"]}


def _api_ads(spec: dict, country: str) -> list[dict] | None:
    token = os.getenv("META_AD_LIBRARY_TOKEN")
    if not token:
        return None
    version = os.getenv("META_AD_LIBRARY_API_VERSION", "v23.0")
    if not re.fullmatch(r"v\d+\.\d+", version):
        raise ValueError("Invalid META_AD_LIBRARY_API_VERSION")
    fields = "id,page_id,page_name,ad_delivery_start_time,ad_delivery_stop_time,ad_creative_bodies,ad_creative_link_titles,ad_creative_link_captions,ad_creative_link_descriptions,publisher_platforms"
    params = {
        "ad_reached_countries": json.dumps([country]),
        "ad_active_status": "ACTIVE",
        "ad_type": "ALL",
        "search_terms": spec["name"],
        "fields": fields,
        "limit": 30,
    }
    # Header authentication avoids tokens in URLs, logs, cache keys, and draft evidence.
    with httpx.Client(timeout=25, follow_redirects=False, trust_env=False) as client:
        response = client.get(
            f"https://graph.facebook.com/{version}/ads_archive",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
    if response.status_code != 200:
        raise RuntimeError(
            f"Ads Library API unavailable (HTTP {response.status_code}); check Library access"
        )
    result = []
    for row in response.json().get("data") or []:
        if not _advertiser_matches(str(row.get("page_name") or ""), spec):
            continue
        ad_id = str(row.get("id") or "")
        if not ad_id.isdigit():
            continue
        result.append(
            {
                "id": ad_id,
                "pageId": row.get("page_id"),
                "pageName": row.get("page_name"),
                "activeStatus": "active",
                "activeStatusSource": "Meta Ads Library ACTIVE filter",
                "source": "meta_ads_library_api",
                "adLibraryUrl": f"https://www.facebook.com/ads/library/?id={ad_id}",
                "text": "\n".join(
                    str(text)
                    for key in (
                        "ad_creative_bodies",
                        "ad_creative_link_titles",
                        "ad_creative_link_descriptions",
                    )
                    for text in row.get(key) or []
                ),
                "startedAt": row.get("ad_delivery_start_time"),
                "platforms": row.get("publisher_platforms") or [],
            }
        )
        if len(result) == MAX_ADS:
            break
    return result


def _page_ads(page: dict, spec: dict, source_url: str) -> list[dict]:
    cards = page.get("ads") or []
    query_id = (parse_qs(urlparse(source_url).query).get("id") or [""])[0]
    visible_ids = set(re.findall(r"Library ID:\s*(\d+)", str(page.get("text") or ""), re.I))
    if not cards and query_id.isdigit() and visible_ids == {query_id}:
        cards = [{**page, "id": query_id}]
    results = []
    for card in cards:
        text = str(card.get("text") or "")
        name = str(card.get("pageName") or "")
        if not name:
            name = next(
                (line.strip() for line in text.splitlines() if _advertiser_matches(line, spec)), ""
            )
        if not _advertiser_matches(name, spec):
            continue
        active = card.get("activeStatus") == "active" or bool(
            re.search(r"(?im)^\s*active\s*$", text)
        )
        if not active:
            continue
        ad_id = str(card.get("id") or "")
        if not ad_id.isdigit():
            continue
        results.append(
            {
                "id": ad_id,
                "pageId": card.get("pageId"),
                "pageName": name,
                "activeStatus": "active",
                "activeStatusSource": "Visible Ads Library active label",
                "text": text[:7000],
                "source": "ads_library_browser",
                "links": card.get("links") or [],
                "adLibraryUrl": f"https://www.facebook.com/ads/library/?id={ad_id}",
            }
        )
    return results


def _discover(spec: dict, config: ResearchConfig, browser: Any) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []
    # Screenshot-only injected browsers (including existing offline test fakes) must not
    # accidentally initiate real API requests or fallback browser work.
    if not hasattr(browser, "inspect_page"):
        return [], ["Ads Library discovery is unavailable in this browser"]
    ads: list[dict] = []
    try:
        ads = _api_ads(spec, config.country) or []
    except Exception as error:
        warnings.append(f"Ads Library API unavailable: {type(error).__name__}")
    search = "https://www.facebook.com/ads/library/?" + urlencode(
        {
            "active_status": "active",
            "ad_type": "all",
            "country": config.country,
            "q": spec["name"],
            "search_type": "keyword_unordered",
        }
    )
    urls = list(config.ad_library_urls)
    if not ads:
        urls.append(search)
    for url in urls:
        if len({ad["id"] for ad in ads}) >= MAX_ADS:
            break
        try:
            page = browser.inspect_page(url)
            discovered = _page_ads(page, spec, url)
            if not discovered:
                warnings.append(
                    "No matching active advertiser cards were observable in Ads Library"
                )
            ads.extend(discovered)
        except Exception as error:
            warnings.append(f"Ads Library browser access unavailable: {type(error).__name__}")
    unique = {ad["id"]: ad for ad in ads}
    return list(unique.values())[:MAX_ADS], list(dict.fromkeys(warnings))


def relevance_context(context: Any) -> dict:
    """Stable creative evidence only: exclude draft paths, timestamps, metrics and warnings."""
    text_fields = {
        "name",
        "title",
        "body",
        "theme",
        "primaryPromise",
        "audienceIntent",
        "visualHook",
        "suggestedLandingTheme",
        "visibleText",
        "productClaims",
        "ctaIntent",
        "goal",
        "component",
        "primaryMetric",
    }
    texts: set[str] = set()
    creative_ids: set[str] = set()

    def visit(value: Any, *, field: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"creatives", "analyses", "analysis", "selectedStep", "step"} or key in text_fields:
                    visit(child, field=key)
                elif key in {"id", "creativeId", "ad_id"} and field in {"creatives", "analyses"}:
                    creative_ids.add(str(child))
        elif isinstance(value, list):
            for child in value:
                visit(child, field=field)
        elif isinstance(value, str) and (field in text_fields or not field):
            text = " ".join(value.split())
            if text:
                texts.add(text)

    visit(context)
    return {"creativeIds": sorted(creative_ids), "text": sorted(texts)}


def _relevance(ad: dict, context: Any) -> int:
    keywords = set(re.findall(r"[a-z]{4,}", " ".join(context.get("text", [])).lower()))
    words = set(re.findall(r"[a-z]{4,}", str(ad.get("text") or "").lower()))
    return len(keywords & words)


def _research_one(
    settings: Settings,
    key: str,
    config: ResearchConfig,
    context: Any,
    browser: Any,
    reader: Any,
    on_event: Any,
) -> dict:
    spec = COMPETITORS[key]
    search = "https://www.facebook.com/ads/library/?" + urlencode(
        {"q": spec["name"], "country": config.country, "active_status": "active", "ad_type": "all"}
    )
    result = {
        "competitor": key,
        "competitorName": spec["name"],
        "country": config.country,
        "status": "blocked",
        "sourceKind": "ads_library",
        "url": search,
        "fetchedAt": _now(settings),
        "schemaVersion": SCHEMA_VERSION,
        "ads": [],
        "provenance": [],
        "journey": [],
        "screenshots": [],
        "read": None,
        "error": None,
        "warnings": [],
        "cacheHit": False,
    }
    if not browser.available():
        result["error"] = (
            "Ads Library research unavailable: install gstack or configure GSTACK_BROWSE"
        )
        return result
    ads, warnings = _discover(spec, config, browser)
    result["warnings"] = warnings
    verified: list[dict] = []
    for ad in ads:
        _emit(
            on_event,
            "step_started",
            id=f"ad:{key}:{ad['id']}",
            label=f"Inspecting {spec['name']} ad {ad['id']}",
            url=ad["adLibraryUrl"],
        )
        try:
            page = {"links": ad.get("links") or []}
            if not page["links"]:
                page = browser.inspect_page(ad["adLibraryUrl"])
                # Deep links can open a library page containing many ads. Only links
                # observed inside this ad's card can establish its destination.
                matching = next(
                    (card for card in page.get("ads") or [] if str(card.get("id")) == ad["id"]),
                    None,
                )
                if matching:
                    page = matching
                else:
                    visible_ids = set(
                        re.findall(r"Library ID:\s*(\d+)", str(page.get("text") or ""), re.I)
                    )
                    if page.get("ads") or visible_ids != {ad["id"]}:
                        raise ValueError("The selected ad snapshot could not be identified")
            # A stopped ad can still appear briefly in an ACTIVE API response. Visible
            # contrary evidence wins; never promote an archived snapshot as active.
            snapshot_text = str(page.get("text") or "")
            if re.search(r"(?im)^\s*inactive\s*$", snapshot_text):
                ad["activeStatus"] = "inactive"
                ad["error"] = "Ads Library snapshot shows this ad is inactive"
                _emit(
                    on_event,
                    "step_failed",
                    id=f"ad:{key}:{ad['id']}",
                    label="Ad is no longer active",
                    error=ad["error"],
                )
                continue
            destinations = _outbound_links(page)
            destination = next((url for url in destinations if _official_url(url, spec)), None)
            if destination:
                ad["destinationUrl"] = destination
                ad["advertiserVerified"] = True
                ad["verification"] = (
                    "Exact advertiser name and observed official-domain destination"
                )
                verified.append(ad)
                _emit(
                    on_event,
                    "step_completed",
                    id=f"ad:{key}:{ad['id']}",
                    label=f"Found {spec['name']} ad destination",
                    url=destination,
                    adLibraryUrl=ad["adLibraryUrl"],
                )
            else:
                ad["advertiserVerified"] = False
                ad["error"] = (
                    "No observable official-domain destination; advertiser identity not verified"
                )
                _emit(
                    on_event,
                    "step_failed",
                    id=f"ad:{key}:{ad['id']}",
                    label="Ad destination unavailable",
                    error=ad["error"],
                )
        except Exception as error:
            ad["error"] = f"Ad snapshot unavailable: {type(error).__name__}"
            _emit(
                on_event,
                "step_failed",
                id=f"ad:{key}:{ad['id']}",
                label="Ad snapshot unavailable",
                error=ad["error"],
            )
        ad.pop("links", None)
    result["ads"] = ads
    if not verified:
        result["error"] = (
            "No verified active ad destination available. Add an Ads Library link or a reference landing."
        )
        return result
    # One relevant destination per competitor; three sampled ads remain visible as evidence.
    verified.sort(key=lambda ad: _relevance(ad, context), reverse=True)
    destinations = list(
        dict.fromkeys(canonical_destination(ad["destinationUrl"]) for ad in verified)
    )
    selected = next(
        ad for ad in verified if canonical_destination(ad["destinationUrl"]) == destinations[0]
    )
    destination = canonical_destination(selected["destinationUrl"])
    record = research_landing(
        destination,
        settings,
        browser=browser,
        reader=reader,
        refresh=config.refresh,
        include_journey=True,
        on_event=on_event,
    )
    result.update(record.model_dump(by_alias=True))
    result.update(
        sourceKind="ad_destination",
        status="partial" if record.error else "completed",
        selectedAdId=selected["id"],
        adLibraryUrl=selected["adLibraryUrl"],
        advertisedUrl=selected["destinationUrl"],
        uniqueDestinations=destinations,
        provenance=[
            {
                "kind": "ads_library",
                "url": selected["adLibraryUrl"],
                "adId": selected["id"],
                "pageId": selected.get("pageId"),
                "advertiser": selected["pageName"],
                "activeStatus": "active",
            },
            {"kind": "advertised_destination", "url": selected["destinationUrl"]},
            {"kind": "resolved_landing", "url": record.resolved_url or destination},
        ],
    )
    return result


def research_competitors(
    settings: Settings,
    config: ResearchConfig | dict | None,
    context: Any = None,
    *,
    browser: Any = None,
    reader: Any = None,
    on_event: Any = None,
    on_result: Any = None,
) -> list[dict]:
    """Research bounded public journeys; publish each result as soon as it is available."""
    config = (
        config
        if isinstance(config, ResearchConfig)
        else ResearchConfig.model_validate(config or {})
    )
    context = relevance_context(context)
    browser = browser or GstackBrowser(find_browse_binary(settings))
    results: list[dict] = []
    cache_dir = settings.research_dir / "competitors"
    cache_dir.mkdir(parents=True, exist_ok=True)
    for key in config.competitors:
        step = f"competitor:{key}"
        _emit(
            on_event,
            "step_started",
            id=step,
            label=f"Researching {COMPETITORS[key]['name']} ads",
            country=config.country,
        )
        signature = json.dumps(
            {
                "schema": SCHEMA_VERSION,
                "competitor": key,
                "country": config.country,
                "links": config.ad_library_urls,
                "context": context,
            },
            sort_keys=True,
            default=str,
        )
        path = cache_dir / (hashlib.sha256(signature.encode()).hexdigest()[:20] + ".json")
        result = None
        if path.is_file() and not config.refresh:
            try:
                cached = json.loads(path.read_text())
                if (
                    cached.get("status") == "completed"
                    and cached.get("schemaVersion") == SCHEMA_VERSION
                    and cache_is_fresh(cached.get("fetchedAt"), settings)
                ):
                    result = {**cached, "cacheHit": True}
            except (ValueError, OSError):
                pass
        if result is None:
            try:
                result = _research_one(settings, key, config, context, browser, reader, on_event)
            except Exception as error:
                result = {
                    "competitor": key,
                    "competitorName": COMPETITORS[key]["name"],
                    "status": "blocked",
                    "sourceKind": "ads_library",
                    "url": None,
                    "read": None,
                    "screenshots": [],
                    "journey": [],
                    "ads": [],
                    "fetchedAt": _now(settings),
                    "error": f"Research unavailable: {type(error).__name__}",
                }
            result["schemaVersion"] = SCHEMA_VERSION
            path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        results.append(result)
        if on_result:
            on_result(result)
        _emit(
            on_event,
            "step_completed" if result["status"] == "completed" else "step_failed",
            id=step,
            label=f"{COMPETITORS[key]['name']} research",
            status=result["status"],
            error=result.get("error"),
            cacheHit=result.get("cacheHit", False),
        )
    for index, url in enumerate(config.landing_urls):
        step = f"reference:{index}"
        _emit(
            on_event, "step_started", id=step, label="Reading supplied landing reference", url=url
        )
        record = research_landing(
            url,
            settings,
            browser=browser,
            reader=reader,
            refresh=config.refresh,
            include_journey=True,
            on_event=on_event,
        )
        result = {
            **record.model_dump(by_alias=True),
            "sourceKind": "reference",
            "competitor": None,
            "status": "partial" if record.error else "completed",
            "ads": [],
            "provenance": [{"kind": "user_reference", "url": url}],
            "warnings": ["Supplied reference; no competitor-ad destination relationship verified"],
        }
        results.append(result)
        if on_result:
            on_result(result)
        _emit(
            on_event,
            "step_failed" if record.error else "step_completed",
            id=step,
            label="Supplied landing reference",
            status=result["status"],
            error=record.error,
        )
    return results
