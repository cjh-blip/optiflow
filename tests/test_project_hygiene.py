"""AC-9：prompts/ 协作文档卫生检查。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_no_zcode():
    """TR-7.1：prompts/zcode.md 不存在。"""
    assert (ROOT / "prompts" / "zcode.md").exists() is False


def test_prompts_three_modes_sections():
    """TR-7.2：prompts/ 里的 Trae Work 模板已退役，不应再存在。

    2026-09-06 更新：原断言要求 trae-work.md 必须存在且含三模式标题，把已淘汰的
    Trae Work 体系永久钉死在仓库里（与 AGENTS.md「换代停用」互相打脸）。改为断言
    该文件已退役删除——同 test_no_zcode 的卫生语义。
    """
    assert (ROOT / "prompts" / "trae-work.md").exists() is False
    assert (ROOT / "prompts" / "dispatch_fix_parse_config_test.md").exists() is False
    assert (ROOT / "prompts" / "mvp2_stf_exporter_task.md").exists() is False
    assert (ROOT / "prompts" / "parser_furniture_fix_task.md").exists() is False


def test_agents_no_plan_mode_str():
    """TR-7.3：AGENTS.md 不含「Plan 模式」，且声明现役协作体系（DSH）。

    2026-09-05 换代：原断言要求 AGENTS.md 必须含「claude 子员工」，那是已停用的体系，
    继续钉死会把废弃事实永久锁在文档里。改为断言现役体系关键词。
    """
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "Plan 模式" not in text, "不应含「Plan 模式」字样"
    assert "DSH" in text, "应声明现役执行体系 DSH"
    assert "deepseek-v4-flash" in text, "应写明现役模型"


def test_agentspec_points_to_agents_md():
    """TR-7.4：docs/agent-spec.md 不复述协作体系，只指向 AGENTS.md 并记录沿革。

    2026-09-05 换代：原断言要求本文件必须含「Hermes」+「子员工」，同 TR-7.3 的理由改写。
    现在校验的是「单一权威 + 沿革可追」这个结构约束，而不是某一代体系的名字。
    """
    text = (ROOT / "docs" / "agent-spec.md").read_text(encoding="utf-8")
    assert "AGENTS.md" in text, "agent-spec.md 应指向 AGENTS.md 作为协作体系真身"
    assert "沿革" in text, "agent-spec.md 应保留体系沿革，避免旧体系引用复活"
