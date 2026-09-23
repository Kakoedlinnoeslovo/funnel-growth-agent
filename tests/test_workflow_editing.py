from funnel_growth_agent.workflow import RevisionRequest
from funnel_growth_agent.workflow_editing import merge_changes


def test_editing_one_image_keeps_other_slots_and_cross_slot_references():
    first = {
        "group": "Vectors",
        "slot": 0,
        "brief": "A detailed vector illustration of a sunny landscape",
        "medium": "flat-vector",
        "palette": ["#14532d", "#fef3c7"],
    }
    second = {
        "group": "Vectors",
        "slot": 1,
        "brief": "A detailed vector illustration of a blue landscape",
        "medium": "flat-vector",
        "palette": ["#14532d", "#fef3c7"],
        "references": ["previous"],
    }
    previous = {
        "copy": {"hero": {"ctaLabel": "Create vectors"}},
        "media": {"showcase": [first, second]},
    }
    edited = {**second, "brief": "A detailed vector illustration of a green mountain landscape"}
    request = RevisionRequest.model_validate(
        {"expectedRevision": 1, "changes": {"media": {"showcase": [edited]}}}
    )
    merged = merge_changes(
        previous, request.changes.model_dump(by_alias=True, exclude_unset=True, exclude_none=True)
    )
    assert merged["copy"] == previous["copy"]
    assert merged["media"]["showcase"] == [first, edited]
    assert previous["media"]["showcase"][1] == second


def test_composition_empty_omissions_restore_sections_without_erasing_copy():
    previous = {
        "copy": {"hero": {"ctaLabel": "Create"}},
        "composition": {"omit": ["showcase"], "order": ["final-cta"]},
    }
    request = RevisionRequest.model_validate(
        {
            "expectedRevision": 1,
            "changes": {"composition": {"omit": [], "order": ["showcase", "final-cta"]}},
        }
    )
    merged = merge_changes(
        previous, request.changes.model_dump(by_alias=True, exclude_unset=True, exclude_none=True)
    )
    assert merged["composition"] == {"omit": [], "order": ["showcase", "final-cta"]}
    assert merged["copy"] == previous["copy"]
