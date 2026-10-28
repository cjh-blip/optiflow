# 动作契约（Action Contract）

> **唯一权威**。可执行版本在 `src/optiflow/commands.py`；本文是它的说明与边界。
> 改了动作的参数或返回，**本文与 `tests/test_commands.py` 必须同步改** —— 契约漂移比代码漂移贵。

## 一句话

平台层的动作只定义一次：`COMMANDS = {id: {handler, params, returns, description}}`。
四个前端（`api.py` 的 HTTP、`mcp.py`、两个 `-m` CLI、以及外部壳与 agent）一律**查表派发**，
不各自直接摸 core 函数。

为什么值得这么做：现在光枢的动作散在各个前端里（CLI 一个写法、HTTP 一个写法、Tk 壳再一个写法），
加第五个前端（Hana / DSH / agent 直连）就得再写一遍，且每写一遍都会悄悄分叉。
一张表 + 一个 `call()`，第五个前端零成本。

## 怎么用

```bash
# 列出全部动作（JSON）
PYTHONPATH=src python -m optiflow.commands list

# 看一个动作的规格（参数、类型、必填、返回）
PYTHONPATH=src python -m optiflow.commands show plan.summary

# 调用（值能当 JSON 解就当 JSON，否则当字符串）
PYTHONPATH=src python -m optiflow.commands call plan.summary \
    --dxf tests/fixtures/sample_room.dxf \
    --dxf_lum tests/fixtures/sample_lighting.dxf \
    --config tests/fixtures/sample_parse_config.json \
    --ceil_h 4.0

# 或整包给 JSON
PYTHONPATH=src python -m optiflow.commands call report.assemble \
    --params '{"package": "/abs/pkg.json", "criteria": "/abs/crit.json"}'
```

```python
from optiflow.commands import call, list_commands, get

call("plan.summary", {"dxf": "...", "config": "..."})   # 返回 dict
```

退出码：`0` 成功；`2` 失败（`{"ok": false, "error": "..."}` 打到 stdout，**错误里带可行动信息**）。

## 动作清单

| id | 组 | 做什么 | 必备参数 | 返回 |
|---|---|---|---|---|
| `plan.summary` | plan | 房间 DXF（＋灯具图＋配置）→ 摘要四数 ＋ **七项**完整性检查（六项并集＋轮廓点≥3）＋ 家具双口径/单主房 ＋ 校验分桶 | `dxf`, `config` | `{room_count, vertices_total, area_total_m2, furniture_candidates, furniture{count,adopted,excluded,excluded_ids,names}, main_room{id,name,points,area_m2,bbox_m,ceiling_h_m,luminaires{}}, luminaires_total, luminaires_by_kind, luminaires_by_shape, checks[]（七项）, checks_passed, validation{}}` |
| `plan.light_backcalc` | plan | 选灯反推：面积＋目标照度（＋CU/MF/LPD 上限/灯数）→ 总光通量、功率天花板、单灯区间；**不排灯位、不推荐型号** | `area_m2`, `target_lux` | `{total_flux_lm, power_budget_w, per_lamp{}, params_used, state, evidence}` |
| `ir.validate` | plan | IR 校验：jsonschema ＋ 七条业务规则 | `ir` | `{count, halt[], violations[]}` |
| `furniture.from_ir` | furniture | IR 家具记录 → 真机链四字段参数（`id/room_id/bbox/height_m`）；bbox 缺失从 polygon 算，room_id 空直接报错点名；口径只此一处 | `ir` | `{count, items[{id,room_id,bbox,height_m}]}` |
| `service.import_dxf` | plan | DXF 文件 → IR ＋ 完整性检查（读文件并转 base64） | `path` | `PlatformService.import_dxf` 的返回 |
| `report.assemble` | report | 结果包（＋判据表/价格）→ 七块 Markdown | `package` | `{markdown, chars}` |
| `report.metrics` | report | 六项指标板（**数据**，非 markdown）：实测/阈值/判定/出处，UGR 取最不利 | `package` | `{rows[], count}` |
| `report.render_docx` | report | Markdown → DOCX（需 pandoc；缺了明确报错） | `markdown`, `out` | `{docx, bytes}` |
| `service.import_dxf_b64` | plan | DXF 正文（base64）→ IR ＋ 完整性检查；**与 HTTP `/import-dxf` 同形** | `filename`, `content_base64` | 同 `import_dxf` |
| `report.assemble_from_pdf` | report | DIALux 报表 PDF → 抽数 → 七块报告；**与 HTTP `/assemble-report` 同形** | `report_pdf` | 同 `assemble_report_pdf` |
| `service.capabilities` | meta | 能力目录：本表 ＋ 适配器能力声明 | — | `{adapters[], commands[]}` |

