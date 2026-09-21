"""funnel-growth CLI."""

from __future__ import annotations

from pathlib import Path

import typer

from .config import Settings, load_settings
from .memory import existing_variants, latest_applied, latest_run
from .models import HeroCopyChanges, RedesignChanges, SavedProposal

app = typer.Typer(no_args_is_help=True, add_completion=False)
demo_app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    help="Demo console: replay a recorded run next to the live landing, or run it live.",
)
app.add_typer(demo_app, name="demo")


def _settings() -> Settings:
    return load_settings()


def _copy_line(name: str, section: object) -> str | None:
    if section is None:
        return None
    dumped = section.model_dump(by_alias=True, exclude_none=True)  # type: ignore[attr-defined]
    if not dumped:
        return None
    parts = [f"{key}={value!r}" for key, value in dumped.items()]
    return f"  {name}: " + ", ".join(parts)


def format_changes(changes: HeroCopyChanges | RedesignChanges | None) -> list[str]:
    if changes is None:
        return []
    if isinstance(changes, HeroCopyChanges):
        dumped = changes.model_dump(by_alias=True, exclude_none=True)
        return [f"{key}: {value}" for key, value in dumped.items()]
    lines: list[str] = []
    copy = changes.copy_changes
    if copy is not None and not copy.is_empty():
        lines.append("copy:")
        for name, section in (
            ("hero", copy.hero),
            ("videoCta", copy.video_cta),
            ("finalCta", copy.final_cta),
            ("inlineCta", copy.inline_cta),
        ):
            line = _copy_line(name, section)
            if line:
                lines.append(line)
    if changes.layout is not None and not changes.layout.is_empty():
        dumped = changes.layout.model_dump(by_alias=True, exclude_none=True)
        lines.append("layout: " + ", ".join(f"{key}={value}" for key, value in dumped.items()))
    if changes.composition is not None and not changes.composition.is_empty():
        composition = changes.composition
        if composition.order:
            lines.append("composition.order: " + ", ".join(composition.order))
        if composition.omit:
            lines.append("composition.omit: " + ", ".join(composition.omit))
    if changes.media is not None and not changes.media.is_empty():
        lines.append("media:")
        clip = changes.media.hero_video
        if clip is not None:
            lines.append(
                f"  heroVideo: {clip.source} {clip.source_id} {clip.start:g}s+{clip.duration:g}s "
                f"({clip.aspect}) {clip.label!r}"
            )
        for item in changes.media.showcase:
            refs = f" refs={item.references}" if item.references else ""
            if item.brief is not None:
                lines.append(
                    f"  showcase[{item.group} #{item.slot}] {item.tile_model} {item.medium} "
                    f"palette={item.palette}{refs}: {item.brief}"
                )
            else:
                lines.append(
                    f"  showcase[{item.group} #{item.slot}] {item.tile_model} raw{refs}: "
                    f"{item.prompt}"
                )
    return lines


def format_propose(proposal: SavedProposal, *, include_apply: bool) -> str:
    lines: list[str] = [f"Proposal: {proposal.run_id}", ""]
    if proposal.decision == "no_experiment":
        lines += ["no_experiment", proposal.reason or ""]
        if proposal.other_ideas:
            lines += ["", "Alternative ideas", "-----------------"]
            lines += [f"{i}. {idea}" for i, idea in enumerate(proposal.other_ideas, start=1)]
        lines += ["", "No files were modified"]
        return "\n".join(lines)
    baseline = proposal.baseline
    proxy = " proxy" if baseline.is_proxy else ""
    lines += [
        "Problem",
        "-------",
        proposal.problem or "",
        "",
        "Baseline",
        "--------",
        f"{baseline.version}{proxy}",
        f"{baseline.landing_people} → {baseline.cta_people}",
        f"{baseline.cta_rate:.1%}",
        "",
        "Experiment type",
        "---------------",
        proposal.experiment_type or "",
        "",
        "Hypothesis",
        "----------",
        proposal.hypothesis or "",
        "",
        "Changes",
        "-------",
    ]
    lines += format_changes(proposal.changes)
    lines += ["", "Alternative ideas", "-----------------"]
    for index, idea in enumerate(proposal.other_ideas, start=1):
        lines.append(f"{index}. {idea}")
    lines += ["", "No files were modified"]
    if include_apply:
        lines += ["", "Apply with:", "", f"uv run funnel-growth apply {proposal.run_id}"]
    return "\n".join(lines)


