"""Joint seat allocation and congestion analysis for proposal plans."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from .io import Bundle
from .policy import Policy, cancelled_courses
from .students import situations, term_occupancy


class MaxFlow:
    def __init__(self, n: int):
        self.n = n
        self.graph: list[list[list[Any]]] = [[] for _ in range(n)]

    def add_edge(self, u: int, v: int, cap: int) -> list[Any]:
        forward = [v, cap, len(self.graph[v])]
        backward = [u, 0, len(self.graph[u])]
        self.graph[u].append(forward)
        self.graph[v].append(backward)
        return forward

    def max_flow(self, source: int, sink: int) -> int:
        total = 0
        while True:
            level = [-1] * self.n
            level[source] = 0
            queue = [source]
            for u in queue:
                for v, cap, rev in self.graph[u]:
                    if cap > 0 and level[v] < 0:
                        level[v] = level[u] + 1
                        queue.append(v)
            if level[sink] < 0:
                break
            it = [0] * self.n

            def dfs(u: int, pushed: int) -> int:
                if u == sink:
                    return pushed
                while it[u] < len(self.graph[u]):
                    edge = self.graph[u][it[u]]
                    v, cap, rev = edge
                    if cap > 0 and level[v] == level[u] + 1:
                        tr = dfs(v, min(pushed, cap))
                        if tr:
                            edge[1] -= tr
                            self.graph[v][rev][1] += tr
                            return tr
                    it[u] += 1
                return 0

            while True:
                pushed = dfs(source, 10**9)
                if not pushed:
                    break
                total += pushed
        return total


@dataclass
class AssignmentResult:
    assigned: int
    unmet: list[str]
    student_course: dict[str, str]
    course_assigned: dict[str, list[str]]
    min_added_seats: Optional[int]
    added_by_course: dict[str, int]
    all_coverable: bool


def solve_units(units: list[dict[str, Any]], capacities: dict[str, int],
                feasible: dict[str, set[str]]) -> AssignmentResult:
    """Assign one seat to each unit under shared course capacities.

    ``feasible`` maps unit id to the set of currently usable courses.  The
    second half of the solver finds the minimum number of *additional* seats
    required if seats can be added to any feasible course.
    """
    course_ids = sorted(capacities)
    course_index = {c: i for i, c in enumerate(course_ids)}

    # First: current-capacity maximum matching/flow.
    source = 0
    student_start = 1
    course_start = student_start + len(units)
    sink = course_start + len(course_ids)
    flow = MaxFlow(sink + 1)
    student_edges: dict[str, list[list[Any]]] = {}
    for i, unit in enumerate(units):
        node = student_start + i
        flow.add_edge(source, node, 1)
        allowed = feasible.get(unit["id"], set())
        edges = []
        for course in sorted(allowed & set(course_ids)):
            edge = flow.add_edge(node, course_start + course_index[course], 1)
            edges.append(edge)
        student_edges[unit["id"]] = edges
    course_edges = {}
    for course in course_ids:
        cap = max(0, int(capacities.get(course, 0)))
        edge = flow.add_edge(course_start + course_index[course], sink, cap)
        course_edges[course] = edge
    assigned = flow.max_flow(source, sink)

    student_course: dict[str, str] = {}
    course_assigned: dict[str, list[str]] = defaultdict(list)
    for i, unit in enumerate(units):
        for edge in student_edges[unit["id"]]:
            # Forward edge residual capacity 0 means its one unit was used.
            if edge[1] == 0:
                v = edge[0]
                ci = v - course_start
                course = course_ids[ci]
                student_course[unit["id"]] = course
                course_assigned[course].append(unit["id"])
                break
    unmet = [u["id"] for u in units if u["id"] not in student_course]

    # Min-cost augmentation: zero-cost existing seats then unit-cost extra seats.
    n = sink + 1
    graph: list[list[list[Any]]] = [[] for _ in range(n)]

    def add_mcmf_edge(u: int, v: int, cap: int, cost: int) -> tuple[int, int]:
        fwd = [v, cap, cost, len(graph[v])]
        rev = [u, 0, -cost, len(graph[u])]
        graph[u].append(fwd)
        graph[v].append(rev)
        return u, len(graph[u]) - 1

    for i, unit in enumerate(units):
        add_mcmf_edge(source, student_start + i, 1, 0)
        for course in sorted(feasible.get(unit["id"], set()) & set(course_ids)):
            add_mcmf_edge(student_start + i, course_start + course_index[course], 1, 0)
    existing_edge_refs = {}
    extra_edge_refs = {}
    for course in course_ids:
        cap = max(0, int(capacities.get(course, 0)))
        existing_edge_refs[course] = add_mcmf_edge(
            course_start + course_index[course], sink, cap, 0)
        extra_edge_refs[course] = add_mcmf_edge(
            course_start + course_index[course], sink, len(units), 1)

    flow_amount = 0
    cost = 0
    INF = 10**9
    while flow_amount < len(units):
        dist = [INF] * n
        parent_v = [-1] * n
        parent_e = [-1] * n
        dist[source] = 0
        # Small graph; Bellman-Ford/SPFA handles negative residual costs.
        queue = [source]
        in_queue = {source}
        while queue:
            u = queue.pop(0)
            in_queue.discard(u)
            for ei, edge in enumerate(graph[u]):
                v, cap, c, rev = edge
                if cap > 0 and dist[v] > dist[u] + c:
                    dist[v] = dist[u] + c
                    parent_v[v] = u
                    parent_e[v] = ei
                    if v not in in_queue:
                        queue.append(v)
                        in_queue.add(v)
        if dist[sink] == INF:
            break
        add = len(units) - flow_amount
        v = sink
        while v != source:
            u = parent_v[v]
            ei = parent_e[v]
            add = min(add, graph[u][ei][1])
            v = u
        v = sink
        while v != source:
            u = parent_v[v]
            ei = parent_e[v]
            edge = graph[u][ei]
            edge[1] -= add
            rev_index = edge[3]
            graph[v][rev_index][1] += add
            v = u
        flow_amount += add
        cost += add * dist[sink]

    added_by_course: dict[str, int] = {}
    for course, (u, ei) in extra_edge_refs.items():
        edge = graph[u][ei]
        original_cap = len(units)
        residual_cap = edge[1]
        used = original_cap - residual_cap
        if used > 0:
            added_by_course[course] = used

    return AssignmentResult(
        assigned=assigned,
        unmet=unmet,
        student_course=student_course,
        course_assigned=dict(course_assigned),
        min_added_seats=cost if flow_amount == len(units) else None,
        added_by_course=added_by_course,
        all_coverable=flow_amount == len(units),
    )


def course_directions(policy: Policy) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for direction, courses in policy.courses_by_direction.items():
        for course in courses:
            result[course].add(direction)
    return dict(result)


def _proposal_capacity_rows(bundle: Bundle, plan_id: str, as_of: int | None,
                            scope: str) -> list[dict[str, Any]]:
    rows = [r for r in bundle.rows("proposal.csv") if r["id"] == plan_id]
    if scope == "all" or as_of is None:
        return rows
    if scope == "released_to_date":
        return [r for r in rows if int(r["release_round"]) <= as_of]
    if scope == "current_release":
        return [r for r in rows if int(r["release_round"]) == as_of]
    if scope == "future":
        return [r for r in rows if int(r["release_round"]) > as_of]
    raise ValueError(scope)


def analyze_plan(bundle: Bundle, term: int, as_of: int, policy: Policy,
                 plan_id: str | Iterable[str], scope: str = "released_to_date",
                 demand_override: Optional[list[dict[str, Any]]] = None,
                 concurrent_mode: bool = False,
                 decision_as_of: Optional[int] = None,
                 enforce_window_deadline: bool = True) -> dict[str, Any]:
    """Analyse one proposal id or a stack of proposal ids at a point in time.

    ``demand_override`` allows forecast stress cases.  Each override unit may
    provide ``feasible_courses``; otherwise direction eligibility is used.
    """
    if isinstance(plan_id, str):
        plan_ids = [plan_id]
        plan_label = plan_id
    else:
        plan_ids = list(plan_id)
        plan_label = "+".join(plan_ids)
    rows: list[dict[str, Any]] = []
    for pid in plan_ids:
        rows.extend(_proposal_capacity_rows(bundle, pid, as_of, scope))
    decision_round = as_of if decision_as_of is None else decision_as_of
    cancels = cancelled_courses(bundle, term, decision_round, effective_only=True)
    occupancy = term_occupancy(bundle, term, as_of)
    current_situations = situations(bundle, term, as_of)
    by_student = {s["student"]: s for s in current_situations}

    capacity: dict[str, int] = {}
    proposal_courses: dict[str, dict[str, Any]] = {}
    for row in rows:
        course = row["course"]
        capacity[course] = capacity.get(course, 0) + int(row["capacity"])
        proposal_courses[course] = {
            "course": course,
            "release_round": min(int(r["release_round"]) for r in rows if r["course"] == course),
            "capacity": capacity[course],
            "time_band": row["time_band"],
            "proposal_id": plan_label,
        }

    course_to_dirs = course_directions(policy)
    effective_course_dirs: dict[str, set[str]] = {}
    for course, dirs in course_to_dirs.items():
        allowed_dirs = set(dirs)
        if enforce_window_deadline and as_of is not None:
            allowed_dirs = {
                d for d in allowed_dirs
                if policy.last_start.get(d) is None or as_of <= int(policy.last_start[d])
            }
        if allowed_dirs:
            effective_course_dirs[course] = allowed_dirs
    feasible_by_unit: dict[str, set[str]] = {}
    units: list[dict[str, Any]] = []

    if demand_override is None:
        demand = [s for s in current_situations if s["status"] == "unplaced_after_drop"]
    else:
        demand = demand_override

    for item in demand:
        sid = item["student"] if "student" in item else item["id"]
        direction = item["direction"]
        unit_id = item.get("id", sid)
        if "feasible_courses" in item:
            allowed = set(item["feasible_courses"]) & set(capacity)
        else:
            allowed = set()
            for course in capacity:
                if direction not in course_to_dirs.get(course, set()):
                    continue
                if course in cancels:
                    continue
                last_start = policy.last_start.get(direction)
                student_last_transfer = item.get("last_transfer_round")
                if student_last_transfer is not None and last_start is not None:
                    last_start = min(int(last_start), int(student_last_transfer))
                release_round = proposal_courses[course]["release_round"]
                if last_start is not None and release_round > int(last_start):
                    continue
                if (enforce_window_deadline and last_start is not None
                        and as_of is not None and int(as_of) > int(last_start)):
                    # A new assignment at this run round is after the course's
                    # last valid start.  A separate exception/backdating view
                    # can relax this for person-by-person coordination.
                    continue
                # In concurrent mode, a retained active course in the same band
                # makes the target impossible.  Default replacement mode assumes
                # the planned/dropped seat is explicitly released.
                if concurrent_mode:
                    sit = by_student.get(sid)
                    band = proposal_courses[course]["time_band"]
                    if sit and any(
                        c != course and _pool_band(bundle, term, c) == band
                        for c in sit["active_courses"]
                    ):
                        continue
                allowed.add(course)
        feasible_by_unit[unit_id] = allowed
        units.append({"id": unit_id, "student": sid, "direction": direction})

    occupied_counts = {c: len(occupancy.get(c, set())) for c in capacity}
    usable_capacity = {
        c: (0 if c in cancels else capacity[c] - occupied_counts.get(c, 0))
        for c in capacity
    }
    flow_capacity = {c: max(0, n) for c, n in usable_capacity.items()}
    result = solve_units(units, flow_capacity, feasible_by_unit) if units else None

    course_report = []
    for course in sorted(capacity):
        assigned = len(result.course_assigned.get(course, [])) if result else 0
        occupied = occupied_counts.get(course, 0)
        cancelled_row = cancels.get(course)
        base_cap = capacity[course]
        effective = 0 if cancelled_row else base_cap
        net = effective - occupied
        course_report.append({
            "course": course,
            "directions": sorted(course_to_dirs.get(course, set())),
            "current_window_directions": sorted(effective_course_dirs.get(course, set())),
            "release_round": proposal_courses[course]["release_round"],
            "time_band": proposal_courses[course]["time_band"],
            "gross_capacity": base_cap,
            "occupied_confirmed_or_enrolled": occupied,
            "effective_capacity": effective,
            "net_capacity": net,
            "overbooked": occupied > effective,
            "cancelled": bool(cancelled_row),
            "cancellation_received_round": int(cancelled_row["received_round"]) if cancelled_row else None,
            "assigned_from_demand": assigned,
            "remaining_after_assignment": max(0, net - assigned),
            "utilization_including_existing": (occupied + assigned) / effective if effective > 0 else None,
        })

    direction_demand: dict[str, int] = defaultdict(int)
    direction_unmet: dict[str, list[str]] = defaultdict(list)
    direction_assigned: dict[str, int] = defaultdict(int)
    for unit in units:
        d = unit["direction"]
        direction_demand[d] += 1
    if result:
        for unit in units:
            uid = unit["id"]
            d = unit["direction"]
            if uid in result.student_course:
                direction_assigned[d] += 1
            else:
                direction_unmet[d].append(uid)

    # Shared-course contention summary.
    exclusive_net: dict[str, int] = defaultdict(int)
    shared_net: dict[str, int] = defaultdict(int)
    for course, dirs in effective_course_dirs.items():
        if course not in flow_capacity:
            continue
        if len(dirs) == 1:
            exclusive_net[next(iter(dirs))] += flow_capacity[course]
        elif len(dirs) > 1:
            shared_net[course] += flow_capacity[course]
    shared_summary = []
    for course, net in sorted(shared_net.items()):
        required_from_shared = {}
        for d in sorted(effective_course_dirs[course]):
            required_from_shared[d] = max(0, direction_demand.get(d, 0) - exclusive_net.get(d, 0))
        shared_summary.append({
            "course": course,
            "net_capacity": net,
            "minimum_required_by_direction_after_exclusive": required_from_shared,
            "sum_minimum_claims": sum(required_from_shared.values()),
            "slack": net - sum(required_from_shared.values()),
        })

    allocation_ranges = {}
    shared_total = sum(shared_net.values())
    for d in direction_demand:
        e_d = exclusive_net.get(d, 0)
        max_d = min(direction_demand[d], e_d + shared_total)
        other_max = 0
        for other in direction_demand:
            if other == d:
                continue
            other_max += min(direction_demand[other], exclusive_net.get(other, 0) + shared_total)
        min_d = max(0, result.assigned - other_max) if result else 0
        allocation_ranges[d] = {
            "exclusive_net_capacity": e_d,
            "shared_net_capacity_counted_once": shared_total,
            "assigned_range_among_max_total": [min_d, max_d],
            "unmet_range": [
                direction_demand[d] - max_d,
                direction_demand[d] - min_d,
            ],
        }

    blocked = [u["id"] for u in units if not feasible_by_unit.get(u["id"])]
    return {
        "proposal_id": plan_label,
        "proposal_ids": plan_ids,
        "scope": scope,
        "as_of_round": as_of,
        "window_deadline_enforced": enforce_window_deadline,
        "n_demand": len(units),
        "demand_by_direction": dict(direction_demand),
        "assigned": result.assigned if result else 0,
        "unmet": len(result.unmet) if result else 0,
        "unmet_by_direction": {d: len(ids) for d, ids in direction_unmet.items()},
        "blocked_no_supported_path": blocked,
        "all_demand_coverable_with_added_seats": result.all_coverable if result else True,
        "min_added_seats_for_all_demand": result.min_added_seats if result else 0,
        "added_by_course": result.added_by_course if result else {},
        "assignments": result.student_course if result else {},
        "feasible_courses_by_unit": {
            uid: sorted(courses) for uid, courses in feasible_by_unit.items()
        },
        "allocation_example_is_not_unique": True,
        "allocation_ranges": allocation_ranges,
        "course_assigned": result.course_assigned if result else {},
        "courses": course_report,
        "shared_contention": shared_summary,
        "feasible_for_all_demand": (result.unmet == []) if result else True,
    }


def _pool_band(bundle: Bundle, term: int, course: str) -> Optional[str]:
    for row in bundle.rows("pool.csv"):
        if int(row["term"]) == term and row["course"] == course:
            return row["time_band"]
    return None


def analyze_all_plans(bundle: Bundle, term: int, as_of: int, policy: Policy,
                      scope: str = "released_to_date",
                      demand_override: Optional[list[dict[str, Any]]] = None,
                      enforce_window_deadline: bool = True) -> list[dict[str, Any]]:
    plan_ids = sorted({r["id"] for r in bundle.rows("proposal.csv")})
    reports = []
    for pid in plan_ids:
        rows = _proposal_capacity_rows(bundle, pid, as_of, scope)
        if not rows:
            continue
        reports.append(analyze_plan(
            bundle, term, as_of, policy, pid, scope=scope,
            demand_override=demand_override,
            enforce_window_deadline=enforce_window_deadline,
        ))
    return reports
