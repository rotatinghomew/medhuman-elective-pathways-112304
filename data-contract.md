# 资料口径

历史学期为 1 至 8，轮次为学期内相对周次，第四轮代表学期末结算。报退记录的 `round` 是业务发生轮，`received_round` 是教务得知的轮。学生方向只是学期末所需学习目标类别，选课码由课程台账映射到方向；一个方向内列出的等价课程可互相替代，同时只能计一门。代修认定以课程开始轮和有效版次为准，完成结算以该学期确已回收的结果为准。第八学期是观察中的学期。方案的 `capacity` 是整班可用座位，学员可以主动退位，明确确认前的草案不占位。

数据表使用 UTF-8 CSV，第一行是字段名。同一业务号的引用指数据字典约定的原始对象；空白表示来源未提供，集合字段由逗号分隔后整体按 CSV 转义。
- `pool.csv` 的字段为 `term`、`round`、`course`、`capacity`、`time_band`、`published_round`。
- `entrance.csv` 的字段为 `id`、`term`、`direction`、`track`、`planned_course`、`last_transfer_round`。
- `event.csv` 的字段为 `id`、`student`、`round`、`received_round`、`course`、`kind`、`supersedes`。
- `outcome.csv` 的字段为 `student`、`round`、`received_round`、`achieved`、`last_course`、`state`。
- `equivalence.csv` 的字段为 `version`、`term_start`、`term_end`、`direction`、`eligible_courses`、`required_credits`、`last_start`。
- `proposal.csv` 的字段为 `id`、`version`、`course`、`release_round`、`capacity`、`time_band`。
- `commitment.csv` 的字段为 `id`、`student`、`course`、`round`、`state`。
- `decision.csv` 的字段为 `course`、`term`、`effective_round`、`received_round`、`state`。
