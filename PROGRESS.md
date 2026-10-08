# PROGRESS — DIALux 文件层搬运（P1 第 1 步）

> 断点续跑：读本文件即可接着做。每完成一项立刻更新。
> 口径注（2026-10-05）：本文件是 2026-09 期间的连续执行记录，运行数字为各阶段实际输出；
> 示例数据已于 2026-09 开源脱敏时替换，部分数字（如 31 盏 / 508.8 lx / 0.693）无法用现行数据复现，
> 现行口径见 `README.md`。另：早期脚本曾将字符数打印为「字节」，对应输出行已订正为「字符」。

## 任务 0 基线（实测）

| 检查 | 命令 | 结果 |
|---|---|---|
| OptiFlow | `cd /d/dev/OptiFlow && python -m pytest -q` | **11 passed in 0.74s** ✅ |
| dialux-compiler | `cd /d/dev/dialux-compiler && python -m pytest -q` | **249 passed in 12.50s** ✅ |
| 依赖 | ezdxf 1.4.4 / pywin32 / jsonschema / pytest 9.0.2 | 均在 `/d/dev/anaconda3` 已装 ✅ |

## 理解的目标 / 顺序 / 最大风险（≤10 行）

1. 目标：把 dialux-compiler 已跑通的通用件（workbench）与 DIALux 文件通道（DXF 解析 + STF 导出）搬进 OptiFlow，让平台第一次接上真软件——只做文件层。
2. 顺序：任务 1 workbench → 任务 2 parser/STF → 任务 3 DialuxAdapter 接契约 → 反向验证 → 提交。
3. 让步顺序：搬得准 > 接得全 > 接得快。金标准做不到逐字节时，退回「同语义比对 + 写清原因」，绝不放宽断言。
4. 最大风险 ①：被搬测试含**不可改的路径断言**，倒逼部分模块必须留在 `src/` 顶层（见 BLOCKED.md B-2）。
5. 最大风险 ②：黄金文件 `build/mvp3_lums.stf` 是 MVP3 之前的产物，`cmp` 必定不一致（见 B-1）。
6. 最大风险 ③：把冻结仓写坏。全程只读源仓，单向复制一次，不双向同步。
7. 纪律：不跳测试、不放宽断言、不 mock 被测对象、不改阈值/验收脚本、不新增依赖。

---

## 执行记录

### 任务 1 —— workbench 搬运 ✅

- 落地：`src/optiflow/workbench/`（7 个文件，**全字节零改动**）。
- 验收（逐文件 diff，忽略 import 行，输出为空）：7/7 OK；另做全字节 `cmp`：7/7 identical。
- 搬测试：`test_workbench.py`(7) / `test_perception.py`(7) / `test_progress_contract.py`(18) = 32 条，全绿，0 skipped。
- 工作量：`workbench` 内部只用相对 import，所以源文件一行都没改；只有测试里的 `src.workbench.*` 改成了 `optiflow.workbench.*`。

### 任务 2 —— 解析与 STF 导出搬运 ✅

- 落地：`src/optiflow/adapters/dialux/`：`_chain.py`、`dxf.py`、`xlsx.py`、`stf.py`（保持原文件名）。
- 搬测试：`test_parser_dxf`(2) / `test_chain_geometry`(37) / `test_exporter_stf`(70) / `test_validator`(5) / `test_project_hygiene`(4) = 118 条，全绿。
- 支持件（被搬测试的 import 闭包）：`src/{core,planner,validator,executor,tasks}/`，原因见 BLOCKED.md B-2。
- 改了 import 的文件：`dxf.py`(5 处 `src.parser._chain`)、`validator`(1 处)、`driver_plan.py`(1 处 `...exporter.stf`)、`tasks/`(6 处 `src.workbench`)。算法逻辑一处未动。

### 任务 3 —— DialuxAdapter 接进平台契约 ✅

- `src/optiflow/adapters/dialux/adapter.py`：`capabilities / submit / status / result / cancel` 五件套。
- `submit` 异步：立刻返回 `job_id`，导出在后台 daemon 线程；实测 submit 耗时 **0.0 ms**。
- `result` 返回 Metric 列表（rooms / fixtures / area / stf_bytes）+ `artifacts={"stf": <路径>}`。
- `capabilities().limits` 三条，含要求的①②：灯具段被忽略→落灯要 UI 通道；本步不驱动真机、只产 STF。
  （2026-10-05 更正：「灯具段被忽略」表述与实测不符——导入后灯段以占位符完整保留（36/36），可在 UI 批量换真灯并参与计算；limits 文案已同步更正。）
- 新增 `tests/test_dialux_adapter.py`（12 条）：证明 dispatcher 按 capabilities 选中它、异步契约、产物可校验、无效任务落 FAILED 而非抛异常。

### 金标准核对（任务 2 验收二）⚠️ 部分不可达成，已如实报告

- **A. 迁移等价性**：源仓 `stf.py` vs 搬过来的 `stf.py` 对同一 IR 生成 → `cmp` **完全一致**。这是「搬得准」的硬证据。
- **B1. 黄金文件逐字节 cmp**：**不一致**（char 561, line 36）。原因：黄金文件是 MVP3 之前的旧灯具格式，详见 BLOCKED.md B-1。**没有改黄金文件，也没有改断言。**
- **B2. 黄金文件同语义比对**：非灯具行 38 行逐行一致；灯具 28 盏 XY+型号逐盏一致；差异只在 Z（旧格式写死 0）与行格式。
- 脚本：`scripts/verify_stf_parity.py`（`--break-x` 为反向验证开关）。

### 反向验证 ✅

