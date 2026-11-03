"""报告数据包（report_package）的形状契约。

这份包的字段名会被界面与文档两条线同时消费，字段表在 docs/report_package_fields.md。
测试钉的是「形状不会悄悄变」和「该空的必须空」，不是具体数值。
"""

from __future__ import annotations

import pytest

from optiflow.algo.engine import plan_layout
from optiflow.ir import Fixture, Geometry, Metric, Point, Space, TaskSpec
from optiflow.report_package import (
    SCHEMA_VERSION,
    UNCOVERED_ITEMS,
    build_report_package,
)

ROOM = [(0, 0), (8, 0), (8, 6), (0, 6)]


def _task(room_type: str | None = "meeting_room", target: float = 300.0,
          extra=None, fixtures=None) -> TaskSpec:
    base = {"flux": 3000.0, "fixture_name": "测试灯"}
    base.update(extra or {})
    return TaskSpec(
        kind="layout",
        spaces=[Space(id="r1", name="测试房间", room_type=room_type,
                      geometry=Geometry(kind="room", height=4.0,
                                        outline=[Point(x=float(x), y=float(y)) for x, y in ROOM]),
                      work_plane=0.75,
                      reflectance={"ceiling": 0.7, "wall": 0.5, "floor": 0.2})],
        fixtures=fixtures or [],
        constraints=[Metric(name="illuminance_avg", target=target, unit="lx")],
        extra=base,
    )


def _pkg(**kw):
    task = _task(**kw)
    return build_report_package(plan_layout(task), task)


# ---------------------------------------------------------------- 顶层形状

def test_top_level_fields_are_stable():
    pkg = _pkg()
    for key in ("schema_version", "generated_at", "rooms", "standards_cited",
                "shortfalls", "luminaire", "uncovered", "assumptions",
                "warnings", "pipeline", "totals"):
        assert key in pkg, f"缺顶层字段 {key}"
    assert pkg["schema_version"] == SCHEMA_VERSION


def test_room_shape_has_every_contracted_field():
    room = _pkg()["rooms"][0]
    for key in ("space_id", "room_type", "standard_key", "standard_basis", "area_m2",
                "target_lux", "target_basis", "actual_lux", "illuminance_ok",
                "u0", "u0_required", "u0_basis", "u0_ok", "lpd",
                "fixture_count", "nominal_count", "supplement",
                "metric_status", "warnings"):
        assert key in room, f"缺房间字段 {key}"
    for key in ("actual", "current_limit", "target_limit", "compliant",
                "meets_target", "basis"):
        assert key in room["lpd"], f"缺 lpd 子字段 {key}"


# ---------------------------------------------------------------- 追溯

def test_every_cited_limit_has_a_standard_and_table_number():
    for row in _pkg()["standards_cited"]:
        assert row["standard"], f"{row['field']} 没有出处"
        assert any(tag in row["standard"] for tag in ("表", "条")), \
            f"出处没有表号/条号：{row['standard']}"


def test_cited_rows_cover_all_six_limits():
    fields = {r["field"] for r in _pkg()["standards_cited"]}
    assert fields == {"illuminance", "ugr", "u0", "ra", "lpd_current", "lpd_target"}


def test_room_basis_strings_are_never_empty():
    """每个能对标的房间，目标与 U0 都要能说出依据。"""
    room = _pkg()["rooms"][0]
    assert room["target_basis"] and room["u0_basis"] and room["standard_basis"]


# ---------------------------------------------------------------- 缺口

def test_shortfalls_carry_direction_and_space_id():
    pkg = _pkg(extra={"fixture_power_w": 400.0})
    assert pkg["shortfalls"], "这个样本应当有缺口"
    for s in pkg["shortfalls"]:
        assert s["space_id"] == "r1"
        assert s["direction"] in ("under", "over")
        assert s["cause"] and s["action"]


# ---------------------------------------------------------------- 灯具与功率来源

def test_power_source_is_derived_when_ir_lacks_power():
    lum = _pkg()["luminaire"]
    assert lum["power_source"] == "derived"
    assert lum["note"], "derived 必须带警告，不能静默"


def test_power_source_is_declared_when_caller_gives_it():
    lum = _pkg(extra={"fixture_power_w": 25.0})["luminaire"]
    assert lum["power_source"] == "declared"
    assert lum["note"] is None


def test_power_source_is_measured_when_fixture_entry_has_power():
    fx = [Fixture(id="L0", name="灯", properties={"power": 30.0})]
    lum = _pkg(fixtures=fx)["luminaire"]
    assert lum["power_source"] == "measured"


# ---------------------------------------------------------------- 未覆盖项

def test_uncovered_lists_all_six_items():
    items = {u["item"] for u in _pkg()["uncovered"]}
    assert items == {"ugr", "ra", "cct", "price", "energy", "rendering"}


def test_every_uncovered_item_says_what_is_missing():
    for u in _pkg()["uncovered"]:
        assert u["status"] in ("not_implemented", "missing_data")
        assert u["what_is_missing"] and len(u["what_is_missing"]) > 8, \
            f"{u['item']} 的 what_is_missing 太敷衍"


