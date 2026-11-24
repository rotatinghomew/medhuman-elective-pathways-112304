"""Coordination alerts, policy comparability and evidence-aware risk ranges."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from .capacity import analyze_plan
from .history import course_evidence, path_performance
from .policy import Policy, cancelled_courses, policy_for_term
from .stats import wilson
from .students import mature_terms


def commitment_coordination(bundle, term: int, as_of: int, policy: Policy) -> dict[str, Any]:
    entrances = {r["id"]: r for r in bundle.rows("entrance.csv")}
    cancels = cancelled_courses(bundle, term, as_of, effective_only=False)
    rows = []
    for c in bundle.rows("commitment.csv"):
        student = c["student"]
        entrance = entrances.get(student)
        if entrance is None or int(entrance["term"]) != term:
            continue
        if int(c["round"]) > as_of:
            continue
        direction = entrance["direction"]
        course = c["course"]
        state = c["state"]
        active = state in {"confirmed", "active", "accepted", "held"}
        pending = state in {"requested", "pending", "draft", "waitlisted"}
        released = state in {"withdrawn", "cancelled", "released", "declined", "expired"}
        issues = []
        actions = []

        if course not in policy.courses(direction):
            issues.append("not_in_effective_equivalence")
        last_start = policy.last_start.get(direction)
        if last_start is not None and int(c["round"]) > int(last_start):
            issues.append("after_last_start_window")
        decision = cancels.get(course)
        if decision:
            if int(decision["effective_round"]) <= as_of:
                issues.append("course_cancelled")
                if active:
                    actions.append("必须逐人改约或协调其他院系席位")
                elif pending:
                    actions.append("向申请人说明去向已不存在并改荐路径")
            else:
                issues.append("cancellation_received_but_not_effective_yet")

        if released:
            disposition = "released"
            actions.append("席位已释放，不得继续计占")
        elif active and "course_cancelled" in issues:
            disposition = "urgent_confirmed_coordination"
        elif pending and "course_cancelled" in issues:
            disposition = "requested_destination_unavailable"
        elif issues:
            disposition = "policy_review_required"
            actions.append("按有效版次和时间窗逐人核对")
        elif active:
            disposition = "confirmed_occupies_seat"
        elif pending:
            disposition = "requested_not_occupying"
        else:
            disposition = "other_state_review"

        rows.append({
            "commitment_id": c["id"],
            "student": student,
            "direction": direction,
            "course": course,
            "round": int(c["round"]),
            "state": state,
            "occupies": active and "course_cancelled" not in issues,
            "disposition": disposition,
            "issues": issues,
            "actions": actions,
            "decision_received_round": int(decision["received_round"]) if decision else None,
        })
    counts = defaultdict(int)
    for row in rows:
        counts[row["disposition"]] += 1
    return {"rows": rows, "counts": dict(counts)}


def comparable_policy_terms(bundle, policy: Policy, direction: str) -> list[dict[str, Any]]:
    target_credits = policy.required_credits[direction]
    target_last = policy.last_start[direction]
    target_courses = set(policy.courses(direction))
    matches = []
    for term in mature_terms(bundle):
        historical = policy_for_term(bundle, term)
        courses = set(historical.courses(direction))
        same_rule = (
            historical.required_credits[direction] == target_credits
            and historical.last_start[direction] == target_last
        )
        same_course_set = courses == target_courses
        if same_rule and same_course_set:
            matches.append({"term": term, "version": historical.version,
                            "match": "rule_and_courses"})
        elif same_rule:
            matches.append({"term": term, "version": historical.version,
                            "match": "rule_only"})
    return matches


def policy_support_report(bundle, policy: Policy) -> dict[str, Any]:
    paths = path_performance(bundle)
    courses_evidence = course_evidence(bundle)
    by_direction_course: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"n": 0, "successes": 0, "terms": set(), "categories": []}
    )
    for row in paths:
        course = row["target_course"]
        if not course:
            continue
        key = (row["direction"], course)
        item = by_direction_course[key]
        item["n"] += row["n"]
        item["successes"] += row["successes"]
        item["terms"].update(row["terms"])
        item["categories"].append({
            "category": row["category"],
            "gap_rounds": row["gap_rounds"],
            "n": row["n"],
            "success_rate": row["success_rate"],
            "low": row["low"],
            "high": row["high"],
            "support": row["support"],
        })

    directions = {}
    for direction in sorted(policy.courses_by_direction):
        course_rows = []
        for course in sorted(policy.courses(direction)):
            agg = by_direction_course.get((direction, course))
            n = agg["n"] if agg else 0
            k = agg["successes"] if agg else 0
            terms = sorted(agg["terms"]) if agg else []
            p, low, high = wilson(k, n)
            ev = courses_evidence.get(course, {})
            course_rows.append({
                "course": course,
                "transfer_students": n,
                "transfer_successes": k,
                "observed_success_rate": (k / n) if n else None,
                "success_low": low,
                "success_high": high,
                "observed_terms": terms,
                "n_observed_terms": len(terms),
                "support": ev.get("support", "no_evidence") if n == 0 else (
                    "established" if n >= 10 and len(terms) >= 3 else
                    "limited" if n >= 5 or len(terms) >= 2 else "insufficient"
                ),
                "pool_evidence": ev,
                "path_categories": agg["categories"] if agg else [],
                "missing_evidence": _missing_evidence(course, n, terms, ev),
            })
        matches = comparable_policy_terms(bundle, policy, direction)
        directions[direction] = {
            "required_credits": policy.required_credits[direction],
            "last_start": policy.last_start[direction],
            "courses": course_rows,
            "comparable_mature_terms": matches,
            "has_exact_policy_history": any(m["match"] == "rule_and_courses" for m in matches),
            "credit_rule_warning": (
                "required_credits>1，但资料没有课程学数字段，且口径规定同学分等价组同时只能计一门；"
                "不能把它自动解释成两个席位或两门可叠加课。"
                if policy.required_credits[direction] > 1 else None
            ),
        }
    return {"version": policy.version, "term": policy.term, "directions": directions}


def _missing_evidence(course: str, n: int, terms: list[int], ev: dict) -> list[str]:
    missing = []
    if n == 0:
        missing.append("没有经该路径转入并结算的学生")
    elif n < 10:
        missing.append(f"转移学生仅 {n} 人，低于 10 人稳定阈值")
    if len(terms) == 0:
        missing.append("历史课程台账中没有该班/跨院班次或课时记录")
    elif len(terms) < 3:
        missing.append(f"仅有 {len(terms)} 个历史学期班次，低于 3 学期阈值")
    if not ev.get("historical_time_bands"):
        if ev.get("proposal_time_bands"):
            missing.append(
                "只有提案计划时段 "
                + "/".join(ev["proposal_time_bands"])
                + "，没有历史实际上课时段、课时容量利用或完课证据"
            )
        else:
            missing.append("没有历史上课时段/课时证据")
    if not ev.get("n_outcomes"):
        missing.append("没有该课程的学期结果样本")
    if course == "H6":
        missing.append("只有提案中的计划席位和时段，没有实际开放、利用或完成记录")
    return missing


def in_progress_path_summary(situations: list[dict[str, Any]], policy: Policy) -> dict[str, Any]:
    """Summarise observed current-term moves without treating them as final."""
    status_counts = defaultdict(int)
    target_counts = defaultdict(int)
    direction_status = defaultdict(lambda: defaultdict(int))
    credit_rows = []
    for sit in situations:
        d = sit["direction"]
        status_counts[sit["status"]] += 1
        direction_status[d][sit["status"]] += 1
        eligible = set(policy.courses(d))
        active_eligible = [c for c in sit["active_courses"] if c in eligible]
        for course in active_eligible:
            if sit["status"] == "transferred_or_reassigned":
                target_counts[(d, course)] += 1
        required = policy.required_credits[d]
        # Exchange rule: equivalent courses cannot both be counted.  Therefore
        # the countable contribution from this single equivalence group is at
        # most one credit/course substitution.
        countable = min(1, len(active_eligible))
        credit_rows.append({
            "student": sit["student"],
            "direction": d,
            "active_eligible_courses": active_eligible,
            "countable_credits_under_one_equivalent_rule": countable,
            "required_credits": required,
            "credit_requirement_currently_met": countable >= required,
            "multiple_equivalents_do_not_stack": len(active_eligible) > 1,
        })
    return {
        "status_counts": dict(status_counts),
        "by_direction": {d: dict(c) for d, c in direction_status.items()},
        "observed_transfer_targets": {
            f"{d}:{c}": n for (d, c), n in sorted(target_counts.items())
        },
        "credit_audit": {
            "rule": "同一等价组同时只能计一门；当前 required_credits 均为1。",
            "students_meeting_credit_rule_now": sum(r["credit_requirement_currently_met"] for r in credit_rows),
            "students_with_multiple_counted_equivalents": sum(r["multiple_equivalents_do_not_stack"] for r in credit_rows),
            "rows": credit_rows,
        },
        "warning": "观察中学期没有 outcome；这些只是已发生转移/在位状态，不是最终完成或流失。",
    }


def outcome_range_for_assignment(units: list[dict[str, Any]], assignment: dict[str, str],
                                 policy_support: dict[str, Any],
                                 unplaced_intervals: Optional[dict[str, tuple[float, float, float]]] = None
                                 ) -> dict[str, Any]:
    """Bound completions using course evidence; preserve zero-evidence width."""
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for d, report in policy_support["directions"].items():
        for course in report["courses"]:
            lookup[(d, course["course"])] = course
    direction_by_unit = {u.get("id", u.get("student")): u["direction"] for u in units}

    known_expected = 0.0
    known_low = 0.0
    known_high = 0.0
    known_n = 0
    unsupported_n = 0
    unsupported_courses = defaultdict(int)
    for unit_id, course in assignment.items():
        d = direction_by_unit.get(unit_id)
        evidence = lookup.get((d, course)) if d else None
        if evidence and evidence["transfer_students"] > 0:
            known_n += 1
            n = evidence["transfer_students"]
            # Shrink a little toward observed point estimate; ranges remain
            # Wilson bounds from the actual course/direction sample.
            known_expected += evidence["observed_success_rate"]
            known_low += evidence["success_low"]
            known_high += evidence["success_high"]
        else:
            unsupported_n += 1
            unsupported_courses[course] += 1

    unmet_ids = [u.get("id", u.get("student")) for u in units
                 if u.get("id", u.get("student")) not in assignment]
    unmet_n = len(unmet_ids)
    unmet_expected = 0.0
    unmet_low_total = 0.0
    unmet_high_total = 0.0
    unmet_by_direction: dict[str, int] = defaultdict(int)
    unmet_rate_by_direction = {}
    for uid in unmet_ids:
        d = direction_by_unit.get(uid)
        unmet_by_direction[d or "unknown"] += 1
        interval = (unplaced_intervals or {}).get(d or "")
        if interval:
            point, low, high = interval
            unmet_expected += point
            unmet_low_total += low
            unmet_high_total += high
            unmet_rate_by_direction[d] = {"point": point, "low": low, "high": high}

    return {
        "assigned": len(assignment),
        "evidence_backed_assigned": known_n,
        "evidence_backed_expected_successes": known_expected,
        "evidence_backed_low": known_low,
        "evidence_backed_high": known_high,
        "unsupported_assigned": unsupported_n,
        "unsupported_courses": dict(unsupported_courses),
        "unsupported_success_interval": [0.0, float(unsupported_n)],
        "unmet": unmet_n,
        "unmet_by_direction": dict(unmet_by_direction),
        "unmet_rate_intervals": unmet_rate_by_direction,
        "unmet_low": unmet_low_total if unplaced_intervals else None,
        "unmet_high": unmet_high_total if unplaced_intervals else None,
        "unmet_expected_successes": unmet_expected if unplaced_intervals else None,
        # Worst case: unsupported and unmet produce zero.  Best case: every
        # unsupported assigned student succeeds and Wilson upper for unmet.
        "supportable_total_success_interval": [
            known_low,
            known_high + unsupported_n + unmet_high_total,
        ],
    }
