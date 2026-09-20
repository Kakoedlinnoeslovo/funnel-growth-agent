from __future__ import annotations

from pathlib import Path

import pytest

from funnel_growth_agent.config import load_settings, read_default_version


def _lab(tmp_path: Path, default: str = "v8") -> Path:
    lab = tmp_path / "lab"
    (lab / "funnels").mkdir(parents=True)
    (lab / "funnels" / "site.yaml").write_text(
        f"default_version: {default}\npublished_versions:\n  - v7\n  - v8\n", encoding="utf-8"
    )
    return lab


def test_base_version_follows_site_default(tmp_path: Path, monkeypatch) -> None:
    lab = _lab(tmp_path, "v8")
    monkeypatch.setenv("PRICING_LAB_DIR", str(lab))
    monkeypatch.setenv("BASE_VERSION", "")
    settings = load_settings()
    assert settings.base_version == "v8"
    assert settings.landing_path == lab / "funnels" / "v8" / "steps" / "landing.yaml"


def test_base_version_env_override_wins(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PRICING_LAB_DIR", str(_lab(tmp_path, "v8")))
    monkeypatch.setenv("BASE_VERSION", "v7")
    assert load_settings().base_version == "v7"


def test_missing_site_yaml_is_a_clear_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PRICING_LAB_DIR", str(tmp_path))
    monkeypatch.setenv("BASE_VERSION", "")
    with pytest.raises(FileNotFoundError, match="BASE_VERSION"):
        load_settings()


def test_site_without_default_version_is_rejected(tmp_path: Path) -> None:
    site = tmp_path / "site.yaml"
    site.write_text("published_versions:\n  - v8\n", encoding="utf-8")
    with pytest.raises(ValueError, match="default_version"):
        read_default_version(site)
