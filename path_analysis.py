"""Public compatibility entry point for the pathway analysis toolkit.

The implementation lives in the :mod:`pathways` package.  This module exposes
the most common builders for callers that prefer a top-level import.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pathways.analyzer import build_round_report, derive_recommendations
from pathways.capacity import analyze_plan, solve_units
from pathways.history import backtest, drop_ranking_comparison, historical_review
from pathways.io import load_bundle
from pathways.policy import Adjustment, policy_for_term
from pathways.students import actual_path, seat_ledger

__all__ = [
    "Adjustment",
    "analyze_plan",
    "actual_path",
    "backtest",
    "build_round_report",
    "derive_recommendations",
    "drop_ranking_comparison",
    "historical_review",
    "load_bundle",
    "policy_for_term",
    "seat_ledger",
    "solve_units",
]


if __name__ == "__main__":
    from pathways.cli import main

    raise SystemExit(main())
