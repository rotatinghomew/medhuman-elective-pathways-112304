"""Forecast demand cases that feed joint seat allocation."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Optional

from .history import HistoricalModel, visible_state_token
from .stats import binomial_upper_count
from .students import mature_terms, situations


def current_model(bundle, term: int, as_of: int) -> HistoricalModel:
    training = [t for t in mature_terms(bundle) if t < term]
    return HistoricalModel(bundle, training, as_of)


def build_demand_cases(bundle, term: int, as_of: int,
                       model: Optional[HistoricalModel] = None) -> dict[str, Any]:
    """Construct exact and stress demand sets.

    The key modelling choice is deliberate: every currently unplaced student is
    included in service cases even though history shows only a fraction finally
    takes an alternative.  Expected fractions are reported separately.
    """
    if model is None:
        model = current_model(bundle, term, as_of)
    sits = situations(bundle, term, as_of)
    unplaced = [s for s in sits if s["status"] == "unplaced_after_drop"]
    pending_other = [
        s for s in sits
        if s["pending_courses"] and s["status"] != "unplaced_after_drop"
    ]
    pending_ids = {s["student"] for s in pending_other}
    stable_no_pending = [
        s for s in sits
        if s["status"] == "active_planned" and s["student"] not in pending_ids
    ]
    active_planned = [s for s in sits if s["status"] == "active_planned"]

    upper_units: list[dict[str, Any]] = list(unplaced) + list(pending_other)
    forecast_rows = []
    by_token: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for s in stable_no_pending:
        token = visible_state_token(bundle, s["student"], as_of)
        by_token[(s["direction"], token)].append(s)

    # Round 4 is settlement.  At the end of round 3 all round-3 facts with
    # received_round<=3 are already in the state; there is no future seat round
    # to provision.  Earlier rounds do need an upper bound for delayed receipts
    # and next-round transfers learned from historical terms.
    provision_for_future_round = as_of < 3
    extra_upper_by_direction: Counter[str] = Counter()
    expected_stable_alt = 0.0
    upper_stable_alt = 0
    for (direction, token), students in sorted(by_token.items()):
        p, k, n, low, high = model.alt_demand_rate(direction, token)
        upper_n = binomial_upper_count(len(students), high)
        provision_n = upper_n if provision_for_future_round else 0
        extra_upper_by_direction[direction] += provision_n
        expected_stable_alt += len(students) * p
        upper_stable_alt += upper_n
        forecast_rows.append({
            "cohort": "active_without_pending",
            "direction": direction,
            "state": token,
            "current_n": len(students),
            "historical_n": n,
            "historical_alt_count": k,
            "expected_alt_rate": p,
            "alt_rate_low": low,
            "alt_rate_high": high,
            "expected_alt_students": len(students) * p,
            "statistical_upper_alt_students": upper_n,
            "provision_upper_alt_students": provision_n,
        })
        for i in range(provision_n):
            upper_units.append({
                "id": f"upper-{direction}-{token}-{i+1}",
                "student": f"upper-{direction}-{i+1}",
                "direction": direction,
                "synthetic": True,
            })

    expected_unplaced_alt = 0.0
    for direction in sorted({s["direction"] for s in unplaced}):
        students = [s for s in unplaced if s["direction"] == direction]
        token = "unplaced"
        p, k, n, low, high = model.alt_demand_rate(direction, token)
        expected_unplaced_alt += len(students) * p
        forecast_rows.append({
            "cohort": "currently_unplaced",
            "direction": direction,
            "state": token,
            "current_n": len(students),
            "historical_n": n,
            "historical_alt_count": k,
            "expected_alt_rate": p,
            "alt_rate_low": low,
            "alt_rate_high": high,
            "expected_alt_students": len(students) * p,
            "upper_alt_students": len(students),
            "service_treatment": "all current unplaced students retained in seat stress case",
        })

    cases = {
        "known_unplaced": {
            "label": "当前可见无课学生",
            "units": unplaced,
        },
        "known_plus_requests": {
            "label": "无课学生加未确认申请",
            "units": unplaced + pending_other,
        },
        "upper_plausible": {
            "label": "无课+申请+历史区间上沿的新增转移",
            "units": upper_units,
        },
        "stress_all_planned": {
            "label": "所有目前仍在上计划课的学生同时转移",
            "units": unplaced + active_planned,
        },
    }
    counts = {
        "total_students": len(sits),
        "unplaced": len(unplaced),
        "unplaced_by_direction": dict(Counter(s["direction"] for s in unplaced)),
        "pending_other": len(pending_other),
        "pending_other_by_direction": dict(Counter(s["direction"] for s in pending_other)),
        "active_planned": len(active_planned),
        "active_planned_by_direction": dict(Counter(s["direction"] for s in active_planned)),
        "expected_unplaced_alternative_seats": expected_unplaced_alt,
        "pending_requests_counted_as_explicit_demand": len(pending_other),
        "expected_latent_alternative_seats_from_stable": expected_stable_alt,
        "statistical_upper_latent_alternative_seats_from_stable": upper_stable_alt,
        "upper_latent_alternative_seats_from_stable": sum(extra_upper_by_direction.values()),
        "provision_for_future_round": provision_for_future_round,
        "upper_extra_by_direction": dict(extra_upper_by_direction),
        "expected_total_named_demand": (
            expected_unplaced_alt + len(pending_other) + expected_stable_alt
        ),
    }
    return {"counts": counts, "forecast_rows": forecast_rows, "cases": cases}
