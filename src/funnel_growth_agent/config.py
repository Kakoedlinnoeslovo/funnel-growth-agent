"""Environment and path settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_DIR = PACKAGE_DIR.parent.parent


def _path(value: str | None, default: Path) -> Path:
    return Path(value).expanduser().resolve() if value else default.resolve()


@dataclass
class Settings:
    growth_loop_dir: Path
    pricing_lab_dir: Path
    data_dir: Path
    base_version: str = "v7"
    max_report_age_hours: float = 48
    min_landing_people: int = 100
    min_creative_spend: float = 5.0
    min_creative_clicks: int = 10
    min_creative_impressions: int = 100
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-6"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    analysis_schema_version: int = 1
    now: str | None = None

    @property
    def reports_dir(self) -> Path:
        return self.growth_loop_dir / "data" / "output" / "reports"

    @property
    def memory_path(self) -> Path:
        return self.data_dir / "memory" / "runs.jsonl"

    @property
    def analysis_dir(self) -> Path:
        return self.data_dir / "creative_analysis"

    @property
    def landing_path(self) -> Path:
        return self.pricing_lab_dir / "funnels" / self.base_version / "steps" / "landing.yaml"

    @property
    def funnel_path(self) -> Path:
        return self.pricing_lab_dir / "funnels" / self.base_version / "funnel.yaml"

    @property
    def site_path(self) -> Path:
        return self.pricing_lab_dir / "funnels" / "site.yaml"

    @property
    def instructions_path(self) -> Path:
        return PACKAGE_DIR / "instructions.md"

    @property
    def playbook_path(self) -> Path:
        return PACKAGE_DIR / "playbook.md"

    def clock(self) -> datetime:
        if self.now:
            return datetime.fromisoformat(self.now)
        return datetime.now()


def load_settings() -> Settings:
    load_dotenv(REPO_DIR / ".env")
    return Settings(
        growth_loop_dir=_path(os.getenv("GROWTH_LOOP_DIR"), REPO_DIR.parent / "growth-loop"),
        pricing_lab_dir=_path(os.getenv("PRICING_LAB_DIR"), REPO_DIR.parent / "recraft-pricing-lab"),
        data_dir=_path(os.getenv("FUNNEL_GROWTH_DATA_DIR"), REPO_DIR / "data"),
        base_version=os.getenv("BASE_VERSION", "v7"),
        max_report_age_hours=float(os.getenv("MAX_REPORT_AGE_HOURS", "48")),
        min_landing_people=int(os.getenv("MIN_LANDING_PEOPLE", "100")),
        min_creative_spend=float(os.getenv("MIN_CREATIVE_SPEND", "5")),
        min_creative_clicks=int(os.getenv("MIN_CREATIVE_CLICKS", "10")),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    )
