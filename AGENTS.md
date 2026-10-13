# OptiFlow（光枢）项目规则

## 定位

光学软件统一平台。说目标 → 调软件 → 给结果。当前 P0-P5 全部完成，DIALux 文件层与真机链均已打通；
结果包抽取器、DXF 导入（评价点①）已落地。阶段状态见 `README.md`，执行记录见 `PROGRESS.md`。

## 红线

1. **冻结源仓只读**：dialux-compiler 源仓（Windows 侧 `D:\dev\dialux-compiler`）冻结为保底，
   只允许 bug 修复；通用件搬运是一次性动作，此后两仓独立演化（本仓 ⊥ 源仓）。
2. **禁止双向同步**：搬完在源仓 KANBAN 记一笔，不来回倒。
3. **不在别人未提交的半成品上叠改**。
4. **照明项目细节不外发、不录屏**；两仓 GitHub（cjh-blip）为唯一发布源，Windows 侧禁 push（pre-push 钩子已装）。
5. `D:\dev\dialux-references` 只读；`D:\dev\dialux-reconstruction-20260910` 默认不碰。

## 设计纪律

- IR 只表达共享语义；任何软件私有参数走 `TaskSpec.extra`。
- IR 里不出现软件名，`tests/test_ir.py` 有守卫测试。
- 适配器能力必须**先声明后实现**：做不了的事写进 `CapabilityDecl.limits`，尤其是 DIALux 的 UI 层。
- 脆弱实现（如 UIA 驱动）在文件头标 `FRAGILE`。
- **真机结论回流**：真机跑出的结论要变成代码里的默认值或测试（例：STF 写 GBK、UGR 支持），
  不停在手工链路里。

## 环境（两端）

| 端 | 解释器 | 备注 |
|---|---|---|
| Windows | `D:\dev\anaconda3\python.exe`（3.11） | PowerShell 注入 `$env:PYTHONPATH = "src"` |
| Mac | `~/anaconda3/envs/workshop/bin/python3`（3.12） | `PYTHONPATH=src` 前缀；双击 `启动光枢.command` 一键起服务 |

- 依赖：pydantic v2、pytest、**ezdxf**（DXF 解析）、**jsonschema**（validator）；
  可选 `pandas`/`openpyxl`（`xlsx.py` 的惰性通路）。声明见 `pyproject.toml` 与 `requirements.txt`。
- 测试：`python -m pytest -q`（两端同一套）。
- `requirements.txt` **必须保持纯 ASCII**：pip 会按系统 locale（Windows 本机 GBK）解码它。

## 当前状态与下一步

状态表见 `README.md`「阶段状态」；近期闭环（2026-10-08/09）：
结果包抽取器（2 页/29 页版全字段）、DXF 导入（评价点①）、STF 默认 GBK、CAD 真实轮廓直通布灯链。
下一步：报告组装器（评价点④）。

## 协作体系（现役）

- **DSH 桌面版**（DeepSeek Harness）：Windows 侧真机任务执行体，模型 `deepseek-v4-flash`
  （配置在 `~/.dsh/settings.yaml` 的 `agent-default-model`）。
- **Hanako**：Mac 侧开发、决策与交接区协调。
- 本仓历史：P0 骨架期由 AI 助手执笔，P1 起转入 DSH/多体协作。
- 权限边界与体系沿革见 `docs/agent-spec.md`；本文件是协作体系的唯一真身。


## 推送门（公开仓，2026-10-13 起）

推之前必须过：`bash scripts/pre_push_check.sh`（① 关键词扫：本机路径硬拦、项目名公开仓硬拦 ② 全量测试 ③ 跨语言断言 ④ ruff 若可用）。
门不过就不推。**首推教训**：本机路径与项目名曾提交进历史，改工作区无用 —— 必须改历史才干净。

## 目录关系（别搞混）

- `src/optiflow/` = 平台本体（IR / 校验 / 适配器 / 报告 / 摘要 / 动作注册表 `commands.py`）。
- `src/{core,executor,planner,tasks,validator}` = 从 **dialux-compiler** 搬来的 **import 闭包副本**（历史包袱，见 `BLOCKED.md` B-2）。
  **已与上游分叉**（`driver_plan.py` / `planner/core.py` / `validator/__init__.py` 内容均已不同，且缺 `executor/uia/furniture.py`）：
  - 本副本**冻结，不再新增**；
  - **真机链的真相源在 `dialux-compiler` 仓**（UIA 驱动、STF 导出、抽数脚本都在那边，只在 Windows 上跑）；
  - 收编方向：把真机链登记成动作接进 `commands.py`（见 `docs/action-contract.md` 的「待收编」），**不要**拿本副本当基准反向同步。
- 界面两条线（Hana App 版 / 独立桌面版）是**私有仓**，不在本仓；它们共用的 HTML 面板与 `metrics.js` 口径在 App 仓，与本仓的共同接口是动作注册表。
