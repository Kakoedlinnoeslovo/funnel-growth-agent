"""Produce the hero clip and showcase tiles a MediaPlan asks for. Every artifact is cached by
its content key, so a retry after a failed validate re-encodes nothing and calls no API.

Tiles: the house-style prompt (tile_prompt.py) plus reference images go to Nano Banana 2 by default,
N candidates are generated in parallel, a Gemini vision judge picks one, and the chosen tile
becomes a reference for the next slot of its group."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ruamel.yaml import YAML

from .config import Settings
from .events import EmitData, emit_to
from .landing_diff import MAX_ASSET_BYTES, ApplyError, ProducedFiles
from .landing_patch import ProducedMedia
from .models import (
    ROUTE_TO_MODEL,
    CandidateScore,
    HeroVideoPlan,
    JudgeResult,
    MediaPlan,
    ShowcaseImagePlan,
)
from .sources import creative_image_path, creative_video_path
from .tile_prompt import (
    ReferenceRole,
    build_tile_prompt,
    family_of_medium,
    reference_kind,
    style_of,
    variant_prompts,
)

Emit = Callable[[str, str], None]

WIDTHS = (1280, 720)
YTDLP_FORMAT = "bv*[ext=mp4][height<=1080]+ba/b[ext=mp4][height<=1080]/b"
GLAM_MCP_URL = "https://mcp.glam.ai/mcp"

TILE_MODELS = {"nano_banana_pro": "gemini-3-pro-image", "nano_banana_2": "gemini-3.1-flash-image"}
MODEL_TO_ROUTE = {model: route for route, model in ROUTE_TO_MODEL.items()}
TILE_ASPECT = "16:9"
TILE_IMAGE_SIZE = "2K"
MAX_PARALLEL = 4
MAX_STYLE_REFS = 3
NO_IMAGE_BACKEND = "media: no image backend: set GEMINI_API_KEY (Nano Banana) or GLAM_API_KEY"
JUDGE_WEIGHTS = {
    "house_style": 0.30,
    "subject_clarity": 0.20,
    "cleanliness": 0.20,
    "crop_safety": 0.15,
    "palette_adherence": 0.15,
}
# Phone photos live or die on hands, faces and invented text; a natural palette matters less.
JUDGE_WEIGHTS_BY_FAMILY: dict[str, dict[str, float]] = {
    "default": JUDGE_WEIGHTS,
    "ugc": {
        "house_style": 0.30,
        "subject_clarity": 0.20,
        "cleanliness": 0.30,
        "crop_safety": 0.15,
        "palette_adherence": 0.05,
    },
    "editorial": {
        "house_style": 0.30,
        "subject_clarity": 0.20,
        "cleanliness": 0.25,
        "crop_safety": 0.15,
        "palette_adherence": 0.10,
    },
    "screen": {
        "house_style": 0.25,
        "subject_clarity": 0.20,
        "cleanliness": 0.30,
        "crop_safety": 0.15,
        "palette_adherence": 0.10,
    },
}
CLEANLINESS_VETO = 4

_safe_yaml = YAML(typ="safe")


class Runner(Protocol):
    def run(
        self, argv: list[str], *, timeout: float, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]: ...


class SubprocessRunner:
    def run(
        self, argv: list[str], *, timeout: float, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            argv, capture_output=True, text=True, check=False, timeout=timeout, cwd=cwd
        )


# ---------------------------------------------------------------------------------------
# References and specs


@dataclass(frozen=True)
class ResolvedReference:
    ref: str
    kind: str  # tile | creative | previous
    path: Path
    fingerprint: str


def image_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    return "image/jpeg"


def _fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def base_showcase_groups(
    settings: Settings, base_version: str
) -> dict[str, tuple[str | None, list[str]]]:
    """Showcase group label -> (caption, image rels) of the base landing."""
    landing = settings.pricing_lab_dir / "funnels" / base_version / "steps" / "landing.yaml"
    if not landing.is_file():
        return {}
    doc = _safe_yaml.load(landing.read_text(encoding="utf-8")) or {}
    from .landing import landing_image_targets

    groups = landing_image_targets(((doc.get("props") or {}).get("sections")) or [])
    return {
        label: (caption, [str(container[key]) for container, key in targets])
        for label, (caption, targets) in groups.items()
    }


def resolve_landing_asset(settings: Settings, version: str, reference: str) -> Path:
    root = settings.pricing_lab_dir / "funnels"
    if reference.startswith("_shared/"):
        path = root / reference
    elif "/" not in reference and "." not in reference:
        path = root / "_shared/assets/display" / f"{reference}.webp"
    else:
        path = root / version / reference
    if not path.resolve().is_relative_to(root.resolve()):
        raise ApplyError("Landing asset escapes the pricing lab")
    return path


def resolve_references(
    plan: ShowcaseImagePlan,
    settings: Settings,
    base_version: str,
    chosen_previous: Path | None,
) -> list[ResolvedReference]:
    if len(plan.references) > MAX_STYLE_REFS:
        raise ApplyError(f"media: at most {MAX_STYLE_REFS} references per tile")
    groups = base_showcase_groups(settings, base_version)
    out: list[ResolvedReference] = []
    for ref in plan.references:
        kind = reference_kind(ref)
        if kind == "previous":
            if chosen_previous is None:
                raise ApplyError(
                    f"media: {plan.group!r} slot {plan.slot}: 'previous' has no chosen tile yet"
                )
            path = chosen_previous
        elif kind == "video":
            from .sources import creative_video_path
            from .video_analysis import extract_frame, probe_video, video_fingerprint

            _, creative_id, seconds = ref.split(":")
            video = creative_video_path(settings, creative_id)
            duration, _ = probe_video(video)
            timestamp = float(seconds)
            if not 0 <= timestamp < duration:
                raise ApplyError("Video reference timestamp is outside the source clip")
            key = video_fingerprint(video, settings.gemini_model, settings.analysis_schema_version)
            path = settings.analysis_dir / "video" / key / f"reference-{timestamp:.3f}.jpg"
            if not path.is_file():
                extract_frame(video, timestamp, path)
        elif kind == "creative":
            creative_id = ref.split(":", 1)[1]
            path = creative_image_path(settings, creative_id)
            if not path.is_file():
                raise ApplyError(f"media: creative {creative_id} has no local jpg at {path}")
        else:
            _, label, index_text = ref.split(":", 2)
            if label not in groups:
                raise ApplyError(f"media: reference {ref!r}: no showcase group {label!r}")
            images = groups[label][1]
            index = int(index_text)
            if index >= len(images):
                raise ApplyError(f"media: reference {ref!r}: group {label!r} has no image {index}")
            path = resolve_landing_asset(settings, base_version, images[index])
            if not path.is_file():
                raise ApplyError(f"media: reference {ref!r}: {path} is missing")
        out.append(ResolvedReference(ref=ref, kind=kind, path=path, fingerprint=_fingerprint(path)))
    return out


@dataclass(frozen=True)
class TileSpec:
    plan: ShowcaseImagePlan
    model_name: str
    prompt: str
    references: list[ResolvedReference]
    stem: str


def tile_stem(model_name: str, prompt: str, references: list[ResolvedReference]) -> str:
    key = "|".join([model_name, prompt, *[ref.fingerprint for ref in references]]).encode()
    return f"gen-{hashlib.sha1(key).hexdigest()[:12]}"


def tile_seeds(stem: str, n: int) -> list[int]:
    return [
        int(hashlib.sha1(f"{stem}:{k}".encode()).hexdigest()[:8], 16) % (2**31) for k in range(n)
    ]


def build_tile_spec(
    plan: ShowcaseImagePlan,
    settings: Settings,
    base_version: str,
    chosen_previous: Path | None = None,
) -> TileSpec:
    references = resolve_references(plan, settings, base_version, chosen_previous)
    caption = base_showcase_groups(settings, base_version).get(plan.group, (None, []))[0]
    roles = [ReferenceRole(kind=ref.kind, ref=ref.ref) for ref in references]  # type: ignore[arg-type]
    prompt = build_tile_prompt(plan, caption, roles)
    model_name = TILE_MODELS[plan.tile_model]
    return TileSpec(
        plan=plan,
        model_name=model_name,
        prompt=prompt,
        references=references,
        stem=tile_stem(model_name, prompt, references),
    )


# ---------------------------------------------------------------------------------------
# Image backends


class ImageClient(Protocol):
    def generate_candidates(
        self,
        *,
        model: str,
        prompts: list[str],
        references: list[ResolvedReference],
        seeds: list[int],
        dests: list[Path],
        aspect_ratio: str = TILE_ASPECT,
    ) -> list[Path]: ...


class GeminiImageClient:
    """Nano Banana through the Gemini API: references first, then the prompt; N candidates in
    parallel with distinct seeds."""

    RETRY_DELAYS = (2.0, 6.0, 14.0)

    def __init__(self, api_key: str, *, max_parallel: int = MAX_PARALLEL) -> None:
        self.api_key = api_key
        self.max_parallel = max_parallel

    def generate_candidates(
        self,
        *,
        model: str,
        prompts: list[str],
        references: list[ResolvedReference],
        seeds: list[int],
        dests: list[Path],
        aspect_ratio: str = TILE_ASPECT,
    ) -> list[Path]:
        return asyncio.run(self._gather(model, prompts, references, seeds, dests, aspect_ratio))

    async def _gather(
        self,
        model: str,
        prompts: list[str],
        references: list[ResolvedReference],
        seeds: list[int],
        dests: list[Path],
        aspect_ratio: str = TILE_ASPECT,
    ) -> list[Path]:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        ref_parts = [
            types.Part.from_bytes(data=ref.path.read_bytes(), mime_type=image_mime(ref.path))
            for ref in references
        ]
        semaphore = asyncio.Semaphore(self.max_parallel)

        async def one(prompt: str, seed: int, dest: Path) -> Path:
            config = types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(
                    aspect_ratio=aspect_ratio, image_size=TILE_IMAGE_SIZE
                ),
                seed=seed,
                temperature=1.0,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            )
            contents = [*ref_parts, types.Part.from_text(text=prompt)]
            last: Exception | None = None
            for attempt in range(len(self.RETRY_DELAYS) + 1):
                async with semaphore:
                    try:
                        response = await client.aio.models.generate_content(
                            model=model, contents=contents, config=config
                        )
                        break
                    except Exception as error:  # noqa: BLE001 - retried below
                        last = error
                        code = getattr(error, "code", None) or getattr(error, "status_code", None)
                        if attempt < len(self.RETRY_DELAYS) and code in {429, 503}:
                            await asyncio.sleep(self.RETRY_DELAYS[attempt])
                            continue
                        raise
            else:  # pragma: no cover - loop always breaks or raises
                raise RuntimeError(str(last))
            for candidate in response.candidates or []:
                parts = (candidate.content.parts if candidate.content else None) or []
                for part in parts:
                    blob = getattr(part, "inline_data", None)
                    if blob is not None and blob.data:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        tmp = dest.with_name(dest.name + ".tmp")
                        tmp.write_bytes(blob.data)
                        os.replace(tmp, dest)
                        return dest
            raise RuntimeError(f"{model} returned no image for prompt {prompt[:60]!r}")

        return list(
            await asyncio.gather(
                *(one(prompt, seed, dest) for prompt, seed, dest in zip(prompts, seeds, dests))
            )
        )


class HttpGlamClient:
    """glam.ai over its MCP endpoint (JSON-RPC, Bearer glm_* key). Text prompts only."""

    def __init__(self, api_key: str, *, url: str = GLAM_MCP_URL, timeout: float = 120.0) -> None:
        self.api_key = api_key
        self.url = url
        self.timeout = timeout
        self.session_id: str | None = None
        self.initialized = False
        self._id = 1

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _post(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        import httpx

        response = httpx.post(self.url, json=payload, headers=self._headers(), timeout=self.timeout)
        response.raise_for_status()
        session = response.headers.get("mcp-session-id")
        if session:
            self.session_id = session
        if not response.content:
            return None
        if "text/event-stream" in response.headers.get("content-type", ""):
            data: str | None = None
            for line in response.text.splitlines():
                if line.startswith("data:"):
                    data = line[5:].strip()
            return json.loads(data) if data else None
        return response.json()

    def _initialize(self) -> None:
        if self.initialized:
            return
        self._post(
            {
                "jsonrpc": "2.0",
                "id": self._id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "funnel-growth-agent", "version": "0.1.0"},
                },
            }
        )
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.initialized = True

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._initialize()
        self._id += 1
        reply = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._id,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            }
        )
        if not reply:
            raise RuntimeError(f"glam {tool}: empty reply")
        if "error" in reply:
            raise RuntimeError(f"glam {tool}: {reply['error']}")
        result = reply.get("result") or {}
        content = result.get("content") or []
        text = next((item.get("text") for item in content if item.get("type") == "text"), None)
        if result.get("isError"):
            raise RuntimeError(f"glam {tool}: {text or result}")
        if text is None:
            structured = result.get("structuredContent")
            if isinstance(structured, dict):
                return structured
            raise RuntimeError(f"glam {tool}: no text content in reply")
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"glam {tool}: non-JSON reply: {text[:200]}") from error

    def _generate_one(self, *, route_id: str, prompt: str, dest: Path) -> Path:
        reply = self.call(
            "generate_media",
            {
                "route_id": route_id,
                "prompt": prompt,
                "overrides": {"aspect_ratio": TILE_ASPECT, "num_images": 1, "output_format": "png"},
                "wait_seconds": 60,
            },
        )
        urls = list(reply.get("media_urls") or [])
        job_id = reply.get("job_id")
        attempts = 0
        while not urls and job_id and attempts < 4:
            attempts += 1
            reply = self.call("check_job", {"job_id": job_id, "wait_seconds": 30})
            if reply.get("status") == "FAILED":
                raise RuntimeError(f"glam job {job_id} failed: {reply.get('fail_reason')}")
            urls = list(reply.get("media_urls") or [])
        if not urls:
            raise RuntimeError(
                f"glam: no media for job {job_id}: {reply.get('status')} {reply.get('fail_reason') or ''}"
            )
        import httpx

        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        with httpx.stream("GET", urls[0], timeout=self.timeout, follow_redirects=True) as response:
            response.raise_for_status()
            with tmp.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
        os.replace(tmp, dest)
        return dest

    def generate_candidates(
        self,
        *,
        model: str,
        prompts: list[str],
        references: list[ResolvedReference],
        seeds: list[int],
        dests: list[Path],
        aspect_ratio: str = TILE_ASPECT,
    ) -> list[Path]:
        if aspect_ratio != TILE_ASPECT:
            raise RuntimeError(
                "glam backend currently supports only 16:9; use Gemini for other ratios"
            )
        if references:
            raise RuntimeError("glam backend takes no reference images; unset GLAM_API_KEY")
        route = MODEL_TO_ROUTE.get(
            next((name for name, gemini in TILE_MODELS.items() if gemini == model), ""),
            "nano_banana_2_t2i__falai",
        )
        return [
            self._generate_one(route_id=route, prompt=prompt, dest=dest)
            for prompt, dest in zip(prompts, dests)
        ]


# ---------------------------------------------------------------------------------------
# Judge


JUDGE_RUBRIC: dict[str, str] = {
    "default": (
        "- houseStyle: matches the rendering technique, saturation, lighting and finish of the "
        "reference tiles\n"
        "- subjectClarity: exactly one hero subject, readable at thumbnail size, not a collage\n"
        "- cleanliness: no stray letters, text, watermarks, logos, UI or rendering artefacts; any "
        "unintended text scores 3 or lower\n"
        "- cropSafety: the subject and everything important sit inside the central 4:3 area; the "
        "outer eighth on the left and right is cut off\n"
        "- paletteAdherence: uses only {palette} with one dominant colour\n"
    ),
    "ugc": (
        "- houseStyle: reads as an authentic phone photo of a real person or place: candid, "
        "available light, true skin texture, believable clutter, and it sits next to the "
        "reference tiles in subject scale and colour temperature; a polished studio render or "
        "an obviously posed stock photo scores 3 or lower\n"
        "- subjectClarity: one person and one object are the point of the photo, readable at "
        "thumbnail size\n"
        "- cleanliness: no AI artefacts: plastic or waxy skin, extra or fused fingers, warped "
        "hands, melted objects, garbled or invented text, watermarks, logos, phone UI or device "
        "frames; real-world clutter is fine and not penalised; any readable invented text or a "
        "deformed hand scores 3 or lower\n"
        "- cropSafety: the whole person and the object sit inside the central 4:3 area; the "
        "outer eighth on the left and right is cut off\n"
        "- paletteAdherence: {palette} leads through clothing, object and light; other natural "
        "colours are allowed\n"
    ),
    "editorial": (
        "- houseStyle: reads as a magazine-grade lifestyle photograph in a real place with "
        "natural light, and it sits next to the reference tiles in subject scale and colour "
        "temperature; a flat studio sweep or a phone snapshot scores 3 or lower\n"
        "- subjectClarity: exactly one hero subject in a real setting, readable at thumbnail "
        "size\n"
        "- cleanliness: no AI artefacts: plastic skin, extra or fused fingers, warped hands, "
        "melted objects, garbled or invented text, watermarks, logos or UI; believable props are "
        "fine; any readable invented text or a deformed hand scores 3 or lower\n"
        "- cropSafety: the subject and everything important sit inside the central 4:3 area; the "
        "outer eighth on the left and right is cut off\n"
        "- paletteAdherence: {palette} leads through clothing, object and light; other natural "
        "colours are allowed\n"
    ),
    "screen": (
        "- houseStyle: a real phone or laptop in a real scene whose screen shows only the "
        "finished artwork, and it sits next to the reference tiles in subject scale and colour "
        "temperature\n"
        "- subjectClarity: one device and the artwork on it are the point of the photo, readable "
        "at thumbnail size\n"
        "- cleanliness: the device frame and soft generic interface edges are intended; any "
        "readable invented interface text, buttons, menus, logos or watermarks score 3 or "
        "lower, as do warped hands or melted devices\n"
        "- cropSafety: the device and the hands sit inside the central 4:3 area; the outer "
        "eighth on the left and right is cut off\n"
        "- paletteAdherence: {palette} leads through the artwork, the device and the light; "
        "other natural colours are allowed\n"
    ),
}

JUDGE_PROMPT = (
    "You are judging generated showcase tiles for one gallery of a marketing landing page.\n"
    "Images 1-{ref_count} are REFERENCE images: real tiles from the same gallery (house style) "
    "or a winning ad.\n"
    "Images {first_cand}-{last_cand} are CANDIDATES generated from this prompt:\n"
    '"{prompt}"\n'
    "Score every candidate 0-10 on each criterion:\n"
    "{rubric}"
    "Also score briefRelevance 0-10: does this show the intended message and fit the asset role? Penalize misleading product UI or generated examples presented as factual screenshots. Inspect desktop legibility and mobile crop safety.\n"
    "Intended text: {lettering}. Count intended text as correct, not stray.\n"
    'Return JSON only: {{"candidates":[{{"index":1,"houseStyle":0,"subjectClarity":0,'
    '"cleanliness":0,"cropSafety":0,"paletteAdherence":0,"briefRelevance":0,"notes":"one sentence"}}],'
    '"best":1,"reason":"one sentence"}}\n'
    "Index candidates from 1 in the order shown. Do not give design advice."
)


def judge_family(medium: str | None) -> str:
    """The rubric and weights a tile is judged by: its visual family when it has its own,
    else the studio/graphic default."""
    family = family_of_medium(medium)
    return family if family in JUDGE_RUBRIC else "default"


class TileJudge(Protocol):
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
    ) -> JudgeResult: ...


def score_candidates(
    raw: list[dict[str, Any]], n: int, family: str = "default"
) -> tuple[list[CandidateScore], int]:
    """Local, deterministic totals from the judge's per-criterion scores; the judge's own
    `best` is never trusted. Candidates with stray text (or, for photo families, AI artefacts)
    are vetoed unless all are."""
    weights = JUDGE_WEIGHTS_BY_FAMILY.get(family, JUDGE_WEIGHTS)

    def clamp(row: dict[str, Any], key: str) -> int:
        try:
            return max(0, min(10, int(round(float(row.get(key, 0))))))
        except (TypeError, ValueError):
            return 0

    scores: list[CandidateScore] = []
    for k in range(n):
        row = next((r for r in raw if isinstance(r, dict) and _as_int(r.get("index")) == k + 1), {})
        score = CandidateScore(
            index=k,
            house_style=clamp(row, "houseStyle"),
            subject_clarity=clamp(row, "subjectClarity"),
            cleanliness=clamp(row, "cleanliness"),
            crop_safety=clamp(row, "cropSafety"),
            palette_adherence=clamp(row, "paletteAdherence"),
            brief_relevance=clamp(row, "briefRelevance") if "briefRelevance" in row else None,
            notes=str(row.get("notes") or ""),
        )
        score.total = round(
            sum(weight * getattr(score, attr) for attr, weight in weights.items()), 2
        )
        if score.brief_relevance is not None:
            score.total = round(0.8 * score.total + 0.2 * score.brief_relevance, 2)
        scores.append(score)
    eligible = [s for s in scores if s.cleanliness >= CLEANLINESS_VETO] or scores
    chosen = max(eligible, key=lambda s: (s.total, -s.index)).index if eligible else 0
    return scores, chosen


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def candidate_contexts(path: Path) -> list[tuple[str, Path]]:
    """Render the actual contain/crop rules at desktop and phone display sizes."""
    from .video_analysis import run_media

    result = []
    for label, width, height, mode in (
        ("Desktop hero · contain", 620, 349, "contain"),
        ("Phone gallery · center crop", 350, 263, "cover"),
    ):
        suffix = "desktop" if mode == "contain" else "phone"
        dest = path.with_name(path.stem + f"-{suffix}-context.jpg")
        transform = f"scale={width}:{height}:force_original_aspect_ratio=" + (
            "decrease" if mode == "contain" else "increase"
        )
        transform += (
            f",pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0xf5f6f2"
            if mode == "contain"
            else f",crop={width}:{height}"
        )
        run_media(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(path),
                "-vf",
                transform,
                "-frames:v",
                "1",
                str(dest),
            ]
        )
        result.append((label, dest))
    return result


class GeminiTileJudge:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

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
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.settings.gemini_api_key)
        parts: list[Any] = []
        for ref in references:
            parts.append(
                types.Part.from_bytes(data=ref.path.read_bytes(), mime_type=image_mime(ref.path))
            )
        for candidate in candidates:
            parts.append(
                types.Part.from_bytes(data=candidate.read_bytes(), mime_type=image_mime(candidate))
            )
        for index, candidate in enumerate(candidates, 1):
            for label, context in candidate_contexts(candidate):
                parts.extend(
                    [
                        types.Part.from_text(
                            text=f"Candidate {index} · {label}. Simulated placement, not a product screenshot."
                        ),
                        types.Part.from_bytes(data=context.read_bytes(), mime_type="image/jpeg"),
                    ]
                )
        palette_text = ", ".join(palette) if palette else "the prompt's colours"
        rubric = JUDGE_RUBRIC.get(family, JUDGE_RUBRIC["default"]).format(palette=palette_text)
        text = JUDGE_PROMPT.format(
            ref_count=len(references),
            first_cand=len(references) + 1,
            last_cand=len(references) + len(candidates),
            prompt=prompt,
            rubric=rubric,
            lettering=f'"{lettering_text}"' if lettering_text else "none",
        )
        parts.append(types.Part.from_text(text=text))
        response = client.models.generate_content(
            model=self.settings.gemini_model,
            contents=parts,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )
        data = json.loads(response.text or "{}")
        scores, chosen = score_candidates(
            list(data.get("candidates") or []), len(candidates), family
        )
        return JudgeResult(
            stem=stem,
            model=self.settings.gemini_model,
            family=family,
            scores=scores,
            chosen=chosen,
            reason=str(data.get("reason") or ""),
        )


# ---------------------------------------------------------------------------------------
# Tools


@dataclass
class MediaTools:
    runner: Runner
    images: ImageClient | None = None
    judge: TileJudge | None = None
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    ytdlp: str = "yt-dlp"
    cwebp: str = "cwebp"


def default_image_client(settings: Settings) -> ImageClient | None:
    """glam.ai when a key is set, otherwise Nano Banana through the Gemini key."""
    if settings.glam_api_key:
        return HttpGlamClient(settings.glam_api_key)
    if settings.gemini_api_key:
        return GeminiImageClient(settings.gemini_api_key)
    return None


def default_media_tools(settings: Settings) -> MediaTools:
    judge = GeminiTileJudge(settings) if settings.gemini_api_key else None
    return MediaTools(runner=SubprocessRunner(), images=default_image_client(settings), judge=judge)


def _run(
    tools: MediaTools, argv: list[str], *, timeout: float, what: str
) -> subprocess.CompletedProcess[str]:
    try:
        completed = tools.runner.run(argv, timeout=timeout)
    except FileNotFoundError as error:
        raise ApplyError(f"media: {what}: {argv[0]} is not installed") from error
    except subprocess.TimeoutExpired as error:
        raise ApplyError(f"media: {what} timed out after {timeout:.0f}s") from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[-400:]
        raise ApplyError(f"media: {what} failed: {detail}")
    return completed


# ---------------------------------------------------------------------------------------
# Hero clip


def youtube_cache_path(settings: Settings, video_id: str) -> Path:
    return settings.media_cache_dir / "youtube" / f"{video_id}.mp4"


def fetch_youtube(video_id: str, settings: Settings, tools: MediaTools, emit: Emit) -> Path:
    dest = youtube_cache_path(settings, video_id)
    if dest.is_file():
        emit("media", f"youtube {video_id}: cached")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(f"{video_id}.part.mp4")
    emit("media", f"downloading youtube {video_id}")
    _run(
        tools,
        [
            tools.ytdlp,
            "--no-playlist",
            "--no-progress",
            # YouTube needs a JS runtime plus yt-dlp's challenge solver; node is on PATH here.
            "--js-runtimes",
            "node",
            "--remote-components",
            "ejs:github",
            "-f",
            YTDLP_FORMAT,
            "--merge-output-format",
            "mp4",
            "-o",
            str(part),
            f"https://www.youtube.com/watch?v={video_id}",
        ],
        timeout=300,
        what=f"yt-dlp {video_id}",
    )
    if not part.is_file():
        raise ApplyError(f"media: yt-dlp {video_id} produced no file")
    os.replace(part, dest)
    emit("media", f"downloaded youtube {video_id}")
    return dest


def creative_source(creative_id: str, settings: Settings) -> Path:
    path = creative_video_path(settings, creative_id)
    if not path.is_file():
        raise ApplyError(f"media: creative {creative_id} has no local mp4 at {path}")
    return path


def probe_duration(path: Path, tools: MediaTools) -> float:
    completed = _run(
        tools,
        [
            tools.ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ],
        timeout=60,
        what=f"ffprobe {path.name}",
    )
    try:
        return float((completed.stdout or "").strip().splitlines()[0])
    except (IndexError, ValueError) as error:
        raise ApplyError(f"media: ffprobe returned no duration for {path.name}") from error


def encoded_file_names(stem: str) -> list[str]:
    names: list[str] = []
    for width in WIDTHS:
        names += [f"{stem}-{width}.mp4", f"{stem}-{width}.webm", f"{stem}-poster-{width}.webp"]
    return names


def encode_hero_video(
    source: Path, plan: HeroVideoPlan, settings: Settings, tools: MediaTools, emit: Emit
) -> Path:
    """The lab's encode recipe (scripts/encode-funnel-video.mjs) at 1280 and 720, poster = first
    frame of the trim. Returns the cache dir holding the six files."""
    out_dir = settings.media_cache_dir / "encoded" / plan.stem
    names = encoded_file_names(plan.stem)
    if all((out_dir / name).is_file() for name in names):
        emit("media", f"clip {plan.stem}: cached")
        return out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    start = f"{plan.start:g}"
    duration = f"{plan.duration:g}"
    base = [tools.ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    trim = ["-ss", start, "-t", duration, "-i", str(source), "-an"]
    for width in WIDTHS:
        scale = f"scale={width}:-2:flags=lanczos,fps=30"
        mp4 = out_dir / f"{plan.stem}-{width}.mp4"
        tmp = out_dir / f"{plan.stem}-{width}.tmp.mp4"
        _run(
            tools,
            base
            + trim
            + [
                "-vf",
                f"{scale},format=yuv420p",
                "-c:v",
                "libx264",
                "-profile:v",
                "high",
                "-crf",
                "23",
                "-preset",
                "slow",
                "-movflags",
                "+faststart",
                str(tmp),
            ],
            timeout=600,
            what=f"ffmpeg mp4 {width}",
        )
        os.replace(tmp, mp4)
        emit("media", f"encoded {width} mp4")
        webm = out_dir / f"{plan.stem}-{width}.webm"
        tmp = out_dir / f"{plan.stem}-{width}.tmp.webm"
        _run(
            tools,
            base
            + trim
            + [
                "-vf",
                scale,
                "-c:v",
                "libvpx-vp9",
                "-b:v",
                "0",
                "-crf",
                "33",
                "-row-mt",
                "1",
                str(tmp),
            ],
            timeout=600,
            what=f"ffmpeg webm {width}",
        )
        os.replace(tmp, webm)
        emit("media", f"encoded {width} webm")
        png = out_dir / f"{plan.stem}-poster-{width}.png"
        _run(
            tools,
            base
            + [
                "-ss",
                start,
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-vf",
                f"scale={width}:-2",
                str(png),
            ],
            timeout=120,
            what=f"ffmpeg poster {width}",
        )
        poster = out_dir / f"{plan.stem}-poster-{width}.webp"
        tmp = out_dir / f"{plan.stem}-poster-{width}.tmp.webp"
        _run(
            tools,
            [tools.cwebp, "-quiet", "-q", "80", str(png), "-o", str(tmp)],
            timeout=120,
            what=f"cwebp poster {width}",
        )
        os.replace(tmp, poster)
        png.unlink(missing_ok=True)
        emit("media", f"poster {width}")
    return out_dir


# ---------------------------------------------------------------------------------------
# Tiles


@dataclass(frozen=True)
class TileResult:
    spec: TileSpec
    candidates: list[Path]
    judge: JudgeResult | None
    chosen: Path
    thumb: Path
    cached: bool = False
    events: list[str] = field(default_factory=list)


def tile_cache_dir(settings: Settings) -> Path:
    return settings.media_cache_dir / "tiles"


def _webp(tools: MediaTools, src: Path, dest: Path, *, quality: str, width: str, what: str) -> Path:
    tmp = dest.with_name(dest.stem + ".tmp.webp")
    _run(
        tools,
        [tools.cwebp, "-quiet", "-q", quality, "-resize", width, "0", str(src), "-o", str(tmp)],
        timeout=120,
        what=what,
    )
    os.replace(tmp, dest)
    return dest


def read_cached_tile(spec: TileSpec, settings: Settings) -> TileResult:
    """What the cache holds for a spec: candidates, judge, chosen webp and thumb. Any of them
    may be missing (an interrupted run leaves candidates without a judge). No API call."""
    cache = tile_cache_dir(settings)
    stem = spec.stem
    judge_path = cache / f"{stem}.judge.json"
    judge = (
        JudgeResult.model_validate_json(judge_path.read_text(encoding="utf-8"))
        if judge_path.is_file()
        else None
    )
    candidates = sorted(cache.glob(f"{stem}.cand*.png"))
    return TileResult(
        spec=spec,
        candidates=candidates,
        judge=judge,
        chosen=cache / f"{stem}.webp",
        thumb=cache / f"{stem}.thumb.webp",
        cached=True,
    )


def load_cached_tiles(
    plan: MediaPlan, settings: Settings, base_version: str | None = None
) -> list[TileResult]:
    """The cache's view of every tile in a plan, in generation order, without generating
    anything. A slot whose `previous` tile was never chosen (or whose references cannot be
    resolved) ends its group early; callers show the brief alone for the rest."""
    base = base_version or settings.base_version
    order: list[str] = []
    for item in plan.showcase:
        if item.group not in order:
            order.append(item.group)
    results: list[TileResult] = []
    for label in order:
        chosen_previous: Path | None = None
        items = sorted((i for i in plan.showcase if i.group == label), key=lambda i: i.slot)
        for item in items:
            try:
                spec = build_tile_spec(item, settings, base, chosen_previous)
            except ApplyError:
                break
            result = read_cached_tile(spec, settings)
            results.append(result)
            chosen_previous = result.chosen if result.chosen.is_file() else None
    return results


def tile_started_data(spec: TileSpec) -> dict[str, Any]:
    plan = spec.plan
    return {
        "stem": spec.stem,
        "group": plan.group,
        "slot": plan.slot,
        "brief": plan.brief or plan.prompt,
        "medium": plan.medium,
        "palette": list(plan.palette),
        "background": plan.background,
        "model": spec.model_name,
        "references": [
            {"ref": r.ref, "kind": r.kind, "path": str(r.path)} for r in spec.references
        ],
    }


def tile_judged_data(judge: JudgeResult) -> dict[str, Any]:
    return {
        "stem": judge.stem,
        "chosen": judge.chosen,
        "reason": judge.reason,
        "fallback": judge.fallback,
        "scores": [score.model_dump(by_alias=True) for score in judge.scores],
    }


def emit_tile_events(on_data: EmitData | None, result: TileResult) -> None:
    """Replay a finished tile as the same event sequence a fresh generation produces, so a
    cached apply and a live one look identical to a listener."""
    if on_data is None:
        return
    for index, path in enumerate(result.candidates):
        emit_to(
            on_data, "tile_candidate", {"stem": result.spec.stem, "index": index, "path": str(path)}
        )
    if result.judge is not None:
        emit_to(on_data, "tile_judged", tile_judged_data(result.judge))


def generate_tile(
    spec: TileSpec,
    settings: Settings,
    tools: MediaTools,
    emit: Emit,
    *,
    variants: int,
    refresh: bool = False,
    on_data: EmitData | None = None,
) -> TileResult:
    cache = tile_cache_dir(settings)
    cache.mkdir(parents=True, exist_ok=True)
    plan = spec.plan
    stem = spec.stem
    emit_to(on_data, "tile_started", tile_started_data(spec))
    final = cache / f"{stem}.webp"
    thumb = cache / f"{stem}.thumb.webp"
    judge_path = cache / f"{stem}.judge.json"
    variants = max(1, min(4, variants))
    candidates = [cache / f"{stem}.cand{k}.png" for k in range(variants)]
    if final.is_file() and thumb.is_file() and not refresh:
        emit("media", f"tile {stem}: cached")
        result = read_cached_tile(spec, settings)
        emit_tile_events(on_data, result)
        return result
    if tools.images is None:
        raise ApplyError(NO_IMAGE_BACKEND)
    (cache / f"{stem}.prompt.txt").write_text(spec.prompt, encoding="utf-8")
    (cache / f"{stem}.refs.json").write_text(
        json.dumps(
            [
                {"ref": r.ref, "kind": r.kind, "path": str(r.path), "fingerprint": r.fingerprint}
                for r in spec.references
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    missing = [(k, path) for k, path in enumerate(candidates) if refresh or not path.is_file()]
    if missing:
        axes = style_of(plan.medium).axes if plan.medium else None
        prompts = variant_prompts(spec.prompt, variants, axes)
        seeds = tile_seeds(stem, variants)
        emit(
            "media",
            f"tile {stem} ({plan.group} slot {plan.slot}): {len(missing)} candidates via "
            f"{spec.model_name} [{len(spec.references)} refs]",
        )
        try:
            tools.images.generate_candidates(
                model=spec.model_name,
                prompts=[prompts[k] for k, _ in missing],
                references=spec.references,
                seeds=[seeds[k] for k, _ in missing],
                dests=[path for _, path in missing],
                aspect_ratio=plan.aspect_ratio,
            )
        except Exception as error:
            raise ApplyError(f"media: image generation failed for {stem}: {error}") from error
    present = [path for path in candidates if path.is_file()]
    if not present:
        raise ApplyError(f"media: no candidates were produced for {stem}")
    for index, path in enumerate(present):
        emit_to(on_data, "tile_candidate", {"stem": stem, "index": index, "path": str(path)})
    judge: JudgeResult | None
    family = judge_family(plan.medium)
    if tools.judge is None:
        emit("media", "no judge configured; using candidate #0")
        judge = JudgeResult(
            stem=stem, family=family, chosen=0, reason="no judge configured", fallback=True
        )
    else:
        try:
            judge = tools.judge.judge(
                stem=stem,
                candidates=present,
                references=spec.references,
                prompt=spec.prompt,
                palette=list(plan.palette),
                lettering_text=plan.lettering_text,
                family=family,
            )
            picked = judge.scores[judge.chosen] if judge.scores else None
            total = f"{picked.total:.1f}" if picked else "n/a"
            emit("media", f"judge picked #{judge.chosen} ({total})")
        except Exception as error:  # noqa: BLE001 - judge failure must not stop apply
            emit("media", f"judge failed ({str(error)[:120]}); using candidate #0")
            judge = JudgeResult(
                stem=stem,
                family=family,
                chosen=0,
                reason=f"judge failed: {str(error)[:200]}",
                fallback=True,
            )
    judge_path.write_text(judge.model_dump_json(by_alias=True, indent=2), encoding="utf-8")
    emit_to(on_data, "tile_judged", tile_judged_data(judge))
    chosen_src = present[min(judge.chosen, len(present) - 1)]
    _webp(tools, chosen_src, final, quality="82", width="1600", what=f"cwebp {stem}")
    _webp(tools, chosen_src, thumb, quality="74", width="800", what=f"cwebp thumb {stem}")
    emit("media", f"tile {stem}: webp 1600 + thumb 800")
    return TileResult(spec=spec, candidates=present, judge=judge, chosen=final, thumb=thumb)


def generate_tiles(
    plan: MediaPlan,
    settings: Settings,
    tools: MediaTools,
    emit: Emit,
    *,
    variants: int,
    refresh: bool = False,
    base_version: str | None = None,
    on_data: EmitData | None = None,
) -> list[TileResult]:
    """Groups in first-appearance order, slots ascending; the chosen tile of the previous
    slot feeds a `previous` reference."""
    if plan.showcase and tools.images is None:
        raise ApplyError(NO_IMAGE_BACKEND)
    base = base_version or settings.base_version
    order: list[str] = []
    for item in plan.showcase:
        if item.group not in order:
            order.append(item.group)
    results: list[TileResult] = []
    for label in order:
        chosen_previous: Path | None = None
        items = sorted((i for i in plan.showcase if i.group == label), key=lambda i: i.slot)
        for item in items:
            spec = build_tile_spec(item, settings, base, chosen_previous)
            result = generate_tile(
                spec, settings, tools, emit, variants=variants, refresh=refresh, on_data=on_data
            )
            results.append(result)
            chosen_previous = result.chosen
    return results


def _stage(cached: Path, target_dir: Path, name: str) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / name
    shutil.copyfile(cached, target)
    size = target.stat().st_size
    if size == 0 or size > MAX_ASSET_BYTES:
        raise ApplyError(f"media: {name} is {size} bytes (max {MAX_ASSET_BYTES})")
    return target


def produce_media(
    plan: MediaPlan,
    variant_dir: Path,
    settings: Settings,
    tools: MediaTools,
    emit: Emit,
    *,
    variants: int | None = None,
    on_data: EmitData | None = None,
) -> ProducedMedia:
    """Produce every file the plan needs into variant_dir/assets/ and return the refs."""
    if plan.showcase and tools.images is None:
        raise ApplyError(NO_IMAGE_BACKEND)
    hero_ref: dict[str, Any] | None = None
    hero_files: set[str] = set()
    showcase: dict[tuple[str, int], str] = {}
    showcase_files: set[str] = set()
    thumb_files: set[str] = set()
    cached = 0

    clip = plan.hero_video
    if clip is not None:
        encoded = settings.media_cache_dir / "encoded" / clip.stem
        if all((encoded / name).is_file() for name in encoded_file_names(clip.stem)):
            # Encoded earlier from the same source and trim: no download, no probe.
            emit("media", f"clip {clip.stem}: cached")
            cached += len(encoded_file_names(clip.stem))
        else:
            if clip.source == "youtube":
                source = fetch_youtube(clip.source_id, settings, tools, emit)
            else:
                source = creative_source(clip.source_id, settings)
            total = probe_duration(source, tools)
            # Allow endpoint rounding (e.g. a nominal 30s video probes as 29.974s).
            # FFmpeg stops at EOF; larger overruns still indicate an invalid plan.
            if clip.start + clip.duration > total + 0.1:
                raise ApplyError(
                    f"media: clip {clip.start:g}s+{clip.duration:g}s exceeds source length {total:.1f}s"
                )
            encoded = encode_hero_video(source, clip, settings, tools, emit)
        for name in encoded_file_names(clip.stem):
            _stage(encoded / name, variant_dir / "assets" / "video", name)
            hero_files.add(f"assets/video/{name}")

        def rendition(width: int) -> dict[str, Any]:
            item: dict[str, Any] = {
                "webm": f"assets/video/{clip.stem}-{width}.webm",
                "mp4": f"assets/video/{clip.stem}-{width}.mp4",
                "poster": f"assets/video/{clip.stem}-poster-{width}.webp",
            }
            if clip.aspect != "16:9":
                item["aspect"] = clip.aspect
            return item

        hero_ref = {"label": clip.label, "desktop": rendition(1280), "phone": rendition(720)}

    results = (
        generate_tiles(
            plan,
            settings,
            tools,
            emit,
            variants=variants or settings.tile_variants,
            on_data=on_data,
        )
        if plan.showcase
        else []
    )
    for result in results:
        item = result.spec.plan
        name = f"{result.spec.stem}.webp"
        rel = f"assets/showcase/{name}"
        thumb_rel = f"assets/thumbs/showcase/{name}"
        if rel not in showcase_files:
            _stage(result.chosen, variant_dir / "assets" / "showcase", name)
            _stage(result.thumb, variant_dir / "assets" / "thumbs" / "showcase", name)
            showcase_files.add(rel)
            thumb_files.add(thumb_rel)
            if result.cached:
                cached += 2
        showcase[(item.group, item.slot)] = rel

    return ProducedMedia(
        hero_video_ref=hero_ref,
        showcase=showcase,
        files=ProducedFiles(
            hero_video=frozenset(hero_files),
            showcase=frozenset(showcase_files),
            showcase_thumbs=frozenset(thumb_files),
        ),
        cached_files=cached,
        tiles=results,
    )
