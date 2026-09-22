"""Fakes for the media pipeline, research tools and a full landing_redesign proposal."""

from __future__ import annotations

import copy
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

from funnel_growth_agent.media import JUDGE_WEIGHTS, MediaTools, ResolvedReference
from funnel_growth_agent.models import (
    CandidateScore,
    GroupStyle,
    JudgeResult,
    LandingProposal,
    TileStyleRead,
)
from funnel_growth_agent.research import LandingPatternRead

PLACEHOLDER = b"\x89PNG-placeholder-bytes"


class FakeRunner:
    """Pretends to be yt-dlp, ffprobe, ffmpeg and cwebp: records argv, writes placeholder output."""

    def __init__(self, *, duration: str = "89.0", fail: tuple[str, ...] = ()) -> None:
        self.calls: list[list[str]] = []
        self.duration = duration
        self.fail = fail

    def run(
        self, argv: list[str], *, timeout: float, cwd: Path | None = None
    ) -> CompletedProcess[str]:
        self.calls.append(list(argv))
        joined = " ".join(argv)
        for token in self.fail:
            if token in joined:
                return CompletedProcess(argv, 1, "", f"fake failure for {token}")
        tool = Path(argv[0]).name
        if tool == "ffprobe":
            return CompletedProcess(argv, 0, self.duration + "\n", "")
        if tool in {"yt-dlp", "cwebp"}:
            out = Path(argv[argv.index("-o") + 1])
        else:
            out = Path(argv[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        if tool == "cwebp":
            # Keep the input bytes visible so tests can tell which candidate was chosen.
            src = Path(argv[argv.index("-o") - 1])
            out.write_bytes(src.read_bytes() + b"|webp")
        else:
            out.write_bytes(PLACEHOLDER + tool.encode())
        return CompletedProcess(argv, 0, "", "")

    def calls_for(self, tool: str) -> list[list[str]]:
        return [argv for argv in self.calls if Path(argv[0]).name == tool]


class FakeImages:
    """Records every generate_candidates call and writes a distinct placeholder per candidate."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = fail

    def generate_candidates(
        self,
        *,
        model: str,
        prompts: list[str],
        references: list[ResolvedReference],
        seeds: list[int],
        dests: list[Path],
    ) -> list[Path]:
        self.calls.append(
            {
                "model": model,
                "prompts": list(prompts),
                "references": [ref.ref for ref in references],
                "reference_paths": [ref.path for ref in references],
                "seeds": list(seeds),
                "dests": list(dests),
            }
        )
        if self.fail:
            raise RuntimeError("fake image backend refused the prompt")
        for k, dest in enumerate(dests):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(PLACEHOLDER + f"cand{k}".encode())
        return list(dests)


class FakeJudge:
    def __init__(self, *, pick: int = 1, fail: bool = False) -> None:
        self.pick = pick
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def judge(
        self,
        *,
        stem: str,
        candidates: list[Path],
        references: list[ResolvedReference],
        prompt: str,
        palette: list[str],
        lettering_text: str | None,
        family: str,
    ) -> JudgeResult:
        self.calls.append(
            {
                "stem": stem,
                "candidates": list(candidates),
                "references": [ref.ref for ref in references],
                "prompt": prompt,
                "palette": list(palette),
                "lettering_text": lettering_text,
                "family": family,
            }
        )
        if self.fail:
            raise RuntimeError("fake judge exploded")
        pick = min(self.pick, len(candidates) - 1)
        scores = []
        for index in range(len(candidates)):
            value = 9 if index == pick else 6
            score = CandidateScore(
                index=index,
                house_style=value,
                subject_clarity=value,
                cleanliness=value,
                crop_safety=value,
                palette_adherence=value,
                notes="fake",
            )
            score.total = round(sum(w * value for w in JUDGE_WEIGHTS.values()), 2)
            scores.append(score)
        return JudgeResult(
            stem=stem, model="fake-judge", family=family, scores=scores, chosen=pick, reason="fake"
        )


def fake_media_tools(
    *,
    runner: FakeRunner | None = None,
    images: FakeImages | None | bool = True,
    judge: FakeJudge | None | bool = True,
) -> MediaTools:
    if images is True:
        images = FakeImages()
    elif images is False:
        images = None
    if judge is True:
        judge = FakeJudge()
    elif judge is False:
        judge = None
    return MediaTools(runner=runner or FakeRunner(), images=images, judge=judge)


class FakeBrowser:
    def __init__(self, *, available: bool = True) -> None:
        self._available = available
        self.shots: list[tuple[str, int, int, Path]] = []

    def available(self) -> bool:
        return self._available

    def screenshot(self, url: str, *, width: int, height: int, out: Path) -> None:
        self.shots.append((url, width, height, out))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(PLACEHOLDER)


class FakeReader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[Path]]] = []

    def read(self, url: str, shots: list[Path]) -> LandingPatternRead:
        self.calls.append((url, list(shots)))
        return LandingPatternRead.model_validate(
            {
                "heroHeadline": "Design anything",
                "primaryCta": "Start for free",
                "ctaAboveFold": True,
                "heroMedia": "video",
                "firstScreenSections": ["hero", "logos"],
                "proofElements": ["logo strip"],
                "notablePatterns": ["clip autoplays under the headline"],
                "imageryFormats": ["ugc-candid", "Screenshot"],
                "imageryStyle": ["phone-shot creator holding a poster"],
                "peopleShown": True,
            }
        )


class FakeStyleReader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, list[Path]]] = []

    def read(self, label: str, caption: str | None, images: list[Path]) -> GroupStyle:
        self.calls.append((label, caption, list(images)))
        tiles = [
            TileStyleRead(
                subject=f"{label} subject {index}",
                medium="photo" if label == "Photos" else "flat vector",
                palette=["#111111", "#C8F520"],
                background="flat solid",
                composition="one centred subject",
                has_text=False,
                format="studio-product" if label == "Photos" else "illustration",
            )
            for index in range(len(images))
        ]
        return GroupStyle(label=label, caption=caption, family=f"{label} family", tiles=tiles)


BRIEF_TILE: dict[str, Any] = {
    "group": "Vectors",
    "slot": 0,
    "brief": (
        "One strawberry split down the middle, left half a soft pixelated photo, right half "
        "the same strawberry as crisp flat vector shapes"
    ),
    "medium": "flat-vector",
    "palette": ["#C8F520", "#111111", "#FFFFFF"],
    "background": "a flat solid black canvas",
    "references": ["tile:Vectors:1"],
}

LEGACY_TILE: dict[str, Any] = {
    "group": "Vectors & typography",
    "slot": 0,
    "prompt": (
        "A bold vintage badge illustration of a mountain peak with pine trees, rendered as "
        "clean flat vector shapes in navy, amber and cream, no text, no gradients"
    ),
    "aspectRatio": "1:1",
    "route": "nano_banana_2_t2i__falai",
}

REDESIGN_PAYLOAD: dict[str, Any] = {
    "decision": "experiment",
    "experimentType": "landing_redesign",
    "problem": "The first screen sells generic creation while paid ads promise JPG to SVG.",
    "evidence": ["Proxy landing→CTA baseline is 11.9% from recraft-quiz v6."],
    "hypothesis": "A first screen built around vectorizing an image will lift landing_cta_rate.",
    "primaryMetric": "landing_cta_rate",
    "changes": {
        "copy": {
            "hero": {
                "headline": ["Drop a JPG.", "Get an editable SVG."],
                "subhead": "Recraft vectorizes it in seconds.",
                "ctaLabel": "Vectorize my image",
                "reassurance": "Free to use. No credit card required.",
            },
            "videoCta": {"ctaLabel": "Vectorize my image"},
            "finalCta": {"headline": "Ready to vectorize?", "ctaLabel": "Vectorize my image"},
            "inlineCta": {"ctaLabel": "Vectorize my image"},
        },
        "layout": {"layout": "video-first", "stickyCta": True},
        "composition": {
            "order": [
                "press-quotes",
                "logo-strip",
                "showcase",
                "inline-cta",
                "video-cta",
                "plan-preview",
                "final-cta",
            ],
            "omit": ["style-switcher", "inline-cta-2"],
        },
        "media": {
            "heroVideo": {
                "source": "youtube",
                "videoId": "z0r74lakHOM",
                "start": 3,
                "duration": 12,
                "label": "Recraft Vectorize turning a JPG logo into editable paths",
            },
            "showcase": [copy.deepcopy(BRIEF_TILE)],
        },
    },
    "otherIdeas": ["Copy-first with the clip under the button.", "Swap the showcase order."],
}


def redesign_payload(**changes: Any) -> dict[str, Any]:
    payload = copy.deepcopy(REDESIGN_PAYLOAD)
    payload["changes"].update(changes)
    return payload


def redesign_proposal(**changes: Any) -> LandingProposal:
    return LandingProposal.model_validate(redesign_payload(**changes))


class ScriptedModel:
    """Runs the named tools (with arguments) then returns a pre-built proposal."""

    def __init__(self, output: Any, tools: list[tuple[str, dict[str, Any]]] | None = None) -> None:
        self.output = output
        self.tools = tools or [("get_landing_cta_metrics", {})]
        self.results: dict[str, Any] = {}

    def complete(self, _messages: Any, _system: str, execute: Any) -> Any:
        for name, arguments in self.tools:
            self.results[name] = execute(name, arguments)
        return self.output