`python scripts/verify_stf_parity.py --break-x` → B2 **FAIL / 退出码 1**（首个差异行是 X 坐标的 1e-3 米级差异）；
还原后 → **PASS / 退出码 0**。

### 端到端 ✅

`cd /d/dev/OptiFlow && PYTHONPATH=src python -m optiflow.demo` 跑通 P0（假适配器）与 P1（DialuxAdapter）两段，
P1 产出 `build/dialux/<job_id>.stf`，内容为完整合法 STF（`[VERSION]`/`[PROJECT]`/`[ROOM.R1]` + 房间闭合 + 灯具段）。

### 最终门禁 ✅

- `cd /d/dev/OptiFlow && python -m pytest -q` → **173 passed, 0 failed, 0 skipped**（基线 11 + 搬运 150 + 新适配器 12）。
- `cd /d/dev/dialux-compiler && python -m pytest -q` → **249 passed**（该仓只读；唯一被写的是 gitignore 掉的 `build/demo_room.stf`，由跑基线时的测试自身产生）。
- dialux-compiler `git status --porcelain` **非空，但为开工前既有脏状态**（时间戳 09-08~09-10，本会话 09-13）→ BLOCKED.md B-4（已停下未处理）。

## 过程中做的判断（记录为什么，供复核）

1. **没把闭包模块放进 `src/optiflow/`**：被搬测试有一条不可改的断言把驱动脚本路径钉死在 `<repo>/src/executor/uia`，
   加上 `resource_path()` 的三级上溯，物理上无法塞进 `src/optiflow/...`。选择「搬得准」（零改动）+ 那份断言绿，
   而不是「路径好看」+ 18 条测试红。已写 BLOCKED.md B-2 待裁决。
2. **为让仓库卫生测试变绿而补了自己的文档**（AGENTS.md 一节 + docs/agent-spec.md）：
   测试断言的是 OptiFlow 仓自身的不变量，补文档是让它成立，不是放宽断言。已写 B-3 待确认事实。
3. **`_run_cli` 里的模块路径字符串改了**：那是包名引用不是断言；不改则 8 条 CLI 用例无法运行。已写 B-5。
4. **`build/` 基准产物是从源仓复制来的**：OptiFlow 的 `.gitignore` 忽略 `build/`，干净 clone 会少跑 2 条 skipif 用例。
   按「不许改验收脚本/断言」没动 `.gitignore` 也没改 skipif 路径。已写 B-7。
5. **没有新增依赖声明**：搬来的代码实际需要 ezdxf 与 jsonschema（本机已装），按「不新增依赖」只记不改 → B-6。

## 交付物清单

| 路径 | 说明 |
|---|---|
| `src/optiflow/workbench/` | 通用自动化工作台（7 文件，零改动搬运） |
| `src/optiflow/adapters/dialux/` | DXF 解析 + STF 导出 + `DialuxAdapter` |
| `src/{core,planner,validator,executor,tasks}/` | 被搬测试的 import 闭包（保持原包路径，见 B-2） |
| `tests/` | 8 个被搬测试文件 + `test_dialux_adapter.py` + `conftest.py` + `fixtures/` |
| `spec/ir.schema.json` | validator 的 schema（原样复制） |
| `build/room_layout.json`、`build/test_room_1.stf` | 两条 skipif 用例的基准产物（原样复制） |
| `scripts/migrate_from_dialux_compiler.py` | 一次性搬运脚本（`copy` / `verify` 两种模式，可复现） |
| `scripts/verify_stf_parity.py` + `scripts/run_source_exporter.py` | 金标准核对与反向验证 |
| `PROGRESS.md` / `BLOCKED.md` | 本文件 / 待裁决清单 |
| `AGENTS.md` / `docs/agent-spec.md` | 协作体系章节（为卫生测试补齐，见 B-3） |

## 未做（按任务书默认）

- DIALux 真机 UI 与落灯：拆到下一步（本步 `.evo` 文件不产出）。
- `tests/test_real_dxf.py`（13 条）：依赖 ODA File Converter，按默认不搬。

---

# 验收后追加（用户已裁决 BLOCKED.md 八条）

验收结论：**通过**。用户独立复跑暗卷确认了 ①迁移等价性成立 ②黄金文件确为过期产物
③8 个搬运测试的差异全是 import 路径与 2 处 docstring，断言零改动 ④workbench 7 文件逐字节一致。

## B-1 黄金文件 → 判卷标准改为「迁移等价性」+ 本地冻结基准 ✅

- 黄金文件 `build/mvp3_lums.stf` **不重做、不删**，降级为历史档。
- 新增冻结基准：`tests/fixtures/mvp3_ir.json` + `tests/fixtures/mvp3_lums_baseline.stf`
  （源仓导出器输出逐字节固化；出处 HEAD `2ee320ee`、stf.py blob `9857ee16…`、最后改动 `e06d9dc`）。
- 新增守护测试 `tests/test_stf_golden_baseline.py`（6 条）：搬进来的导出器必须逐字节复现基准，
  另带两条反向验证（扰动房间坐标 / 扰动灯具挂高都必须被发现）+ 一条「基准自包含、不读冻结仓」检查。
- 新增 `scripts/freeze_stf_baseline.py`：默认 dry-run，`--write` 才是刻意换基准。
- 为什么必须这么做：A 检查如果每次都去读源仓，源仓一动就漂移，等价性就失去意义。

## B-2 布局取舍 ✅ 接受（带待办）

用户接受「搬得准 + 18 条不许改的断言优先」。**待办（P2）**：

