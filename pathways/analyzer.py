"""High-level round report orchestration."""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Optional

from .capacity import analyze_all_plans, analyze_plan
from .forecast import build_demand_cases, current_model
from .history import (backtest, course_evidence, drop_ranking_comparison,
                      path_performance, visible_state_token)
from .insights import (_missing_evidence, commitment_coordination,
                       in_progress_path_summary,
                       outcome_range_for_assignment, policy_support_report)
from .io import Bundle, load_bundle
from .policy import Adjustment, cancelled_courses, policy_for_term, visible_decisions
from .stress import two_credit_sensitivity
from .students import mature_terms, situations


def json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def visibility_summary(bundle: Bundle, term: int, as_of: int) -> dict[str, Any]:
    student_ids = {r["id"] for r in bundle.rows("entrance.csv") if int(r["term"]) == term}
    term_events = [e for e in bundle.rows("event.csv") if e["student"] in student_ids]
    visible = [e for e in term_events if int(e["received_round"]) <= as_of]
    hidden = [e for e in term_events if int(e["received_round"]) > as_of]
    decisions = [d for d in bundle.rows("decision.csv") if int(d["term"]) == term]
    pool_rows = [p for p in bundle.rows("pool.csv") if int(p["term"]) == term]
    known_pool = [p for p in pool_rows if int(p["published_round"]) <= as_of]
    hidden_pool = [p for p in pool_rows if int(p["published_round"]) > as_of]
    return {
        "as_of_round": as_of,
        "events_visible": len(visible),
        "events_hidden_by_receipt_delay": len(hidden),
        "hidden_event_ids": [e["id"] for e in hidden],
        "pool_rows_published_and_known": len(known_pool),
        "pool_rows_not_yet_published": len(hidden_pool),
        "hidden_pool_keys": sorted({
            f"round{p['round']}:{p['course']}" for p in hidden_pool
        }),
        "visible_decisions": visible_decisions(bundle, term, as_of),
        "hidden_decisions": [d for d in decisions if int(d["received_round"]) > as_of],
        "rule": "只使用 received_round<=本轮 的事实；业务 round>本轮 的未来事实也不进入状态。",
    }


def _unplaced_intervals(model) -> dict[str, tuple[float, float, float]]:
    result = {}
    for row in model.state_summary():
        if row["state"] == "unplaced":
            result[row["direction"]] = (
                row["success_rate"] if row["success_rate"] is not None else 0.5,
                row["success_low"], row["success_high"],
            )
    return result


def _compact_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "proposal_id": plan["proposal_id"],
        "scope": plan["scope"],
        "n_demand": plan["n_demand"],
        "demand_by_direction": plan["demand_by_direction"],
        "assigned": plan["assigned"],
        "unmet": plan["unmet"],
        "unmet_by_direction": plan["unmet_by_direction"],
        "blocked_no_supported_path": plan["blocked_no_supported_path"],
        "assignments": plan.get("assignments", {}),
        "feasible_courses_by_unit": plan.get("feasible_courses_by_unit", {}),
        "min_added_seats_for_all_demand": plan["min_added_seats_for_all_demand"],
        "added_by_course": plan["added_by_course"],
        "allocation_ranges": plan.get("allocation_ranges", {}),
        "shared_contention": plan["shared_contention"],
        "courses": [
            {
                "course": c["course"],
                "directions": c["directions"],
                "release_round": c["release_round"],
                "time_band": c["time_band"],
                "gross_capacity": c["gross_capacity"],
                "occupied": c["occupied_confirmed_or_enrolled"],
                "net_capacity": c["net_capacity"],
                "cancelled": c["cancelled"],
                "assigned_from_demand": c["assigned_from_demand"],
                "remaining": c["remaining_after_assignment"],
            }
            for c in plan["courses"]
        ],
    }


