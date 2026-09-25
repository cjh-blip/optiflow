# OptiFlow（光枢）项目规则

## 定位

光学软件统一平台。说目标 → 调软件 → 给结果。当前 **P0 骨架已完成、P1 文件层已通**（DIALux STF 通道接通，真机 UI 尚未接）。

## 阶段分工

- **P0 由 Hanako 执笔**（契约与文档密集期）
- **P1 起 DSH 主力、Hanako 转辅助**（真机调试归 DSH）

## 红线

1. **不动 `D:\dev\dialux-compiler`**：该仓冻结为保底，只允许 bug 修复。
2. **`D:\dev\dialux-references` 只读**。
3. **`D:\dev\dialux-reconstruction-20260910` 默认不碰**：与 OptiFlow 有交集需先划边界。
4. **禁止双向同步**：从 dialux-compiler 搬运通用件是一次性动作，搬完在源仓 KANBAN 记一笔，此后两边独立演化。
5. **不在别人未提交的半成品上叠改**。

## 设计纪律

- IR 只表达共享语义；任何软件私有参数走 `TaskSpec.extra`。
- IR 里不出现软件名，`tests/test_ir.py` 有守卫测试。
- 适配器能力必须**先声明后实现**：做不了的事写进 `CapabilityDecl.limits`，尤其是 DIALux 的 UI 层。
- 脆弱实现（如 UIA 驱动）在文件头标 `FRAGILE`。

## 环境

- Python：`D:\dev\anaconda3\python.exe`（3.11.15）
- 依赖：pydantic v2、pytest、**ezdxf**（DXF 解析）、**jsonschema**（validator）；
  可选 `pandas`/`openpyxl`（`xlsx.py` 的惰性通路，当前无调用方）。声明见 `pyproject.toml` 与 `requirements.txt`。
- 测试：`D:\dev\anaconda3\python.exe -m pytest tests/ -v`
- 依赖文件 `requirements.txt` **必须保持纯 ASCII**：pip 会按系统 locale（本机 GBK）解码它，中文注释会直接报错。

## 当前状态与下一步

见 `README.md` 阶段状态表。

已完成：P0 骨架（IR + 适配器契约 + 假适配器端到端）；P1 的 DIALux **文件层**
（DXF 解析 + STF 导出 + `DialuxAdapter`，见 `PROGRESS.md`）。

下一步：DIALux 真机 UI 与落灯（需用户拍板后单独开）；P2 光通量法算法层。

## 协作体系（现役）

本仓现役执行体系是 **DSH 桌面版**（DeepSeek Harness）顶层会话直接执行，模型 **deepseek-v4-flash**
（配置在 `C:\Users\cjh\.dsh\settings.yaml` 的 `agent-default-model`）。

- 执行：DSH 顶层会话直接读写代码、跑门禁、改文档；需要并行或隔离上下文时用 `subagent` / `workflow` 拆分。
- 本仓 P0 骨架期由 Hanako 执笔，P1 起改由 DSH 主力（见「阶段分工」）。
- 权限边界与体系沿革见 `docs/agent-spec.md`；本文件是协作体系的唯一真身。

