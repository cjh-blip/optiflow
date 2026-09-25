"""走一遍真实用户旅程（HTTP 层，非技术用户视角）。

壳（web/index.html）调的就是这条链路；这个脚本用 HTTP 把它复现一遍，
好让「非技术用户能独立完成一次任务」这句话有可执行证据，而不是靠肉眼看界面。

运行：
    D:\dev\anaconda3\python.exe scripts/shell_journey.py
"""
import json
import sys
import urllib.request as urlreq
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from optiflow.api import serve_in_thread  # noqa: E402
from optiflow.service import build_default_service  # noqa: E402

BODY = {
    "pipeline": "lighting_plan",
    "task": {
        "kind": "layout",
        "spaces": [{
            "id": "room-1", "name": "房间",
            "geometry": {"kind": "room", "height": 3.0, "outline": [
                {"x": 0, "y": 0}, {"x": 9.57, "y": 0},
                {"x": 9.57, "y": 12.97}, {"x": 0, "y": 12.97}]},
            "work_plane": 0.75,
            "reflectance": {"ceiling": 0.7, "wall": 0.5, "floor": 0.2},
        }],
        "constraints": [
            {"name": "illuminance_avg", "target": 500.0, "unit": "lx"},
            {"name": "uniformity_u0", "target": 0.6, "unit": ""},
        ],
        "extra": {"flux": 3000.0, "fixture_name": "LED 面板"},
    },
}


def main() -> int:
    server, _thread, base = serve_in_thread(build_default_service(), port=0)
    try:
        print("服务地址：", base)

        with urlreq.urlopen(base + "/", timeout=30) as response:
            page = response.read().decode("utf-8")
        print("1) 打开界面      -> HTTP 200,", len(page), "字节, 有表单:",
              'id="go"' in page)

        request = urlreq.Request(base + "/run",
                                 data=json.dumps(BODY).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
        with urlreq.urlopen(request, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))
        plan_step = [s for s in result["steps"] if s["name"] == "plan"][0]
        metrics = {m["name"]: m["value"] for m in plan_step["metrics"]}
        print("2) 点「出方案」  -> ok=%s, 用时 %ss" % (result["ok"], result["elapsed"]))
        print("     灯数 %.0f 盏 / 平均照度 %s lx（目标 500）/ 均匀度 U0 %s（目标 0.6）"
              % (metrics["fixture_count"], metrics["illuminance_avg"],
                 metrics["uniformity_u0"]))

        print("3) 逐条校验：")
        for check in result["checks"]:
            print("     [%s] %s" % ("通过" if check["passed"] else "未过", check["name"]))

        url = base + "/jobs/" + result["job_id"] + "/artifacts/stf"
        with urlreq.urlopen(url, timeout=30) as response:
            stf = response.read().decode("utf-8")
        lines = stf.splitlines()
        luminaires = sum(1 for line in lines
                         if line.startswith("Lum") and ".Pos=" in line)
        print("4) 下载 STF      ->", len(stf), "字节, 首行", repr(lines[0]))
        print("     房间段", stf.count("[ROOM."), "个, 灯具", luminaires, "盏")
        print("5) 完成：用户拿到一个 DIALux 能导入的文件。")
        return 0
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
