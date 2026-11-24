"""Command line interface for the pathway and transfer-seat tool."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .analyzer import build_round_report
from .audit import append_snapshot, review_snapshots
from .history import backtest, historical_review
from .io import load_bundle
from .report import render_markdown


def _write_round_report(data: Path, term: int, round_no: int, out: Path,
                        run_backtest: bool = True, snapshot: bool = True) -> dict[str, Any]:
    report = build_round_report(data, term=term, as_of=round_no, run_backtest=run_backtest)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"round_t{term}_r{round_no}.json"
    md_path = out / f"round_t{term}_r{round_no}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    snapshot_path = None
    if snapshot:
        snapshot_path = append_snapshot(out, report)
    print(json.dumps({
        "term": term,
        "round": round_no,
        "json": str(json_path),
        "markdown": str(md_path),
        "snapshot": str(snapshot_path) if snapshot_path else None,
        "unplaced": report["demand"]["unplaced"],
        "plan_unmet_current_window": {
            p["proposal_id"]: p["unmet"]
            for p in report["immediate_capacity"]["all"]
        },
        "plan_unmet_retroactive_coordination": {
            p["proposal_id"]: p["unmet"]
            for p in report.get("retroactive_coordination_capacity", [])
        },
        # Kept as the compact default summary; it is the ordinary current-window view.
        "plan_unmet": {
            p["proposal_id"]: p["unmet"]
            for p in report["immediate_capacity"]["all"]
        },
    }, ensure_ascii=False))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="人文微专业路径预测、分轮转轨席位与事实重估工具",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    round_parser = sub.add_parser("round", help="运行指定学期/轮次分析")
    round_parser.add_argument("--data", type=Path, default=Path("data"))
    round_parser.add_argument("--term", type=int, default=8)
    round_parser.add_argument("--round", type=int, required=True)
    round_parser.add_argument("--out", type=Path, default=Path("reports"))
    round_parser.add_argument("--no-backtest", action="store_true")
    round_parser.add_argument("--no-snapshot", action="store_true")

    all_parser = sub.add_parser("all-rounds", help="依次生成第1/2/3轮报告和快照")
    all_parser.add_argument("--data", type=Path, default=Path("data"))
    all_parser.add_argument("--term", type=int, default=8)
    all_parser.add_argument("--rounds", type=int, nargs="+", default=[1, 2, 3])
    all_parser.add_argument("--out", type=Path, default=Path("reports"))

    back_parser = sub.add_parser("backtest", help="只运行历史成熟学期滚动回测")
    back_parser.add_argument("--data", type=Path, default=Path("data"))
    back_parser.add_argument("--out", type=Path, default=Path("reports"))

    history_parser = sub.add_parser(
        "history-review", help="生成既往学期逐轮预测与实际结果对照",
    )
    history_parser.add_argument("--data", type=Path, default=Path("data"))
    history_parser.add_argument("--out", type=Path, default=Path("reports"))

    review_parser = sub.add_parser("review", help="复盘已保存快照与已回收结果")
    review_parser.add_argument("--data", type=Path, default=Path("data"))
    review_parser.add_argument("--out", type=Path, default=Path("reports"))

    args = parser.parse_args(argv)
    if args.command == "round":
        _write_round_report(
            args.data, args.term, args.round, args.out,
            run_backtest=not args.no_backtest,
            snapshot=not args.no_snapshot,
        )
        return 0
    if args.command == "all-rounds":
        for round_no in args.rounds:
            _write_round_report(args.data, args.term, round_no, args.out)
        return 0
    if args.command == "backtest":
        result = backtest(load_bundle(args.data))
        args.out.mkdir(parents=True, exist_ok=True)
        path = args.out / "backtest.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        for row in result["by_round"]:
            print(row)
        print(f"written: {path}")
        return 0
    if args.command == "history-review":
        result = historical_review(load_bundle(args.data))
        args.out.mkdir(parents=True, exist_ok=True)
        path = args.out / "historical_review.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        compact = [
            {k: v for k, v in row.items() if k != "students"}
            for row in result["reviews"]
        ]
        print(json.dumps(compact, ensure_ascii=False, indent=2, sort_keys=True))
        print(f"written: {path}")
        return 0
    if args.command == "review":
        result = review_snapshots(args.data, args.out)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
