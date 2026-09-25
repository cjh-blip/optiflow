"""UIA 通道：computer use 驱动 DIALux（STF 建壳已真机验证）。"""
from src.executor.uia.driver import (
    STEPS,
    StepEvent,
    blocking_dialogs,
    cancel_file_dialogs,
    run_import,
    succeeded,
)

__all__ = [
    "STEPS",
    "StepEvent",
    "blocking_dialogs",
    "cancel_file_dialogs",
    "run_import",
    "succeeded",
]
