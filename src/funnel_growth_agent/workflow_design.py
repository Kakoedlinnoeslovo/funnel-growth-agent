"""Prepare the persisted, real-renderer direction previews before producing assets.

Three recipes are attempted; a direction the model cannot get past validation is reported
and skipped, and the run continues as long as two distinct options survive.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from copy import deepcopy
from dataclasses import replace

from .apply import patch_identities, patch_site
from .blueprint import RECIPES, baseline_blueprint, compose_document, require_renderer
from .campaign import campaign_fingerprint
from .landing_patch import dump_yaml, load_yaml
from .models import PageBlueprint
from .proposal import propose
from .workflow_catalog import snapshot_metrics
from .workflow_preview import copy_lab


def prepare_directions(workflow, draft: dict, payload: dict, selected, analyses) -> None:
    folder = workflow.path(draft["id"])
    base = workflow.settings_for(draft, folder / "base")
    require_renderer(base.pricing_lab_dir)
    set_id = uuid.uuid4().hex
    lab = folder / "directions" / set_id / "lab"
    copy_lab(folder / "base", lab)
    settings = replace(base, pricing_lab_dir=lab)
    previous = (
        draft["revisions"][draft["readyRevision"] - 1]["proposal"]
        if draft.get("readyRevision")
        else None
    )
    if previous is None:
        baseline_page = baseline_blueprint(base.landing_path)
        if baseline_page:
            previous = {"changes": baseline_page}
    recipes = deepcopy(RECIPES)
    old_page = (previous or {}).get("changes", {})
    has_proof = any(
        row.get("component") in {"logo-strip", "press-quotes", "testimonials", "quote"}
        for row in load_yaml(base.landing_path).get("props", {}).get("sections", [])
    )
    for recipe in recipes:
        if not has_proof:
            recipe["body"] = [kind for kind in recipe["body"] if kind != "proof"]
        if old_page.get("blocks"):
            if recipe["theme"] == old_page["theme"]:
                recipe["layout"] = next(
                    layout
                    for layout in ("split", "centered", "media-first")
                    if layout != old_page["blocks"][0]["layout"]
                )
            if recipe["body"] == [b["kind"] for b in old_page["blocks"][1:-1]]:
                recipe["body"][-2:] = reversed(recipe["body"][-2:])
    preview_number = max(
        [
            int(m.group(1))
            for path in (lab / "funnels").iterdir()
            if (m := re.fullmatch(r"v([0-9]+)", path.name))
        ]
        or [0]
    )
    records = []
    failures: list[str] = []
    for index, recipe in enumerate(recipes, 1):
        workflow._event(
            draft,
            "step_started",
            {"id": recipe["id"], "label": "Design " + recipe["label"], "stage": "design"},
        )
        brief = {
            "task": "Design a full landing_rebuild with the supplied recipe. No image generation yet.",
            "goalPrompt": draft.get("goalPrompt", ""),
            "campaignBrief": draft.get("campaignBrief"),
            "changeLevel": "heavy",
            "recipe": recipe,
            "revisionRequest": payload,
            "previousProposal": previous,
            "analyses": [a.model_dump(by_alias=True) for a in analyses],
            "selectionReview": draft.get("selectionReview"),
            "supportingContext": draft.get("context"),
            "requirements": "Compose exactly the recipe body kinds between the hero and the final cta, in the listed order. Baseline proof and pricing sections are preserved automatically, so include a proof block only when the recipe lists one. Use verified baseline proof only, via sourceSectionId. Existing images must exactly match rawSections image paths. media.showcase group is the new block id and slot its image index. Plan new images using the page art direction where needed. Preserve supported claims. The renderer retains quiz and pricing sections. Each direction needs a different hero layout, theme and body composition.",
        }
        try:
            proposal = _direction(
                workflow,
                draft,
                settings,
                lab,
                recipe,
                brief,
                selected,
                analyses,
                preview_number + index,
            )
        except Exception as error:
            # One rejected direction must not discard the directions already designed:
            # the remaining options stay choosable and the reason stays on the timeline.
            failures.append(f"{recipe['label']}: {error}")
            workflow._event(
                draft,
                "step_failed",
                {
                    "id": recipe["id"],
                    "label": recipe["label"] + " unavailable",
                    "stage": "design",
                    "error": str(error),
                },
            )
            continue
        records.append({**recipe, **proposal})
        workflow._event(
            draft,
            "step_completed",
            {"id": recipe["id"], "label": recipe["label"] + " ready", "stage": "design"},
        )
    if len(records) < 2:
        raise ValueError(
            f"Only {len(records)} of {len(recipes)} design directions could be produced. "
            + " | ".join(failures)
        )
    signatures = {
        (r["proposal"]["changes"]["theme"], r["proposal"]["changes"]["blocks"][0]["layout"])
        for r in records
    }
    if len(signatures) != len(records):
        raise ValueError(
            "Directions repeat the same composition; retry to produce distinct options"
        )
    dist = workflow.builder(
        lab,
        {"draftId": draft["id"], "directionSetId": set_id, "variant": records[0]["previewVersion"]},
        draft["buildEnvironment"],
    )
    draft["directionSet"] = {
        "id": set_id,
        "records": records,
        "unavailable": failures,
        "dist": str(dist),
        "input": payload,
        "expectedRevision": draft.get("readyRevision") or 0,
        "campaignFingerprint": campaign_fingerprint(draft),
    }
    draft["status"] = "awaiting_direction"
    workflow._event(
        draft,
        "directions_ready",
        {
            "label": "Choose a direction before generating final imagery",
            "stage": "design",
            "directionSetId": set_id,
            "unavailable": failures,
        },
    )


def _direction(
    workflow, draft: dict, settings, lab, recipe: dict, brief: dict, selected, analyses, number: int
) -> dict:
    """Design one direction and write its own preview version into the shared lab."""
    proposal = propose(
        settings,
        model=workflow.model,
        creatives=selected,
        analyses=analyses,
        metrics=snapshot_metrics(draft["reportSnapshot"], settings),
        brief=json.dumps(brief),
        browser=workflow.browser,
        reader=workflow.reader,
        style_reader=workflow.style_reader,
        on_event=lambda kind, data: workflow._event(draft, kind, data),
        research_records=(draft.get("context") or {}).get("competitors", []),
    )
    if not isinstance(proposal.changes, PageBlueprint):
        raise ValueError(
            "Heavy direction did not return a full landing blueprint. Retry generation."
        )
    preview_version = f"v{number}"
    target = lab / "funnels" / preview_version
    if target.exists():
        raise ValueError("Reserved direction preview namespace already exists")
    shutil.copytree(lab / "funnels" / draft["baseVersion"], target)
    document = compose_document(
        load_yaml(target / "steps/landing.yaml"), proposal.changes, placeholders=True
    )
    dump_yaml(target / "steps/landing.yaml", document)
    patch_identities(target / "funnel.yaml", preview_version, "landing_rebuild")
    patch_site(lab / "funnels/site.yaml", preview_version, recipe["label"], "landing_rebuild")
    return {"proposal": proposal.model_dump(by_alias=True), "previewVersion": preview_version}
