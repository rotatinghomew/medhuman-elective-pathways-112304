# 人文选修路径预测与分批转轨席位

面向教务每轮排课/开放转轨时运行的路径分析工具。它把入组计划、分轮课程台账、
报退与补登记回执、跨院等价规则、学期结果、提案席位、保留位和停开决定放在同一个
“本轮可知事实”口径下，输出：

- 退课后即时转轨、隔轮回流、仍无课、观察中未结算等路径区分；
- 历史成熟学期的路径成功率、小样本区间、可比学期/班次支持度和滚动回测；
- A/B 方向共享跨院课时的联合席位分配、挤占点、缺口区间和最少加位数；
- confirmed/requested/withdrawn 保留位的占位与释放；
- 迟收停开决定后的事实重估、仍可行路径和逐人协调清单；
- 新增/调整等价课（如 H6）的物理覆盖区间与证据缺口；
- 每轮 JSON/Markdown 快照，以及日后用实际结果复盘快照的命令。

工具只依赖 Python 标准库；仓库锁定的 `numpy/scipy` 不是运行必需依赖。

## 快速开始

在仓库根目录执行：

```sh
# 一次生成第 1、2、3 轮报告，并写入 reports/snapshots.jsonl
python3 pathway_tool.py all-rounds --data data --out reports

# 只跑某一轮（例如收到第 3 轮回执和停开决定后）
python3 pathway_tool.py round --data data --term 8 --round 3 --out reports

# 历史成熟学期滚动回测
python3 pathway_tool.py backtest --data data --out reports

# 既往每个学期/轮次的学生级预测与实际结果对照
python3 pathway_tool.py history-review --data data --out reports

# 复盘本 out 目录保存过的所有轮次快照
python3 pathway_tool.py review --data data --out reports

# 测试
python3 -m unittest discover -s tests -v
```

如果按环境安全要求使用 `python3 -I`，请用脚本绝对路径并显式传数据绝对路径，例如：

```sh
python3 -I /workspace/pathway_tool.py round \
  --data /workspace/data --round 3 --out /tmp/reports
```

## 主要输出

`round_t<term>_r<round>.json` / `.md` 是同一结果的机器可读和教务可读版本。JSON 中常用键：

- `visibility`：本轮可见/因 `received_round` 延迟而不可见的事件和决定；
- `demand`、`in_progress_paths`：当前无课、申请、仍在位和观察中转移；
- `immediate_capacity`：按 `all`、`released_to_date`、`current_release` 口径的联合排位；
- `demand_case_matrix`：当前无课、加申请、历史区间上沿、极端同时转移等需求情形；
- `commitment_coordination`：保留位占位状态和需要逐人协调的对象；
- `fact_reassessment`：把缺口变化拆成“学生/保留位新事实”“`last_start` 窗口关闭”和“停开决定”；
- `policy_support`、`historical_paths`、`backtest`：历史证据和预测做法比较；
- `equivalence_adjustment`：H6 等等价调整的覆盖变化和证据缺口；
- `student_forecasts`、`student_path_options`（在容量结果内）：学生级预测和可行路径；
- `recommendations`：可直接展示的关键提示。

`reports/snapshots.jsonl` 是逐轮追加的审计快照。学期未结算时，`review` 会标记
`observing_no_final_outcome`，不会把这些学生计为失败或完成；将来 outcome 回收后，
同一命令会计算已结算快照的 Brier 分数。

## 关键口径

### 1. 本轮可知，不泄漏补登记

事件同时有：

- `round`：业务发生轮；
- `received_round`：教务得知轮。

第 `r` 轮分析只采用 `received_round <= r` 且业务轮不晚于 `r` 的事实。因此，一条
第 2 轮发生、第 3 轮才收到的同轮转课接受，在第 2 轮预测中仍是“可见无课/未确认”，
到第 3 轮才进入已安置路径。历史复盘使用完整事实，但滚动训练只使用目标学期之前的
成熟学期，避免同学期 outcome 泄漏。

### 2. 未结算记录只贡献过程

只有 outcome 中确已回收的 `closed` 学期进入成功率和回测。第 8 学期没有 outcome，
所以：

- 其 accept/drop/commitment 会影响当前席位和路径；
- 不会进入成功/失败训练标签；
- 不会被当作流失或完课。

### 3. 路径分类

完整历史事实分为：

- `stayed`：没有报退；
- `direct_transfer`：退课和首次接受替代课在同一业务轮；
- `delayed_return`：退课后隔一轮才接受课程，即“先无课、后回流”；
- `dropped_no_return`：成熟学期中退课后没有后续接受。

第 2 轮回执受限时，模型使用当时可见的 `stable:*`、`unplaced`、`placed:*` 信息状态，
而不是偷看完整路径。

### 4. 小样本和班次支持

每条“方向 × 路径 × 替代课”结果给出 Wilson 95% 区间和支持度：

- `established`：至少 10 名转移学生且覆盖至少 3 个历史学期/班次；
- `limited`：达到 5 人或 2 学期之一；
- `insufficient` / `no_evidence`：样本或班次不足。

