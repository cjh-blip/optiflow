# Agent 协作规范（补充文档）

> **协作体系真身在 `AGENTS.md`**（工具分工、开发流程、铁律）。本文件不复述，
> 只补两件 AGENTS.md 没有的东西：权限边界表 和 体系沿革。
>
> 本仓现役执行体系是 DSH 桌面版顶层会话直接执行，模型 deepseek-v4-flash。
> （本仓没有 Hermes / claude 子员工 / cc-switch 那段历史——那是源仓 dialux-compiler 的沿革，勿套用。）

## 1. 权限边界

| 主体 | 可读 | 可写 | 禁止 |
|------|------|------|------|
| 架构师（人） | 全部 | 全部 | — |
| DSH 顶层会话 | 整个仓库 | `src/`、`tests/`、`spec/`、`docs/`、`scripts/`、`AGENTS.md`、`README.md` | 未经明确要求不 `git commit`；不改宪法文件 |
| DSH subagent | 整个仓库 | 派给它的范围 | 不自行 commit；结果回主会话裁决 |
| computer use 通道 | DIALux UI | 驱动 DIALux 建模/导入/保存 | 不动 DIALux 安装目录与 `ProgramData` 下的厂商包 |

补充约束：

- **`D:\dev\dialux-compiler` 是保底冻结仓**：只读，只允许 bug 修复；搬运是一次性单向复制，
  禁止双向同步（见 AGENTS.md「红线」）。
- **判卷标准不许碰**：被搬测试的断言、黄金文件一个字不许改；改格式必须重新做真机/端到端验证。
- **动文档或动代码前先跑 `git status`**：工作区有未提交改动时停下问架构师。

## 2. 体系沿革

| 时期 | 体系 | 状态 |
|---|---|---|
| P0 骨架期 | Hanako 执笔（契约与文档密集期，见 AGENTS.md「阶段分工」） | 已交棒 |
| P1 起 | **DSH 桌面版顶层会话直接执行 + deepseek-v4-flash** | **现役** |

沿革说明：OptiFlow 是 2026-09 新建的仓，**没有** Hermes / `claude --agent` 子员工 / cc-switch
这一段历史（那是源仓 dialux-compiler 的沿革，随搬运的测试一并被提到，勿套用到本仓）。
本仓只需要记住一件事：现役体系是 DSH 桌面版直接执行。
