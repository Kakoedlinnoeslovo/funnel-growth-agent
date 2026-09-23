"""Source-backed step catalog and non-executable, contract-preserving edits."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .landing_patch import load_yaml

CONTRACT = "funnel-steps-v1"
COMPONENTS = {
    "landing",
    "brand-studio-landing",
    "onboarding",
    "quiz",
    "interstitial",
    "workspace",
    "paywall",
    "precomputed-examples",
    "icon-brief",
    "icon-workspace",
    "quiz-question",
    "quiz-recap",
    "quiz-proof",
    "quiz-upload",
    "quiz-loader",
    "success",
}
TEXT_FIELDS = {
    "headline",
    "heading",
    "title",
    "subhead",
    "subline",
    "helper",
    "body",
    "label",
    "ctaLabel",
    "buttonLabel",
    "continueLabel",
    "skipLabel",
    "placeholder",
    "message",
    "reassurance",
    "description",
    "eyebrow",
    "subtitle",
    "projectTitle",
    "readyToast",
    "dismissLabel",
    "question",
    "selectCta",
    "selectLabel",
}
# Copy templates carry {plan}/{price} slots the renderer fills from the real product
# catalog. An edit may reword around them but never drop or invent one, so a rewritten
# CTA can never state a price the funnel does not actually charge.
PLACEHOLDER = re.compile(r"\{[a-zA-Z][a-zA-Z0-9_]*\}")
PROTECTED = {
    "stats",
    "liveCounter",
    "proof",
    "testimonials",
    "quotes",
    "prices",
    "plans",
    "billing",
    "products",
    "headlineByAnswer",
    "press-quotes",
    "logo-strip",
    "plan-preview",
}


class DesignBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    kind: Literal["hero", "text", "image", "video", "cards", "faq", "button", "native"]
    heading: str = Field(default="", max_length=300)
    body: str = Field(default="", max_length=4000)
    media: str = Field(default="", max_length=500)
    label: str = Field(default="", max_length=120)
    action: Literal["none", "continue", "back"] = "none"
    columns: Literal[1, 2, 3] = 1
    align: Literal["left", "center"] = "left"
    items: list[Annotated[str, Field(max_length=4000)]] = Field(default_factory=list, max_length=12)


class StepDesign(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schemaVersion: Literal[1] = 1
    background: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")
    foreground: str = Field(default="#18181b", pattern=r"^#[0-9a-fA-F]{6}$")
    accent: str = Field(default="#466cf5", pattern=r"^#[0-9a-fA-F]{6}$")
    font: Literal["sans", "serif"] = "sans"
    width: int = Field(default=1120, ge=320, le=1600)
    spacing: int = Field(default=32, ge=8, le=96)
    blocks: list[DesignBlock] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def unique(self):
        if len({b.id for b in self.blocks}) != len(self.blocks):
            raise ValueError("Design block IDs must be unique")
        return self


class StepProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hypothesis: str = Field(min_length=1, max_length=4000)
    rationale: str = Field(default="", max_length=6000)
    copyPatch: dict[str, str] = Field(default_factory=dict)
    design: StepDesign | None = None


def tree_hash(folder: Path) -> str:
    sha = hashlib.sha256()
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            raise ValueError("Funnel sources cannot contain symlinks")
        if path.is_file():
            sha.update(path.relative_to(folder).as_posix().encode())
            sha.update(path.read_bytes())
    return sha.hexdigest()


def steps(folder: Path) -> list[dict]:
    manifest = load_yaml(folder / "funnel.yaml")
    result = []
    for relative in manifest.get("steps", []):
        if not isinstance(relative, str):
            raise ValueError("Funnel step references must be filenames")
        if "/" not in relative and not relative.endswith(".yaml"):
            relative = f"steps/{relative}.yaml"
        path = (folder / relative).resolve()
        if not path.is_relative_to(folder.resolve()) or not path.is_file():
            raise ValueError("Invalid funnel step reference")
        data = load_yaml(path)
        result.append(
            {
                "id": data["id"],
                "title": data["title"],
                "component": data["component"],
                "path": data["path"],
                "file": relative,
                "hash": hashlib.sha256(path.read_bytes()).hexdigest(),
                "editable": data["component"] in COMPONENTS,
                "role": "paywall" if data["component"] == "paywall" else "step",
                "primaryMetric": "step_payment_rate"
                if data["component"] == "paywall"
                else "step_completion_rate",
            }
        )
    return result


def step_document(folder: Path, step_id: str) -> tuple[dict, dict]:
    item = next((s for s in steps(folder) if s["id"] == step_id), None)
    if not item:
        raise ValueError("Unknown funnel step")
    return item, load_yaml(folder / item["file"])


def copy_fields(document: dict) -> dict[str, str]:
    result = {}

    def visit(value, path=()):
        if isinstance(value, dict):
            if value.get("component") in PROTECTED:
                return
            for key, child in value.items():
                if key in PROTECTED:
                    continue
                if key in TEXT_FIELDS and isinstance(child, str):
                    result["/".join((*path, key))] = child
                elif (
                    key in TEXT_FIELDS
                    and isinstance(child, list)
                    and all(isinstance(line, str) for line in child)
                ):
                    for index, line in enumerate(child):
                        result["/".join((*path, key, str(index)))] = line
                elif (
                    key in TEXT_FIELDS
                    and isinstance(child, dict)
                    and child
                    and all(isinstance(line, str) for line in child.values())
                ):
                    # A variant map such as selectCta: {annually: ..., monthly: ...}.
                    for name, line in child.items():
                        result["/".join((*path, key, name))] = line
                elif isinstance(child, (dict, list)):
                    visit(child, (*path, key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, (*path, str(index)))

    visit(document.get("props", {}), ("props",))
    return result


def copy_placeholders(document: dict) -> dict[str, list[str]]:
    """The slots each editable value may use, for the fields that have any. The renderer fills
    these from the real product catalog, so this is the whole legal vocabulary: a token outside
    it renders literally, and a literal price in their place would outlive the catalog."""
    return {
        pointer: sorted(set(PLACEHOLDER.findall(text)))
        for pointer, text in copy_fields(document).items()
        if PLACEHOLDER.search(text)
    }


def needs_native(document: dict) -> bool:
    return document["component"] not in {"landing", "brand-studio-landing"} or any(
        s.get("component") == "quiz-hero" for s in document.get("props", {}).get("sections", [])
    )


def assets_in(value) -> set[str]:
    if isinstance(value, dict):
        return set().union(*(assets_in(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(assets_in(v) for v in value)) if value else set()
    return (
        {value}
        if isinstance(value, str) and value.startswith(("assets/", "_shared/assets/"))
        else set()
    )


def validate_design(design: StepDesign, document: dict, allowed_assets: set[str]) -> None:
    natives = [b for b in design.blocks if b.kind == "native"]
    if len(natives) != int(needs_native(document)):
        raise ValueError(
            "This design must retain exactly one native interaction for this step"
            if needs_native(document)
            else "This landing uses continue actions rather than a native interaction"
        )
    if not needs_native(document) and not any(b.action == "continue" for b in design.blocks):
        raise ValueError("A landing design needs a continue action")
    for block in design.blocks:
        if block.media and block.media not in allowed_assets:
            raise ValueError("Design media must be a saved funnel or imported asset")
        if needs_native(document) and block.action == "continue":
            raise ValueError("Use the native interaction to advance this step")
        if document["component"] == "success" and block.action != "none":
            raise ValueError("Success-page handoff belongs to its native controller")


def apply_proposal(
    document: dict, proposal: StepProposal, level: str, *, assets: set[str] | None = None
) -> dict:
    result = copy.deepcopy(document)
    allowed = copy_fields(document)
    for pointer, text in proposal.copyPatch.items():
        if pointer not in allowed or not text.strip() or len(text) > 4000:
            raise ValueError(f"Unsupported copy field: {pointer}")
        wanted = set(PLACEHOLDER.findall(allowed[pointer]))
        found = set(PLACEHOLDER.findall(text))
        if wanted != found:
            detail = ", ".join(
                part
                for part in (
                    f"invented {' '.join(sorted(found - wanted))}" if found - wanted else "",
                    f"dropped {' '.join(sorted(wanted - found))}" if wanted - found else "",
                )
                if part
            )
            raise ValueError(
                f"Copy for {pointer} {detail}. It may contain exactly "
                f"{' '.join(sorted(wanted))}, which the renderer fills from the real catalog; "
                "any other slot renders literally, so state no amount it does not provide."
            )
        parts = pointer.split("/")
        owner = result
        for part in parts[:-1]:
            owner = owner[int(part)] if isinstance(owner, list) else owner[part]
        owner[int(parts[-1]) if isinstance(owner, list) else parts[-1]] = text
    if proposal.design:
        design = proposal.design.model_dump()
        if level == "light":
            previous = document.get("design")

            def shape(value):
                return {
                    **value,
                    "blocks": [
                        {
                            k: v
                            for k, v in b.items()
                            if k not in {"heading", "body", "label", "items"}
                        }
                        for b in value["blocks"]
                    ],
                }

            if not previous or shape(previous) != shape(design):
                raise ValueError("Light edits preserve composition, media and theme")
        validate_design(proposal.design, document, assets_in(document) | (assets or set()))
        result["design"] = design
    if level == "heavy" and not proposal.design:
        raise ValueError("Heavy redesign requires a structured composition")
    return result


def canonical_url(raw: str) -> str:
    url = urlsplit(raw.strip())
    if url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password:
        raise ValueError("Paste an HTTP or HTTPS page URL")
    query = [
        (k, v)
        for k, v in parse_qsl(url.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid", "msclkid"}
    ]
    return urlunsplit(
        (
            url.scheme.lower(),
            url.netloc.lower(),
            url.path.rstrip("/") or "/",
            urlencode(sorted(query)),
            url.fragment,
        )
    )


def require_renderer(lab: Path) -> None:
    path = lab / "funnels/growth-capabilities.json"
    caps = json.loads(path.read_text()) if path.is_file() else {}
    if CONTRACT not in caps.get("contracts", []):
        raise ValueError(
            "Install funnel-steps-v1 in the renderer before building or publishing this funnel"
        )