def demand_case_matrix(bundle, term, as_of, policy, demand_bundle) -> list[dict[str, Any]]:
    matrix = []
    for case_name, case in demand_bundle["cases"].items():
        for scope in ("released_to_date", "all"):
            plans = analyze_all_plans(
                bundle, term, as_of, policy, scope=scope,
                demand_override=case["units"],
            )
            matrix.append({
                "case": case_name,
                "label": case["label"],
                "scope": scope,
                "results": [_compact_plan(p) for p in plans],
            })
    return matrix


def reassessment(bundle, term, as_of, policy, demand_bundle) -> list[dict[str, Any]]:
    """Separate newly visible student facts from newly visible course decisions."""
    if as_of <= 1:
        return []
    prior = as_of - 1
    current_units = demand_bundle["cases"]["known_unplaced"]["units"]
    student_ids = {r["id"] for r in bundle.rows("entrance.csv") if int(r["term"]) == term}
    new_events = [
        e for e in bundle.rows("event.csv")
        if e["student"] in student_ids
        and prior < int(e["received_round"]) <= as_of
    ]
    new_decisions = [
        d for d in bundle.rows("decision.csv")
        if int(d["term"]) == term and prior < int(d["received_round"]) <= as_of
    ]
    rows = []
    for pid in sorted({r["id"] for r in bundle.rows("proposal.csv")}):
        if pid == "P3":
            # P3 is an add-on/new-equivalence proposal and is assessed in the
            # adjustment section; the cancellation story concerns P1/P2.
            continue
        before = analyze_plan(bundle, term, prior, policy, pid, scope="all")
        student_view = analyze_plan(
            bundle, term, as_of, policy, pid, scope="all",
            demand_override=current_units, decision_as_of=prior,
            enforce_window_deadline=False,
        )
        window_view = analyze_plan(
            bundle, term, as_of, policy, pid, scope="all",
            demand_override=current_units, decision_as_of=prior,
            enforce_window_deadline=True,
        )
        after = analyze_plan(bundle, term, as_of, policy, pid, scope="all")
        rows.append({
            "proposal_id": pid,
            "prior_round": prior,
            "current_round": as_of,
            "before": _compact_plan(before),
            "new_student_facts_backdated_window": _compact_plan(student_view),
            "current_window_old_decisions": _compact_plan(window_view),
            "after_new_facts_and_decisions": _compact_plan(after),
            "unmet_change_from_student_facts": (
                student_view["unmet"] - before["unmet"]
            ),
            "unmet_change_from_last_start_window": (
                window_view["unmet"] - student_view["unmet"]
            ),
            "unmet_change_from_decisions": (
                after["unmet"] - window_view["unmet"]
            ),
            "total_unmet_change": after["unmet"] - before["unmet"],
        })
    return [{
        "newly_visible_event_ids": [e["id"] for e in new_events],
        "newly_visible_events": new_events,
        "newly_visible_decisions": new_decisions,
        "plan_reassessments": rows,
    }]


