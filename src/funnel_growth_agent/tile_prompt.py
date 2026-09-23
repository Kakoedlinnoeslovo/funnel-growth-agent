"""House-style prompt builder for showcase tiles. Pure and deterministic: Claude supplies a
brief, medium, palette, background and references; this turns them into the narrative
paragraph Nano Banana wants (medium noun, technique nouns, named colours, explicit
background, explicit composition, reference roles by ordinal, trailing text rule).

Every medium belongs to a visual family (graphic, studio, ugc, editorial, screen). The family
is the hypothesis the agent tests; the medium is how it renders. The six original mediums keep
their exact wording so cached tiles stay valid: the cache stem hashes the prompt."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from .models import LETTERING_TEXT_RE, ShowcaseImagePlan, VisualFamily

ReferenceKind = Literal["tile", "creative", "video", "previous"]

ORDINALS = ("first", "second", "third", "fourth")

# ---------------------------------------------------------------------------------------
# Studio / graphic house style (the original six mediums; do not reword, see module doc)

COMPOSITION = (
    "Compose it as a single hero subject, centred, filling about 60% of the frame, with "
    "generous empty margin on all sides; keep everything important inside the central 4:3 "
    "area because the tile is cropped at its left and right edges. If two states are "
    "compared, split one object down its middle or stack the states close together in that "
    "central area; never push them out to the far left and right."
)
STUDIO_PALETTE_RULE = "Use only {palette} with one dominant colour."
STUDIO_AXES = (
    "Compose it from a slightly elevated three-quarter view.",
    "Light it with one soft key light from the upper left so the subject casts a short, "
    "soft shadow.",
    "Treat the background as a subtle two-tone studio sweep, darker toward the bottom edge.",
)

# ---------------------------------------------------------------------------------------
# UGC / editorial / screen: real people, real places, phone or documentary cameras

UGC_COMPOSITION = (
    "Frame it like a phone photo: the person and what they hold sit in the middle at chest "
    "height, filling about 60% of the height, with the surroundings soft around them. "
    "Off-centre by a hand's width is fine, but keep the whole person and the object inside "
    "the central 4:3 area because the tile is cropped at its left and right edges. If two "
    "states are compared, the person holds both within one hand-span in that central area, "
    "never one at each edge."
)
EDITORIAL_COMPOSITION = (
    "Compose it as a single hero subject in a real setting, centred, filling about 60% of "
    "the frame, with the environment softly around it; keep everything important inside the "
    "central 4:3 area because the tile is cropped at its left and right edges. If two states "
    "are compared, keep them close together in that central area; never push them out to "
    "the far left and right."
)
NATURAL_PALETTE_RULE = (
    "Let {palette} lead the frame through the clothing, the object and the light, with the "
    "rest of the scene muted and natural."
)
WINDOW_LIGHT = "Light it with soft daylight from a window to one side."
OVERCAST_LIGHT = "Shoot it outdoors under flat overcast daylight."
GOLDEN_LIGHT = "Shoot it in warm, low golden-hour light."


@dataclass(frozen=True)
class MediumStyle:
    family: VisualFamily
    opener: str
    background: str
    composition: str
    palette_rule: str
    axes: tuple[str, ...]
    allows_text: bool = False
    people: bool = False
    one_line: str = ""


MEDIUM_STYLES: dict[str, MediumStyle] = {
    "flat-vector": MediumStyle(
        family="graphic",
        opener=(
            "A flat vector illustration of {brief}. Clean closed shapes, bold 3px outlines, flat "
            "two-tone cel-shading, crisp edges, no gradients and no texture, artwork that exports "
            "as editable SVG paths."
        ),
        background="a flat, solid off-white canvas",
        composition=COMPOSITION,
        palette_rule=STUDIO_PALETTE_RULE,
        axes=STUDIO_AXES,
        one_line="flat vector shapes with bold outlines, exports as SVG",
    ),
    "lettering": MediumStyle(
        family="graphic",
        opener=(
            'A typographic specimen: the words "{text}" set as custom display lettering, {brief}. '
            "Sharp bezier letterforms, consistent stroke weight, tight optical kerning, at most "
            "one decorative flourish."
        ),
        background="a flat, solid single-colour field",
        composition=COMPOSITION,
        palette_rule=STUDIO_PALETTE_RULE,
        axes=STUDIO_AXES,
        allows_text=True,
        one_line="custom display lettering of the quoted words; the only medium with text",
    ),
    "photo": MediumStyle(
        family="studio",
        opener=(
            "A photorealistic studio photograph of {brief}. Shot on an 85mm lens at f/2.8, soft "
            "diffused key light from the upper left, true skin and material texture, shallow "
            "depth of field, editorial finish."
        ),
        background="a seamless studio sweep",
        composition=COMPOSITION,
        palette_rule=STUDIO_PALETTE_RULE,
        axes=STUDIO_AXES,
        people=True,
        one_line="polished studio photograph, 85mm, seamless sweep",
    ),
    "illustration": MediumStyle(
        family="graphic",
        opener=(
            "A hand-drawn illustration of {brief}. Visible brush and pencil texture, confident "
            "line work, painterly flat colour blocks with a touch of grain."
        ),
        background="a flat, lightly textured paper tone",
        composition=COMPOSITION,
        palette_rule=STUDIO_PALETTE_RULE,
        axes=STUDIO_AXES,
        one_line="hand-drawn illustration with brush and pencil texture",
    ),
    "3d-icons": MediumStyle(
        family="graphic",
        opener=(
            "A 3D rendered icon of {brief}. Soft matte clay material, rounded bevelled edges, "
            "gentle global illumination, subtle ambient occlusion, floating just above the "
            "ground plane."
        ),
        background="a smooth studio backdrop with a soft floor shadow",
        composition=COMPOSITION,
        palette_rule=STUDIO_PALETTE_RULE,
        axes=STUDIO_AXES,
        one_line="soft matte clay 3D icon",
    ),
    "mockup": MediumStyle(
        family="studio",
        opener=(
            "A product mockup showing {brief}. The design sits on one real physical object, "
            "photographed in even studio light with realistic print and material texture, "
            "without screens, interface chrome or buttons."
        ),
        background="a plain studio surface",
        composition=COMPOSITION,
        palette_rule=STUDIO_PALETTE_RULE,
        axes=STUDIO_AXES,
        one_line="the design printed on one real object, studio light, no screens",
    ),
    "ugc-selfie": MediumStyle(
        family="ugc",
        opener=(
            "A front-camera phone selfie of {brief}. Arm's-length framing, the person looking "
            "into the lens, available light from a window, shot on an iPhone, candid and "
            "unposed, true-to-life skin texture with subtle grain, slightly imperfect framing; "
            "not a studio shoot, no phone UI or device frame."
        ),
        background="a real, slightly untidy room at home",
        composition=UGC_COMPOSITION,
        palette_rule=NATURAL_PALETTE_RULE,
        axes=(WINDOW_LIGHT, OVERCAST_LIGHT, GOLDEN_LIGHT),
        people=True,
        one_line="front-camera phone selfie of a real person with the thing they made",
    ),
    "ugc-candid": MediumStyle(
        family="ugc",
        opener=(
            "A candid phone photo of {brief}, taken by a friend from across the table. "
            "Available light, shot on an iPhone, unposed and mid-moment, true skin texture "
            "with subtle grain, everyday clutter left in place; not a studio shoot, no phone "
            "UI or device frame."
        ),
        background="a kitchen or cafe table with everyday things on it",
        composition=UGC_COMPOSITION,
        palette_rule=NATURAL_PALETTE_RULE,
        axes=(
            "Shoot it over the friend's shoulder, slightly from above.",
            WINDOW_LIGHT,
            GOLDEN_LIGHT,
        ),
        people=True,
        one_line="candid phone photo of a real person mid-moment, taken by a friend",
    ),
    "ugc-unboxing": MediumStyle(
        family="ugc",
        opener=(
            "An overhead phone photo of {brief}, hands in frame opening or holding it on a "
            "table. Available light, shot on an iPhone, candid, packaging and everyday clutter "
            "visible, true material texture with subtle grain, slightly tilted framing; not a "
            "studio shoot, no phone UI or device frame."
        ),
        background="a wooden table top seen from above",
        composition=UGC_COMPOSITION,
        palette_rule=NATURAL_PALETTE_RULE,
        axes=(
            "Shoot it straight down from directly overhead.",
            WINDOW_LIGHT,
            "Shoot it from a low three-quarter angle across the table.",
        ),
        people=True,
        one_line="overhead phone photo, hands unboxing or holding the printed result",
    ),
    "lifestyle": MediumStyle(
        family="editorial",
        opener=(
            "A lifestyle editorial photograph of {brief}. Shot on a 35mm lens in natural "
            "window light, a real environment with believable props, magazine-grade colour "
            "grading, true material texture, calm and unhurried."
        ),
        background="a bright home studio with a window to one side",
        composition=EDITORIAL_COMPOSITION,
        palette_rule=NATURAL_PALETTE_RULE,
        axes=(STUDIO_AXES[0], WINDOW_LIGHT, GOLDEN_LIGHT),
        people=True,
        one_line="magazine-style lifestyle photograph of a person or scene, 35mm, window light",
    ),
    "device-screen": MediumStyle(
        family="screen",
        opener=(
            "A photograph of {brief} shown on the screen of a phone or laptop held or placed "
            "in a real scene. The screen shows only the finished artwork with soft, generic "
            "interface edges and no readable interface text; the device and hands are real, "
            "lit by available light, with true material texture."
        ),
        background="a desk by a window",
        composition=EDITORIAL_COMPOSITION,
        palette_rule=NATURAL_PALETTE_RULE,
        axes=(
            "Shoot it over the shoulder of the person holding the device.",
            WINDOW_LIGHT,
            "Shoot it straight down from directly overhead.",
        ),
        one_line=(
            "a real phone or laptop showing the finished artwork, no readable interface text"
        ),
    ),
}

FAMILY_MEDIUMS: dict[str, tuple[str, ...]] = {
    family: tuple(name for name, style in MEDIUM_STYLES.items() if style.family == family)
    for family in ("graphic", "studio", "ugc", "editorial", "screen")
}

# Kept for callers and tests that import the original names.
MEDIUM_OPENERS = {name: style.opener for name, style in MEDIUM_STYLES.items()}
DEFAULT_BACKGROUND = {name: style.background for name, style in MEDIUM_STYLES.items()}
VARIANT_AXES = ("", *STUDIO_AXES)

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
# A ugc tile sits next to studio neighbours without becoming one, and `previous` locks the
# same person across a group.
UGC_REFERENCE_ROLE = {
    "tile": (
        "The {ordinal} image is a real tile from the same gallery: keep its subject scale and "
        "colour temperature so the two sit together, but this one is a phone photo of a real "
        "person, not a studio render."
    ),
    "previous": (
        "The {ordinal} image is the neighbouring tile in this gallery and shows the same "
        "person: reproduce their exact face, hair, skin tone and distinctive features, in a "
        "new moment."
    ),
}


@dataclass(frozen=True)
class ReferenceRole:
    kind: ReferenceKind
    ref: str


def reference_kind(ref: str) -> ReferenceKind:
    if ref == "previous":
        return "previous"
    if ref.startswith("video:"):
        return "video"
    if ref.startswith("creative:"):
        return "creative"
    return "tile"


def style_of(medium: str | None) -> MediumStyle:
    """The style table row for a medium; raw-prompt tiles (no medium) behave as `photo`."""
    return MEDIUM_STYLES[medium or "photo"]


def family_of_medium(medium: str | None) -> VisualFamily | None:
    style = MEDIUM_STYLES.get(medium or "")
    return style.family if style else None


def reference_role_sentence(kind: ReferenceKind, ordinal: str, style: MediumStyle) -> str:
    if kind == "video":
        return f"The {ordinal} image is an identified frame from the source video. Use it as composition and subject evidence only, never fabricate a product screenshot, endorsement or product capability."
    template = REFERENCE_ROLE[kind]
    if style.people and style.family in {"ugc", "editorial"}:
        template = UGC_REFERENCE_ROLE.get(kind, template)
    return template.format(ordinal=ordinal)


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
        return plan.prompt.strip() + image_contract(plan)
    assert plan.brief is not None and plan.medium is not None
    style = style_of(plan.medium)
    brief = " ".join(plan.brief.split()).rstrip(".")
    if plan.medium == "lettering":
        text = plan.lettering_text or ""
        opener = style.opener.format(text=text, brief=_strip_quoted(brief, text))
    else:
        opener = style.opener.format(brief=brief)
    gallery = f'It belongs to the "{plan.group}" gallery of a landing page'
    if group_caption:
        gallery += f": {group_caption.strip().rstrip('.')}."
    else:
        gallery += "."
    parts = [
        opener,
        gallery,
        style.palette_rule.format(palette=_palette_phrase(plan.palette)),
        f"The background is {plan.background or style.background}.",
        plan.composition or style.composition,
    ]
    for index, role in enumerate(references[: len(ORDINALS)]):
        parts.append(reference_role_sentence(role.kind, ORDINALS[index], style))
    parts.append(image_contract(plan))
    parts.append(trailing_sentence(plan))
    return " ".join(part.strip() for part in parts if part.strip())


def variant_prompts(base: str, n: int, axes: Sequence[str] | None = None) -> list[str]:
    """n prompt variants, each differing from the base along one controlled axis. Variant 0 is
    the base; the axis sentence goes before the trailing text sentence. `axes` defaults to
    the studio axes; pass `style_of(medium).axes` for the medium's own."""
    axes = tuple(axes) if axes is not None else STUDIO_AXES
    n = max(1, min(n, len(axes) + 1))
    base = base.strip()
    out = [base]
    head, sep, tail = base.rpartition(". ")
    for axis in axes[: n - 1]:
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


def image_contract(plan: ShowcaseImagePlan) -> str:
    if (
        not any((plan.composition, plan.intended_message, plan.art_direction))
        and plan.role == "illustration"
        and plan.aspect_ratio == "16:9"
    ):
        return ""  # Keep old cached briefs byte-for-byte stable.
    return " " + " ".join(
        filter(
            None,
            [
                f"Asset role: {plan.role}. Target aspect ratio: {plan.aspect_ratio}.",
                f"Intended message: {plan.intended_message}." if plan.intended_message else None,
                f"Shared art direction: {plan.art_direction}." if plan.art_direction else None,
                f"Composition: {plan.composition}." if plan.prompt and plan.composition else None,
                "This is a generated illustration, never present it as a real Recraft output or interface screenshot. Keep the subject legible at desktop and phone display sizes.",
            ],
        )
    )