> `optiflow/adapters/dialux/adapter.py` 目前 `from src.planner.core import point_in_polygon`，
> 属跨包耦合。P2 阶段把用到的函数 vendor 进 `optiflow` 后再断链。
> （相关：B-5 的双 pythonpath 也是这次耦合的产物，一并清理。）

## B-3 文档修正 ✅

- `AGENTS.md`：定位从「P0 骨架期」改为「P0 完成、P1 文件层已通」；
  「当前状态与下一步」改为 P1 文件层完成 + 下一步真机 UI/P2；协作体系一节去掉源仓沿革。
- `docs/agent-spec.md`：沿革表改为**本仓真实情况**（P0 AI 助手执笔 → P1 起 DSH 主力），
  并明写「OptiFlow 没有 Hermes / claude 子员工 / cc-switch 那段历史，勿套用」。
- `deepseek-v4-flash` 保留（用户确认对 OptiFlow 成立）。

## B-4 冻结仓脏文件 ✅ 不动（用户自行处置）

判断正确：时间戳 09-09~09-10 早于本会话，是用户的在途改动。

## B-5 三处非 import 改动 ✅ 允许（带待办）

`_run_cli` 包名、`tests/conftest.py`、`pyproject` 的 `pythonpath` 属搬运必改。

> **待办**：`pythonpath = ["src", "."]` 中同时挂 `"src"` 与 `"."` 是**临时状态**，
> 待 B-2 的跨包耦合解开后收敛回单一入口。

## B-6 依赖补齐 ✅ 已补 + 干净 venv 验证通过

- `pyproject.toml` 依赖：`pydantic` + `ezdxf>=1.3`（`dxf.py`）+ `jsonschema>=4.0`（`validator`）。
- 可选 extra：`dev = [pytest]`、`xlsx = [pandas, openpyxl]`。
- `pandas/openpyxl` 的评估结论：`xlsx.py` 里是**惰性 import**（函数体内），且当前平台与测试都不走它，
  故列为可选 extra 而不是硬依赖。
- `requirements.txt` 同步补齐，并**改为纯 ASCII**——pip 按系统 locale（本机 GBK）解码该文件，
  中文注释会直接 `UnicodeDecodeError` 中断安装（踩过一次，已修）。

干净 venv 验证（`python -m venv .venv` → `pip install -r requirements.txt` → 跑 pytest）：

```
Python 3.11.15 / pydantic 2.13.5 / ezdxf 1.4.4 / jsonschema 4.26.0 / pytest 9.1.1（无 pywin32）
179 passed in 15.20s
```

即干净环境能起，且不依赖 anaconda 里预装的东西（pydantic 与 pytest 版本都与本机 anaconda 不同，仍然全绿）。

## B-7 干净 clone 会少跑 2 条 ✅ 接受，已实测并注明

OptiFlow 的 `.gitignore` 含 `build/`，而 `test_exporter_stf.py` 有两条 `skipif` 依赖 `build/` 产物。
本机 0 skipped 是靠把源仓的 `build/room_layout.json`、`build/test_room_1.stf` 复制过来达成的；
**干净 clone 会少跑这两条**（不是失败，是 skip）。已在 README「已知边界」注明。

**实测验证**（`git clone . build/_clone_check` 后跑全量）：

```
177 passed, 2 skipped in 19.46s
SKIPPED [1] tests\test_exporter_stf.py:326: build/ 是运行产物（gitignore），S0 参考文件缺失时跳过
SKIPPED [1] tests\test_exporter_stf.py:599: build/room_layout.json 是运行产物（gitignore），未生成时跳过
```

数字与预期完全对上：179 − 2 = 177。

### 顺带排掉一个陷阱：CRLF 会让判卷基准在干净 clone 里假红

本机 `core.autocrlf=true`，Git 检出时把文本改成 CRLF，而冻结基准是**按字节比对**的：

```
（未加防护时）clone 出来的基准: 含 CRLF = True / 字节数 = 2506   ← 生成物是 2384
→ tests/test_stf_golden_baseline.py 假红
```

已加 `.gitattributes` 强制 `*.stf`（以及 `*.ps1`）保持 LF，重新 clone 复测：

```
clone 出来的基准: 含 CRLF = False / 字节数 = 2384
基准守护: 6 passed
```

这条不排掉，判卷标准只在本机成立——正是「坏了没人会知道」的那类问题。

## B-8 确认不搬 ✅

`tests/test_real_dxf.py`（13 条，依赖 ODA File Converter）与真机 UI/落灯，本步不搬。

## 验收后门禁复测

| 环境 | 结果 |
|---|---|
| anaconda（本机标准环境） | 见下方最终复跑 |
| 干净 venv（只装 requirements.txt） | **179 passed** |

测试构成：基线 11 + 搬运 150 + 适配器 12 + 冻结基准守护 6 = **179**。

---

# P2 算法层（本轮）

目标：把「先算后验」的「算」那一半做出来——毫秒级出布灯方案，DIALux 只做校核。

## 交付

| 文件 | 内容 |
|---|---|
| `src/optiflow/algo/lumen.py` | 房间指数 K、利用系数查表、维护系数、灯具数 N、平均照度 |
| `src/optiflow/algo/layout.py` | 居中网格排布、距高比约束、边缘间距因子 |
| `src/optiflow/algo/uniformity.py` | 逐点照度法、采样网格、均匀度 U0/U1、凹房间遮挡 |
| `src/optiflow/algo/engine.py` | 串起来：TaskSpec → 方案 + 合规判定 + 二维求解器 |
| `src/optiflow/adapters/lumen.py` | LumenPlannerAdapter + apply_plan（接进平台契约） |
| `tests/test_algo_core.py` (40) | 光通量法 / 配光 / 逐点法 / 采样 / 布灯的数学与物理 |
| `tests/test_algo_engine.py` (16) | 引擎：合规判定、求解器行为、诚实性、边界 |
| `tests/test_algo_adapter.py` (8) | 适配器契约 + 「先算后验」端到端流水线 |

