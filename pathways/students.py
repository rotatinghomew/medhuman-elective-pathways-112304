"""Point-in-time student seat ledgers and path classifications."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

from .io import Bundle

ACTIVE_COMMITMENT_STATES = {"confirmed", "active", "accepted", "held"}
INACTIVE_COMMITMENT_STATES = {"withdrawn", "cancelled", "released", "declined", "expired"}
PENDING_COMMITMENT_STATES = {"requested", "pending", "draft", "waitlisted"}


@dataclass
class Fact:
    round: int
    order: int
    kind: str
    course: str
    source: str
    state: str | None = None


def entries_by_term(bundle: Bundle, term: int) -> list[dict]:
    return [r for r in bundle.rows("entrance.csv") if int(r["term"]) == term]


def mature_terms(bundle: Bundle) -> list[int]:
    """Terms with a recovered terminal outcome for every entrant."""
    entries = bundle.rows("entrance.csv")
    outcomes = bundle.rows("outcome.csv")
    by_term: dict[int, set[str]] = defaultdict(set)
    for row in entries:
        by_term[int(row["term"])].add(row["id"])
    outcome_students = {r["student"] for r in outcomes if r.get("state") == "closed"}
    mature = []
    for term, ids in sorted(by_term.items()):
        if ids and ids <= outcome_students:
            mature.append(term)
    return mature


def _facts_for_student(bundle: Bundle, student_id: str, as_of: Optional[int],
                       include_commitments: bool = True) -> list[Fact]:
    facts: list[Fact] = []
    order = 0
    for row in bundle.rows("event.csv"):
        if row["student"] != student_id:
            continue
        received = row["received_round"]
        business_round = int(row["round"])
        if as_of is not None:
            if received is not None and int(received) > as_of:
                continue
            # A future-dated business fact is not part of the current state.
            if business_round > as_of:
                continue
        facts.append(Fact(
            round=business_round,
            order=order,
            kind="event_" + row["kind"],
            course=row["course"],
            source=row["id"],
            state=row["kind"],
        ))
        order += 1

    if include_commitments:
        for row in bundle.rows("commitment.csv"):
            if row["student"] != student_id:
                continue
            round_ = int(row["round"])
            if as_of is not None and round_ > as_of:
                continue
            state = row["state"]
            if state in ACTIVE_COMMITMENT_STATES:
                kind = "commitment_confirm"
            elif state in INACTIVE_COMMITMENT_STATES:
                kind = "commitment_release"
            else:
                kind = "commitment_pending"
            facts.append(Fact(
                round=round_, order=10_000 + order, kind=kind,
                course=row["course"], source=row["id"], state=state,
            ))
            order += 1
    # Preserve source order within a round.  Event ids/lines encode the
    # business sequence (for example drop E2 before replacement accept E3).
    facts.sort(key=lambda f: (f.round, f.order))
    return facts


def seat_ledger(bundle: Bundle, student_id: str,
                as_of: Optional[int] = None) -> dict[str, Any]:
    """Return active, pending and released seats for one student.

    A confirmed commitment occupies immediately.  An event drop or a commitment
    in an explicit terminal/release state frees the seat.  Requested/draft
    commitments are returned as pending and never occupy.
    """
    active: dict[str, str] = {}
    pending: dict[str, str] = {}
    released: set[str] = set()
    timeline = _facts_for_student(bundle, student_id, as_of)
    for fact in timeline:
        course = fact.course
        if fact.kind in {"event_accept", "commitment_confirm"}:
            active[course] = fact.source
            pending.pop(course, None)
            released.discard(course)
        elif fact.kind in {"event_drop", "commitment_release"}:
            active.pop(course, None)
            pending.pop(course, None)
            released.add(course)
        elif fact.kind == "commitment_pending":
            if course not in active:
                pending[course] = fact.source
    return {
        "student": student_id,
        "active_courses": sorted(active),
        "active_sources": active,
        "pending_courses": sorted(pending),
        "pending_sources": pending,
        "released_courses": sorted(released),
        "facts": timeline,
    }


def term_occupancy(bundle: Bundle, term: int, as_of: int) -> dict[str, set[str]]:
    """Distinct occupied course/student seats known at ``as_of``."""
    occupancy: dict[str, set[str]] = defaultdict(set)
    for entrance in entries_by_term(bundle, term):
        ledger = seat_ledger(bundle, entrance["id"], as_of)
        for course in ledger["active_courses"]:
            occupancy[course].add(entrance["id"])
    return dict(occupancy)


def situations(bundle: Bundle, term: int, as_of: int) -> list[dict[str, Any]]:
    """Build the point-in-time situation for every entrant."""
    result: list[dict[str, Any]] = []
    for entrance in entries_by_term(bundle, term):
        sid = entrance["id"]
        ledger = seat_ledger(bundle, sid, as_of)
        visible_events = [
            e for e in bundle.rows("event.csv")
            if e["student"] == sid
            and (e["received_round"] is None or int(e["received_round"]) <= as_of)
            and int(e["round"]) <= as_of
        ]
        has_drop = any(e["kind"] == "drop" for e in visible_events)
        active = ledger["active_courses"]
        if not active and has_drop:
            status = "unplaced_after_drop"
        elif not active:
            status = "inactive"
        elif any(e["kind"] == "drop" for e in visible_events):
            status = "transferred_or_reassigned"
        else:
            status = "active_planned"
        result.append({
            "student": sid,
            "term": term,
            "direction": entrance["direction"],
            "track": entrance["track"],
            "planned_course": entrance["planned_course"],
            "last_transfer_round": entrance["last_transfer_round"],
            "status": status,
            "active_courses": active,
            "pending_courses": ledger["pending_courses"],
            "released_courses": ledger["released_courses"],
            "has_visible_drop": has_drop,
            "ledger": ledger,
        })
    return result


def actual_path(bundle: Bundle, student_id: str) -> dict[str, Any]:
    """Classify the complete historical path without receipt truncation."""
    events = sorted(
        [e for e in bundle.rows("event.csv") if e["student"] == student_id],
        key=lambda e: (int(e["round"]), int(e["received_round"]), e["_line"]),
    )
    drops = [e for e in events if e["kind"] == "drop"]
    accepts = [e for e in events if e["kind"] == "accept"]
    first_drop = drops[0] if drops else None
    later_accepts = accepts
    if first_drop is not None:
        later_accepts = [
            e for e in accepts
            if (int(e["round"]), e["_line"]) >= (int(first_drop["round"]), first_drop["_line"])
        ]
    target_event = later_accepts[0] if later_accepts else None
    target = target_event["course"] if target_event else None
    if first_drop is None:
        category = "stayed"
        gap = None
    elif target_event is None:
        category = "dropped_no_return"
        gap = None
    else:
        gap = int(target_event["round"]) - int(first_drop["round"])
        category = "direct_transfer" if gap == 0 else "delayed_return"
    return {
        "category": category,
        "drop_round": int(first_drop["round"]) if first_drop else None,
        "target_course": target,
        "target_round": int(target_event["round"]) if target_event else None,
        "gap_rounds": gap,
    }
