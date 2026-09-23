"""Score propose outputs on diverse snapshots. Do not retry one week ten times."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import Settings
from .metrics import _iter_reports
from .models import LandingProposal, NoExperiment, SavedProposal

UNSUPPORTED = ("guaranteed", "best in the world", "#1 ai", "unlimited free pro")


def score_proposal(
    proposal: SavedProposal | LandingProposal | NoExperiment, context: dict[str, Any]
) -> dict[str, Any]:
    baseline_version = ""
    if isinstance(proposal, SavedProposal):
        baseline_version = (proposal.baseline.version or "").lower()
        decision = proposal.decision
        evidence = proposal.evidence
        hypothesis = proposal.hypothesis
        reason = proposal.reason
        changes = proposal.changes
        experiment_type = proposal.experiment_type
    elif isinstance(proposal, NoExperiment):
        decision = "no_experiment"
        evidence = []
        hypothesis = None
        reason = proposal.reason
        changes = None
        experiment_type = None
    else:
        decision = "experiment"
        evidence = proposal.evidence
        hypothesis = proposal.hypothesis
        reason = None
        changes = proposal.changes
        experiment_type = proposal.experiment_type

    blob = " ".join(evidence).lower()
    context_blob = json.dumps(context).lower()
    grounded = bool(evidence or reason) and (
        decision == "no_experiment"
        or "proxy" in blob
        or (bool(baseline_version) and baseline_version in blob)
        or "cta" in blob
        or "ad" in blob
        or any(token in context_blob for token in blob.split() if len(token) > 4)
    )
    understands = True
    mismatch = True
    falsifiable = (
        None
        if decision == "no_experiment"
        else bool(hypothesis)
        and ("will" in (hypothesis or "").lower() or "if " in (hypothesis or "").lower())
    )
    minimal = None if decision == "no_experiment" else changes is not None
    no_claim = not any(
        phrase in blob or phrase in (hypothesis or "").lower() for phrase in UNSUPPORTED
    )
    previous = context.get("previous") or []
    not_duplicate = hypothesis not in {
        item.get("hypothesis") for item in previous if isinstance(item, dict)
    }
    would_test = None if decision == "no_experiment" else bool(hypothesis)
    return {
        "grounded": grounded,
        "understands_creatives": understands,
        "mismatch_or_none": mismatch,
        "falsifiable": falsifiable,
        "minimal": minimal,
        "no_unsupported_claim": no_claim,
        "not_duplicate": not_duplicate,
        "would_test": would_test,
        "experiment_type_ok": experiment_type
        in {None, "hero_copy", "landing_redesign", "landing_rebuild"},
    }


def valid_proposal(
    proposal: SavedProposal | LandingProposal | NoExperiment, card: dict[str, Any]
) -> bool:
    if isinstance(proposal, SavedProposal):
        decision = proposal.decision
        reason = proposal.reason
        hypothesis = proposal.hypothesis
    elif isinstance(proposal, NoExperiment):
        decision = "no_experiment"
        reason = proposal.reason
        hypothesis = None
    else:
        decision = "experiment"
        reason = None
        hypothesis = proposal.hypothesis
    if not card["grounded"]:
        return False
    if decision == "experiment" and not hypothesis:
        return False
    if decision == "no_experiment" and not reason:
        return False
    if (
        not card["experiment_type_ok"]
        or not card["no_unsupported_claim"]
        or not card["not_duplicate"]
    ):
        return False
    return True


def summarize_gate(cards: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(cards) or 1
    grounded = sum(1 for card in cards if card["grounded"])
    valid = sum(1 for card in cards if card.get("valid"))
    worth = sum(1 for card in cards if card.get("worth") or card.get("correctly_declined"))
    claims = sum(1 for card in cards if not card["no_unsupported_claim"])
    return {
        "n": len(cards),
        "grounded": grounded,
        "valid": valid,
        "worth_or_declined": worth,
        "unsupported_claims": claims,
        "pass": grounded / n >= 0.8 and valid / n >= 0.8 and worth / n >= 0.7 and claims == 0,
    }


def load_snapshots(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def available_snapshots(settings: Settings) -> list[dict[str, str]]:
    """Distinct report days/weeks for the scored gate — not retries of one week."""
    rows: list[dict[str, str]] = []
    for path, data in _iter_reports(settings.reports_dir):
        period = data.get("period") or {}
        rows.append(
            {
                "path": str(path),
                "kind": str(
                    period.get("kind") or ("week" if path.parent.name.endswith("_week") else "day")
                ),
                "title": str(data.get("title") or path.parent.name),
                "generated_at": str(data.get("generated_at") or ""),
            }
        )
    return rows
