"""House-style prompt builder for showcase tiles. Pure and deterministic: Claude supplies a
brief, medium, palette, background and references; this turns them into the narrative
paragraph Nano Banana wants (medium noun, technique nouns, named colours, explicit
background, explicit composition, reference roles by ordinal, trailing text rule)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from .models import LETTERING_TEXT_RE, ShowcaseImagePlan

ReferenceKind = Literal["tile", "creative", "previous"]

ORDINALS = ("first", "second", "third", "fourth")

COMPOSITION = (
    "Compose it as a single hero subject, centred, filling about 60% of the frame, with "
    "generous empty margin on all sides; keep everything important inside the central 4:3 "
    "area because the tile is cropped at its left and right edges. If two states are "
    "compared, split one object down its middle or stack the states close together in that "
    "central area; never push them out to the far left and right."
)

DEFAULT_BACKGROUND = {
    "flat-vector": "a flat, solid off-white canvas",
    "lettering": "a flat, solid single-colour field",
    "photo": "a seamless studio sweep",
    "illustration": "a flat, lightly textured paper tone",
    "3d-icons": "a smooth studio backdrop with a soft floor shadow",
    "mockup": "a plain studio surface",
}

MEDIUM_OPENERS = {
    "flat-vector": (
        "A flat vector illustration of {brief}. Clean closed shapes, bold 3px outlines, flat "
        "two-tone cel-shading, crisp edges, no gradients and no texture, artwork that exports "
        "as editable SVG paths."
    ),
    "lettering": (
        'A typographic specimen: the words "{text}" set as custom display lettering, {brief}. '
        "Sharp bezier letterforms, consistent stroke weight, tight optical kerning, at most "
        "one decorative flourish."
    ),
    "photo": (
        "A photorealistic studio photograph of {brief}. Shot on an 85mm lens at f/2.8, soft "
        "diffused key light from the upper left, true skin and material texture, shallow "
        "depth of field, editorial finish."
    ),
    "illustration": (
        "A hand-drawn illustration of {brief}. Visible brush and pencil texture, confident "
        "line work, painterly flat colour blocks with a touch of grain."
    ),
    "3d-icons": (
        "A 3D rendered icon of {brief}. Soft matte clay material, rounded bevelled edges, "
        "gentle global illumination, subtle ambient occlusion, floating just above the "
        "ground plane."
    ),
    "mockup": (
        "A product mockup showing {brief}. The design sits on one real physical object, "
        "photographed in even studio light with realistic print and material texture, "
        "without screens, interface chrome or buttons."
    ),
}

REFERENCE_ROLE = {
    "tile": (
        "The {ordinal} image is a style reference from the same gallery: match its rendering "
        "technique, colour saturation, lighting and level of detail, but show a different "
        "subject."
    ),
    "creative": (
        "The {ordinal} image is a winning ad: borrow its subject treatment, palette and split "
        "composition, not its interface elements, buttons or headline text."
    ),
    "previous": (
        "The {ordinal} image is the neighbouring tile in this gallery: stay in the same "
        "visual family so the two read as one set."
    ),
}

VARIANT_AXES = (
    "",
    "Compose it from a slightly elevated three-quarter view.",
    "Light it with one soft key light from the upper left so the subject casts a short, "
    "soft shadow.",
    "Treat the background as a subtle two-tone studio sweep, darker toward the bottom edge.",
)


@dataclass(frozen=True)
class ReferenceRole:
    kind: ReferenceKind
    ref: str


def reference_kind(ref: str) -> ReferenceKind:
    if ref == "previous":
        return "previous"
    if ref.startswith("creative:"):
        return "creative"
    return "tile"


def _palette_phrase(palette: Sequence[str]) -> str:
    items = list(palette)
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _strip_quoted(brief: str, text: str) -> str:
    stripped = brief.replace(f'"{text}"', "").strip(" ,;:")
    stripped = " ".join(stripped.split())
    return stripped or "set with confident, generous spacing"


def trailing_sentence(plan: ShowcaseImagePlan) -> str:
    text = plan.lettering_text if plan.medium == "lettering" else None
    if text:
        return f'Render exactly the text "{text}" and no other text.'
    return "No text."


def build_tile_prompt(
    plan: ShowcaseImagePlan,
    group_caption: str | None,
    references: Sequence[ReferenceRole] = (),
) -> str:
    """The base prompt (variant 0). Raw `prompt` overrides pass through untouched."""
    if plan.prompt:
        return plan.prompt.strip()
    assert plan.brief is not None and plan.medium is not None
    brief = " ".join(plan.brief.split()).rstrip(".")
    if plan.medium == "lettering":
        text = plan.lettering_text or ""
        opener = MEDIUM_OPENERS["lettering"].format(text=text, brief=_strip_quoted(brief, text))
    else:
        opener = MEDIUM_OPENERS[plan.medium].format(brief=brief)
    gallery = f'It belongs to the "{plan.group}" gallery of a landing page'
    if group_caption:
        gallery += f": {group_caption.strip().rstrip('.')}."
    else:
        gallery += "."
    parts = [
        opener,
        gallery,
        f"Use only {_palette_phrase(plan.palette)} with one dominant colour.",
        f"The background is {plan.background or DEFAULT_BACKGROUND[plan.medium]}.",
        COMPOSITION,
    ]
    for index, role in enumerate(references[: len(ORDINALS)]):
        parts.append(REFERENCE_ROLE[role.kind].format(ordinal=ORDINALS[index]))
    parts.append(trailing_sentence(plan))
    return " ".join(part.strip() for part in parts if part.strip())


def variant_prompts(base: str, n: int) -> list[str]:
    """n prompt variants, each differing from the base along one controlled axis. Variant 0 is
    the base; the axis sentence goes before the trailing text sentence."""
    n = max(1, min(n, len(VARIANT_AXES)))
    base = base.strip()
    out = [base]
    head, sep, tail = base.rpartition(". ")
    for axis in VARIANT_AXES[1:n]:
        if sep and tail.lower().endswith("text."):
            out.append(f"{head}. {axis} {tail}")
        else:
            out.append(f"{base} {axis}")
    return out


def prompt_word_count(text: str) -> int:
    return len(text.split())


def lettering_text_of(brief: str) -> str | None:
    match = LETTERING_TEXT_RE.search(brief)
    return match.group(1) if match else None