def adjustment_analysis(bundle, term, as_of, policy, demand_bundle,
                        support_baseline) -> dict[str, Any]:
    adjusted_policy, proposed_evidence = Adjustment(
        name="add_visible_proposed_equivalence",
        include_proposed_course=True,
    ).apply(policy, bundle, as_of)
    units = demand_bundle["cases"]["known_unplaced"]["units"]
    comparisons = []
    stacks = [
        ("P1", "P1", ["P1"]),
        ("P2", "P2", ["P2"]),
        ("P3_baseline_eligibility", "P3", ["P3"]),
        ("P1_plus_P3_with_new_equivalence", "P1+P3", ["P1", "P3"]),
        ("P2_plus_P3_with_new_equivalence", "P2+P3", ["P2", "P3"]),
        ("P3_with_new_equivalence", "P3-adjusted", ["P3"]),
    ]
    for label, _pid, ids in stacks:
        use_policy = adjusted_policy if "new_equivalence" in label else policy
        for scope in ("released_to_date", "all"):
            plan = analyze_plan(
                bundle, term, as_of, use_policy, ids, scope=scope,
                demand_override=units,
            )
            comparisons.append({"label": label, **_compact_plan(plan)})

    baseline_p1 = analyze_plan(bundle, term, as_of, policy, "P1", scope="all", demand_override=units)
    adjusted_p1 = analyze_plan(bundle, term, as_of, adjusted_policy, ["P1", "P3"], scope="all", demand_override=units)
    baseline_p2 = analyze_plan(bundle, term, as_of, policy, "P2", scope="all", demand_override=units)
    adjusted_p2 = analyze_plan(bundle, term, as_of, adjusted_policy, ["P2", "P3"], scope="all", demand_override=units)
    coord_baseline_p1 = analyze_plan(
        bundle, term, as_of, policy, "P1", scope="all", demand_override=units,
        enforce_window_deadline=False)
    coord_adjusted_p1 = analyze_plan(
        bundle, term, as_of, adjusted_policy, ["P1", "P3"], scope="all",
        demand_override=units, enforce_window_deadline=False)
    coord_baseline_p2 = analyze_plan(
        bundle, term, as_of, policy, "P2", scope="all", demand_override=units,
        enforce_window_deadline=False)
    coord_adjusted_p2 = analyze_plan(
        bundle, term, as_of, adjusted_policy, ["P2", "P3"], scope="all",
        demand_override=units, enforce_window_deadline=False)
    coord_pairs = {
        "P1": (coord_baseline_p1, coord_adjusted_p1),
        "P2": (coord_baseline_p2, coord_adjusted_p2),
    }
    intervals = _unplaced_intervals(current_model(bundle, term, as_of))
    delta = []
    for name, before, after in (
        ("P1", baseline_p1, adjusted_p1),
        ("P2", baseline_p2, adjusted_p2),
    ):
        outlook = outcome_range_for_assignment(
            units, after["assignments"], support_baseline, intervals,
        )
        coord_before, coord_after = coord_pairs[name]
        coord_outlook = outcome_range_for_assignment(
            units, coord_after["assignments"], support_baseline, intervals,
        )
        delta.append({
            "plan": name,
            "current_window": {
                "unmet_before": before["unmet"],
                "unmet_after": after["unmet"],
                "seat_coverage_gain": before["unmet"] - after["unmet"],
                "min_added_before": before["min_added_seats_for_all_demand"],
                "min_added_after": after["min_added_seats_for_all_demand"],
                "outcome_range": outlook,
            },
            "retroactive_exception_coordination": {
                "unmet_before": coord_before["unmet"],
                "unmet_after": coord_after["unmet"],
                "seat_coverage_gain": coord_before["unmet"] - coord_after["unmet"],
                "min_added_before": coord_before["min_added_seats_for_all_demand"],
                "min_added_after": coord_after["min_added_seats_for_all_demand"],
                "outcome_range": coord_outlook,
                "warning": "仅当教务允许按第2轮回溯认定/逐人改约时成立，不是第3轮普通可选路径。",
            },
            # Backwards-compatible top-level summary points at the ordinary
            # current-window result.
            "unmet_before": before["unmet"],
            "unmet_after": after["unmet"],
            "seat_coverage_gain": before["unmet"] - after["unmet"],
            "min_added_before": before["min_added_seats_for_all_demand"],
            "min_added_after": after["min_added_seats_for_all_demand"],
            "new_capacity": {c["course"]: c["gross_capacity"] for c in after["courses"]},
            "adjusted_outcome_range": outlook,
        })

    evidence_index = course_evidence(bundle)
    path_rows = path_performance(bundle)
    proposed_course_support = []
    for item in proposed_evidence:
        course = item["course"]
        direction = item["direction"]
        ev = evidence_index.get(course, {"support": "no_evidence"})
        matching = [
            p for p in path_rows
            if p["target_course"] == course and p["direction"] == direction
        ]
        transfer_n = sum(p["n"] for p in matching)
        transfer_k = sum(p["successes"] for p in matching)
        terms = sorted({t for p in matching for t in p["terms"]})
        n = ev.get("n_outcomes", 0) or 0
        proposed_course_support.append({
            **item,
            **ev,
            "transfer_students": transfer_n,
            "transfer_successes": transfer_k,
            "observed_terms": terms,
            "missing_evidence": _missing_evidence(course, transfer_n, terms, ev),
        })
    h6 = next((x for x in proposed_course_support if x["course"] == "H6"), None)
    if h6 is None:
        ev = evidence_index.get("H6", {"support": "no_evidence"})
        h6 = {
            "course": "H6", "direction": "A", **ev,
            "transfer_students": 0, "transfer_successes": 0, "observed_terms": [],
            "missing_evidence": _missing_evidence("H6", 0, [], ev),
        }
    return {
        "proposed_policy": adjusted_policy.to_dict(),
        "proposed_evidence": proposed_evidence,
        "proposed_course_support": proposed_course_support,
        "h6_evidence": h6,
        "stack_results": comparisons,
        "coverage_deltas": delta,
        "attribution_rules": [
            "取消 H3 造成的席位/选择减少是已发生事实，计入真实选择收缩。",
            "加入 H6 的覆盖率改善只是席位可行性；H6 没有学生转移、结果或历史班次，不能把 H3/H5 成功率照搬为 H6 完成率。",
            "H6 的可支持成功区间必须保留 [0, H6可分配人数] 的零证据宽度，直到有实际开放、利用和结算数据。",
        ],
    }


