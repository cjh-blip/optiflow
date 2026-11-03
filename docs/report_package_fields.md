# report_package 字段表（v1）

> 这份包的字段名会被**界面**和**文档**两条线同时消费，所以先定字段再填值。
> 改字段就是改契约：加字段可以，改名字要同时通知两条线。
> 生成器：`src/optiflow/report_package.py::build_report_package`
> 落盘脚本：`scripts/report_package.py`

## 顶层

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema_version` | int | 当前 1。不兼容变更才 +1 |
| `generated_at` | str | ISO8601 UTC |
| `rooms` | array | 房间清单，见下 |
| `standards_cited` | array | 判据出处表，见下。**报告附录直接用** |
| `shortfalls` | array | 缺口与建议，形状见下 |
| `luminaire` | object | 灯具清单 |
| `uncovered` | array | 未覆盖项。**不许留白** |
| `assumptions` | array\<str\> | 算法层声明的前提（如层高按 4.0 m 假设） |
| `warnings` | array\<str\> | 整份计划级 warning |
| `pipeline` | object \| null | 指回某次运行：`{job_id, steps, source}` |
| `totals` | object | 汇总，见下 |

## rooms[]（每间房一条）

| 字段 | 类型 | 说明 |
|---|---|---|
| `space_id` | str | 空间 id |
| `room_type` | str \| null | 原始类型值（可能是键 `meeting_room` 或中文「会议室」）；null = 未指定 |
| `standard_key` | str \| null | 判据表键；null = 未指定或未收录，**该房间不做标准对标** |
| `standard_basis` | str | 人话版对标依据（中文类型名 + 各格出处） |
| `area_m2` | float | 面积 |
| `target_lux` | float | 本房间照度目标 |
| `target_basis` | str | 目标从哪来：空间自带 / 任务显式 / 判据表标准值（带表号）/ 内置默认 |
| `actual_lux` | float | 实测平均照度（流明法） |
| `illuminance_ok` | bool | 是否达目标 |
| `u0` | float | 实测均匀度 |
| `u0_required` | float | 本房间 U₀ 要求 |
| `u0_basis` | str | U₀ 要求从哪来（含覆盖关系） |
| `u0_ok` | bool | 是否达 U₀ 要求 |
| `lpd` | object | `{actual, current_limit, target_limit, compliant, meets_target, basis}`；`compliant`/`meets_target` 为 null = 无标准不判 |
| `fixture_count` | int | **实际布灯数**（轮廓筛除 + 补灯之后） |
| `nominal_count` | int | 按面积算的**名义**盏数。与 `fixture_count` 不同是设计意图 |
| `supplement` | object \| null | 补灯记录 `{nominal_count, actual_before, dropped_ratio, rounds, actual_after, nominal_after}`；null = 没补过 |
| `metric_status` | object | 每个指标一态，见下 |
| `warnings` | array\<str\> | 本房间 warning |

### metric_status 四态

| 值 | 含义 | 界面该怎么显示 |
|---|---|---|
| `computed` | 算了，且有类型时已按判据比对 | 正常显示数值 + 达标/不达标 |
| `not_computed` | 算法层没实现 | 显示「未实现」，**不要显示成 0 或横杠** |
| `not_specified` | 房间类型未指定，无标准可依 | 显示实测值 + 「类型未指定，未对标」 |
| `not_in_standard` | 该类型标准里这一格未收录 | 显示「标准未收录」 |

覆盖的指标：`illuminance_avg` / `uniformity_u0` / `power_density` / `ugr` / `ra`。
`ugr` 与 `ra` 当前恒为 `not_computed`（依赖未接入，见下）。

## standards_cited[]（每个用到的限值一行）

| 字段 | 类型 | 说明 |
|---|---|---|
| `room_type` | str | 中文类别名（如「会议室」） |
| `field` | str | `illuminance` / `ugr` / `u0` / `ra` / `lpd_current` / `lpd_target` |
| `label` | str | 中文名 |
| `value` | float \| null | 限值 |
| `unit` | str | `lx` / `W/m2` / 空 |
| `standard` | str | **标准号 + 表号**，如 `GB 55015-2021 表 3.3.7-3` |

## shortfalls[]（缺口与建议）

沿用算法层 `RoomPlan.shortfalls` 的形状，另加 `space_id`：

| 字段 | 说明 |
|---|---|
| `space_id` | 哪间房 |
| `metric` | `uniformity_u0` / `power_density` / … |
| `label` | 中文名 |
| `actual` / `target` / `unit` | 实测、目标、单位 |
| `gap` | `actual - target`（**符号按 `direction` 读，不要直接按正负判好坏**） |
| `gap_pct` | 差距百分比 |
| `direction` | `under` = 不够（照度/U₀）；`over` = 超了（LPD） |
| `severity` | `below_target` / `non_compliant` |
| `cause` | 原因（代码级，含具体数值与算式） |
| `action` | 建议动作 |
| `standard` | 该条对标的出处（仅 LPD 项有） |

## luminaire（灯具清单）

| 字段 | 说明 |
|---|---|
| `model` | 型号/名称；IR 里没有就是 null |
| `flux_lm` | 单灯光通量 |
| `power_w` | 单灯功率 |
| `power_source` | `measured` = 灯具条目带的额定功率；`declared` = 调用方给；`derived` = **从光通量按假定光效反推**；`mixed` / `unknown` |
| `power_source_detail` | 原始来源描述（算法层原话） |
| `power_basis` | 反推依据 |
| `count` | 总盏数 |
| `total_power_w` | 总功率 |
| `note` | 当 `power_source=derived` 时给一句警告；否则 null |

**`power_source=derived` 时，报告里必须带警告**：换灯具型号或光效，LPD 与合规结论都会变。

## uncovered[]（未覆盖项，不许留白）

| 字段 | 说明 |
|---|---|
| `item` | `ugr` / `ra` / `cct` / `price` / `energy` / `rendering` |
| `label` | 中文名 |
| `status` | `not_implemented` = 算法没做；`missing_data` = 缺数据 |
| `what_is_missing` | **缺什么才能做**，写具体（要 SPD、要配光曲线、要报价表…） |

新增未覆盖项要同时更新 `UNCOVERED_ITEMS` 里的四条信息；**删条目要写理由**。

## totals

| 字段 | 说明 |
|---|---|
| `room_count` | 房间数 |
| `fixture_count` | 总盏数 |
| `area_m2` | 总面积 |
| `total_power_w` | 总功率 |
| `rooms_fully_compliant` | 照度达标且 LPD 未判不合规的房间数 |

## 追溯规则

1. 包里每个数值要么来自某次 `plan_layout` 的结果（`pipeline` 指回 job），要么来自判据表某行（`standards_cited` 有出处）。**没有第三种来源。**
2. 房间类型未指定时，`standard_key` / `lpd.*_limit` / `lpd.compliant` 全为 null，`metric_status` 为 `not_specified`——这不是「缺数据」，是「没有标准可依」，两者在报告里要分开写。