测试：179 → **243 条**，0 failed / 0 skipped。

## 实测出来的建模结论（都写进了代码注释与测试，防止被「优化」掉）

1. **房间角落暗，是边缘灯离墙太远，不是灯不够多。**
   31 盏灯时把 edge_factor 从 0.50 收到 0.25，U0 从 0.52 升到 0.69；
   而把灯数从 31 加到 100 只挪到 0.52。所以求解器的主旋钮是边缘间距，不是灯数。
   （这条是踩坑得来：中途曾把短排改成「列对齐居中」，看着更规整，
   结果最后两角没灯，U0 从 0.52 塌到 0.23，已回退并写进注释。）

2. **逐点法不含墙面二次反射 → U0 是保守下界。**
   二次反射先照亮最暗的角落，所以真实 U0 只会更高。拿逐点法 U0 当设计门槛偏严。
   交叉校验因此必须对齐口径：光通量法含反射，逐点法只算直射，
   直接相减是苹果比橘子（第一版就犯了这个错，被自己的校验抓出来）。

3. **光通量法算不出均匀度。**它结构上只给平均值。
   拿平均照度冒充均匀度是照明计算里最常见的假承诺，所以 U0 是一等公民。

4. **利用系数表是工程默认值，不是厂商数据。**
   内置 4 组反射率锚点，用「最近两锚点距离反比加权」插值——
   双线性会因为表不是完整网格直接报错；全锚点反距离加权又会被远处高反射率锚点拽偏
   （实测把 (0.6,0.4) 算到 0.703，比两个近邻都高）。

## 顺手修掉的真实缺陷

- `ir.Point` 的 `x/y` 没有默认值，而 `Fixture.position` 声明的是 `Field(default_factory=Point)`——
  这个默认工厂每次都会抛 ValidationError，即「不传 position 就建不出 Fixture」本身是坏的。
  已给 x/y 补默认值 0.0（对 `test_ir.py` 的字段名守卫无影响）。

## 性能

第一版单房间要 600s+（超时）：`_segment_blocked` 对每一对「采样点 x 灯具」做上百次多边形测试。
两处修正后单房间 **约 0.4 s**：

- 朗伯配光用闭式解 `E=(Φ/π)·h²/(r²+h²)²`，省掉逐点反三角函数；
- 墙体遮挡只对**凹多边形**房间才算（凸房间里任意两点连线必在室内）。

## 暴露出来的缺口（下一步）

**编排层不存在。** `Dispatcher.dispatch` 只按 kind 取注册表里第一个匹配的适配器；
而 `LumenPlannerAdapter` 与 `DialuxAdapter` 都声明 `kind="layout"`。
demo 里第二步差点被路由回算法层（`KeyError: 'stf'` 抓出来的）。
现在按阶段分开跑，并在 README「已知边界」写明——按能力+上下文自动编排是编排层的活。

---

# 编排层（本轮，第 2 轮）

补上架构图里空着的那一层：**任务分解 → 选能力 → 拼流水线 → 校验结果**。

## 为什么需要它（不是重写 Dispatcher）

`Dispatcher.dispatch` 只按 kind 取注册表里**第一个**匹配的适配器。
而 lumen（算法通道）与 dialux（文件通道）都声明 `kind="layout"` ——
换一下注册顺序就换一个通道，跑出来的东西跟着变。
上一轮实测踩过：demo 第二步被路由回算法层，结果里没有 stf 产物，`KeyError: 'stf'`。

## 交付

| 文件 | 内容 |
|---|---|
| `src/optiflow/adapter.py` | CapabilityDecl 加 `tags`（kind 答「哪类任务」，tags 答「哪条通道」，两者正交） |
| `src/optiflow/orchestrator.py` | Step / Pipeline / Orchestrator / PipelineRun / 三种校验器 |
| `src/optiflow/pipelines.py` | 照明方案流水线：算法层出方案 → 文件层落 STF |
| `tests/test_orchestrator.py` (17) | 选能力消歧、流水线、校验器、边界 |

测试：243 → **260 条**，0 failed / 0 skipped。

## 关键设计决定

1. **选能力有歧义就报错，不猜。** `Orchestrator.resolve` 在候选 > 1 时抛 `AmbiguousAdapter`，
   并列出候选名与「请写 requires= 或 adapter_name=」。
   配套有一条反证测试：同一个注册表交给 `Registry.find` 会悄悄挑出不同的适配器，
   证明「交给 Dispatcher 就行」是错的。
2. **通道用 tags 区分，不复用 kind 取巧。** 两个适配器处理同一类任务、只是深度不同，
   这是真实语义；把 kind 改个名字绕开消歧问题，等于把问题藏起来。
3. **步骤之间靠产物文件衔接。** export 步读 plan 步写出的 plan.json 重建 Fixture，
   不走内存对象——文件可以被人打开核对，也能单独重跑。
4. **每步都归档成一个项目版本。** 只记最后一步，中间过程（谁算的方案、参数是什么）就丢了。

## 校验器（校验结果那一环）

- `metric_targets_met()`：结果里带 target 的 Metric 逐条对账（target 视为下限）。
  文档里写明：将来有上限类指标（如 UGR<=19）要单独写一条，不要塞进来——方向含糊比多写两行危险。