def choice_window_timeline(bundle: Bundle, term: int, policy: Policy) -> list[dict[str, Any]]:
    """Explicit round-by-round eligibility implied by release and start windows."""
    result = []
    plan_ids = sorted({r["id"] for r in bundle.rows("proposal.csv")})
    for run_round in (1, 2, 3):
        cancels = {
            c: row for c, row in cancelled_courses(bundle, term, run_round, effective_only=True).items()
        }
        plans = []
        for pid in plan_ids:
            rows = [r for r in bundle.rows("proposal.csv") if r["id"] == pid]
            by_direction = {}
            for direction in policy.courses_by_direction:
                courses = []
                last_start = policy.last_start[direction]
                for row in rows:
                    course = row["course"]
                    if course not in policy.courses(direction):
                        continue
                    release = int(row["release_round"])
                    selectable = release <= run_round <= last_start and course not in cancels
                    courses.append({
                        "course": course,
                        "release_round": release,
                        "capacity": int(row["capacity"]),
                        "time_band": row["time_band"],
                        "last_start": last_start,
                        "selectable": selectable,
                        "reason": (
                            "cancelled" if course in cancels else
                            "before_release" if release > run_round else
                            "after_last_start" if run_round > last_start else "eligible"
                        ),
                    })
                by_direction[direction] = courses
            plans.append({"proposal_id": pid, "courses_by_direction": by_direction})
        result.append({"run_round": run_round, "plans": plans})
    return result


