# tests 说明

跑法：`pytest -q`（全量）、`pytest tests/test_parser_shell.py -q`（单文件）。

## 一处隔离 env，三处平台依赖

`test_parser_shell.py` 的子进程 env 是**完全隔离**的（按 key 白名单构造，不是继承后追加），
所以与平台相关的三件事必须自己填，缺一处就在某台机器上掉一层：

| 依赖 | 缺了的症状 | 键 |
|---|---|---|
| 编码 | 子进程按系统 locale 输出，父端按 UTF-8 解 → `stdout` 变 None，下游 `strip()` 报 AttributeError | `PYTHONUTF8` / `PYTHONIOENCODING` |
| 家目录 | ezdxf 在 import 阶段 `Path("~").expanduser()` → RuntimeError | `HOME` / `USERPROFILE` |
| 包定位 | 装在用户级 site-packages 的包消失 → `ModuleNotFoundError` | `APPDATA`（Windows） |

**判据：凡按 key 白名单构造 env 的地方，都该按平台的包解析路径核一遍。**
来历：`c21ac92`（编码＋家目录）、`cc75dec`（APPDATA），以及同一病根在
`scripts/cross_parser_parity.py` 的 `031cd79`。再出现第四个 Windows 专用键时，
改走「显式拼当前解释器 site-packages 进 PYTHONPATH」，不再逐个补键。
