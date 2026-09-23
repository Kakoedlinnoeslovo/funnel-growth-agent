"""Keep measured outcomes, Meta attribution, and missing evidence distinct."""

from copy import deepcopy


def audience_for_selection(report: dict, creatives: list[dict]) -> dict:
    ids = {str(row["id"]) for row in creatives if row.get("source") != "upload"}
    measured = {str(row.get("ad_id")): row for row in report.get("creatives", [])}
    block = deepcopy(report.get("creative_audience") or {})
    block["creative_funnels"] = [
        r for r in block.get("creative_funnels", []) if str(r.get("ad_id")) in ids
    ]
    for dataset in (block.get("breakdowns") or {}).values():
        dataset["rows"] = [r for r in dataset.get("rows", []) if str(r.get("ad_id")) in ids]
    outcomes = []
    for creative in creatives:
        row = measured.get(str(creative["id"]), {})
        outcomes.append(
            {
                "adId": creative["id"],
                "name": creative["name"],
                "posthogPayments": row.get("payments"),
                "warehousePayers": row.get("payers"),
                "signups": row.get("signups_utm"),
                "checkouts": row.get("checkouts"),
                "spend": row.get("spend_usd"),
                "clicks": row.get("link_clicks"),
            }
        )
    observed = any(
        (r["posthogPayments"] or 0) > 0 or (r["warehousePayers"] or 0) > 0 for r in outcomes
    )
    block.update(
        outcomes=outcomes,
        status="observed" if observed else "insufficient_evidence",
        summary=(
            "Observed conversions are shown by source; attribution is directional."
            if observed
            else "Insufficient conversion evidence. Explore clicks, CTA engagement and checkouts without declaring a winning audience."
        ),
    )
    block.setdefault(
        "coverage",
        {"status": "unavailable", "reason": "This saved report has no audience breakdowns."},
    )
    return block