def test_uncovered_items_are_declared_once():
    """清单是单一真相源，别在生成器里另抄一份。"""
    pkg = _pkg()["uncovered"]
    assert len(pkg) == len(UNCOVERED_ITEMS)


# ---------------------------------------------------------------- 类型未指定

def test_untyped_room_has_null_limits_and_not_specified_status():
    room = _pkg(room_type=None)["rooms"][0]
    assert room["standard_key"] is None
    assert room["lpd"]["current_limit"] is None
    assert room["lpd"]["compliant"] is None
    assert room["metric_status"]["illuminance_avg"] == "not_specified"
    assert room["metric_status"]["ugr"] == "not_computed"
    assert room["actual_lux"] is not None, "实测值还是要给"
    assert _pkg(room_type=None)["standards_cited"] == [], \
        "没类型就不该有判据出处条目"


def test_totals_match_rooms():
    pkg = _pkg()
    assert pkg["totals"]["room_count"] == len(pkg["rooms"])
    assert pkg["totals"]["fixture_count"] == sum(r["fixture_count"] for r in pkg["rooms"])


def test_pipeline_can_be_attached():
    task = _task()
    plan = plan_layout(task)
    pkg = build_report_package(plan, task,
                               pipeline={"job_id": "job-x", "steps": [], "source": "test"})
    assert pkg["pipeline"]["job_id"] == "job-x"


# ---------------------------------------------------------------- 三层判据

def test_verdict_is_three_layers_not_one_boolean():
    v = _pkg()["rooms"][0]["verdict"]
    for key in ("compulsory_ok", "standard_pass", "target_met", "evaluated", "unevaluated"):
        assert key in v, f"缺 {key}"
    assert isinstance(v["evaluated"], dict) and v["evaluated"]


def test_standard_pass_is_false_when_u0_fails():
    """关键防线：U0 不达标就不能叫 standard_pass，哪怕照度与 LPD 都过了。"""
    room = _pkg()["rooms"][0]
    if room["u0_ok"]:
        pytest.skip("这个样本 U0 恰好达标，测不出这道防线")
    assert room["verdict"]["standard_pass"] is False
    assert room["verdict"]["evaluated"]["uniformity_u0"] is False
    assert room["verdict"]["evaluated"]["illuminance_avg"] is True


def test_uncomputed_metrics_are_listed_not_counted_as_pass():
    v = _pkg()["rooms"][0]["verdict"]
    assert "ugr" in v["unevaluated"] and "ra" in v["unevaluated"]
    assert "ugr" not in v["evaluated"], "没算的项不许冒充通过"


def test_untyped_room_standard_pass_is_null():
    v = _pkg(room_type=None)["rooms"][0]["verdict"]
    assert v["standard_pass"] is None, "没有标准可依就没有 pass 可言"
    assert v["compulsory_ok"] == "unevaluated"
    assert "illuminance_avg" in v["unevaluated"]


# ---------------------------------------------------------------- 强制项三态

def test_compulsory_is_tri_state_not_boolean():
    v = _pkg()["rooms"][0]["verdict"]
    assert v["compulsory_ok"] in ("pass", "fail", "unevaluated")


def test_compulsory_is_unevaluated_because_ra_and_svm_are_not_computed():
    """Ra 挂着 GB 55016 的强制要求，没算就不能说「符合强制规范」。"""
    v = _pkg()["rooms"][0]["verdict"]
    assert v["compulsory_ok"] == "unevaluated"
    assert "ra" in v["compulsory_missing"]
    assert "svm" in v["compulsory_missing"]
    assert v["compulsory_detail"]["lpd_current"] is True


def test_ra_cite_includes_mandatory_standard():
    rows = [r for r in _pkg()["standards_cited"] if r["field"] == "ra"]
    assert rows, "Ra 必须有出处行"
    assert any("GB 55016" in r["standard"] for r in rows), \
        "Ra 是强制要求，出处必须带 GB 55016，不能只挂推荐性国标"


def test_compulsory_fail_when_lpd_over_current_limit():
    v = _pkg(extra={"fixture_power_w": 400.0})["rooms"][0]["verdict"]
    assert v["compulsory_ok"] == "fail"
    assert v["compulsory_detail"]["lpd_current"] is False


def test_totals_no_longer_claim_fully_compliant():
    t = _pkg()["totals"]
    assert "rooms_fully_compliant" not in t, "这个名字会骗人，已拆成三层"
    for key in ("rooms_compulsory_pass", "rooms_compulsory_fail",
                "rooms_compulsory_unevaluated", "rooms_standard_pass", "rooms_target_met"):
        assert key in t


def test_totals_standard_pass_counts_u0_failures():
    pkg = _pkg()
    if pkg["rooms"][0]["u0_ok"]:
        pytest.skip("U0 达标，测不出计数差")
    assert pkg["totals"]["rooms_standard_pass"] == 0
    assert pkg["totals"]["rooms_compulsory_ok"] == 1
