"""Immutable-ish JSONL snapshot audit and later actual-result review."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .io import load_bundle
from .stats import brier_score


def snapshot_path(outdir: str | Path) -> Path:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    return outdir / "snapshots.jsonl"


def append_snapshot(outdir: str | Path, report: dict[str, Any]) -> Path:
    path = snapshot_path(outdir)
    record = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "term": report["term"],
        "as_of_round": report["as_of_round"],
        "schema_version": report["schema_version"],
        "report": report,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def load_snapshots(outdir: str | Path) -> list[dict[str, Any]]:
    path = snapshot_path(outdir)
    if not path.exists():
        return []
    records = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def review_snapshots(data_dir: str | Path, outdir: str | Path) -> dict[str, Any]:
    bundle = load_bundle(data_dir)
    outcomes = {r["student"]: r for r in bundle.rows("outcome.csv")}
    reviews = []
    for record in load_snapshots(outdir):
        report = record["report"]
        term = int(report["term"])
        forecasts = report.get("student_forecasts", [])
        pairs = []
        actual_known = 0
        for f in forecasts:
            outcome = outcomes.get(f["student"])
            if outcome and int(outcome["term"] if "term" in outcome else 4) == 4:
                # Outcome table has no term column; student ids are term-scoped.
                actual_known += 1
                y = int(outcome["achieved"])
                pairs.append((f["predicted_success"], y))
        plan_summary = []
        for p in report.get("immediate_capacity", {}).get("all", []):
            plan_summary.append({
                "proposal_id": p["proposal_id"],
                "unmet": p["unmet"],
                "assigned": p["assigned"],
            })
        reviews.append({
            "saved_at": record["saved_at"],
            "term": term,
            "as_of_round": report["as_of_round"],
            "actual_outcomes_known": actual_known,
            "prediction_brier_where_settled": brier_score(pairs),
            "demand_unplaced": report.get("demand", {}).get("unplaced"),
            "plans": plan_summary,
            "status": "settled" if actual_known == len(forecasts) and forecasts else (
                "partial_actuals" if actual_known else "observing_no_final_outcome"
            ),
        })
    return {"snapshots": reviews, "count": len(reviews)}
