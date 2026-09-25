# BLOCKED — 待裁决清单（DIALux 文件层搬运 / P1 第 1 步）

> 本文件随交付提交。以下每条都附实测证据；本步按「搬得准 > 接得全 > 接得快」继续推进，
> 未停等（除 B-4 属「收不回的操作」，已按规矩停下不自行处理）。

---

## B-1 【最重要】任务书指定的「金标准」比对不可能通过：黄金文件是 MVP3 之前的过期产物

任务书原文：「用同一份 mvp3_ir.json 生成 STF，与黄金文件比对 cmp … 必须一致」。
**实测不一致，且不可能一致**——这不是搬运出错，是黄金文件本身过期。

实测：

```
$ cmp /d/dev/dialux-compiler/build/mvp3_lums.stf <新仓产物>
/d/dev/dialux-compiler/build/mvp3_lums.stf /d/dev/OptiFlow/build/golden_check/mvp3_lums_new.stf differ: char 561, line 36
退出码: 1
```

证据链：

1. 黄金文件（mtime 2026-09-05 22:45）灯具段是**旧的一行占位格式**：
   `Lum1=0.945 7.091 0 CIRCLE-r7.6-0`
2. 现版 `src/exporter/stf.py`（源仓 commit `e06d9dc` feat(MVP3)）已改成**三行格式**，
   代码注释明写旧格式「是错的」：
   `Lum1=CIRCLE-r7.6-0` / `Lum1.Pos=0.945 7.091 2.8` / `Lum1.Rot=0 0 0`
3. 旧格式把灯具 Z **写死成 0**；新格式写真实挂高 2.8。
4. 因此「同一批次产物」这个前提不成立：黄金文件早于 MVP3 的灯具格式修复。

本步**没有**改断言、没有改黄金文件（都在冻结仓/判卷标准里，一个字没动）。
替代验证（`scripts/verify_stf_parity.py`，正查绿、反向验证红→绿）：

- A. 迁移等价性：同一 IR 分别用**源仓 stf.py** 与**搬过来的 stf.py** 生成 → `cmp` **完全一致（退出码 0）**
  —— 直接证明搬运没改变任何行为。
- B2. 与黄金文件同语义比对：非灯具行 38 行**逐行一致**；灯具 28 盏的 **XY 坐标 + 型号逐盏一致**；
  差异只在灯具 Z（黄金写死 0，新仓 2.8）与行格式。

**待裁决**：是否按现版格式重新生成黄金文件、冻结为 OptiFlow 的新基准？（那会改判卷标准，本步不敢擅动）

---

## B-2 目录布局偏离任务书：部分模块没放进 `src/optiflow/`，而是留在 `src/` 顶层

任务书指定 `src/optiflow/workbench/` 与 `src/optiflow/adapters/dialux/`。**这两个都照做了**；
但被搬测试的 import 闭包（core / planner / validator / executor / tasks）留在了 `src/` 顶层原包路径。

原因（硬约束，不是偷懒）：

1. `test_progress_contract.py` 里有一条**不许改的断言**：
   `assert DRIVER_PS1.parent == Path(__file__).resolve().parent.parent / "src" / "executor" / "uia"`
   它把驱动脚本位置钉死在 `<repo>/src/executor/uia`。测试断言属于「判卷标准碰都不许碰」。
2. `src/core/env.py` 的 `resource_path()` 用 `Path(__file__).parent.parent.parent` 上溯仓库根，
   要让它算出 `<repo>/src/executor/uia/*.ps1`，env.py 必须位于 `src/<单个包>/env.py`。
   放在 `src/optiflow/...` 下的任何深度都会算错，且这属于「改算法逻辑」，被禁止。
3. 逐层推演后：要让那 18 条测试在「断言不动」的前提下全绿，`src/core`、`src/executor` 必须在顶层；
   为保持一致，同闭包的 `planner/validator/tasks` 也一并保持在原包路径（零改动，搬得最准）。

代价：OptiFlow 里出现 `optiflow.*` 与 `src.*` 两个顶层命名空间，`src/` 顶层多出 5 个包。
**待裁决**：接受这个布局，还是宁可让那 18 条测试红掉也要挪进 `optiflow/`？（后者与本步验收「0 failed」冲突）

---

## B-3 为让 test_project_hygiene（4 条）变绿，动了 OptiFlow 自己的文档

该测试是**仓库卫生守卫**，断言 OptiFlow 仓自己要满足三条不变量：

