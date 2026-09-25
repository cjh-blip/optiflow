"""真浏览器端到端（BLOCKED.md B-9）：Playwright 打开壳 → 填表 → 出方案 → 下载 STF。

为什么必须有这条：静态校验（node --check + 标签配对）证明不了 DOM 逻辑对——
`byId("width")` 写成 `byId("widht")` 照样过语法检查。这条在真浏览器里跑完整旅程。

依赖与跳过：
- 缺 playwright 库 → 整模块 skip（pip install playwright）
- 缺可用 chromium → skip（本机实测：playwright==1.61.0 + 缓存 chromium-1228，
  用 channel="chromium" 走完整版；标准 `playwright install chromium` 的环境两种 launch 都行）

产物：截图落 build/browser_check/（gitignore 的运行产物目录）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytest.importorskip("playwright.sync_api", reason="需要 playwright 库：pip install playwright")

from playwright.sync_api import sync_playwright  # noqa: E402


def _launch_or_skip(p):
    """拿到 chromium：优先完整版（channel='chromium'），退回默认（headless shell）。

    两种缓存形态都覆盖：只有完整版（本机）或标准安装（两者都有）。都没有就 skip。
    """
    errors = []
    for kwargs in ({"channel": "chromium"}, {}):
        try:
            return p.chromium.launch(**kwargs)
        except Exception as e:  # noqa: BLE001 - 汇总后统一 skip
            errors.append(f"{kwargs or 'default'}: {str(e).splitlines()[0]}")
    pytest.skip("无可用 chromium（请在装好浏览器的环境运行）：" + " | ".join(errors))


@pytest.fixture(scope="module")
def shell_env():
    """起服务 + 浏览器（module 级共享，两个测试各开独立 context）。"""
    from optiflow.api import serve_in_thread
    from optiflow.service import build_default_service

    server, _thread, base = serve_in_thread(build_default_service(), port=0)
    with sync_playwright() as p:
        browser = _launch_or_skip(p)
        yield {"base": base, "browser": browser}
        browser.close()
    server.shutdown()
    server.server_close()


def _open_shell(shell_env):
    context = shell_env["browser"].new_context()
    page = context.new_page()
    page.goto(shell_env["base"] + "/", wait_until="load")
    return context, page


def test_shell_journey_in_real_browser(shell_env) -> None:
    """默认参数全旅程：表单 → 出方案 → 达标 → 校验全过 → 下载 STF → 截图。"""
    context, page = _open_shell(shell_env)
    try:
        # 表单就位（壳的静态结构）
        assert page.locator("#go").is_visible()
        assert page.input_value("#width") == "11.9"
        assert page.input_value("#depth") == "8.78"
        # capabilities 的异步加载也走通（页面第二个 fetch 路径）
        page.wait_for_function(
            "document.getElementById('caps').textContent.indexOf('加载中') === -1"
        )

        page.click("#go")
        page.wait_for_selector("#resultCard:not([hidden])", timeout=60_000)

        hint = page.text_content("#hint")
        assert "完成" in hint, hint

        summary = page.text_content("#summary")
        assert "方案达标" in summary, summary

        # 校验清单：没有一条 fail
        assert page.locator("#checks li.fail").count() == 0
        assert page.locator("#checks li.pass").count() >= 5

        # 产物链接（至少 STF 一个）
        stf_link = page.locator("#artifacts a", has_text="STF")
        assert stf_link.count() == 1
        href = stf_link.first.get_attribute("href")
        assert href and href.endswith("/artifacts/stf")

        # 同源 fetch 取 STF 本体（走页面自己的网络栈）
        stf_text = page.evaluate(
            "(url) => fetch(url).then(r => r.text())",
            shell_env["base"] + href if href.startswith("/") else href,
        )
        assert stf_text.startswith("[VERSION]")
        assert "[ROOM." in stf_text

        # 截图存证（运行产物，不入库）
        shot_dir = ROOT / "build" / "browser_check"
        shot_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(shot_dir / "shell_result.png"), full_page=True)
    finally:
        context.close()


def test_shell_reflects_user_input(shell_env) -> None:
    """改目标照度 600 → 结果里必须出现 600（证明表单输入真的进了计算链）。"""
    context, page = _open_shell(shell_env)
    try:
        page.fill("#target", "600")
        page.click("#go")
        page.wait_for_selector("#resultCard:not([hidden])", timeout=60_000)
        assert "600" in page.text_content("#checks")
    finally:
        context.close()