当前资料中，H3/H4 有较充分样本；H5 从第 4 学期才出现且分方向样本只有 5–6 人；
H6 没有历史学生、结果、台账班次或时段证据。

### 5. 联合席位而非分方向相加

容量核算是一个二分图最大流：

- 学生/需求单位 → 该方向有效等价且未停开、满足 `last_start` 和提案范围的课程；
- 课程 → 扣除已确认/已在读占用后的净席位。

因此 A、B 两个方向对共享 H5 的需求不会被重复使用。结果中的
`allocation_ranges` 给出总安置数最大时各方向仍可能的缺口区间；
`min_added_seats_for_all_demand` 用最小费用增广计算所有有路径学生都安置所需的最少加位。

`requested/pending/draft` 不占位；`confirmed/active/accepted/held` 立即占位；
事件 drop 或 `withdrawn/cancelled/released/declined/expired` 明确释放席位。
单纯草案、超时未确认或分析猜测都不会释放已确认席位。

## 当前合成资料上的可复核结论

以下数字由仓库测试固定：

- 第 2 轮可知 19 名无课学生（A=13、B=6）；另有 T8S004 的 H3 `requested` 不占位。
  - P1 全方案净席 18，缺 1；共享 H5 净 4、A 至少需要 5，是具体挤占点。
  - P2 已放轮次只有 H4/H5，当前轮缺 8；若把第 3 轮 H3 计入完整方案，19 人可全部安置，但没有余量。
- 第 3 轮新可见 T8S004、T8S007 两条迟收 drop，且得知 H3 停开。
  - 无课学生增至 21（A=15、B=6）。
  - V3/B 的 `last_start=2`，因此第 3 轮普通新选位不能再把 B 学生新排入 H4/H5。
  - 当前轮口径：P1 可安置 4、缺 17（A=11、B=6）；P2 可安置 8、缺 13（A=7、B=6）。
  - 若教务批准按第 2 轮回溯/逐人例外，`retroactive_coordination_capacity` 给出可挽救口径：P1 缺 11、P2 缺 10；这不是普通第 3 轮路径。
  - P1 缺口可拆为：`+2` 来自新学生事实、`+6` 来自 B 时间窗关闭、`+8` 来自 H3 停开；P2 为 `+0` 学生事实、`+6` 时间窗、`+7` 停开。
- K1/T8S001 的 H5 confirmed、K2/T8S003 的 H4 confirmed 各占一席；K4/T8S010 的 H5 withdrawn 不占位；K3/T8S004 的 H3 requested 不占位但在停开后成为需逐人说明/改荐的申请。另有 6 名 B 无课学生触发 `window_deadline_cases`，需要逐人决定是否回溯协调。
- 自动识别到的 H6 新增等价路径可提供 8 个物理席位：
  - 当前轮窗口下，P1+P3 缺口 17 → 9，P2+P3 缺口 13 → 6；
  - 若同时批准第 2 轮回溯/例外，缺口可为 P1+P3=3、P2+P3=2。
  - 但 H6 没有历史转移、结果或班次，完成率区间必须保留 `[0, 可分配人数]` 的零证据宽度，不能照搬 H3 的约 92%。
- 滚动回测的 Brier 分数显示：把任何可见报退都当作失败明显最差（第 2 轮约 0.300、第 3 轮约 0.344）；信息状态模型在第 2 轮约 0.164、第 3 轮约 0.100，均优于退课即失败和方向基准。

## 自定义等价调整

库 API 可做额外 what-if：

```python
from pathways.io import load_bundle
from pathways.policy import policy_for_term, Adjustment
from pathways.capacity import analyze_plan

bundle = load_bundle("data")
policy = policy_for_term(bundle, term=8)
what_if, evidence = Adjustment(
    name="remove_h3_add_h7",
    add={"A": ["H7"]},
    remove={"A": ["H3"]},
).apply(policy, bundle, as_of=3)

plan = analyze_plan(
    bundle, term=8, as_of=3, policy=what_if,
    plan_id=["P1", "P3"], scope="all",
)
```

如果加入的 H7 不在 `proposal.csv` 中，它只改变等价资格、不增加席位；报告会把它标成无容量/无历史支撑，而不是虚构承接能力。

## 代码结构

- `pathways/io.py`：manifest/CSV 校验和类型转换；
- `pathways/policy.py`：有效等价版本、时间窗、决定可见性和 what-if；
- `pathways/students.py`：回执感知席位台账、状态和完整历史路径；
- `pathways/history.py`：路径统计、Wilson 区间、预测模型、滚动回测和历史复盘；
- `pathways/capacity.py`：最大流/最小加位费用流和共享课竞争区间；
- `pathways/forecast.py`：当前需求、申请、历史区间上沿和极端同时转移情形；
- `pathways/insights.py`：协调清单、证据支持度、结果区间；
- `pathways/stress.py`：时段冲突和透明标注的多学分敏感性；
- `pathways/audit.py`：JSONL 快照与实际结果复盘；
- `pathways/report.py`、`pathways/cli.py`：Markdown/JSON 和命令行；
- `tests/test_pathways.py`：固定关键口径和上述数字。