1. `prompts/zcode.md` 等 5 个文件不存在 → OptiFlow 本来就没有 `prompts/`，天然通过；
2. `AGENTS.md` 不含「Plan 模式」且含 `DSH` 与 `deepseek-v4-flash` → **原本缺最后一项**；
3. `docs/agent-spec.md` 含 `AGENTS.md` 与「沿革」→ **该文件原本不存在**。

本步的动作（都在 OptiFlow 白名单内，没碰测试断言）：

- `AGENTS.md` 追加「协作体系（现役）」一节，写明 DSH 桌面版 + `deepseek-v4-flash`；
- 新建 `docs/agent-spec.md`（权限边界表 + 体系沿革，指向 AGENTS.md 作为唯一真身）。

**待裁决**：「模型是 deepseek-v4-flash」这条事实取自冻结仓 `AGENTS.md` 的 2026-09-05 定案
（配置在 `C:\Users\cjh\.dsh\settings.yaml` 的 `agent-default-model`）。请确认对 OptiFlow 也成立；
若不成立，应改的是这两份文档，而不是测试断言。

---

## B-4 完成条件「dialux-compiler `git status --porcelain` 为空」**当前不成立**（非本步造成，已停下未处理）

实测输出（本会话 2026-09-13 00:36）：

```
$ cd /d/dev/dialux-compiler && git status --porcelain
 M KANBAN.md
?? "208会议室_4m_含家具_灯具优化_照度计算.evo"
?? docs/plan-from-scratch-modeling.md
?? "对话聊天日志_20260908.txt"
```

时间戳证明是**开工前就脏**：KANBAN.md 2026-09-10 18:00、plan-from-scratch-modeling.md 2026-09-10 17:59、
对话聊天日志 2026-09-09 08:45、.evo 2026-09-08 22:51；本会话开始于 2026-09-13 00:30 之后。

本步对该仓**没有做过任何源码写操作**（只读文件 + 按任务书跑 pytest），249 条复测仍全绿。
唯一被写入的是 **gitignore 掉的运行时产物**，且是任务书强制要求的「跑基线」本身造成的：

```
$ git check-ignore -v build/demo_room.stf
.gitignore:29:build/      build/demo_room.stf
```

`build/demo_room.stf` 由 `tests/test_progress_contract.py::test_kernel_plan_driven_progress_contract` 经
`UiaDriver(ir, import_fn=fake_import)` 以默认 `stf_path="build/demo_room.stf"` 写出（OptiFlow 侧跑同一测试也会写自己的 `build/`）。
因为它被 gitignore，所以**不影响上面的 `git status --porcelain` 结果**。

清理他仓未提交改动属「收不回的操作」，按规矩停下、写进本清单、继续做别的。

**待裁决**：这 4 项是架构师自己的在途改动，还是遗留脏文件？要不要我处理？（需要授权才动冻结仓）

---

## B-5 两处「非 import」的改动，需要确认是否越界

1. `tests/test_exporter_stf.py:470` 的 `_run_cli` 里，模块路径字符串
   `"src.exporter.stf"` → `"optiflow.adapters.dialux.stf"`。
   这不是 import 行，是**包名引用**；不改的话 8 条 CLI 用例全部无法运行。
2. 新增 `tests/conftest.py`：把仓库根与 `src/` 注入 `sys.path` 与 `PYTHONPATH`
   （子进程形式的 CLI 用例需要）。
3. `pyproject.toml` 的 `pythonpath` 由 `["src"]` 改为 `["src", "."]`（追加，未删原有项）。

**待裁决**：以上三处是否算「改验收脚本」？本步判断不算（没有放宽任何断言、没有改阈值、
测试条数与通过条件一字未动），但请确认。

---

## B-6 依赖声明未跟上（按「不新增依赖」规矩未改）

搬过来的代码实际需要：

- `optiflow/adapters/dialux/dxf.py` / `_chain.py` → **ezdxf**（本机已装 1.4.4）
- `src/validator/__init__.py` → **jsonschema**（本机已装；缺失时 `validate_ir` 报 SCHEMA_LOAD_FAIL）
- `optiflow/adapters/dialux/xlsx.py` → pandas / openpyxl（当前无测试触发，属可选）

OptiFlow 的 `pyproject.toml` 依赖仍只有 `pydantic`，`requirements.txt` 只有 `pydantic` + `pytest`。
任务书说「不新增依赖，必须加的写 BLOCKED.md」——所以这里只记不改。

