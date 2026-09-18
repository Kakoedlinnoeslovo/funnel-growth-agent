"""funnel-growth CLI."""

from __future__ import annotations

from pathlib import Path

import typer

from .config import Settings, load_settings
from .memory import existing_variants, latest_applied, latest_run
from .models import SavedProposal

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _settings() -> Settings:
    return load_settings()


def format_propose(proposal: SavedProposal, *, include_apply: bool) -> str:
    lines: list[str] = [f"Proposal: {proposal.run_id}", ""]
    if proposal.decision == "no_experiment":
        lines += ["no_experiment", proposal.reason or "", "", "No files were modified"]
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
    if proposal.changes:
        dumped = proposal.changes.model_dump(by_alias=True, exclude_none=True)
        for key, value in dumped.items():
            lines.append(f"{key}: {value}")
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
def apply(run_id: str = typer.Argument(...)) -> None:
    from .apply import apply_run

    try:
        typer.echo(apply_run(_settings(), run_id))
    except Exception as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error


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
    typer.echo("Pass: ≥8/10 grounded, ≥8/10 valid, ≥7/10 worth testing or correctly declined, 0 unsupported claims.")
    listed = load_snapshots(Path(snapshots)) if snapshots else available_snapshots(settings)
    for index, item in enumerate(listed, start=1):
        title = item.get("title") or item.get("path") or str(index)
        typer.echo(f"  {index}. {title}")
    if not settings.anthropic_api_key:
        typer.echo("ANTHROPIC_API_KEY is not set; listing snapshots only. Run propose per snapshot to score.")
        raise typer.Exit(code=0)
    typer.echo(str(summarize_gate([])))


def _has_apply() -> bool:
    try:
        from . import apply as _apply

        return bool(_apply)
    except ImportError:
        return False
