"""极简壳 —— 单页「说目标 → 出结果」。

验收标准是「**非技术用户能独立完成一次任务**」，所以：

- 界面在 `web/index.html`，由 HTTP 接口在 `GET /` 直接吐出来——
  不需要另起前端服务器、不需要构建步骤、不引前端框架；
- 页面自己就是用户：填房间尺寸与目标照度 → 出一版布灯方案 → 下载 DIALux 能导入的 STF；
- 结果区把**逐条校验**与**模型边界**一起显示。
  只给漂亮数字不给边界的界面，比没有界面更危险——用户会拿它当结论用。

壳是「最后定」的那一层（计划文档第六节）：换 Electron/Tauri 都行，
接口对了换壳不疼。所以这个文件只负责把 HTML 交给传输层，不含任何业务逻辑。
"""
from __future__ import annotations

from pathlib import Path

PAGE_PATH = Path(__file__).resolve().parent / "web" / "index.html"


def page_html() -> str:
    """读单页界面。文件缺失要报错而不是回一个空页面——空页面没人看得出哪里坏了。"""
    if not PAGE_PATH.exists():
        raise FileNotFoundError(
            f"壳的页面文件缺失：{PAGE_PATH}（打包时别忘了把它带上）")
    return PAGE_PATH.read_text(encoding="utf-8")


CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".stf": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}


def content_type_for(path: Path) -> str:
    return CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


__all__ = ["PAGE_PATH", "content_type_for", "page_html"]