## 约定

1. **参数类型白名单**：`str` / `int` / `float` / `bool` / `json` / `path` / `markdown`。
   `json` 类参数既可给已解析的对象，也可给 JSON 文件路径（本表替你读）。
2. **多给参数报错**：不认识的键**不静默忽略**，直接报「不认识参数，它认这些」。
3. **少给必填报错**：说清缺哪个、那是什么。
4. **失败抛异常**：库层不管用户界面（与 `plan_summary` 同一纪律）；壳负责把异常变成人话。
5. **路径由调用方给**：本表不做 cwd 猜测（`ParseConfig.from_json` 是裸 `read_text`，相对路径会踩空）。
6. **加新动作两步**：`commands.py` 登记一条 ＋ `tests/test_commands.py` 加一条（能调用、能报错）。

## 回执约定（`{ok, state, evidence}`）

凡是对外回话的动作（HTTP 端点、MCP 工具、CLI 的 `call`）统一三个键：

| 键 | 放什么 | 例 |
|---|---|---|
| `ok` | 成功/失败（失败另有 `message`/`error`，必须带可行动原因） | `true` |
| `state` | **关键中间量**：让上层不用解析正文就能判断（行数、房间数、灯位数、检查过几项、块数、字符数） | `{rows: 6, missing: []}` · `{room_count: 1, checks_passed: 6}` · `{blocks: 7, chars: 2845}` |
| `evidence` | **输入出处与产物**：路径、生成物位置（截图/PDF/DOCX/MD），供事后对账 | `{inputs: {package: "..."}, artifacts: {markdown: "..."}}` |

为什么：验收原本靠人眼看界面或解析正文；有了这三键，验收可以**读返回值**完成（storytold 的 `ui.inspect` 同思路）。
现状（2026-10-13）：Hana App 三条路由、独立桌面版三端点与浏览器侧指标板、OptiFlow 的 `/metrics` 类响应都已带 `state`/`evidence`；断言钉在
`optiflow-workbench-dev/route-integration.mjs`（三条路由各一条）。

## 外部注册（`register()`）

平台**不依赖任何壳**（依赖方向 app → 平台），但"动作只定义一次"要求壳把自己的步骤也放进同一张表。于是平台只给口子：

```python
from optiflow.commands import Command, Param, register

register(Command(id="dialux.parse", handler=..., group="dialux",
                 description="…", returns="…",
                 params=(Param("dxf", "path"), Param("out", "path"))))
```

- 登记后 `list_commands()` / CLI / `GET /commands` / 两侧壳**同一张表**都能看到并派发；
- 默认**不许覆盖**已有 id（防手滑换实现），要换显式 `replace=True`；
- 用途实例：
  - 2026-10-13：Windows 侧 `optiflow-app` 壳启动登记真机链四步
    （`dialux.parse` / `dialux.overlay` / `dialux.inputs` / `dialux.one_shot`）；
  - 2026-10-18（队列 E 阶段1）：同壳登记真机 UIA 五动作
    `dialux.real.import_stf` / `place_luminaires` / `place_furniture` / `ugr` / `export_report`
    ——经 dialux-compiler `scripts/real_action.py` 子进程执行（真相源留 dc，不收编）；
    非 Windows 调用拿人话守卫；进度走壳内 `set_step_sink` 侧信道（**参数契约不变**）。
    本节是外部注册的记录处；静态清单仍只列平台自登记动作。

## 真机前置条件（`dialux.real.*`，2026-10-28 真机实测定）

这四条是**真机实测**逼出来的约束，实现落在 Windows 侧 runner（真相源 dc），
本表只定契约，改动前先投一行短知会：

1. **`import_stf` 前置：空项目**。已含结果档案（真灯/计算点）的项目里导入必崩
   （DIALux 保存时重载 ResultsArchive 与几何对不上，SEHException）。
   实现二选一：导入前清结果档案；或清不掉时跳过保存并如实报「未保存」。
2. **保存永远另存，不覆盖当前项目文件**。10-28 实测 `save` 把 5.77 MB 的
   28 盏真灯项目覆盖成 0.09 MB 空壳。另存命名 `<原名>_imported_<yyyymmddHHMM>.evo`，
   或调用方显式传目标路径；`save` 步骤禁用「覆盖当前文件」语义。
