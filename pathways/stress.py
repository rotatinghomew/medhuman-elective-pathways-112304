"""Time-band and multi-credit sensitivity checks.

The exchange bundle has no per-course credit column.  The production path
model therefore treats one eligible course as one substitution seat, matching
all recovered outcomes.  Because V3/B sets ``required_credits=2``, this module
also reports a clearly labelled hypothetical in which every course is one
credit and two distinct eligible courses may be stacked.  That hypothetical is
not the audited baseline: the data contract says equivalent courses cannot be
double counted.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .capacity import _pool_band
from .students import situations, term_occupancy


def time_band_collisions(bundle, term: int, policy, plan_report: dict[str, Any]) -> dict[str, Any]:
    bands: dict[str, list[str]] = defaultdict(list)
    for course in plan_report["courses"]:
        if course.get("time_band"):
            bands[course["time_band"]].append(course["course"])
    pool_bands: dict[str, list[str]] = defaultdict(list)
    for direction, courses in policy.courses_by_direction.items():
        for course in courses:
            band = _pool_band(bundle, term, course)
            if band:
                pool_bands[band].append(course)
    return {
        "proposal_id": plan_report["proposal_id"],
        "proposal_bands": {b: sorted(cs) for b, cs in sorted(bands.items())},
        "same_band_course_groups": {b: sorted(cs) for b, cs in bands.items() if len(cs) > 1},
        "pool_equivalent_bands": {b: sorted(set(cs)) for b, cs in sorted(pool_bands.items())},
    }


def two_credit_sensitivity(bundle, term: int, as_of: int, policy,
                           plan_report: dict[str, Any]) -> dict[str, Any]:
    sits = situations(bundle, term, as_of)
    occupancy = term_occupancy(bundle, term, as_of)
    by_student = {s["student"]: s for s in sits}
    course_rows = {c["course"]: c for c in plan_report["courses"]}

    direction_results = {}
    for direction, required in policy.required_credits.items():
        if required <= 1:
            continue
        eligible = set(policy.courses(direction))
        students = [s for s in sits if s["direction"] == direction]
        deficits = []
        total_extra_seats = 0
        active_eligible = 0
        for s in students:
            held = [c for c in s["active_courses"] if c in eligible]
            count = len(held)
            active_eligible += count
            deficit = max(0, required - count)
            total_extra_seats += deficit
            if deficit:
                deficits.append({"student": s["student"], "held": held, "deficit": deficit})

        # Proposal-specific bands override historical pool bands.
        def band(course: str):
            if course in course_rows:
                return course_rows[course]["time_band"]
            return _pool_band(bundle, term, course)

        eligible_bands = {c: band(c) for c in eligible if band(c)}
        band_to_courses = defaultdict(list)
        for c, b in eligible_bands.items():
            band_to_courses[b].append(c)
        distinct_bands = sorted(band_to_courses)
        can_stack_without_conflict = len(distinct_bands) >= required

        # For a two-course load, every pair needs two different bands.  In the
        # seeded data there are two bands, so every second seat must be in the
        # minority/opposite band.  Compute the strongest simple bottleneck:
        # number of students with a deficit versus net seats in each band.
        net_by_band = Counter()
        gross_by_band = Counter()
        for course, row in course_rows.items():
            b = row["time_band"]
            if b:
                net_by_band[b] += max(0, row["net_capacity"])
                gross_by_band[b] += row["gross_capacity"]
        # Include published home/pool capacity for non-proposal eligible courses.
        for course in eligible - set(course_rows):
            b = _pool_band(bundle, term, course)
            if b:
                for pool_row in bundle.rows("pool.csv"):
                    if (int(pool_row["term"]) == term and pool_row["course"] == course
                            and int(pool_row["round"]) == as_of):
                        occupied = len(occupancy.get(course, set()))
                        net_by_band[b] += max(0, int(pool_row["capacity"]) - occupied)
                        gross_by_band[b] += int(pool_row["capacity"])
                        break

        direction_results[direction] = {
            "required_credits": required,
            "students": len(students),
            "active_eligible_course_seats": active_eligible,
            "hypothetical_additional_one_credit_seats": total_extra_seats,
            "students_with_deficit": len(deficits),
            "deficit_students": deficits,
            "eligible_course_bands": eligible_bands,
            "band_to_courses": {b: sorted(cs) for b, cs in band_to_courses.items()},
            "net_capacity_by_band_in_plan_plus_pool": dict(net_by_band),
            "can_stack_without_time_conflict_if_rules_allow_multiple": can_stack_without_conflict,
            "contract_compliant_warning": (
                "资料口径规定等价课程同时只能计一门，且没有课程学数字段；"
                "因此这只是‘每门1学分且允许叠加两门’的敏感性，不是可审计毕业结论。"
            ),
        }

    collisions = time_band_collisions(bundle, term, policy, plan_report)
    return {
        "proposal_id": plan_report["proposal_id"],
        "scope": plan_report["scope"],
        "direction_checks": direction_results,
        "collisions": collisions,
    }
