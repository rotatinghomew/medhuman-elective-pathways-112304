"""Historical path evidence, point-in-time models and rolling backtests."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Optional

from .io import Bundle
from .stats import bayes_rate, brier_score, log_loss, support_label, wilson
from .students import actual_path, mature_terms, seat_ledger


def course_evidence(bundle: Bundle) -> dict[str, dict[str, Any]]:
    """Summarise historical pool/outcome evidence for every course."""
    pool_terms: dict[str, set[int]] = defaultdict(set)
    pool_bands: dict[str, set[str]] = defaultdict(set)
    capacities: dict[str, list[int]] = defaultdict(list)
    for row in bundle.rows("pool.csv"):
        course = row["course"]
        pool_terms[course].add(int(row["term"]))
        if row.get("time_band"):
            pool_bands[course].add(row["time_band"])
        if row.get("capacity") is not None:
            capacities[course].append(int(row["capacity"]))

    outcome_students: dict[str, set[str]] = defaultdict(set)
    outcome_success: Counter[str] = Counter()
    outcome_total: Counter[str] = Counter()
    for row in bundle.rows("outcome.csv"):
        course = row.get("last_course") or ""
        if not course:
            continue
        outcome_students[course].add(row["student"])
        outcome_total[course] += 1
        outcome_success[course] += int(row["achieved"])

    proposal_terms: dict[str, set[int]] = defaultdict(set)
    proposal_bands: dict[str, set[str]] = defaultdict(set)
    proposal_releases: dict[str, set[int]] = defaultdict(set)
    for row in bundle.rows("proposal.csv"):
        course = row["course"]
        proposal_terms[course].add(-1)  # current/future plan marker
        if row.get("time_band"):
            proposal_bands[course].add(row["time_band"])
        if row.get("release_round") is not None:
            proposal_releases[course].add(int(row["release_round"]))

    result = {}
    courses = set(pool_terms) | set(outcome_total) | {
        r["course"] for r in bundle.rows("proposal.csv")
    }
    for course in sorted(courses):
        historical_terms = sorted(t for t in pool_terms.get(course, set()) if t < 8)
        k = outcome_success[course]
        n = outcome_total[course]
        p, low, high = wilson(k, n)
        n_terms = len(historical_terms)
        result[course] = {
            "course": course,
            "historical_terms": historical_terms,
            "n_historical_terms": n_terms,
            "historical_time_bands": sorted(pool_bands.get(course, set())),
            "pool_capacities_seen": sorted(set(capacities.get(course, []))),
            "n_outcomes": n,
            "successes": k,
            "success_rate": p if n else None,
            "success_low": low if n else None,
            "success_high": high if n else None,
            "support": support_label(n, n_terms),
            "in_proposal": course in proposal_terms,
            "proposal_time_bands": sorted(proposal_bands.get(course, set())),
            "proposal_release_rounds": sorted(proposal_releases.get(course, set())),
        }
    return result


def path_performance(bundle: Bundle) -> list[dict[str, Any]]:
    """Full-fact historical path table for mature terms."""
    entrances = {r["id"]: r for r in bundle.rows("entrance.csv")}
    outcomes = {r["student"]: r for r in bundle.rows("outcome.csv")}
    groups: dict[tuple[str, str, Optional[str], Optional[int]], dict[str, Any]] = {}
    for sid, entrance in entrances.items():
        term = int(entrance["term"])
        if term not in mature_terms(bundle):
            continue
        path = actual_path(bundle, sid)
        outcome = outcomes.get(sid)
        achieved = int(outcome["achieved"]) if outcome else None
        key = (
            entrance["direction"], path["category"], path["target_course"],
            path["gap_rounds"],
        )
        group = groups.setdefault(key, {
            "direction": entrance["direction"],
            "category": path["category"],
            "target_course": path["target_course"],
            "gap_rounds": path["gap_rounds"],
            "students": [],
            "terms": set(),
            "n": 0,
            "successes": 0,
        })
        group["students"].append(sid)
        group["terms"].add(term)
        group["n"] += 1
        if achieved == 1:
            group["successes"] += 1

    result = []
    for group in groups.values():
        n = group["n"]
        k = group["successes"]
        p, low, high = wilson(k, n)
        n_terms = len(group["terms"])
        result.append({
            "direction": group["direction"],
            "category": group["category"],
            "target_course": group["target_course"],
            "gap_rounds": group["gap_rounds"],
            "n": n,
            "successes": k,
            "success_rate": k / n if n else None,
            "bayes_rate": bayes_rate(k, n),
            "low": low,
            "high": high,
            "terms": sorted(group["terms"]),
            "n_terms": n_terms,
            "support": support_label(n, n_terms),
            "student_ids": group["students"],
        })
    result.sort(key=lambda r: (r["direction"], r["category"], str(r["target_course"]),
                               r["gap_rounds"] if r["gap_rounds"] is not None else -9))
    return result


def visible_state_token(bundle: Bundle, student_id: str, as_of: int) -> str:
    """Information-state token available at a receipt-aware forecast round."""
    ledger = seat_ledger(bundle, student_id, as_of)
    active = ledger["active_courses"]
    entrance = next(r for r in bundle.rows("entrance.csv") if r["id"] == student_id)
    visible_drops = [
        e for e in bundle.rows("event.csv")
        if e["student"] == student_id
        and e["kind"] == "drop"
        and (e["received_round"] is None or int(e["received_round"]) <= as_of)
        and int(e["round"]) <= as_of
    ]
    if not active:
        return "unplaced" if visible_drops else "inactive"
    if len(active) == 1 and active[0] == entrance["planned_course"] and not visible_drops:
        return f"stable:{active[0]}"
    return "placed:" + "+".join(active)


class HistoricalModel:
    """Fit direction and information-state rates on selected mature terms."""

    def __init__(self, bundle: Bundle, training_terms: list[int], as_of: int):
        self.bundle = bundle
        self.training_terms = training_terms
        self.as_of = as_of
        entrances = {r["id"]: r for r in bundle.rows("entrance.csv")}
        outcomes = {r["student"]: r for r in bundle.rows("outcome.csv")}
        self.dir_n: Counter[str] = Counter()
        self.dir_k: Counter[str] = Counter()
        self.state_n: Counter[tuple[str, str]] = Counter()
        self.state_k: Counter[tuple[str, str]] = Counter()
        self.final_course: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        self.final_alt: Counter[tuple[str, str]] = Counter()
        self.final_none: Counter[tuple[str, str]] = Counter()

        term_set = set(training_terms)
        for sid, entrance in entrances.items():
            term = int(entrance["term"])
            if term not in term_set or sid not in outcomes:
                continue
            direction = entrance["direction"]
            y = int(outcomes[sid]["achieved"])
            token = visible_state_token(bundle, sid, as_of)
            self.dir_n[direction] += 1
            self.dir_k[direction] += y
            key = (direction, token)
            self.state_n[key] += 1
            self.state_k[key] += y
            final_course = outcomes[sid].get("last_course") or ""
            self.final_course[key][final_course or "(none)"] += 1
            if final_course and final_course != entrance["planned_course"]:
                self.final_alt[key] += 1
            if not final_course:
                self.final_none[key] += 1

    def direction_rate(self, direction: str) -> float:
        n = self.dir_n[direction]
        return bayes_rate(self.dir_k[direction], n, prior_k=2, prior_n=4)

    def success_probability(self, direction: str, token: str,
                            method: str = "state") -> float:
        base = self.direction_rate(direction)
        if method == "base":
            return base
        key = (direction, token)
        n = self.state_n[key]
        k = self.state_k[key]
        if method == "naive_drop":
            if token == "unplaced" or token.startswith("placed:"):
                return 0.0
            return base
        if method == "state":
            if n == 0:
                return base
            # Shrink exact information-state evidence toward the direction rate.
            return (k + 4.0 * base) / (n + 4.0)
        raise ValueError(method)

    def alt_demand_rate(self, direction: str, token: str) -> tuple[float, int, int, float, float]:
        key = (direction, token)
        n = self.state_n[key]
        k = self.final_alt[key]
        p, low, high = wilson(k, n)
        if n == 0:
            p = self.direction_rate(direction)
        return p, k, n, low, high

    def state_summary(self) -> list[dict[str, Any]]:
        result = []
        for (direction, token), n in sorted(self.state_n.items()):
            k = self.state_k[(direction, token)]
            alt = self.final_alt[(direction, token)]
            none = self.final_none[(direction, token)]
            p, low, high = wilson(k, n)
            ap, alow, ahigh = wilson(alt, n)
            result.append({
                "direction": direction,
                "state": token,
                "n": n,
                "successes": k,
                "success_rate": k / n if n else None,
                "success_low": low,
                "success_high": high,
                "alt_demand": alt,
                "alt_rate": alt / n if n else None,
                "alt_low": alow,
                "alt_high": ahigh,
                "no_final_course": none,
                "final_courses": dict(self.final_course[(direction, token)]),
            })
        return result


def drop_ranking_comparison(bundle: Bundle) -> list[dict[str, Any]]:
    """Compare a naive raw-drop ranking with separated path outcomes."""
    entrances = {r["id"]: r for r in bundle.rows("entrance.csv")}
    outcomes = {r["student"]: r for r in bundle.rows("outcome.csv")}
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for sid, entrance in entrances.items():
        term = int(entrance["term"])
        if term not in mature_terms(bundle):
            continue
        key = (entrance["direction"], entrance["planned_course"])
        group = groups.setdefault(key, {
            "direction": entrance["direction"],
            "planned_course": entrance["planned_course"],
            "entrants": 0,
            "raw_drops": 0,
            "direct_transfer": 0,
            "delayed_return": 0,
            "dropped_no_return": 0,
            "drop_then_success": 0,
            "terms": set(),
        })
        group["entrants"] += 1
        group["terms"].add(term)
        path = actual_path(bundle, sid)
        if path["category"] == "stayed":
            continue
        group["raw_drops"] += 1
        group[path["category"]] += 1
        outcome = outcomes.get(sid)
        if path["category"] in {"direct_transfer", "delayed_return"} and outcome and int(outcome["achieved"]) == 1:
            group["drop_then_success"] += 1

    result = []
    for rank, group in enumerate(sorted(groups.values(), key=lambda g: -g["raw_drops"]), start=1):
        n = group["entrants"]
        drops = group["raw_drops"]
        result.append({
            "raw_drop_rank": rank,
            "direction": group["direction"],
            "planned_course": group["planned_course"],
            "entrants": n,
            "raw_drops": drops,
            "raw_drop_rate": drops / n if n else None,
            "direct_transfer": group["direct_transfer"],
            "delayed_return": group["delayed_return"],
            "dropped_no_return": group["dropped_no_return"],
            "drop_then_success": group["drop_then_success"],
            "no_return_rate_per_entrant": group["dropped_no_return"] / n if n else None,
            "terms": sorted(group["terms"]),
            "interpretation": (
                "raw drops mix immediate transfers, delayed returns and non-return; "
                "use category-specific rates rather than this rank alone"
            ),
        })
    return result


def historical_review(bundle: Bundle) -> dict[str, Any]:
    """Per-term/round retrospective predictions joined to recovered outcomes."""
    mature = mature_terms(bundle)
    entrances = {r["id"]: r for r in bundle.rows("entrance.csv")}
    outcomes = {r["student"]: r for r in bundle.rows("outcome.csv")}
    reviews = []
    for target_term in mature:
        training = [t for t in mature if t < target_term]
        if not training:
            continue
        term_ids = [sid for sid, e in entrances.items() if int(e["term"]) == target_term]
        for as_of in (1, 2, 3):
            model = HistoricalModel(bundle, training, as_of)
            pairs = []
            student_rows = []
            for sid in term_ids:
                if sid not in outcomes:
                    continue
                y = int(outcomes[sid]["achieved"])
                d = entrances[sid]["direction"]
                token = visible_state_token(bundle, sid, as_of)
                p = model.success_probability(d, token, "state")
                pairs.append((p, y))
                student_rows.append({
                    "student": sid,
                    "direction": d,
                    "state": token,
                    "predicted_success": p,
                    "actual_achieved": y,
                    "actual_last_course": outcomes[sid].get("last_course"),
                })
            reviews.append({
                "term": target_term,
                "as_of_round": as_of,
                "training_terms": training,
                "n": len(pairs),
                "actual_successes": sum(y for _, y in pairs),
                "brier": brier_score(pairs),
                "log_loss": log_loss(pairs),
                "students": student_rows,
            })
    return {"reviews": reviews}


def backtest(bundle: Bundle, rounds: tuple[int, ...] = (1, 2, 3)) -> dict[str, Any]:
    """Rolling-origin, receipt-aware comparison of simple predictors."""
    mature = mature_terms(bundle)
    entrances = {r["id"]: r for r in bundle.rows("entrance.csv")}
    outcomes = {r["student"]: r for r in bundle.rows("outcome.csv")}
    by_round: dict[int, dict[str, list[tuple[float, int]]]] = {
        r: {"base": [], "naive_drop": [], "state": []} for r in rounds
    }
    fold_rows = []
    for target_term in mature:
        training = [t for t in mature if t < target_term]
        if not training:
            continue
        for as_of in rounds:
            model = HistoricalModel(bundle, training, as_of)
            fold_counts = Counter()
            for sid, entrance in entrances.items():
                if int(entrance["term"]) != target_term or sid not in outcomes:
                    continue
                y = int(outcomes[sid]["achieved"])
                token = visible_state_token(bundle, sid, as_of)
                d = entrance["direction"]
                for method in ("base", "naive_drop", "state"):
                    p = model.success_probability(d, token, method)
                    by_round[as_of][method].append((p, y))
                fold_counts["n"] += 1
            fold_rows.append({"target_term": target_term, "as_of": as_of,
                             "training_terms": training, "n": fold_counts["n"]})

    summary = []
    for as_of in rounds:
        for method, pairs in by_round[as_of].items():
            ys = [y for _, y in pairs]
            summary.append({
                "as_of_round": as_of,
                "method": method,
                "n": len(pairs),
                "events": sum(ys),
                "brier": brier_score(pairs),
                "log_loss": log_loss(pairs),
            })
    overall = []
    for method in ("base", "naive_drop", "state"):
        pairs = []
        for as_of in rounds:
            pairs.extend(by_round[as_of][method])
        overall.append({
            "method": method,
            "n": len(pairs),
            "brier": brier_score(pairs),
            "log_loss": log_loss(pairs),
        })
    best_by_round = {}
    for as_of in rounds:
        rows = [s for s in summary if s["as_of_round"] == as_of]
        best = min(rows, key=lambda s: s["brier"] if s["brier"] is not None else 9e9)
        best_by_round[str(as_of)] = best["method"]
    return {
        "folds": fold_rows,
        "by_round": summary,
        "overall": overall,
        "best_by_round": best_by_round,
        "interpretation": [
            "base=只使用方向历史完成率",
            "naive_drop=把任何可见报退都当作最终失败，是‘按退课数排名’的学生级近似",
            "state=按该轮实际可知的在位/未安置/已转入状态做Beta收缩估计",
        ],
    }