def derive_recommendations(report: dict[str, Any]) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    as_of = report["as_of_round"]
    all_plans = {p["proposal_id"]: p for p in report["immediate_capacity"]["all"]}
    released_plans = {p["proposal_id"]: p for p in report["immediate_capacity"]["released_to_date"]}

    for pid, plan in sorted(all_plans.items()):
        if plan["unmet"]:
            minimum = plan["min_added_seats_for_all_demand"]
            if minimum is None:
                message = (
                    f"{pid} 在第{as_of}轮有 {plan['unmet']} 人缺路径；其中存在当前规则下无可行课程者，"
                    "仅加座位不能解决，需先调整等价目录、时间窗或开课决定。"
                )
            else:
                message = (
                    f"{pid} 在第{as_of}轮按全部当前无课学生联合排位后缺 {plan['unmet']} 席；"
                    f"若所有缺口均有可行课程，最小加位总数为 {minimum}。"
                )
            recs.append({
                "type": "capacity_shortfall",
                "plan": pid,
                "round": as_of,
                "message": message,
                "unmet_by_direction": plan["unmet_by_direction"],
                "minimum_added": minimum,
            })
        for shared in plan.get("shared_contention", []):
            if shared["slack"] <= 0:
                recs.append({
                    "type": "shared_course_contention",
                    "plan": pid,
                    "course": shared["course"],
                    "message": (
                        f"{pid} 的共享课 {shared['course']} 最低方向需求合计 "
                        f"{shared['sum_minimum_claims']}、净座 {shared['net_capacity']}，"
                        f"缺口/零余量为 {-shared['slack']}；不能把各方向单独口径的可承接数相加。"
                    ),
                })

    upper_rows = [
        m for m in report["demand_case_matrix"]
        if m["case"] == "upper_plausible" and m["scope"] == "all"
    ]
    if upper_rows and as_of < 3:
        known_unmet = {pid: p["unmet"] for pid, p in all_plans.items()}
        for plan in upper_rows[0]["results"]:
            if plan["proposal_id"] in {"P1", "P2"} and plan["unmet"] > known_unmet.get(plan["proposal_id"], 0):
                recs.append({
                    "type": "reserve_margin",
                    "plan": plan["proposal_id"],
                    "message": (
                        f"按历史区间上沿预留新增转移后，{plan['proposal_id']} 全方案缺 "
                        f"{plan['unmet']} 席；较当前无课口径多 "
                        f"{plan['unmet'] - known_unmet[plan['proposal_id']]} 席，建议作为余量上限测算。"
                    ),
                    "minimum_added": plan["min_added_seats_for_all_demand"],
                })

    for coord in report["commitment_coordination"]["rows"]:
        if "unavailable" in coord["disposition"] or "urgent" in coord["disposition"]:
            recs.append({
                "type": "person_coordination",
                "commitment_id": coord["commitment_id"],
                "student": coord["student"],
                "message": f"{coord['student']} 的 {coord['course']} 路径失效：{coord['disposition']}",
            })

    window_cases = report["commitment_coordination"].get("window_deadline_cases", [])
    if window_cases:
        recs.append({
            "type": "window_deadline_coordination",
            "students": [c["student"] for c in window_cases],
            "message": (
                f"{len(window_cases)} 名学生在当前轮已超过方向 last_start；"
                "普通路径关闭，若要使用第2轮席位必须逐人回溯/例外协调。"
            ),
        })

    adj = report["equivalence_adjustment"]
    h6 = adj.get("h6_evidence") or {}
    if h6.get("transfer_students", 0) == 0:
        recs.append({
            "type": "unsupported_new_path",
            "course": "H6",
            "message": "H6 可改善物理覆盖，但没有历史转移/完成/班次证据；只能给 0 到可分配人数的宽区间，不能照搬 H3 成功率。",
        })
    return recs


