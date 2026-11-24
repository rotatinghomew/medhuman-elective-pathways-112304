"""Behavior tests for receipt-aware paths, joint seats and audit snapshots."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pathways.analyzer import build_round_report
from pathways.audit import append_snapshot, review_snapshots
from pathways.capacity import analyze_plan
from pathways.history import drop_ranking_comparison
from pathways.io import load_bundle
from pathways.policy import policy_for_term
from pathways.students import actual_path, seat_ledger, term_occupancy

DATA = ROOT / "data"


class PointInTimeTest(unittest.TestCase):
    def setUp(self):
        self.bundle = load_bundle(DATA)

    def test_late_receipt_does_not_leak_into_round_two(self):
        ledger2 = seat_ledger(self.bundle, "T8S004", 2)
        self.assertEqual(ledger2["active_courses"], ["H1"])
        self.assertEqual(ledger2["pending_courses"], ["H3"])

        ledger3 = seat_ledger(self.bundle, "T8S004", 3)
        self.assertEqual(ledger3["active_courses"], [])
        self.assertIn("H1", ledger3["released_courses"])

    def test_confirmed_occupies_and_withdrawn_releases(self):
        occupancy2 = term_occupancy(self.bundle, 8, 2)
        self.assertEqual(occupancy2["H5"], {"T8S001"})
        self.assertEqual(occupancy2["H4"], {"T8S003"})
        self.assertNotIn("T8S010", occupancy2.get("H5", set()))

    def test_business_path_is_distinct_from_receipt_visibility(self):
        # Drop and replacement accept both occur in round 2, although the
        # accept/receipt arrives in round 3.
        path = actual_path(self.bundle, "T1S002")
        self.assertEqual(path["category"], "direct_transfer")
        self.assertEqual(path["target_course"], "H3")
        self.assertEqual(path["gap_rounds"], 0)

        delayed = actual_path(self.bundle, "T1S012")
        self.assertEqual(delayed["category"], "delayed_return")
        self.assertEqual(delayed["gap_rounds"], 1)

    def test_raw_drop_rank_splits_three_path_types(self):
        ranking = drop_ranking_comparison(self.bundle)
        h1 = next(row for row in ranking if row["planned_course"] == "H1")
        h2 = next(row for row in ranking if row["planned_course"] == "H2")
        self.assertEqual(h1["raw_drop_rank"], 1)
        self.assertEqual((h1["raw_drops"], h1["direct_transfer"],
                          h1["delayed_return"], h1["dropped_no_return"]),
                         (81, 41, 14, 26))
        self.assertEqual((h2["raw_drops"], h2["direct_transfer"],
                          h2["delayed_return"], h2["dropped_no_return"]),
                         (42, 22, 9, 11))


class CapacityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_bundle(DATA)
        cls.policy = policy_for_term(cls.bundle, 8)
        cls.r2 = build_round_report(DATA, 8, 2, run_backtest=False)
        cls.r3 = build_round_report(DATA, 8, 3, run_backtest=False)

    def plan(self, report, pid):
        return next(p for p in report["immediate_capacity"]["all"] if p["proposal_id"] == pid)

    def test_round_two_and_three_demands(self):
        self.assertEqual(self.r2["demand"]["unplaced"], 19)
        self.assertEqual(self.r3["demand"]["unplaced"], 21)
        self.assertEqual(self.r3["demand"]["unplaced_by_direction"], {"A": 15, "B": 6})

    def test_joint_capacity_shortages(self):
        p1r2 = self.plan(self.r2, "P1")
        p2r2 = self.plan(self.r2, "P2")
        self.assertEqual(p1r2["unmet"], 1)
        self.assertEqual(p2r2["unmet"], 0)

        p1r3 = self.plan(self.r3, "P1")
        p2r3 = self.plan(self.r3, "P2")
        # Ordinary round-3 assignments cannot start B courses after B's
        # last_start=2.  H3 is also cancelled, leaving both A and B gaps.
        self.assertEqual(p1r3["assigned"], 4)
        self.assertEqual(p1r3["unmet"], 17)
        self.assertEqual(p1r3["unmet_by_direction"], {"A": 11, "B": 6})
        self.assertEqual(p2r3["assigned"], 8)
        self.assertEqual(p2r3["unmet"], 13)
        self.assertEqual(p2r3["unmet_by_direction"], {"A": 7, "B": 6})

        retro = {p["proposal_id"]: p for p in self.r3["retroactive_coordination_capacity"]}
        self.assertEqual(retro["P1"]["unmet"], 11)
        self.assertEqual(retro["P2"]["unmet"], 10)

    def test_round_three_b_window_is_closed(self):
        cases = self.r3["commitment_coordination"]["window_deadline_cases"]
        self.assertEqual(len(cases), 6)
        self.assertTrue(all(c["direction"] == "B" for c in cases))

    def test_released_scope_does_not_count_future_round_three_h3(self):
        p2_released = next(
            p for p in self.r2["immediate_capacity"]["released_to_date"]
            if p["proposal_id"] == "P2"
        )
        self.assertEqual(p2_released["unmet"], 8)

    def test_late_cancellation_decomposition(self):
        block = self.r3["fact_reassessment"][0]
        p1 = next(x for x in block["plan_reassessments"] if x["proposal_id"] == "P1")
        p2 = next(x for x in block["plan_reassessments"] if x["proposal_id"] == "P2")
        self.assertEqual(p1["unmet_change_from_student_facts"], 2)
        self.assertEqual(p1["unmet_change_from_last_start_window"], 6)
        self.assertEqual(p1["unmet_change_from_decisions"], 8)
        self.assertEqual(p1["after_new_facts_and_decisions"]["unmet"], 17)
        self.assertEqual(p2["unmet_change_from_last_start_window"], 6)
        self.assertEqual(p2["unmet_change_from_decisions"], 7)
        self.assertEqual(p2["after_new_facts_and_decisions"]["unmet"], 13)

    def test_h6_is_unsupported_but_adds_physical_coverage(self):
        adj = self.r3["equivalence_adjustment"]
        self.assertEqual(adj["h6_evidence"]["transfer_students"], 0)
        self.assertEqual(adj["h6_evidence"]["n_historical_terms"], 0)
        deltas = {x["plan"]: x for x in adj["coverage_deltas"]}
        # Ordinary round-3 window: H6 restores A coverage but cannot restore B.
        self.assertEqual(deltas["P1"]["current_window"]["unmet_after"], 9)
        self.assertEqual(deltas["P2"]["current_window"]["unmet_after"], 6)
        # If round-2 backdating/exceptions are approved, B can use H4 and the
        # only remaining gaps are the three/two A seats.
        self.assertEqual(deltas["P1"]["retroactive_exception_coordination"]["unmet_after"], 3)
        self.assertEqual(deltas["P2"]["retroactive_exception_coordination"]["unmet_after"], 2)

    def test_b_direction_round_three_cannot_use_later_release(self):
        # P2's H3 releases in round 3 and is A-only anyway; construct a direct
        # policy/window invariant for B's last_start=2.
        self.assertEqual(self.policy.last_start["B"], 2)
        plan = analyze_plan(self.bundle, 8, 3, self.policy, "P2", scope="all")
        h3 = next(c for c in plan["courses"] if c["course"] == "H3")
        self.assertNotIn("B", h3["directions"])


class BacktestAndAuditTest(unittest.TestCase):
    def test_state_model_beats_naive_drop_ranking(self):
        report = build_round_report(DATA, 8, 3, run_backtest=True)
        rows = {
            (x["as_of_round"], x["method"]): x["brier"]
            for x in report["backtest"]["by_round"]
        }
        self.assertLess(rows[(2, "state")], rows[(2, "naive_drop")])
        self.assertLess(rows[(3, "state")], rows[(3, "naive_drop")])
        self.assertLess(rows[(3, "state")], rows[(3, "base")])

    def test_snapshot_review_reports_observing_term_eight(self):
        report = build_round_report(DATA, 8, 3, run_backtest=False)
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            append_snapshot(out, report)
            review = review_snapshots(DATA, out)
            self.assertEqual(review["count"], 1)
            self.assertEqual(review["snapshots"][0]["status"], "observing_no_final_outcome")
            self.assertIsNone(review["snapshots"][0]["prediction_brier_where_settled"])


if __name__ == "__main__":
    unittest.main()
