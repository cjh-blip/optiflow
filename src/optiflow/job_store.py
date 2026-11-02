"""任务句柄落盘：进程会死，job 不能跟着消失。

存的是一份「能回答 status() 的快照」，**不是 ServiceJob 对象本身** ——
对象里有锁、有后台线程、有 monotonic 时钟，这三样跨进程都没有意义。
把对象 pickle 出去再读回来，会得到一个看起来像 job、实际再也推不动的僵尸。

启动时的做法是「认账」而不是「清理」：上次进程留下的 queued / running 记录
一律标成 ``interrupted`` 并保留。任务没做完是事实，让它消失才是撒谎。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

#: 记录格式版本。字段含义变化时 +1。
SCHEMA_VERSION = 1

#: 业务根下的任务记录目录名。
JOBS_DIR = "jobs"

#: 进程已退出但记录还停在途中的状态。
INTERRUPTED = "interrupted"

#: 还在途中的状态。只有这两个会在重启时被认成 interrupted。
IN_FLIGHT = ("queued", "running")

#: 终态。落盘后不再变化。
TERMINAL = ("done", "failed", "cancelled", "error", INTERRUPTED)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


class JobStore:
    """一个业务根一个 JobStore。目录不存在时不会凭写操作创建（读是纯读）。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root) / JOBS_DIR

    # ------------------------------------------------------------ 路径
    def path_of(self, job_id: str) -> Path:
        safe = (job_id or "").strip()
        if not safe or any(c in safe for c in ("/", "\\", ":", "..")):
            raise ValueError(f"job_id 不合法：{job_id!r}")
        return self.root / f"{safe}.json"

    # ------------------------------------------------------------ 写
    def save_record(self, record: Dict[str, Any]) -> Path:
        payload = dict(record)
        payload["schema_version"] = SCHEMA_VERSION
        payload["updated_wall"] = time.time()
        path = self.path_of(str(payload["job_id"]))
        _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2))
        return path

    def save_job(self, job: Any) -> Optional[Path]:
        """从 ServiceJob 取快照落盘。job 缺字段时降级而不是抛——落盘失败不该带崩任务。"""
        try:
            record = {
                "job_id": job.job_id,
                "pipeline": job.pipeline,
                "status": getattr(job.status, "value", str(job.status)),
                "stage": job.stage,
                "percent": round(float(job.percent), 1),
                "message": job.message,
                "steps": job.steps_view() if hasattr(job, "steps_view") else [],
                "pid": os.getpid(),
                "started_wall": getattr(job, "started_wall", time.time()),
                "finished_wall": getattr(job, "finished_wall", None),
                "error": job.error,
            }
            return self.save_record(record)
        except (OSError, ValueError, AttributeError):
            # 落盘是旁路：它坏了不该让正在跑的任务跟着坏。
            return None

    # ------------------------------------------------------------ 读
    def load_record(self, job_id: str) -> Optional[Dict[str, Any]]:
        path = self.path_of(job_id)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            return None

    def snapshot(self, job_id: str) -> Optional[Dict[str, Any]]:
        """把落盘记录收敛成 status() 的形状，让调用方不用区分内存与磁盘。"""
        record = self.load_record(job_id)
        if record is None:
            return None
        out: Dict[str, Any] = {
            "job_id": record.get("job_id", job_id),
            "pipeline": record.get("pipeline", ""),
            "status": record.get("status", "unknown"),
            "stage": record.get("stage", "unknown"),
            "percent": record.get("percent", 0.0),
            "message": record.get("message", ""),
            "steps": record.get("steps") or [],
        }
        if record.get("error") is not None:
            out["error"] = record["error"]
        started, finished = record.get("started_wall"), record.get("finished_wall")
        if isinstance(started, (int, float)) and isinstance(finished, (int, float)):
            out["elapsed"] = round(finished - started, 3)
        elif isinstance(started, (int, float)):
            out["elapsed"] = round(time.time() - started, 3)
        return out

    def ids(self) -> List[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.stem for p in self.root.glob("*.json"))

    # ------------------------------------------------------------ 重启认账
    def reap_interrupted(self, *, pid: Optional[int] = None) -> List[str]:
        """把上一个进程留下的在途记录标成 interrupted，返回被标的 job_id。

        跳过 ``pid`` 相同的记录：那说明是本进程自己起的任务，还在跑。
        """
        me = pid if pid is not None else os.getpid()
        touched: List[str] = []
        for job_id in self.ids():
            record = self.load_record(job_id)
            if record is None:
                continue
            if record.get("status") not in IN_FLIGHT:
                continue
            if record.get("pid") == me:
                continue
            record["status"] = INTERRUPTED
            record["stage"] = INTERRUPTED
            record["message"] = "上一次运行中断（进程已退出），这不是任务的最终结果"
            record["finished_wall"] = record.get("finished_wall") or time.time()
            try:
                self.save_record(record)
            except OSError:
                continue
            touched.append(job_id)
        return touched


__all__ = [
    "INTERRUPTED",
    "JOBS_DIR",
    "JobStore",
    "SCHEMA_VERSION",
    "TERMINAL",
]
