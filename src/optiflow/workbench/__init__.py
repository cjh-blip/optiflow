"""通用自动化工作台（全抄 BetterGI 架构，不绑定具体软件）。

- task.py      任务抽象（执行单元）
- trigger.py   实时触发器（ITaskTrigger 模式）
- flow.py      一条龙（OneDragon：顺序+开关+断点）
- config.py    配置持久化
- dispatcher.py 触发器调度器（周期轮询+优先级+独占）

具体软件任务在 src/tasks/<app>/（当前 dialux/；Zemax/Transport 复用同骨架）。
"""