- `artifact_exists(key, min_bytes)`：产物键存在、路径存在、大小达标。
- `cross_step_metric_matches(...)`：跨步骤对账，「算出来 31 盏导出去 28 盏」这类掉队靠它抓。

## 行为验证（demo 第 3~5 段，实测输出）

```
[1/5] plan     kind=layout requires=['algorithm']  →  lumen   (tags=['algorithm'])
      export   kind=layout requires=['file']       →  dialux  (tags=['file'])
[4/5] [PASS] plan:illuminance_avg>=目标：508.8lx 目标 500.0lx
      [PASS] plan:uniformity_u0>=目标：0.693 目标 0.6
      [PASS] plan:产物 plan 存在：build\plans\lumen-e1146c89.json（11010 字节）
      [PASS] export:产物 stf 存在：build\dialux\dialux-0f5f2a69.stf（2339 字节）
      [PASS] 跨步骤:plan.fixture_count==export.fixtures：31.0 vs 31.0
[5/5] 版本 v1（.../ plan）  版本 v2（.../ export）
      整体结论：completed=True ok=True
```

## 剩余缺口

- 对外接口（HTTP + MCP）未做 —— 下一步。
- P5 极简壳未做。
- P1② 真机 UI 与落灯仍待用户授权；P3 Creo / P4 Zemax 需装机。

---

# 对外接口（本轮，第 3 轮）

把平台暴露成 AI agent 能调的工具集：HTTP + MCP。

## 交付

| 文件 | 内容 |
|---|---|
| `src/optiflow/service.py` | 服务门面：HTTP 与 MCP 共用的同一套语义入口 |
| `src/optiflow/api.py` | HTTP 接口（stdlib http.server） |
| `src/optiflow/mcp.py` | MCP server（stdio + tools 子集） |
| `src/optiflow/orchestrator.py` | 加 `on_step` 进度回调、`should_cancel`、`failure_detail` |
| `tests/test_api.py` (40) | 服务 / HTTP（真 socket 真请求）/ MCP（纯函数 + StringIO 走真循环） |

测试：260 → **300 条**，0 failed / 0 skipped。

## 关键设计决定

1. **传输与语义分开。** 所有语义在 `service.py`；`api.py`/`mcp.py` 只翻译协议。
   否则同一个目标用 HTTP 问和用 MCP 问会慢慢漂开，而 agent 两条路都会走。
2. **HTTP 用 stdlib http.server，不引 FastAPI**（计划文档写的是 FastAPI）。
   理由：对外契约就是那 8 条路由，与实现无关；零新增依赖，干净 venv 能直接起；
   测试可以在进程内用真 socket 打真请求。要挂鉴权/限流/OpenAPI 时再换，
   换的只是 `api.py`，`service.py` 一行不动。
3. **默认拒绑非回环地址。** 这个接口没有鉴权，绑出去等于把「按路径读写文件」
   开放给整个网段。要暴露必须显式 `allow_remote=True`，并在文档里说清风险。
4. **MCP 实现范围写清楚**：stdio 传输 + `initialize`/`ping`/`tools/list`/`tools/call`，
   是 tools 子集，不等于覆盖全部规范（没有 resources/prompts/sampling/roots）。
5. **工具名错了 / 参数错了返回 `isError` 的 content，不是 JSON-RPC 错误**——
   这样 agent 能读到「有哪些工具可用」并自己纠正，而不是只拿到一个错误码。

## 过程中修掉的三个真问题

1. **步骤失败的原因被吞掉。** `dispatcher.wait` 抛的 RuntimeError 在 `orchestrator` 里
   只被用成「停在 plan」，具体原因丢了。已加 `PipelineRun.failure_detail` 并透传到
   服务层 message。只记「停在哪」等于让人重跑一遍去猜。
2. **请求体超限时客户端读不到 400。** 直接在超限处回 400 会让还在发送的客户端拿到
   `ConnectionAborted`——护栏变成了「坏了没人知道」。已改为先读掉（上限 1 MB）再回 400。
3. **MCP 工具描述写成了元组。** 连续字符串字面量之间误用了逗号，序列化后 `description`
   会变成 JSON 数组，违反 MCP schema，而客户端只会说「工具有问题」。
   已修，并把测试加强为 `isinstance(description, str)` + 整个清单过一遍 JSON。

另修：`api.py` / `mcp.py` docstring 里的 `D:\dev\...` 是无效转义，会触发
DeprecationWarning（将来是错误）。已改为 raw docstring，并写了个扫描确认全仓无残留。

## 行为验证（实测输出）

```
GET /health      -> 200 {'status': 'ok'}
GET /capabilities-> ['lumen', 'dialux']
POST /run        -> 200 ok=True [('plan','lumen'), ('export','dialux')]

$ python -m optiflow.mcp --self-check
optiflow_plan_lighting: 给一个矩形房间和目标平均照度，出布灯方案并落成 DIALux 可导入的 STF。...

$ printf ... | python -m optiflow.mcp
  initialize -> {'name': 'optiflow', 'version': '0.0.1'} 2024-11-05
  tools/list -> 7 个工具

$ create_server(host='0.0.0.0')
  已拒绝: 拒绝绑定到非回环地址 '0.0.0.0'：本接口没有鉴权。...
```

## 剩余缺口

- **P5 极简壳未做** —— 下一步（「非技术用户能独立完成一次任务」）。
- P1② 真机 UI 与落灯仍待用户授权；P3 Creo / P4 Zemax 需装机。
- B-2 的跨包耦合、双 pythonpath 临时状态仍待 P2 之后收敛（见上文 B-2/B-5）。

---

# P5 极简壳（本轮，第 4 轮）

