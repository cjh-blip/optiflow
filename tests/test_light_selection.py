# -*- coding: utf-8 -*-
"""选灯反推（plan.light_backcalc）：函数级 ＋ 注册表级，含口算可核的基准数。"""
import pytest

from optiflow.commands import CommandError, call, list_commands
from optiflow.light_selection import DEFAULT_CU, DEFAULT_MF, backcalc


def test_baseline_round_numbers():
    """54㎡ / 300lx / 缺省 CU·MF → 300×54÷0.48 = 33750 lm（口算可核）。"""
    r = backcalc(area_m2=54, target_lux=300)
    assert r["total_flux_lm"] == 33750
    assert r["params_used"]["cu"] == DEFAULT_CU and r["params_used"]["mf"] == DEFAULT_MF
    assert r["params_used"]["cu_is_default"] is True
    assert r["power_budget_w"] is None and r["per_lamp"] is None
    assert r["evidence"]["inputs"]["area_m2"] == 54.0
    assert "Φ_total" in r["evidence"]["formula"]


def test_with_lpd_and_count():
    """加 LPD≤6.5 与 9 盏 → 天花板 351W；每盏 3750 lm / 39W。"""
    r = backcalc(area_m2=54, target_lux=300, lpd_cap=6.5, count=9)
    assert r["power_budget_w"] == 351.0
    assert r["per_lamp"] == {"flux_lm": 3750, "power_w": 39.0}
    assert r["state"]["flux_per_lamp"] == 3750


def test_custom_cu_mf_flags_not_default():
    r = backcalc(area_m2=100, target_lux=500, cu=0.5, mf=0.7)
    assert r["total_flux_lm"] == round(500 * 100 / (0.5 * 0.7))   # 142857
    assert r["params_used"]["cu_is_default"] is False
    assert r["params_used"]["mf_is_default"] is False


def test_count_without_lpd_keeps_power_none():
    r = backcalc(area_m2=54, target_lux=300, count=9)
    assert r["per_lamp"]["flux_lm"] == 3750
    assert r["per_lamp"]["power_w"] is None


@pytest.mark.parametrize("kwargs,keyword", [
    (dict(area_m2=0, target_lux=300), "面积"),
    (dict(area_m2=54, target_lux=-1), "照度"),
    (dict(area_m2=54, target_lux=300, cu=1.5), "利用系数"),
    (dict(area_m2=54, target_lux=300, mf=0), "维护系数"),
    (dict(area_m2=54, target_lux=300, lpd_cap=-2), "LPD"),
    (dict(area_m2=54, target_lux=300, count=0), "灯数"),
])
def test_rejects_bad_input_with_chinese_reason(kwargs, keyword):
    with pytest.raises(ValueError) as exc:
        backcalc(**kwargs)
    assert keyword in str(exc.value)


def test_via_registry_call():
    out = call("plan.light_backcalc", {"area_m2": 54, "target_lux": 300, "count": 9})
    assert out["total_flux_lm"] == 33750
    assert out["state"]["total_flux_lm"] == 33750
    assert out["evidence"]["formula"]
    assert out["params_used"]["cu_is_default"] is True     # 注册表缺省不冒充显式传入


def test_via_registry_missing_required_and_bad_input():
    with pytest.raises(CommandError) as exc:
        call("plan.light_backcalc", {"area_m2": 54})
    assert "target_lux" in str(exc.value)
    with pytest.raises(ValueError):
        call("plan.light_backcalc", {"area_m2": -1, "target_lux": 300})


def test_registered_in_table():
    ids = [c["id"] for c in list_commands()]
    assert "plan.light_backcalc" in ids