@app.command()
def propose(
    refresh_creatives: bool = typer.Option(False, "--refresh-creatives"),
) -> None:
    from .proposal import propose as run_propose

    settings = _settings()
    try:
        result = run_propose(settings, refresh_creatives=refresh_creatives)
    except Exception as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    typer.echo(format_propose(result, include_apply=_has_apply()))


@app.command("status")
def status_cmd() -> None:
    settings = _settings()
    variants = existing_variants(settings)
    latest = latest_run(settings)
    applied = latest_applied(settings)
    lines = [
        f"Base version: {settings.base_version}",
        f"Existing variants: {', '.join(variants) if variants else '(none)'}",
    ]
    if latest:
        lines.append(f"Latest proposal: {latest.run_id} ({latest.status})")
    else:
        lines.append("Latest proposal: (none)")
    if applied:
        lines.append(f"Latest applied variant: {applied.variant}")
        if applied.evaluation:
            lines.append(
                f"Evaluation: {applied.evaluation.result} "
                f"ctaRate={applied.evaluation.cta_rate} causal={applied.evaluation.causal}"
            )
        if applied.learning:
            lines.append(f"Learning: {applied.learning}")
    else:
        lines.append("Latest applied variant: (none)")
    typer.echo("\n".join(lines))


@app.command()
def apply(
    run_id: str = typer.Argument(...),
    page: bool = typer.Option(False, "--page", help="Also write the run's HTML page afterwards."),
) -> None:
    from .apply import apply_run

    def echo_event(stage: str, message: str) -> None:
        typer.echo(f"• {stage}: {message}", err=True)

    settings = _settings()
    try:
        typer.echo(apply_run(settings, run_id, on_event=echo_event))
    except Exception as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    if page:
        from .proposal_page import write_page

        typer.echo(f"\nPage: {write_page(settings, run_id)}")


@app.command()
def show(
    run_id: str = typer.Argument(...),
    open_page: bool = typer.Option(False, "--open"),
) -> None:
    """Write one HTML page for a run: evidence, hypothesis, every change as before → after,
    the clip plan and the showcase tiles with candidates and judge scores. Reads memory and
    caches only: no API calls, no lab changes."""
    from .proposal_page import write_page

    try:
        path = write_page(_settings(), run_id)
    except KeyError:
        typer.echo(f"unknown run {run_id}", err=True)
        raise typer.Exit(code=1) from None
    except Exception as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"Page: {path}")
    if open_page:
        typer.launch(str(path))


@app.command()
def tiles(
    run_id: str = typer.Argument(...),
    variants: int | None = typer.Option(None, "--variants", min=1, max=4),
    open_sheet: bool = typer.Option(False, "--open"),
    refresh: bool = typer.Option(False, "--refresh"),
) -> None:
    """Generate a run's showcase tiles into the cache (no lab changes) and write a contact
    sheet with every candidate and the judge's scores. `apply` reuses the cache."""
    from .apply import check_media_plan
    from .media import default_media_tools, generate_tiles
    from .memory import get_run
    from .models import RedesignChanges
    from .tile_sheet import write_sheet

    settings = _settings()
    try:
        row = get_run(settings, run_id)
        proposal = SavedProposal.model_validate(row.proposal)
        changes = proposal.changes
        if (
            not isinstance(changes, RedesignChanges)
            or not changes.media
            or not changes.media.showcase
        ):
            typer.echo(f"{run_id} has no showcase tiles", err=True)
            raise typer.Exit(code=1)
        check_media_plan(changes.media, proposal, settings)
        results = generate_tiles(
            changes.media,
            settings,
            default_media_tools(settings),
            lambda stage, message: typer.echo(f"• {stage}: {message}", err=True),
            variants=variants or settings.tile_variants,
            refresh=refresh,
            base_version=proposal.base_version,
        )
        path = write_sheet(settings, run_id, results)
    except typer.Exit:
        raise
    except Exception as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    for result in results:
        judge = result.judge
        picked = judge.scores[judge.chosen] if judge and judge.scores else None
        score = f" ({picked.total:.1f})" if picked else ""
        typer.echo(
            f"{result.spec.plan.group} slot {result.spec.plan.slot}: {result.spec.stem} "
            f"#{judge.chosen if judge else 0}{score}{' cached' if result.cached else ''}"
        )
    typer.echo(f"Sheet: {path}")
    if open_sheet:
        typer.launch(str(path))