**待裁决**：是否把 `ezdxf>=1.3`、`jsonschema>=4.0` 写进 OptiFlow 的依赖声明？
（不写的话，干净环境跑测试会在 import 阶段失败）

---

## B-7 两条 `skipif` 用例依赖 `build/` 产物，而 `build/` 在 .gitignore 里

本机 0 skipped 是通过把源仓的 `build/room_layout.json`、`build/test_room_1.stf` 复制过来达成的。
但 OptiFlow 的 `.gitignore` 含 `build/`，所以**干净 clone 里这两条会 skip**：

- `test_exporter_stf.py::test_real_room_layout_generates`
- `test_exporter_stf.py::test_s0_reference_same_key_skeleton`

按规矩没有改 `.gitignore`（那是改判卷环境，且 `build/` 本就是运行产物目录）。

**待裁决**：把这两个基准产物放进版本控制（例如挪到 `tests/fixtures/` 并同步改 skipif 里的路径——
但改路径就是改断言，不许），还是接受「干净 clone 少跑 2 条」？

---

## B-8 按任务书「默认」未搬的部分（记录在案，非阻塞）

- `tests/test_real_dxf.py`（13 条）**未搬**：依赖 ODA File Converter 与真实图纸，任务书默认「本步不搬」。
- DIALux 真机 UI 通道（`executor/uia/*.ps1` 驱动、落灯、出报告）**未接**：任务书默认拆到下一步。
  本步只把 `executor/uia/driver.py` 等**文件**搬过来（因为 `test_progress_contract` 18 条要它们），
  但 `DialuxAdapter` 不调用任何真机代码——见 `capabilities().limits`。

---

## 顺手活

无。本步没有夹带任何 bug 修复、重构或依赖安装。

## 无阻塞项时的说明

除 B-4 外，其余各条都是「已按默认推进 + 需要事后确认」，不构成本步停工理由。

---

# 后续轮次新增（P2 / 编排层 / 对外接口 / 极简壳 之后）

## B-9 极简壳没在真浏览器里验证过（待裁决：要不要装浏览器）

P5 的验收标准是「非技术用户能独立完成一次任务」，最硬的证据是真浏览器里点一遍。
但本机没有 Playwright 浏览器（`ms-playwright` 缓存不存在），下载 chromium 约 150 MB——
按「装依赖一律停下待裁决」的规矩，没有擅自装。

已做的替代验证：

- JS 过 `node --check`（退出码 0）；HTML 标签配对全部 OK；
- 页面里的 `fetch` 目标与真实路由逐一对照（有测试）；
- `scripts/shell_journey.py` 用 HTTP 把同一条链路完整跑通：
  打开界面 → 出方案（31 盏 / 508.8 lx / U0 0.693）→ 5 条校验全过 → 下载 1954 字节 STF。

**仍未覆盖**：浏览器里 JS 的实际运行（DOM 交互、渲染结果）。
静态语法通过不等于逻辑对——比如把 `byId("width")` 写成 `byId("widht")` 照样能过 `node --check`。

**待裁决**：允许我 `npx playwright install chromium`（约 150 MB）补一条真浏览器端到端测试吗？

## B-10 B-2 还剩下最后一小截：`--validate` 路径仍依赖搬运件

本轮把适配器对 `src.planner.core` 的依赖还掉了（提成 `optiflow/geometry.py`），
并加了「只挂 `src/` 也要能跑通主链路」的守卫测试。

但 `optiflow/adapters/dialux/stf.py` 里还剩一处惰性 import：

```python
    from src.validator import validate_ir  # 懒加载：不用 --validate 时不牵 jsonschema
````
```

只在 `--validate` 时触发（`test_exporter_stf.py` 有 5 条 CLI 用例走它，靠 conftest 的 PYTHONPATH 兜住）。
彻底断链要把 validator（320 行 + `spec/ir.schema.json` 依赖）也搬进 optiflow，
那会动到「被判卷的搬运件」，**待裁决**：现在搬，还是维持惰性不动？

## B-11 P1② / P3 / P4 的状态（明确记录，非新问题）

- **P1② DIALux 真机 UI 与落灯**：等用户明确授权。它会真实启动并操作桌面上的 DIALux，
  属于会打扰用户的操作，一直没动。
- **P3 Creo / P4 Zemax**：本机未装（`/c/Program Files` 下没有 PTC / Zemax / ANSYS）。
  计划文档写的是「装机后启用」，所以只能留白接口，不能假装做完。

