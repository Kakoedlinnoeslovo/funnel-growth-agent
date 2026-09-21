"""Contact sheet for generated showcase tiles: every candidate with its judge scores, the
chosen one marked, the references and the built prompt. One self-contained HTML file.

`render_tile_card` is shared with the proposal page."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import Settings
from .page_style import CSS, document, esc, rel_link, swatch

__all__ = ["CRITERIA", "CSS", "render_sheet", "render_tile_card", "write_sheet"]

CRITERIA = (
    ("house_style", "style"),
    ("subject_clarity", "subject"),
    ("cleanliness", "clean"),
    ("crop_safety", "crop"),
    ("palette_adherence", "palette"),
)


def _rel(path: Path, base_dir: Path) -> str:
    return rel_link(path, base_dir)


def _candidates_html(result: Any, base_dir: Path) -> str:
    out = ["<div class='muted' style='margin-top:10px'>Candidates</div><div class='row'>"]
    scores = {score.index: score for score in (result.judge.scores if result.judge else [])}
    for index, candidate in enumerate(result.candidates):
        chosen = result.judge is not None and result.judge.chosen == index
        cls = "cand chosen" if chosen else "cand"
        out.append(f"<div class='{cls}'><img src='{esc(_rel(candidate, base_dir))}'>")
        head = f"#{index}"
        if chosen:
            head += " <span class='badge'>chosen</span>"
        out.append(f"<div>{head}</div>")
        score = scores.get(index)
        if score is not None:
            cells = "".join(f"<td>{label} {getattr(score, attr)}</td>" for attr, label in CRITERIA)
            out.append(
                f"<table><tr>{cells}<td><b>total {score.total:.1f}</b></td></tr></table>"
                f"<div class='muted'>{esc(score.notes)}</div>"
            )
        out.append("</div>")
    out.append("</div>")
    if result.judge is not None and result.judge.reason:
        out.append(
            f"<div class='muted' style='margin-top:8px'>Judge: {esc(result.judge.reason)}</div>"
        )
    return "".join(out)


def _references_html(spec: Any, base_dir: Path) -> str:
    if not spec.references:
        return ""
    out = ["<div class='muted'>References</div><div class='row'>"]
    for ref in spec.references:
        out.append(
            f"<div class='ref'><img src='{esc(_rel(ref.path, base_dir))}'>"
            f"<div class='muted'>{esc(ref.ref)}</div></div>"
        )
    out.append("</div>")
    return "".join(out)


def render_tile_card(
    result: Any, base_dir: Path, *, brief_first: bool = False, run_id: str | None = None
) -> str:
    """One card for a media.TileResult. Sheet style leads with the prompt; brief style (the
    proposal page) leads with what Claude asked for and folds the built prompt away."""
    spec = result.spec
    plan = spec.plan
    out = ["<div class='card'>"]
    if brief_first:
        pills = [esc(plan.medium or "prompt"), esc(plan.tile_model)]
        out.append(
            f"<h2>{esc(plan.group)} · slot {plan.slot} "
            + " ".join(f"<span class='pill'>{pill}</span>" for pill in pills)
            + "</h2>"
        )
    else:
        out.append(
            f"<h2>{esc(plan.group)} · slot {plan.slot} · "
            f"<span class='muted'>{esc(spec.model_name)} · {esc(spec.stem)}</span></h2>"
        )
    if result.cached and not brief_first:
        out.append("<span class='warn'>cached</span> ")
    if result.judge is not None and result.judge.fallback:
        out.append("<span class='warn'>judge fallback: candidate #0</span>")
    if brief_first:
        out.append(f"<p style='margin:8px 0'>{esc(plan.brief or plan.prompt)}</p>")
        if plan.palette:
            out.append("<div>" + " ".join(swatch(colour) for colour in plan.palette) + "</div>")
        if plan.background:
            out.append(f"<div class='muted'>on {esc(plan.background)}</div>")
        out.append(_references_html(spec, base_dir))
        out.append(
            f"<details><summary>Built prompt · {esc(spec.stem)}</summary>"
            f"<pre>{esc(spec.prompt)}</pre></details>"
        )
    else:
        out.append(f"<pre>{esc(spec.prompt)}</pre>")
        out.append(_references_html(spec, base_dir))
    if result.candidates:
        out.append(_candidates_html(result, base_dir))
    elif brief_first:
        hint = f"funnel-growth tiles {run_id}" if run_id else "funnel-growth tiles <run_id>"
        out.append(
            f"<div class='muted' style='margin-top:10px'>No candidates yet. Run {esc(hint)}.</div>"
        )
    out.append("</div>")
    return "".join(out)


def render_sheet(run_id: str, results: list[Any], *, base_dir: Path) -> str:
    """`results` are media.TileResult objects; paths are written relative to base_dir."""
    body = [
        f"<h1>Showcase tiles for {esc(run_id)}</h1>",
        f"<div class='muted'>{len(results)} tile(s). Lime outline = chosen by the judge.</div>",
    ]
    body += [render_tile_card(result, base_dir) for result in results]
    return document(f"Tiles {run_id}", "\n".join(body))


def write_sheet(settings: Settings, run_id: str, results: list[Any]) -> Path:
    sheets = settings.media_cache_dir / "sheets"
    sheets.mkdir(parents=True, exist_ok=True)
    path = sheets / f"{run_id}.html"
    path.write_text(render_sheet(run_id, results, base_dir=sheets), encoding="utf-8")
    return path
