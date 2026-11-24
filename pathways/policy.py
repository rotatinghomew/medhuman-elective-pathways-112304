"""Versioned equivalence rules and what-if adjustments."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, Iterable, List, Optional, Set

from .io import Bundle


@dataclass(frozen=True)
class Policy:
    term: int
    version: str
    courses_by_direction: Dict[str, frozenset[str]]
    required_credits: Dict[str, int]
    last_start: Dict[str, int]
    source_rows: tuple[dict, ...] = ()

    def courses(self, direction: str) -> frozenset[str]:
        return self.courses_by_direction.get(direction, frozenset())

    def to_dict(self) -> dict:
        return {
            "term": self.term,
            "version": self.version,
            "courses_by_direction": {
                k: sorted(v) for k, v in sorted(self.courses_by_direction.items())
            },
            "required_credits": dict(sorted(self.required_credits.items())),
            "last_start": dict(sorted(self.last_start.items())),
        }


def parse_course_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def policy_for_term(bundle: Bundle, term: int,
                    version: Optional[str] = None) -> Policy:
    """Select the effective equivalence rows for a term.

    Term ranges are inclusive.  If ``version`` is supplied, only that version is
    considered; this is useful for audited replays.
    """
    selected: list[dict] = []
    for row in bundle.rows("equivalence.csv"):
        if version is not None and row["version"] != version:
            continue
        if int(row["term_start"]) <= term <= int(row["term_end"]):
            selected.append(row)
    if not selected:
        raise KeyError(f"第 {term} 学期没有有效等价规则")
    courses: Dict[str, Set[str]] = {}
    credits: Dict[str, int] = {}
    last_start: Dict[str, int] = {}
    for row in selected:
        direction = row["direction"]
        courses[direction] = set(parse_course_list(row["eligible_courses"]))
        credits[direction] = int(row["required_credits"])
        last_start[direction] = int(row["last_start"])
    chosen_version = version or selected[0]["version"]
    return Policy(
        term=term,
        version=chosen_version,
        courses_by_direction={k: frozenset(v) for k, v in courses.items()},
        required_credits=credits,
        last_start=last_start,
        source_rows=tuple(selected),
    )


@dataclass
class Adjustment:
    """A transparent equivalence what-if.

    ``add`` and ``remove`` map direction codes to course iterables.  Setting
    ``include_proposed_course`` additionally turns visible
    ``proposed_equivalent_<DIR>`` decisions into additions.
    """
    name: str = "baseline"
    add: Dict[str, Iterable[str]] | None = None
    remove: Dict[str, Iterable[str]] | None = None
    include_proposed_course: bool = False
    proposed_only: bool = False

    def apply(self, policy: Policy, bundle: Bundle, as_of: int) -> tuple[Policy, list[dict]]:
        courses = {d: set(cs) for d, cs in policy.courses_by_direction.items()}
        evidence: list[dict] = []
        additions = {d: set(cs) for d, cs in (self.add or {}).items()}
        removals = {d: set(cs) for d, cs in (self.remove or {}).items()}

        if self.include_proposed_course:
            for decision in bundle.rows("decision.csv"):
                if int(decision["term"]) != policy.term:
                    continue
                if decision["received_round"] is None or int(decision["received_round"]) > as_of:
                    continue
                state = decision["state"] or ""
                prefix = "proposed_equivalent_"
                if state.startswith(prefix):
                    direction = state[len(prefix):]
                    additions.setdefault(direction, set()).add(decision["course"])
                    evidence.append({
                        "course": decision["course"],
                        "direction": direction,
                        "decision_state": state,
                        "received_round": int(decision["received_round"]),
                    })

        for direction, values in additions.items():
            courses.setdefault(direction, set()).update(values)
        for direction, values in removals.items():
            courses.setdefault(direction, set()).difference_update(values)

        new_policy = replace(
            policy,
            courses_by_direction={d: frozenset(cs) for d, cs in courses.items()},
        )
        return new_policy, evidence


def visible_decisions(bundle: Bundle, term: int, as_of: int) -> list[dict]:
    """Decisions known by ``as_of`` using their receipt round."""
    result = []
    for row in bundle.rows("decision.csv"):
        if int(row["term"]) != term:
            continue
        received = row["received_round"]
        if received is not None and int(received) <= as_of:
            result.append(row)
    return result


def cancelled_courses(bundle: Bundle, term: int, as_of: int,
                      effective_only: bool = False) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for row in visible_decisions(bundle, term, as_of):
        if row["state"] != "cancelled":
            continue
        if effective_only and int(row["effective_round"]) > as_of:
            continue
        result[row["course"]] = row
    return result