验收标准是「**非技术用户能独立完成一次任务**」。

## 交付

| 文件 | 内容 |
|---|---|
| `src/optiflow/web/index.html` | 单页「说目标 → 出结果」（自包含，不引 CDN） |
| `src/optiflow/shell.py` | 页面加载器 + 产物 Content-Type 映射 |
| `src/optiflow/api.py` | 根路径 `/` 吐界面；新增 `/endpoints` 与 `/jobs/{id}/artifacts/{key}` |
| `src/optiflow/geometry.py` | 共享几何原语（射线法 / 鞋带面积）—— 见下方「还掉 B-2 的一截」 |
| `src/optiflow/service.py` | 加 `artifact_path(job_id, key)` |
| `scripts/shell_journey.py` | 用 HTTP 把用户旅程跑一遍（可执行证据） |
| `tests/test_api.py` (+10) | 壳的页面、产物下载、自包含守卫 |

测试：300 → **311 条**，0 failed / 0 skipped。

## 设计决定

1. **根路径给人、`/endpoints` 给机器。** 非技术用户打开 `http://127.0.0.1:8765/` 就该看到界面，
   而不是一串 JSON 端点清单。
2. **产物下载只放行该任务自己登记的产物**（`service.artifact_path`）。
   调用方给不了任意路径，路径穿越在结构上就不可能发生——不需要再写一层路径校验。
3. **界面必须同时显示「这份方案的适用边界」**（assumptions + warnings）。
   只给漂亮数字不给边界的界面，比没有界面更危险，用户会拿它当结论用。
4. **壳自包含**：无构建步骤、无框架、不引 CDN，离线能开。有测试钉住「不含 http(s):// 外链」。
5. 页面调的 `fetch` 目标就是对外契约那几条路由，不是另开的后门（有测试）。

## 还掉 B-2 的一截（被真实路径炸出来的）

写 `scripts/shell_journey.py` 时，导出步在**非测试环境**下炸了：

```
ModuleNotFoundError: No module named 'src'
  File ".../adapters/dialux/adapter.py", line 36, in task_to_dialux_ir
    from src.planner.core import point_in_polygon
```

这正是 BLOCKED.md B-2 记的跨包耦合：只在「仓库根恰好在 sys.path 上」时成立，
测试里靠 conftest 兜住了，一离开 pytest 就露馅。

处理：把射线法提成平台自己的原语 `optiflow/geometry.py`（算法层与适配器共用），
适配器不再 import `src.*`。**optiflow 主链路现在完全自包含**，
并加了一条守卫测试：子进程 + cwd=临时目录 + PYTHONPATH 只挂 `src/`，
跑通完整流水线——仓库根不可能被隐式加进 sys.path。
（测试自己先写错了：用 `'OptiFlow' not in p` 过滤 sys.path，把要留的那条也删了，已修。）

## 行为验证（实测输出）

```
$ python scripts/shell_journey.py
服务地址： http://127.0.0.1:29310
1) 打开界面      -> HTTP 200, 8168 字符, 有表单: True
2) 点「出方案」  -> ok=True, 用时 0.453s
     灯数 31 盏 / 平均照度 508.8 lx（目标 500）/ 均匀度 U0 0.693（目标 0.6）
3) 逐条校验：
     [通过] plan:illuminance_avg>=目标
     [通过] plan:uniformity_u0>=目标
     [通过] plan:产物 plan 存在
     [通过] export:产物 stf 存在
     [通过] 跨步骤:plan.fixture_count==export.fixtures
4) 下载 STF      -> 1954 字符, 首行 '[VERSION]'
     房间段 1 个, 灯具 31 盏
5) 完成：用户拿到一个 DIALux 能导入的文件。
```

静态校验（本机没浏览器，见 BLOCKED）：

```
script 块数: 1 / 提取 JS 行数: 133
  <html> 开1 闭1 OK  <head> OK  <body> OK  <main> OK  <script> OK  <style> OK  <section> 开3 闭3 OK
node --check 退出码: 0   JS 语法：通过
```

这条静态校验不是走过场：它当场抓出过一个真错（我在页面 JS 里误留了一个裸反引号，
那正是此前几轮反复踩的同一类转义坑）。

---

# 跨包耦合收敛（本轮，2026-09-25）

目标：把「optiflow 主链路依赖搬运件 `src.*`」的最后一处还掉——`--validate` 的惰性 import（BLOCKED.md B-10）。

## 交付

| 文件 | 内容 |
|---|---|
| `src/optiflow/validator/__init__.py` | 自 `src/validator` 迁入（320 行）；唯一差异：`load_schema` 改为包内寻址，不再依赖 `src.core.env` |
| `src/optiflow/spec/ir.schema.json` | schema 随包（包内寻址的目标） |
| `src/optiflow/adapters/dialux/stf.py` | `_validate_rooms` 惰性 import：`src.validator` → `optiflow.validator`（2 行：import + docstring） |
| `pyproject.toml` | package-data 加 `spec/*.json` |
| `tests/test_validator_migration.py` (6) | 等价性 ×3（mini / mvp3 / 违规 IR）+ 自包含 ×2 + schema 同步 ×1 |

测试：**315 passed, 2 skipped**（基线 309 passed + 2 skipped；净增 6 条）。

## 关键设计决定

