"""Markdown rendering for registrar-facing round reports."""
from __future__ import annotations

from typing import Any


def _pct(x):
    if x is None:
        return "—"
    return f"{100*x:.1f}%"


def _num(x, digits=2):
    if x is None:
        return "—"
    if isinstance(x, int):
        return str(x)
    return f"{x:.{digits}f}"


def render_markdown(report: dict[str, Any]) -> str:
    r = report
    lines: list[str] = []
    lines.append(f"# 第 {r['term']} 学期第 {r['as_of_round']} 轮路径与转轨席位分析")
    lines.append("")
    lines.append("> 口径：仅使用 `received_round <= 本轮` 且业务轮次不晚于本轮的事实。"
                 "第八学期未结算，只计入已发生转移，不计为流失或完课。")
    lines.append("")
    if r.get("recommendations"):
        lines.append("## 结论与建议")
        lines.append("")
        for rec in r["recommendations"]:
            lines.append(f"- {rec['message']}")
        lines.append("")

    d = r["demand"]
    lines.append("## 1. 本轮学生状态")
    lines.append("")
    lines.append(f"- 总人数：{d['total_students']}")
    lines.append(f"- 可见报退后无课：**{d['unplaced']}**；分方向 {d['unplaced_by_direction']}")
    lines.append(f"- 未确认申请（不占位）：{d['pending_other']}")
    lines.append(f"- 仍在上计划课：{d['active_planned']}；分方向 {d['active_planned_by_direction']}")
    lines.append(f"- 历史区间上沿估计还可能新增转移：{d['upper_latent_alternative_seats_from_stable']} 人 "
                 f"（{d['upper_extra_by_direction']}）")
    vis = r["visibility"]
    lines.append(f"- 因回执延迟在本轮不可见的事件：{vis['events_hidden_by_receipt_delay']} 条 "
                 f"{vis['hidden_event_ids'] if vis['hidden_event_ids'] else ''}")
    hidden_decisions = vis.get("hidden_decisions", [])
    if hidden_decisions:
        lines.append(f"- 尚未得知的决定：{[x['course'] + ':' + x['state'] for x in hidden_decisions]}")
    lines.append(
        f"- 分轮台账：已公布可知 {vis.get('pool_rows_published_and_known', '—')} 行，"
        f"尚未公布 {vis.get('pool_rows_not_yet_published', '—')} 行。"
    )
    lines.append("")

    lines.append("## 2. 各方案联合席位测算（按全部当前无课学生）")
    lines.append("")
    lines.append("| 方案 | 口径 | 需求 | 可安置 | 缺口 | A缺口区间 | B缺口区间 | 全覆盖至少加位 |")
    lines.append("|---|---:|---:|---:|---:|---|---|---:|")
    for p in r["immediate_capacity"]["all"]:
        ranges = p.get("allocation_ranges", {})
        a = ranges.get("A", {}).get("unmet_range", ["—", "—"])
        b = ranges.get("B", {}).get("unmet_range", ["—", "—"])
        min_added = p["min_added_seats_for_all_demand"]
        min_added_text = "需先修规则" if min_added is None else str(min_added)
        lines.append(
            f"| {p['proposal_id']} | 全方案 | {p['n_demand']} | {p['assigned']} | "
            f"**{p['unmet']}** | {a[0]}–{a[1]} | {b[0]}–{b[1]} | "
            f"{min_added_text} |"
        )
    for p in r["immediate_capacity"]["released_to_date"]:
        if p["proposal_id"] in {x["proposal_id"] for x in r["immediate_capacity"]["all"]}:
            min_added = p["min_added_seats_for_all_demand"]
            min_added_text = "需先修规则" if min_added is None else str(min_added)
            lines.append(
                f"| {p['proposal_id']} | 已放轮次 | {p['n_demand']} | {p['assigned']} | "
                f"**{p['unmet']}** | — | — | {min_added_text} |"
            )
    lines.append("")
    lines.append("缺口区间表示总安置数最大时，共享课在两个方向间分配仍可形成的方向结果范围；"
                 "总缺口不变，但 A/B 的具体缺口可随分配规则变化。上表是当前轮普通新选位口径；"
                 "超过 `last_start` 的方向不会被强行分配。")
    retro = r.get("retroactive_coordination_capacity", [])
    if retro:
        bits = [f"{p['proposal_id']}={p['unmet']}" for p in retro if p["proposal_id"] in {"P1", "P2"}]
        lines.append("")
        lines.append(f"- 放宽 `last_start` 限制/按对应 release 轮回溯时，缺口可为：{'; '.join(bits)}；"
                     "若当前轮已超过 last_start，这只能通过逐人例外或回溯认定实现。")
    lines.append("")

    lines.append("### 共享课挤占点")
    lines.append("")
    for p in r["immediate_capacity"]["all"]:
        for s in p.get("shared_contention", []):
            slack = s["slack"]
            if slack < 0:
                flag = f"⚠️ 最低共享需求超过净座 {-slack} 个"
            elif slack == 0:
                flag = "⚠️ 恰好占满，无余量"
            else:
                flag = f"余量 {slack}"
            lines.append(
                f"- {p['proposal_id']} / {s['course']}：净座 {s['net_capacity']}，"
                f"各方向在用完专属课后的最低共享要求 {s['minimum_required_by_direction_after_exclusive']}，"
                f"合计 {s['sum_minimum_claims']}；{flag}。"
            )
    for p in retro:
        for s in p.get("shared_contention", []):
            slack = s["slack"]
            flag = (f"超过净座 {-slack} 个" if slack < 0 else
                    "恰好占满" if slack == 0 else f"余量 {slack}")
            lines.append(
                f"- 回溯口径 {p['proposal_id']} / {s['course']}：净座 {s['net_capacity']}，"
                f"最低共享要求 {s['minimum_required_by_direction_after_exclusive']}，"
                f"合计 {s['sum_minimum_claims']}；{flag}。"
            )
    lines.append("")

    lines.append("## 3. 新事实重估与逐人协调")
    lines.append("")
    for block in r["fact_reassessment"]:
        lines.append(f"本轮新可见事件：{block['newly_visible_event_ids']}")
        lines.append("")
        for dec in block["newly_visible_decisions"]:
            lines.append(f"- 新决定：{dec['course']} `{dec['state']}`，业务生效轮 {dec['effective_round']}，回执轮 {dec['received_round']}")
        lines.append("")
        lines.append("| 方案 | 上轮缺口 | 学生/保留位事实 | last_start窗口关闭 | 停开决定 | 本轮普通缺口 |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for x in block["plan_reassessments"]:
            lines.append(
                f"| {x['proposal_id']} | {x['before']['unmet']} | "
                f"{x['unmet_change_from_student_facts']} | "
                f"{x['unmet_change_from_last_start_window']} | "
                f"{x['unmet_change_from_decisions']} | "
                f"**{x['after_new_facts_and_decisions']['unmet']}** |"
            )
        lines.append("")

    coord = r["commitment_coordination"]
    lines.append("| 编号 | 学生 | 方向 | 课程 | 状态 | 是否占位 | 处置 |")
    lines.append("|---|---|---|---|---|---|---|")
    for x in coord["rows"]:
        lines.append(
            f"| {x['commitment_id']} | {x['student']} | {x['direction']} | {x['course']} | "
            f"{x['state']} | {'是' if x['occupies'] else '否'} | {x['disposition']} |"
        )
    lines.append("")
    urgent = [x for x in coord["rows"] if "coordination" in x["disposition"] or "unavailable" in x["disposition"]]
    if urgent:
        lines.append("需要说明：")
        for x in urgent:
            lines.append(f"- {x['student']} / {x['course']}：{'；'.join(x['actions']) or x['disposition']}")
    window_cases = coord.get("window_deadline_cases", [])
    if window_cases:
        ids = "、".join(c["student"] for c in window_cases)
        lines.append(f"- ⏳ 时间窗已关闭、需逐人回溯协调：{ids}。")
        lines.append("  这些学生在普通第3轮口径下无当前可选课；机器报告的 "
                     "`retroactive_coordination_capacity` 给出若按第2轮回溯可挽救的席位。")
    else:
        lines.append("- 没有已确认且受停开影响的保留位；H3 影响的是未确认申请和潜在需求。")
    lines.append("")

    all_plan_map = {p["proposal_id"]: p for p in r["immediate_capacity"]["all"]}
    retro_plan_map = {p["proposal_id"]: p for p in r.get("retroactive_coordination_capacity", [])}
    if "P1" in all_plan_map and "P2" in all_plan_map:
        lines.append("### 学生级仍可用路径与最大流示例分配")
        lines.append("")
        lines.append("| 学生 | P1 可行课（示例安置） | P2 可行课（示例安置） |")
        lines.append("|---|---|---|")
        ids = sorted(set(all_plan_map["P1"].get("feasible_courses_by_unit", {}))
                     | set(all_plan_map["P2"].get("feasible_courses_by_unit", {})))
        for sid in ids:
            cells = []
            for pid in ("P1", "P2"):
                p = all_plan_map[pid]
                options = p.get("feasible_courses_by_unit", {}).get(sid, [])
                assigned = p.get("assignments", {}).get(sid)
                if not options:
                    retro_plan = retro_plan_map.get(pid, {})
                    retro_options = retro_plan.get("feasible_courses_by_unit", {}).get(sid, [])
                    retro_assignment = retro_plan.get("assignments", {}).get(sid)
                    if retro_options:
                        retro_text = "/".join(retro_options)
                        if retro_assignment:
                            retro_text += f" → {retro_assignment}"
                        text = f"当前已过窗口；回溯可：{retro_text}"
                    else:
                        text = "无可行路径"
                else:
                    text = "/".join(options)
                    if assigned:
                        text += f" → {assigned}"
                cells.append(text)
            lines.append(f"| {sid} | {cells[0]} | {cells[1]} |")
        lines.append("")
        lines.append("示例分配不唯一；共享课的方向分配区间见第2节。")
        lines.append("")

    lines.append("## 4. 历史路径风险能否区分")
    lines.append("")
    lines.append("### 退课数排名为什么不够")
    lines.append("")
    lines.append("| 原始退课排名 | 方向 | 计划课 | 人数 | 退课数 | 退课率 | 即时转轨 | 隔轮回流 | 无回流 | 退课后成功 |")
    lines.append("|---:|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for x in r.get("drop_ranking_comparison", []):
        lines.append(
            f"| {x['raw_drop_rank']} | {x['direction']} | {x['planned_course']} | "
            f"{x['entrants']} | {x['raw_drops']} | {_pct(x['raw_drop_rate'])} | "
            f"{x['direct_transfer']} | {x['delayed_return']} | "
            f"{x['dropped_no_return']} | {x['drop_then_success']} |"
        )
    lines.append("")
    lines.append("原始退课数只按规模排序，把即时转轨、隔轮回流和最终无回流混在同一计数中。")
    lines.append("")
    lines.append("### 分路径成功率")
    lines.append("")
    lines.append("| 方向 | 实际路径 | 目标课 | 间隔 | 人数 | 完成率 | 95%区间 | 支持度 |")
    lines.append("|---|---|---|---:|---:|---:|---|---|")
    for x in r["historical_paths"]:
        if x["n"] < 3 and x["support"] == "insufficient":
            continue
        target = x["target_course"] or "—"
        gap = "—" if x["gap_rounds"] is None else str(x["gap_rounds"])
        lines.append(
            f"| {x['direction']} | {x['category']} | {target} | {gap} | {x['n']} | "
            f"{_pct(x['success_rate'])} | {_pct(x['low'])}–{_pct(x['high'])} | {x['support']} |"
        )
    lines.append("")
    if r.get("backtest"):
        lines.append("### 滚动历史回测（Brier 越低越好）")
        lines.append("")
        lines.append("| 轮次 | 方向基准 | 退课即失败 | 信息状态模型 | 最佳 |")
        lines.append("|---:|---:|---:|---:|---|")
        for round_no in (1, 2, 3):
            rows = {x["method"]: x for x in r["backtest"]["by_round"] if x["as_of_round"] == round_no}
            best = r["backtest"]["best_by_round"].get(str(round_no), "—")
            lines.append(
                f"| {round_no} | {_num(rows['base']['brier'],4)} | "
                f"{_num(rows['naive_drop']['brier'],4)} | "
                f"{_num(rows['state']['brier'],4)} | `{best}` |"
            )
        lines.append("")
        lines.append("第2轮的回执延迟使部分‘无可见课’实际是同轮转课；状态模型只使用当时回执可知的混合概率，"
                     "不把后来才收到的接受记录泄漏回第2轮。")
        lines.append("")

    lines.append("## 5. 等价调整与证据范围")
    lines.append("")
    adj = r["equivalence_adjustment"]
    h6 = adj.get("h6_evidence") or {}
    lines.append(f"拟新增路径：H6→A；历史转移学生 {h6.get('transfer_students', 0)}，"
                 f"历史班次 {h6.get('n_observed_terms', 0)}，支持度 `{h6.get('support')}`。")
    for m in h6.get("missing_evidence", []):
        lines.append(f"- 缺证据：{m}")
    lines.append("")
    lines.append("| 组合 | 口径 | 需求 | 安置 | 缺口 | 加位需求 |")
    lines.append("|---|---|---:|---:|---:|---:|")
    for x in adj["stack_results"]:
        if x["scope"] != "all":
            continue
        min_added = x["min_added_seats_for_all_demand"]
        min_added_text = "需先修规则" if min_added is None else str(min_added)
        lines.append(f"| {x['label']} | {x['scope']} | {x['n_demand']} | {x['assigned']} | "
                     f"**{x['unmet']}** | {min_added_text} |")
    lines.append("")
    for delta in adj.get("coverage_deltas", []):
        current = delta.get("current_window", {})
        coord = delta.get("retroactive_exception_coordination", {})
        outlook = current.get("outcome_range") or {}
        interval = outlook.get("supportable_total_success_interval")
        if interval:
            lines.append(
                f"- {delta['plan']}+P3（当前轮窗口）：席位缺口 "
                f"{current.get('unmet_before')}→{current.get('unmet_after')}；"
                f"证据课 Wilson 区间加 H6 零证据宽度下，成功数上界约 {interval[1]:.1f}。"
            )
        if coord:
            lines.append(
                f"  - 若批准第2轮回溯/例外：{coord.get('unmet_before')}→{coord.get('unmet_after')}；"
                "该结果需逐人协调，不能当作普通第3轮可选位。"
            )
    if adj.get("coverage_deltas"):
        lines.append("")
    for d, report in r["policy_support"]["directions"].items():
        lines.append(f"### 方向 {d}")
        lines.append("")
        lines.append(f"- 规则：required_credits={report['required_credits']}, last_start={report['last_start']}")
        matches = report["comparable_mature_terms"]
        exact = [m["term"] for m in matches if m["match"] == "rule_and_courses"]
        lines.append(f"- 规则与课程集合均可比的成熟学期：{exact if exact else '无'}")
        if report.get("credit_rule_warning"):
            lines.append(f"- ⚠️ {report['credit_rule_warning']}")
        for c in report["courses"]:
            lines.append(
                f"  - {c['course']}：转移 {c['transfer_students']} 人，"
                f"成功率 {_pct(c['observed_success_rate'])}，"
                f"区间 {_pct(c['success_low'])}–{_pct(c['success_high'])}，`{c['support']}`"
            )
        lines.append("")

    lines.append("## 6. 时段与时间窗约束")
    lines.append("")
    for stress in r["time_and_credit_stress"]:
        collisions = stress.get("collisions", {})
        same = collisions.get("same_band_course_groups", {})
        bands = collisions.get("proposal_bands", {})
        lines.append(f"- {stress['proposal_id']} 提案时段：{bands}")
        if same:
            lines.append(f"  - 同一时段课程组：{same}。若学生仍保留其中一门作为并行必要课，则不能同时选另一门；转轨替换时必须先明确释放旧席位。")
        for d, check in stress["direction_checks"].items():
            lines.append(
                f"  - {d}：若把 required_credits={check['required_credits']} 错误地假设为多门叠加，"
                f"还需 {check['hypothetical_additional_one_credit_seats']} 席；"
                f"无冲突叠加={check['can_stack_without_time_conflict_if_rules_allow_multiple']}。"
                f" 合同口径警告：{check['contract_compliant_warning']}"
            )
    lines.append("- 时间窗：本轮只把 `release_round <= 方向 last_start` 的课程计入该方向可行路径；"
                 "第3轮的 P2/H3 对 `last_start=2` 的 B 方向不可用于代修。")
    timeline = next((x for x in r.get("choice_window_timeline", []) if x["run_round"] == r["as_of_round"]), None)
    if timeline:
        lines.append("")
        lines.append(f"### 第 {r['as_of_round']} 轮可选/不可选判定")
        for plan in timeline["plans"]:
            bits = []
            for d, courses in plan["courses_by_direction"].items():
                for c in courses:
                    if not c["selectable"]:
                        bits.append(f"{d}:{c['course']}（{c['reason']}）")
            if bits:
                lines.append(f"- {plan['proposal_id']} 不可选：{'; '.join(bits)}")
        lines.append("- 未列出的提案等价课在满足公布轮、`last_start` 且无停开决定时可选。")
    lines.append("")
    lines.append("---")
    lines.append("完整机器可读结果见同名 `.json`；历次快照可通过 `review` 子命令复盘。")
    return "\n".join(lines)