def build_round_report(data: str | Path | Bundle = "data", term: int = 8,
                       as_of: int = 3, run_backtest: bool = True) -> dict[str, Any]:
    bundle = data if isinstance(data, Bundle) else load_bundle(data)
    policy = policy_for_term(bundle, term)
    model = current_model(bundle, term, as_of)
    demand_bundle = build_demand_cases(bundle, term, as_of, model=model)
    support = policy_support_report(bundle, policy)
    coordination = commitment_coordination(bundle, term, as_of, policy)

    immediate_plans = {
        scope: [_compact_plan(p) for p in analyze_all_plans(
            bundle, term, as_of, policy, scope=scope,
            demand_override=demand_bundle["cases"]["known_unplaced"]["units"],
        )]
        for scope in ("released_to_date", "current_release", "all")
    }
    full_plan_objects = analyze_all_plans(
        bundle, term, as_of, policy, scope="all",
        demand_override=demand_bundle["cases"]["known_unplaced"]["units"],
    )
    coordination_plan_objects = analyze_all_plans(
        bundle, term, as_of, policy, scope="all",
        demand_override=demand_bundle["cases"]["known_unplaced"]["units"],
        enforce_window_deadline=False,
    )
    intervals = _unplaced_intervals(model)
    outcomes = {r["student"]: r for r in bundle.rows("outcome.csv")}
    current_situations = situations(bundle, term, as_of)
    in_progress = in_progress_path_summary(current_situations, policy)
    strict_by_plan = {p["proposal_id"]: p for p in full_plan_objects}
    coord_by_plan = {p["proposal_id"]: p for p in coordination_plan_objects}
    window_cases = []
    for sit in current_situations:
        direction = sit["direction"]
        last_start = policy.last_start.get(direction)
        if sit["status"] != "unplaced_after_drop" or last_start is None or as_of <= last_start:
            continue
        retroactive_options = {}
        for pid in ("P1", "P2"):
            strict = strict_by_plan.get(pid)
            relaxed = coord_by_plan.get(pid)
            if not strict or not relaxed:
                continue
            sid = sit["student"]
            retroactive_options[pid] = {
                "ordinary_current_paths": strict["feasible_courses_by_unit"].get(sid, []),
                "backdated_or_exception_paths": relaxed["feasible_courses_by_unit"].get(sid, []),
                "example_backdated_assignment": relaxed["assignments"].get(sid),
            }
        window_cases.append({
            "student": sit["student"],
            "direction": direction,
            "last_start": last_start,
            "message": (
                f"第{as_of}轮已超过 {direction} 方向 last_start={last_start}；"
                "普通新选位不可认定，只能逐人申请回溯/例外协调。"
            ),
            "options": retroactive_options,
        })
    coordination["window_deadline_cases"] = window_cases
    coordination["counts"]["window_deadline_cases"] = len(window_cases)
    student_forecasts = []
    for sit in current_situations:
        sid = sit["student"]
        token = visible_state_token(bundle, sid, as_of)
        outcome = outcomes.get(sid)
        student_forecasts.append({
            "student": sid,
            "direction": sit["direction"],
            "track": sit["track"],
            "planned_course": sit["planned_course"],
            "status": sit["status"],
            "active_courses": sit["active_courses"],
            "pending_courses": sit["pending_courses"],
            "state_token": token,
            "predicted_success": model.success_probability(sit["direction"], token, "state"),
            "base_success": model.success_probability(sit["direction"], token, "base"),
            "actual_achieved": int(outcome["achieved"]) if outcome else None,
            "actual_last_course": outcome.get("last_course") if outcome else None,
            "outcome_received_round": int(outcome["received_round"]) if outcome else None,
        })

    outcome_outlooks = {}
    for plan in full_plan_objects:
        units = demand_bundle["cases"]["known_unplaced"]["units"]
        outcome_outlooks[plan["proposal_id"]] = outcome_range_for_assignment(
            units, plan["assignments"], support, intervals,
        )

    stress = [
        two_credit_sensitivity(bundle, term, as_of, policy, p)
        for p in full_plan_objects
    ]
    report = {
        "schema_version": "pathways.report/v1",
        "term": term,
        "as_of_round": as_of,
        "mature_terms": mature_terms(bundle),
        "effective_policy": policy.to_dict(),
        "choice_window_timeline": choice_window_timeline(bundle, term, policy),
        "visibility": visibility_summary(bundle, term, as_of),
        "demand": demand_bundle["counts"],
        "demand_forecast_rows": demand_bundle["forecast_rows"],
        "in_progress_paths": in_progress,
        "commitment_coordination": coordination,
        "policy_support": support,
        "immediate_capacity": immediate_plans,
        "retroactive_coordination_capacity": [
            _compact_plan(p) for p in coordination_plan_objects
        ],
        "demand_case_matrix": demand_case_matrix(bundle, term, as_of, policy, demand_bundle),
        "outcome_outlooks": outcome_outlooks,
        "student_forecasts": student_forecasts,
        "time_and_credit_stress": stress,
        "fact_reassessment": reassessment(bundle, term, as_of, policy, demand_bundle),
        "equivalence_adjustment": adjustment_analysis(
            bundle, term, as_of, policy, demand_bundle, support,
        ),
        "drop_ranking_comparison": drop_ranking_comparison(bundle),
        "historical_paths": path_performance(bundle),
        "point_in_time_state_rates": model.state_summary(),
    }
    if run_backtest:
        report["backtest"] = backtest(bundle)
    report["recommendations"] = derive_recommendations(report)
    return json_safe(report)