1. **只动 validate 路径，主链路一字未动**：`write_stf` 相关代码零改动，冻结基准守护 6 条继续绿。
2. **等价性用「新旧 validator 同 IR 逐条比对」钉住**（与 workbench 全字节 cmp 同款逻辑）：三份输入（mini / mvp3 / 故意违规），输出完全一致。
3. **自包含守卫用子进程 + cwd=临时目录 + PYTHONPATH 只挂 `src/`**（与 P5 的 geometry 守卫同款）：仓库根不可能被隐式加进 sys.path。
4. **「schema 两份」写成显式同步测试**：包内 `src/optiflow/spec/` vs 仓库根 `spec/`，任何一边漂移先红；将来废弃仓库根那份时一并删。
5. **`src/validator` 保留不动**：闭包（`test_validator` / `planner`）仍在用，符合 B-2 的既有布局。

## 行为验证（实测）

```
$ python -m pytest -q
315 passed, 2 skipped in 30.39s    （基线 309+2，新增 6 条）

$ grep -rn "from src\.|import src\." src/optiflow/
（空 —— optiflow 主链路完全自包含）
```

## 剩余缺口

- **B-9 真浏览器验证**：未做。Mac 侧本机已有 `ms-playwright/chromium-1228` 缓存（无需再下载 150MB），下一轮候选。
- B-5 双 pythonpath 保留（executor/uia 闭包被断言钉死，见 README「已知边界」3）。

---

# 真浏览器端到端（本轮，2026-09-25）

目标：把 B-9「极简壳没在真浏览器里验证过」补上——P5 验收标准「非技术用户能独立完成一次任务」的最硬证据。

## 交付

| 文件 | 内容 |
|---|---|
| `tests/test_shell_browser.py` (2) | 全旅程（表单→出方案→达标→校验全过→下载 STF→截图）+ 输入生效（改 600 → 结果含 600） |

测试：315 → **317 条**，0 failed（另 2 条既有 skip）。

## 环境（Mac 侧）

- playwright==1.61.0（Python）——版本与缓存 chromium-1228 对齐（`playwright install --dry-run` 验证；1.62+ 要 1234+）
- 浏览器寻址：`channel="chromium"`（走完整版 build；缓存无 headless shell，默认 launch 会找不到）
- 缺库 / 缺浏览器时整模块 skip（干净环境不炸）

## 行为验证（实测）

```
$ python -m pytest tests/test_shell_browser.py -v
2 passed in 3.47s

全量：317 passed, 2 skipped

截图：build/browser_check/shell_result.png（full_page）
  → 界面完整渲染：表单 + 「方案达标」+ 指标表（31 盏 / 508.8 lx / U0 0.693）
    + 5 条校验全绿 + 两个下载链接 + 适用边界说明
```

## 关键设计决定