3. **`ugr` 前置：项目里必须有真灯**。28 个占位符下 UGR 挂死 >10 分钟（占位符无
   配光分布，DIALux 算不出）。`run_ugr` 入口检查灯具类型，占位符直接报错
   「先换真灯再算 UGR」，不进计算。
4. **家具四字段口径以 `furniture.from_ir` 为准**。IR 家具缺 `room_id/bbox` 是
   接口断点（10-28 现场手拼）。任何前端要家具参数先过它，不许自己拼 bbox。

## 待收编（有意留白，不代表遗漏）

| 动作 | 为什么还没进来 | 收编前提 |
|---|---|---|
| `pipeline.submit` / `.status` / `.result` / `.cancel` | 要一个 `PlatformService` 实例。**已定：注册表内惰性单例**（`commands._service()`，要换输出根就清掉它）；但 job 类动作带异步生命周期，收编要先定「job 归谁看」 | 定下 job 归属（HTTP 侧已有 `/jobs`，MCP 侧已有 `optiflow_job_*`） |
| ~~`report.assemble_pdf`~~ | **已收编**为 `report.assemble_from_pdf`（2026-10-13） | — |
| 真机链老五件（出 STF / 驱动 DIALux / 截图核对 / 抽数出报告 / 一条龙） | 已大半以**壳侧登记**落地（2026-10-13 四步；2026-10-18 阶段1 再加 `dialux.real.*` 五动作经 dc runner 子进程）；实现本体仍在 **dialux-compiler** 仓 | **不打算收编**：真相源留 dc，平台只认动作表（跨仓子进程已是终态） |
| 指标板（六项）的**单一实现** | JS 侧已有 `metrics.js`、Python 侧已有 `report_assembler`；**跨语言一致性断言已落地**（`scripts/metrics_parity.mjs`，24 格全同：6 行 × 4 列、带/不带判据表两组；2026-10-24 回流为 POSIX/Windows 双通） | 若要再收成一处，得先决定 JS 那份是否退化成「只有渲染」 |

## 与其他前端的关系（现状 → 目标）

| 前端 | 现状 | 目标 |
|---|---|---|
| `api.py`（HTTP） | 自己写路由 ＋ 直接调 service | 路由层只做「解析请求 → `call(id, params)` → 包装响应」 |
| `mcp.py` | 自己列工具 | 工具表由 `list_commands()` 生成 |
| `plan_summary` / `report_assembler` 的 `-m` CLI | 各自的 argparse | 保留（它们是"能单跑"的优点），但内部走同一 handler |
| 独立桌面版 / Hana App | 各自三个 HTTP 端点 | 同样查表派发；真机链动作进来后，两端点变多点 |

## 变更记录

- 2026-10-13：首版。登记 6 个纯函数动作；`service.*` 与真机链动作留待收编。
- 2026-10-13（M2.5）：补 2 个与 HTTP 端点同形的动作（共 8 个）；`api.py` 的 `/import-dxf`、`/assemble-report` 改走 `call()`（HTTP 契约不变，`tests/test_api.py` 50 项全绿）；`mcp.py` 的工具表由本表**生成**（不再手写第二份）；新增「回执约定」一节。
  同日由跨语言一致性断言抓出并修掉一处口径措辞差（无判据时：`—` → `—（标准未规定）`，与 JS 侧逐字一致）。
- 2026-10-16：新增 `plan.light_backcalc`（选灯反推尺子）：利用系数法反推总光通量/功率天花板/单灯区间，纯函数＋测试；两条护栏（不排灯位、不推荐型号）写进 description。清单同时补上此前漏列的 `report.metrics`（动作 2026-10-13 已登记，清单当时未同步）。
- 2026-10-28：新增 `furniture.from_ir`（真机发现 C 的接口断点：IR 家具缺 room_id/bbox）；新增「真机前置条件」四节（空项目导入 / 另存不覆盖 / UGR 真灯前置 / 家具口径单源），对应真机实测两崩一挂一断点。
- 2026-10-18（队列 E 阶段1）：壳侧新增 `dialux.real.*` 五动作（`app_real.py` 经 `register()` 登记，self-check 动作 14 → 19）：子进程走 dc `scripts/real_action.py`（JSONL step/receipt 契约），非 Windows 与缺仓两道人话守卫，进度走 `set_step_sink` 侧信道（参数与回执 shape 不变）；本静态清单仍只列平台自登记的 10 个，外部注册详见上节。