@app.command()
def evaluate(run_id: str = typer.Argument(...)) -> None:
    from .evaluate import evaluate_run

    try:
        typer.echo(evaluate_run(_settings(), run_id))
    except Exception as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error


@app.command("quality-gate")
def quality_gate_cmd(
    snapshots: str | None = typer.Option(None, "--snapshots"),
) -> None:
    from .quality_gate import available_snapshots, load_snapshots, summarize_gate

    settings = _settings()
    typer.echo("Quality gate: score propose on distinct snapshots, not 10 retries of one week.")
    typer.echo(
        "Pass: ≥8/10 grounded, ≥8/10 valid, ≥7/10 worth testing or correctly declined, 0 unsupported claims."
    )
    listed = load_snapshots(Path(snapshots)) if snapshots else available_snapshots(settings)
    for index, item in enumerate(listed, start=1):
        title = item.get("title") or item.get("path") or str(index)
        typer.echo(f"  {index}. {title}")
    if not settings.anthropic_api_key:
        typer.echo(
            "ANTHROPIC_API_KEY is not set; listing snapshots only. Run propose per snapshot to score."
        )
        raise typer.Exit(code=0)
    typer.echo(str(summarize_gate([])))


def _has_apply() -> bool:
    try:
        from . import apply as _apply

        return bool(_apply)
    except ImportError:
        return False


@demo_app.callback()
def demo(
    ctx: typer.Context,
    recording: str | None = typer.Option(
        None, "--recording", help="Run id, file name or path. Default: the newest recording."
    ),
    live: bool = typer.Option(False, "--live", help="Run propose and apply for real."),
    port: int = typer.Option(8765, "--port"),
    speed: float = typer.Option(1.0, "--speed", help="Replay pacing multiplier."),
    open_browser: bool = typer.Option(False, "--open"),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Serve the console. Without a subcommand this replays a recording (or runs live)."""
    if ctx.invoked_subcommand is not None:
        return
    from .demo.events import resolve_recording
    from .demo.server import serve

    settings = _settings()
    try:
        path = None if live else resolve_recording(settings, recording)
    except FileNotFoundError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    serve(
        settings,
        recording=path,
        live=live,
        port=port,
        speed=speed,
        open_browser=open_browser,
        verbose=verbose,
    )


@demo_app.command("synthesize")
def demo_synthesize(run_id: str = typer.Argument(...)) -> None:
    """Build a replayable recording for a run that already exists, from memory and caches
    only. No model is called; nothing in the lab changes."""
    from .demo.synthesize import synthesize_recording

    try:
        path = synthesize_recording(_settings(), run_id)
    except KeyError:
        typer.echo(f"unknown run {run_id}", err=True)
        raise typer.Exit(code=1) from None
    except Exception as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"Recording: {path}")


@demo_app.command("record")
def demo_record(
    refresh_creatives: bool = typer.Option(False, "--refresh-creatives"),
    no_apply: bool = typer.Option(False, "--no-apply", help="Stop after propose."),
) -> None:
    """Run propose (and apply) for real, headless, writing the event log for later replay."""
    import time

    from .apply import apply_run
    from .demo.events import JsonlSink, Recorder, demo_dir, recording_path
    from .proposal import propose as run_propose

    settings = _settings()
    sink = JsonlSink(
        demo_dir(settings) / f"recording-{time.strftime('%Y-%m-%dT%H%M%S')}.events.jsonl"
    )

    def echo(kind: str, data: dict) -> None:
        if kind in {"tool_call", "apply_stage", "tile_judged", "proposal_saved", "apply_done"}:
            detail = (
                data.get("name") or data.get("message") or data.get("runId") or data.get("stem")
            )
            typer.echo(f"• {kind}: {detail}", err=True)

    recorder = Recorder(sinks=[sink, lambda event: echo(event.kind, event.data)])
    try:
        saved = run_propose(settings, refresh_creatives=refresh_creatives, on_event=recorder)
    except Exception as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    path = sink.rename(recording_path(settings, saved.run_id))
    typer.echo(format_propose(saved, include_apply=False))
    if saved.decision != "experiment" or no_apply:
        typer.echo(f"\nRecording: {path}")
        return
    recorder.phase = "apply"
    try:
        typer.echo(apply_run(settings, saved.run_id, on_data=recorder))
    except Exception as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    finally:
        sink.close()
    typer.echo(f"\nRecording: {path}\nReplay with: uv run funnel-growth demo --open")
