from __future__ import annotations

from funnel_growth_agent.agent import ToolLoop
from funnel_growth_agent.showcase_style import read_showcase_style, style_cache_path
from redesign_helpers import FakeStyleReader


def test_style_read_covers_every_group_with_reference_ids_and_caches(settings) -> None:
    reader = FakeStyleReader()
    style = read_showcase_style(settings, reader=reader)
    assert style.error is None
    assert [group.label for group in style.groups] == ["Vectors", "Photos"]
    assert style.groups[0].caption == "Logos and icons."
    assert [tile.reference_id for tile in style.groups[0].tiles] == [
        "tile:Vectors:0",
        "tile:Vectors:1",
        "tile:Vectors:2",
    ]
    assert style.groups[1].family == "Photos family"
    assert len(reader.calls) == 2 and [p.name for p in reader.calls[0][2]] == [
        "vector-1.webp",
        "vector-2.webp",
        "vector-3.webp",
    ]
    assert style_cache_path(settings).is_file()
    again = read_showcase_style(settings, reader=reader)
    assert len(reader.calls) == 2 and again.groups == style.groups
    read_showcase_style(settings, reader=reader, refresh=True)
    assert len(reader.calls) == 4


def test_style_read_degrades_without_gemini_or_images(settings) -> None:
    record = read_showcase_style(settings)
    assert record.read_at and "GEMINI_API_KEY" in (record.error or "")
    (settings.pricing_lab_dir / "funnels" / "v7" / "assets" / "showcase" / "photo-1.webp").unlink()
    record = read_showcase_style(settings, reader=FakeStyleReader())
    assert "missing on disk" in (record.error or "")


def test_tool_loop_exposes_get_showcase_style(settings) -> None:
    loop = ToolLoop(settings, ranked=[], analyses=[], style_reader=FakeStyleReader())
    result = loop.execute("get_showcase_style", {})
    assert result["baseVersion"] == "v7"
    assert result["groups"][0]["tiles"][1]["referenceId"] == "tile:Vectors:1"
