"""JSONL experiment memory."""

from __future__ import annotations

from .config import Settings
from .models import MemoryRow, PreviousRun


def _rows(settings: Settings) -> list[MemoryRow]:
    path = settings.memory_path
    if not path.is_file():
        return []
    rows: list[MemoryRow] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(MemoryRow.model_validate_json(line))
    return rows


def append_run(settings: Settings, row: MemoryRow) -> None:
    settings.memory_path.parent.mkdir(parents=True, exist_ok=True)
    with settings.memory_path.open("a", encoding="utf-8") as handle:
        handle.write(row.model_dump_json(by_alias=True) + "\n")


def update_run(settings: Settings, run_id: str, **changes: object) -> MemoryRow:
    rows = _rows(settings)
    updated: MemoryRow | None = None
    rewritten: list[str] = []
    for row in rows:
        if row.run_id == run_id:
            payload = row.model_dump()
            payload.update(changes)
            updated = MemoryRow.model_validate(payload)
            rewritten.append(updated.model_dump_json(by_alias=True))
        else:
            rewritten.append(row.model_dump_json(by_alias=True))
    if updated is None:
        raise KeyError(run_id)
    settings.memory_path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
    return updated


def get_run(settings: Settings, run_id: str) -> MemoryRow:
    for row in _rows(settings):
        if row.run_id == run_id:
            return row
    raise KeyError(run_id)


def latest_run(settings: Settings) -> MemoryRow | None:
    rows = _rows(settings)
    return rows[-1] if rows else None


def latest_applied(settings: Settings) -> MemoryRow | None:
    for row in reversed(_rows(settings)):
        if row.status == "applied" and row.variant:
            return row
    return None


def list_runs(settings: Settings) -> list[MemoryRow]:
    return _rows(settings)


def get_previous_runs(
    settings: Settings,
    experiment_type: str = "hero_copy",
    limit: int = 5,
) -> list[PreviousRun]:
    matched: list[PreviousRun] = []
    for row in reversed(_rows(settings)):
        proposal = row.proposal or {}
        row_type = proposal.get("experimentType") or proposal.get("experiment_type")
        if row_type != experiment_type:
            continue
        evaluation = row.evaluation
        matched.append(
            PreviousRun(
                run_id=row.run_id,
                experiment_type=row_type,
                hypothesis=proposal.get("hypothesis"),
                changes=proposal.get("changes"),
                result=evaluation.result if evaluation else None,
                evaluation=evaluation,
                learning=row.learning,
                status=row.status,
            )
        )
        if len(matched) >= limit:
            break
    return matched


def existing_variants(settings: Settings) -> list[str]:
    root = settings.pricing_lab_dir / "funnels"
    if not root.is_dir():
        return []
    prefix = f"{settings.base_version}_a"
    return sorted(
        path.name for path in root.iterdir() if path.is_dir() and path.name.startswith(prefix)
    )