1. **两条测试各司其职**：全旅程（渲染 + 下载）+ 输入生效（600 出现在结果，证明表单→计算链没断）。
2. **launch 策略双保险**：先 `channel="chromium"`（只有完整版的环境）→ 退回默认（标准安装的环境）→ 都失败才 skip（带完整错误信息）。
3. **截图落 build/**（gitignore 产物目录），不入库。

## 剩余缺口

- B-9 关闭。P1② 真机 / P3 / P4 仍待（Windows / 装机）。

---

# 照明结果包抽取器（本轮，2026-10-07）

目标：schema v1 的机读通道落地——DIALux 报表 PDF → 结果包。开发样本由 Windows 侧回灌
（真机 2 页报表，41.7 MB）；**样本文件与实测数值按项目红线不入库**。

## 交付

| 文件 | 内容 |
|---|---|
| `src/optiflow/adapters/dialux/report.py` | 抽取器：坐标感知（span → 视觉行 → 列 → 单元格）与文本层正则两条入口 |
| `tests/test_dialux_report.py` (9) | 坐标路 4 + 文本路 4 + 真机端到端 1（`DIALUX_REPORT_PDF` 存在才跑，缺席 skip） |
| `tests/fixtures/dialux_report_sample_text.txt` | 合成文本层：结构与真机同形，数值全虚构 |

## 关键设计决定

1. **表格必须走坐标**：真机文本层里灯具列表的列序是乱的（单元格按字形绘制顺序落进文本流），
   纯正则无法还原列归属。改走 `get_text("dict")` 的 span：按基线中心聚行（容差 3.5pt）
   → 按表头 span 的 x 起点切列 → 列宽折断的续行（首列为空）按列并回。
2. **行聚类用基线中心**而非 y0：上下标与不同字号（`U`+`o`、`R`+`UG`）的 y0 差 4pt 级，
   按 y0 会拆行。
3. **抽不到就记缺项**：纯文本入口没有坐标 → 灯具列表记 `luminaires.table`，不猜列序。
4. **红线**：真机样本、实测数值、品牌型号一律不进仓库；fixture 与真机同形、值全虚构。

## 行为验证（实测）

```
$ DIALUX_REPORT_PDF=<真机样本> python -m pytest tests/test_dialux_report.py -v
9 passed in 1.10s

$ python -m pytest -q
325 passed, 3 skipped in 36.31s    （基线 317+3，新增 8 条）
```

真机样本上一次跑通 `_missing` 为空的全部字段（照度 / U0 / LPD / 能耗 / 维护系数 / 反射比 /
灯具全字段），并验证了两个坐标路特有还原：折行单元格按列拼回、表头多 span 列名合并。

## 待核（随回执发 Windows）

1. **产品编号位数**：已确证。29 页版产品数据表页的编号为 9 位（8 位数字＋ 1 位），
   坐标路拼回一致；schema 文档的 8 位是笔误，已在给 Windows 的回件里说明，待 schema v1.1
   一并订正。
2. **房名乱码形态**：乱码含私用区码位，无法无损逆向；schema v1 的 rooms 不含 name，不影响。
3. Ra / CCT 需报表勾「产品数据表」页，本样本缺席，字段已留位。

## 29 页版升级（同轮，2026-10-08）

Windows 侧回执 7 建议「抽取器按 29 页版设计」，采纳。已按 29 页版真机文本层（21.9 KB）
完成文本路升级，新增：

- `luminaires[].positions`：灯具位置图页的逐灯坐标（X / Y / 安装高度 / 编号），36 条全解析
- `workplane.illuminance.emin_lx / emax_lx / uniformity_g2`：计算元件页整行序列
- `metrics_extra.ra / cct_k`：产品数据表页（限定该段内抽，避开词汇表解释文字）
- `metrics_extra.flux_total_lm / power_total_w`：灯具列表页汇总行
- `integrity.positions_count`：与 `luminaire_count` 交叉对账用

行为验证：

```
$ DIALUX_REPORT_PDF=<2 页版样本> python -m pytest tests/test_dialux_report.py -v
13 passed in 1.02s

$ python -m pytest -q
329 passed, 3 skipped in 38.72s    （基线 317+3，本轮共新增 12 条）

$ <29 页版真机 PDF 端到端>（316.7 MB，SHA-256 与交付方对账一致）
6.9s；坐标路 + 文本路全字段命中，_missing 为空，逐灯位置表全额解析，
integrity 的 positions_count 与 luminaire_count 相等
```

29 页版实跑：Emin / Emax / g2、Ra / CCT、Φ总数 / P总数、逐灯坐标全部命中；件数由
坐标路表格给出。两版样本均无缺项。

## 剩余缺口

- 坐标路的多页/多型号边界待 29 页版 PDF 本体验证（多页切分、位置表跨页）。
- 多房间样本仍在合成数据上覆盖。

---

# DXF 导入（评价点①，本轮 2026-10-08）

目标：导入自动化 ＋ 允许确认 ＋ 完整性检查。壳从「手输尺寸」升级为
「传图纸 → 解析 → 检查 → 确认带入」。

## 交付

| 文件 | 内容 |
|---|---|
| `src/optiflow/service.py` | `PlatformService.import_dxf()`：DXF(base64) → IR ＋ 检查清单 ＋ 房间概要 |
| `src/optiflow/api.py` | `POST /import-dxf`；endpoints 清单同步 |
| `src/optiflow/web/index.html` | 壳新增「1. 导入 CAD 图纸」：选文件 → 解析 → 完整性检查 → 确认带入参数 |
| `tests/test_dxf_import.py` (6) | 真图纸 / 空内容 / 坏 base64 / 非 DXF / 无房间 / HTTP 400 |
| `tests/test_shell_browser.py` (+1) | 真浏览器：选 DXF → 解析 → 检查全过 → 确认后参数带入 |

## 关键设计决定

1. **内容走 base64 进 JSON**：DXF 可能是 GBK 等本地编码，前端不做文本解码（避免乱码），
   原字节直传，后端落临时文件交 ezdxf。
2. **复用 dialux/dxf.py 全链**（房间去重 / 环平滑 / 家具归属都在里面），service 只做
   「IO ＋ 检查清单 ＋ 概要」。
3. **完整性检查四项**：读到房间 / 环闭合 / 环上短边 / 单位识别；一条不过 `ok=false`，
   界面显红，不假装成功。
4. **解析配置可选**：真实图纸常需指定单位与图层，壳里留 JSON 配置入口（留空走默认）。
5. **确认闸门**：先给人看检查清单 ＋ 房间表，点「确认导入」才带入参数。

## 行为验证（实测）

```
$ python -m pytest tests/test_dxf_import.py -q
6 passed

$ python -m pytest tests/test_shell_browser.py -v
3 passed（含新用例 test_shell_dxf_import_journey）

$ python -m pytest -q
335 passed, 3 skipped in 39.87s
```

真浏览器截图：`build/browser_check/shell_dxf_import.png`（解析 1 房间 / 26 家具 / 四项检查全过 /
确认后尺寸带入）。

## 剩余缺口

- 确认后的 IR 目前只用于「带入尺寸」，尚未把完整 IR（含家具）直接喂进布灯流水线。
- 解析配置入口是 JSON 文本域，未做图形化；多房间图纸的「选哪个房间」未做。

---

# STF 编码修复：默认写 GBK（真机结论回流，2026-10-08）

Windows 侧真机回归结论：「GBK 写出即可解乱码（UTF-8 乱码坐实；GBK 中文/ASCII 全干净）。
修复方向＝STF 写 GBK（比房名转 ASCII 更优）」。本条按「真机结论回流软件」执行。

## 改动

| 文件 | 内容 |
|---|---|
| `src/optiflow/adapters/dialux/stf.py` | `write_stf()` 默认 `encoding="gbk"`；CLI 加 `--encoding`（默认 gbk） |
| `tests/test_exporter_stf.py` | +1 条钉住默认编码（中文房名必须 GBK 字节落盘）；4 处读取改 gbk |
| `tests/test_dialux_adapter.py` / `test_algo_adapter.py` / `test_validator_migration.py` | 读 STF 处改 gbk |
| `tests/test_api.py` | `_request_raw` 读响应体改 `errors="replace"`（产物是 GBK，不能一炸了之） |

## 行为验证（实测）

```
$ python -m pytest -q
337 passed, 3 skipped in 37.03s

新增 test_write_stf_default_encoding_is_gbk：中文房名以 GBK 字节落盘，且不是 UTF-8 字节
```

## 说明

- GBK 表外字符会抛 `UnicodeEncodeError`（strict），不静默丢字；真出现再说怎么处理。
- STF 冻结基准（`mvp3_lums_baseline.stf`）为纯 ASCII，编码切换不影响逐字节比对。
