"""Read landing→CTA slices from growth-loop report_data.json."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .models import Baseline, MetricsSlice

QUIZ_FUNNEL_IDS = ("recraft-quiz", "recraft-quiz-v7", "recraft-quiz-v7-a1")


class StaleReportError(RuntimeError):
    pass


def _parse_generated_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _report_kind(data: dict[str, Any], path: Path) -> str:
    kind = (data.get("period") or {}).get("kind")
    if kind in {"week", "day"}:
        return kind
    return "week" if path.parent.name.endswith("_week") else "day"


def _age_hours(data: dict[str, Any], path: Path, now: datetime) -> float:
    generated = _parse_generated_at(data.get("generated_at"))
    if generated is None:
        generated = datetime.fromtimestamp(path.stat().st_mtime)
    return max(0.0, (now - generated).total_seconds() / 3600)


def _iter_reports(reports_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    found: list[tuple[Path, dict[str, Any]]] = []
    if not reports_dir.is_dir():
        return found
    for path in sorted(reports_dir.glob("*/report_data.json")):
        found.append((path, json.loads(path.read_text(encoding="utf-8"))))
    return found


def load_latest_reports(settings: Settings) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    weekly: tuple[datetime, dict[str, Any]] | None = None
    daily: tuple[datetime, dict[str, Any]] | None = None
    for path, data in _iter_reports(settings.reports_dir):
        generated = _parse_generated_at(data.get("generated_at")) or datetime.fromtimestamp(
            path.stat().st_mtime
        )
        data = {**data, "_path": str(path)}
        kind = _report_kind(data, path)
        if kind == "week":
            if weekly is None or generated > weekly[0]:
                weekly = (generated, data)
        elif daily is None or generated > daily[0]:
            daily = (generated, data)
    return (weekly[1] if weekly else None, daily[1] if daily else None)


def baseline_from_report(
    report: dict[str, Any],
    *,
    base_version: str,
    source: str = "growth-loop",
) -> Baseline | None:
    flows = list(report.get("ph_flows") or [])
    if not flows:
        return None

    def score(flow: dict[str, Any]) -> tuple[int, int]:
        quiz = 1 if flow.get("funnel_id") in QUIZ_FUNNEL_IDS or str(flow.get("funnel_id", "")).startswith(
            "recraft-quiz"
        ) else 0
        return (quiz, int(flow.get("landing_people") or 0))

    flow = max(flows, key=score)
    landing_row = next((row for row in flow.get("rows") or [] if row.get("node_id") == "landing"), None)
    landing_people = int(flow.get("landing_people") or (landing_row or {}).get("viewed") or 0)
    cta_row = next(
        (
            row
            for row in flow.get("rows") or []
            if row.get("node_id") in {"making", "cta"} and row.get("node_id") != "landing"
        ),
        None,
    )
    rate = landing_row.get("continue_rate") if landing_row else None
    if cta_row and cta_row.get("viewed") is not None:
        cta_people = int(cta_row["viewed"])
        if not rate and landing_people:
            rate = cta_people / landing_people
    elif rate is not None and landing_people:
        cta_people = int(round(float(rate) * landing_people))
    else:
        cta_people = 0
        rate = 0.0
    version = str(flow.get("funnel_version") or "")
    period = report.get("period") or {}
    current = period.get("current") or {}
    period_label = current.get("end") or report.get("run_date")
    return Baseline(
        source=source,
        funnel_id=str(flow.get("funnel_id") or ""),
        version=version,
        funnel_version=version,
        is_proxy=version != base_version,
        period=str(period_label) if period_label else None,
        landing_people=landing_people,
        cta_people=cta_people,
        cta_rate=float(rate or 0.0),
    )


def get_landing_cta_metrics(settings: Settings) -> MetricsSlice:
    weekly, daily = load_latest_reports(settings)
    if weekly is None and daily is None:
        raise StaleReportError(
            "Cannot generate proposal.\n\n"
            "No analytics report was found.\n\n"
            "Run:\n\n"
            "uv run growth-loop daily"
        )
    primary = weekly or daily
    assert primary is not None
    path = Path(str(primary.get("_path") or settings.reports_dir))
    now = settings.clock()
    age = _age_hours(primary, path, now)
    baseline = baseline_from_report(primary, base_version=settings.base_version)
    if baseline is None:
        raise StaleReportError(
            "Cannot generate proposal.\n\nLatest analytics report has no funnel flows."
        )
    fresh = age <= settings.max_report_age_hours and baseline.landing_people >= settings.min_landing_people
    if age > settings.max_report_age_hours:
        raise StaleReportError(
            "Cannot generate proposal.\n\n"
            "Latest analytics report is stale.\n\n"
            "Run:\n\n"
            "uv run growth-loop daily"
        )
    if baseline.landing_people < settings.min_landing_people:
        raise StaleReportError(
            "Cannot generate proposal.\n\n"
            f"Latest analytics report has {baseline.landing_people} landing people "
            f"(MIN_LANDING_PEOPLE={settings.min_landing_people}).\n\n"
            "Run:\n\n"
            "uv run growth-loop daily"
        )
    daily_baseline = None
    if weekly is not None and daily is not None:
        daily_baseline = baseline_from_report(daily, base_version=settings.base_version)
    return MetricsSlice(
        generated_at=str(primary.get("generated_at") or ""),
        age_hours=round(age, 2),
        fresh=fresh,
        report_kind=_report_kind(primary, path),
        report_path=str(path),
        baseline=baseline,
        daily=daily_baseline,
    )
