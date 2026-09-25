"""TR-MVP2：optiflow.adapters.dialux.stf 单元测试（IR → DIALux STF）。

覆盖：段结构、顶点数/闭合、Height=ceil_h、3 位小数精度、-0.0 归零、零长边剔除、
绕向（CW 保留 / --ccw 翻转）、家具伪 space 过滤、多楼层告警、输入校验 ValueError、
CLI 退出码与编码兜底、真实 build/room_layout.json 全量生成。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from optiflow.adapters.dialux.stf import PROG_NAME, PROG_VERS, ir_to_stf, write_stf

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).parent / "fixtures" / "mini_ir_2rooms.json"
REAL_IR = ROOT / "build" / "room_layout.json"


@pytest.fixture
def mini_ir() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _one_room_ir(polygon, ceil_h=2.8, **space_extra) -> dict:
    """最小单房间 IR，供精度/退化/绕向用例复用。"""
    space = {"id": "R1", "name": "R1", "polygon": polygon, "luminaires": []}
    if ceil_h is not None:
        space["ceil_h"] = ceil_h
    space.update(space_extra)
    return {
        "schema_version": "0.1",
        "project": {"name": "p", "source": {"dwg": "f.dxf"}, "units": "m"},
        "storeys": [{"level": 1, "spaces": [space]}],
    }


def _kv(text: str, key: str) -> str:
    """取第一个 `key=value` 的 value。"""
    m = re.search(rf"^{re.escape(key)}=(.*)$", text, flags=re.MULTILINE)
    assert m, f"输出中找不到 {key}="
    return m.group(1)


def _room_block(text: str, room_key: str) -> str:
    """取 `[ROOM.R1]` 段正文（到下一个 `[` 开头的段或文件末）。"""
    start = text.index(f"[ROOM.{room_key}]")
    rest = text[start + len(f"[ROOM.{room_key}]"):]
    nxt = rest.find("\n[")
    return rest if nxt < 0 else rest[:nxt]


def _points(block: str) -> list:
    return [tuple(m.group(1).split())
            for m in re.finditer(r"^Point\d+=(.*)$", block, flags=re.MULTILINE)]


def _signed_area(pts) -> float:
    ring = [(float(p[0]), float(p[1])) for p in pts]
    if len(ring) > 2 and ring[0] == ring[-1]:
        ring.pop()
    s = 0.0
    for i in range(len(ring)):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % len(ring)]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def _log_msgs(caplog) -> str:
    return " ".join(r.getMessage() for r in caplog.records)


# ---------- 结构 ----------

def test_sections_present(mini_ir):
    """TR-MVP2.1：三个必需段 + 每房间一个 RoomN 索引。"""
    text = ir_to_stf(mini_ir)
    for sec in ("[VERSION]", "[PROJECT]", "[ROOM.R1]", "[ROOM.R2]"):
        assert sec in text, f"缺失段 {sec}"
    assert _kv(text, "STFF") == "1.0"
    assert _kv(text, "Progname") == "dialux-compiler"
    assert _kv(text, "NrRooms") == "2", "家具伪 space 不应算房间"
    assert _kv(text, "Room1") == "ROOM.R1"
    assert _kv(text, "Room2") == "ROOM.R2"
    assert "[ROOM.R3]" not in text, "家具伪 space（家具_F1）不应生成 ROOM 段"
    assert _kv(text, "Name") == "mini-2rooms"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", _kv(text, "Date")), "Date 必须是 YYYY-MM-DD"


def test_room_points_and_height(mini_ir):
    """TR-MVP2.2：NrPoints == 实际 Point 行数 == 顶点数+1（补首点闭合）；Height=ceil_h。"""
    text = ir_to_stf(mini_ir)

    r1 = _room_block(text, "R1")
    pts1 = _points(r1)
    # fixture R1 是 4 个顶点的未闭合矩形 → 闭合后 5 点
    assert int(re.search(r"NrPoints=(\d+)", r1).group(1)) == len(pts1) == 5
    assert _kv(r1, "Height") == "2.8"
    assert _kv(r1, "Name") == "开放办公区"
    assert _kv(r1, "Description") == "R1", "Description 应写 space.id，便于回溯 IR"

    r2 = _room_block(text, "R2")
    pts2 = _points(r2)
    # fixture R2 已闭合（5 项，首尾相同）→ 唯一顶点 4 个，闭合后仍 5 点
    assert int(re.search(r"NrPoints=(\d+)", r2).group(1)) == len(pts2) == 5
    assert _kv(r2, "Height") == "3.05"


def test_polygon_closed_and_xyz(mini_ir):
    """TR-MVP2.3：首尾点相同（闭合）；每点是 X Y Z 三段且 Z=0（IR 第三维有意丢弃）。"""
    text = ir_to_stf(mini_ir)
    for key in ("R1", "R2"):
        pts = _points(_room_block(text, key))
        assert pts[0] == pts[-1], f"{key} 多边形未闭合：{pts[0]} != {pts[-1]}"
        for p in pts:
            assert len(p) == 3, f"{key} 点不是 X Y Z 三段：{p}"
            assert p[2] == "0", f"{key} 房间地板应在 Z=0，实际 {p[2]}"
    # fixture R1 第三个顶点是 [5, 4, 0]（schema Point2 允许 maxItems=3）：Z 被丢弃，仍输出 Z=0
    assert mini_ir["storeys"][0]["spaces"][0]["polygon"][2] == [5, 4, 0]


def test_no_close_option(mini_ir):
    """--no-close：与 S0 验证文件 test_room_1.stf 一致的不重复首点写法。"""
    text = ir_to_stf(mini_ir, close_polygon=False)
    pts = _points(_room_block(text, "R1"))
    assert len(pts) == 4
    assert pts[0] != pts[-1]


def test_coord_precision_3_decimals(mini_ir):
    """TR-MVP2.4：坐标最多 3 位小数（10.1234 → 10.123），且不写多余的尾零。"""
    text = ir_to_stf(mini_ir)
    xs = [p[0] for p in _points(_room_block(text, "R2"))]
    assert "10.123" in xs, xs
    assert "10.1234" not in xs
    for p in _points(_room_block(text, "R1")) + _points(_room_block(text, "R2")):
        for v in p:
            frac = v.split(".")[1] if "." in v else ""
            assert len(frac) <= 3, f"坐标 {v} 小数位 > 3"
            assert not (frac and frac.endswith("0")), f"坐标 {v} 有多余尾零"


def test_negative_zero_normalized():
    """真实 IR 里有 -1.42e-16 这种坐标，round 后是 -0.0，必须输出 "0" 而不是 "-0"。"""
    ir = _one_room_ir([[-1.42e-16, 0], [5, 0], [5, 4], [-9.42e-18, 4]])
    pts = _points(_room_block(ir_to_stf(ir), "R1"))
    assert pts[0][0] == "0", f"负零未归一化：{pts[0]}"
    assert "-0" not in [v for p in pts for v in p]


def test_integer_and_large_values_no_scientific_notation():
    """_fmt 不能输出科学计数法，整数位的零也不能被 rstrip 吃掉。"""
    ir = _one_room_ir([[0, 0], [100, 0], [1200.5, 3000], [0, 3000]])
    xs = [v for p in _points(_room_block(ir_to_stf(ir), "R1")) for v in p]
    assert "100" in xs and "1200.5" in xs and "3000" in xs
    assert not any("e" in v.lower() for v in xs), xs


def test_zero_length_edges_dropped():
    """相邻重复顶点（3 位小数下重合）= 零长墙段，应被剔除且 NrPoints 同步减少。"""
    ir = _one_room_ir([[0, 0], [0, 0], [5, 0], [5, 0.0001], [5, 4], [0, 4]])
    r1 = _room_block(ir_to_stf(ir), "R1")
    pts = _points(r1)
    # 6 个原始点 → 去掉 1 个完全重复 + 1 个 3 位小数下重合 → 4 唯一点 → 闭合 5 点
    assert len(pts) == 5, pts
    assert int(re.search(r"NrPoints=(\d+)", r1).group(1)) == len(pts)


def test_winding_preserved_by_default():
    """默认保留 IR 原始绕向（真实房间是 CW），不静默翻转。"""
    cw = [[0, 0], [0, 4], [5, 4], [5, 0]]  # signed_area < 0
    assert _signed_area(cw) < 0
    pts = _points(_room_block(ir_to_stf(_one_room_ir(cw)), "R1"))
    assert _signed_area(pts) < 0, "默认不应翻转绕向"


def test_normalize_ccw_flips_cw():
    """normalize_ccw=True → CW 输入翻成 CCW（与 S0 参考文件绕向一致），顶点集合不变。"""
    cw = [[0, 0], [0, 4], [5, 4], [5, 0]]
    pts = _points(_room_block(ir_to_stf(_one_room_ir(cw), normalize_ccw=True), "R1"))
    assert _signed_area(pts) > 0, "--ccw 未生效"
    assert {(p[0], p[1]) for p in pts} == {("0", "0"), ("0", "4"), ("5", "4"), ("5", "0")}


def test_include_furniture_needs_ceil_h(mini_ir):
    """include_furniture=True 但家具无 ceil_h（parser 现状）→ ValueError 指向 --ceil-h。"""
    with pytest.raises(ValueError, match="ceil-h"):
        ir_to_stf(mini_ir, include_furniture=True)


def test_include_furniture_with_default_ceil_h(mini_ir):
    """include_furniture=True + default_ceil_h → 家具也导成房间（逃生口真能用）。"""
    text = ir_to_stf(mini_ir, include_furniture=True, default_ceil_h=2.5)
    assert _kv(text, "NrRooms") == "3"
    assert _kv(_room_block(text, "R3"), "Height") == "2.5"


def test_furniture_skipped_by_id(mini_ir):
    """家具伪 space 改名后仍靠 furniture[].id 兜底识别（无 ceil_h 且无 furniture 键）。"""
    spaces = mini_ir["storeys"][0]["spaces"]
    spaces[2]["name"] = "F1"  # 去掉「家具_」前缀
    text = ir_to_stf(mini_ir)
    assert _kv(text, "NrRooms") == "2", "应通过 furniture[].id 兜底过滤家具"


def test_id_collision_does_not_kill_real_room(mini_ir):
    """id 兜底带门槛：有 ceil_h 的 space 即便 id 撞名 furniture[]，也不能被当家具吃掉。"""
    spaces = mini_ir["storeys"][0]["spaces"]
    spaces[2]["name"] = "F1"
    spaces[2]["ceil_h"] = 2.6  # 变成一个真房间（只是 id 与家具撞名）
    text = ir_to_stf(mini_ir)
    assert _kv(text, "NrRooms") == "3", "有 ceil_h 的 space 不应被 id 兜底误杀"


def test_lums_furns_struct_all_zero(mini_ir):
    """MVP2 范围：NrStruct/NrLums/NrFurns 恒为 0。"""
    text = ir_to_stf(mini_ir)
    assert text.count("NrStruct=0") == 2
    assert text.count("NrLums=0") == 2
    assert text.count("NrFurns=0") == 2


def test_multi_storey_warns(caplog):
    """多楼层：MVP2 只建单层，必须出 WARNING（静默叠房间比报错更糟）。"""
    ir = _one_room_ir([[0, 0], [5, 0], [5, 4], [0, 4]])
    ir["storeys"].append({"level": 2, "elevation": 3.2, "spaces": [
        {"id": "R2", "name": "R2", "polygon": [[0, 0], [5, 0], [5, 4], [0, 4]],
         "ceil_h": 2.8, "luminaires": []},
    ]})
    with caplog.at_level("WARNING"):
        text = ir_to_stf(ir)
    assert _kv(text, "NrRooms") == "2"
    msgs = _log_msgs(caplog)
    assert "2 个楼层" in msgs and "Z=0" in msgs, msgs


def test_space_elevation_warns(caplog):
    """space 级非零 elevation 被忽略（地板固定 Z=0）→ 必须有专属 WARNING。"""
    ir = _one_room_ir([[0, 0], [5, 0], [5, 4], [0, 4]], elevation=3.5)
    with caplog.at_level("WARNING"):
        text = ir_to_stf(ir)
    msgs = _log_msgs(caplog)
    assert "elevation=3.5" in msgs, msgs
    assert "个楼层" not in msgs, "单层 IR 不该出多楼层告警"
    assert all(p[2] == "0" for p in _points(_room_block(text, "R1")))


@pytest.mark.parametrize("value", [0, 0.0, False, True, None, "3.5"])
def test_no_elevation_warning_for_zero_or_non_number(caplog, value):
    """elevation=0 / False / None / 字符串 → 不该告警（bool 守卫也在此覆盖）。"""
    ir = _one_room_ir([[0, 0], [5, 0], [5, 4], [0, 4]], elevation=value)
    with caplog.at_level("WARNING"):
        ir_to_stf(ir)
    assert "elevation" not in _log_msgs(caplog), _log_msgs(caplog)


def test_storey_elevation_warns(caplog):
    """storey 级非零 elevation 同样告警。"""
    ir = _one_room_ir([[0, 0], [5, 0], [5, 4], [0, 4]])
    ir["storeys"][0]["elevation"] = 4.2
    with caplog.at_level("WARNING"):
        ir_to_stf(ir)
    assert "elevation=4.2" in _log_msgs(caplog)


def test_ir_input_not_mutated(mini_ir):
    """生成器不得改动入参 IR（不可变约定）。"""
    import copy

    snapshot = copy.deepcopy(mini_ir)
    ir_to_stf(mini_ir)
    assert mini_ir == snapshot, "ir_to_stf 修改了入参"


def test_s0_reference_parity():
    """AC-MVP2 关键：单个 5×4m/2.8m 房间的输出与 S0 实测导入成功的
    build/test_room_1.stf 逐行同构（差异只在 Progname/Name/Description/Date 的取值）。

    这条断言锁死 STF 骨架——键名、键顺序、`0 0 0` 紧凑数字写法。若有人改动格式，
    必须重新在 DIALux evo 里验证导入，而不是让测试悄悄变绿。
    """
    ir = {
        "schema_version": "0.1",
        "project": {"name": "test_room_1", "source": {"dwg": "x.dxf"}, "units": "m"},
        "storeys": [{"level": 1, "spaces": [{
            "id": "R1", "name": "Test Room 1",
            "polygon": [[0, 0], [5, 0], [5, 4], [0, 4]],
            "ceil_h": 2.8, "luminaires": [],
        }]}],
    }
    text = ir_to_stf(ir, close_polygon=False, date="2026-09-02")
    assert text == (
        "[VERSION]\n"
        "STFF=1.0\n"
        f"Progname={PROG_NAME}\n"
        f"Progvers={PROG_VERS}\n"
        "\n"
        "[PROJECT]\n"
        "Name=test_room_1\n"
        "Date=2026-09-02\n"
        f"Planer={PROG_NAME}\n"
        "Description=\n"
        "NrRooms=1\n"
        "Room1=ROOM.R1\n"
        "\n"
        "[ROOM.R1]\n"
        "Name=Test Room 1\n"
        "Description=R1\n"
        "Height=2.8\n"
        "NrPoints=4\n"
        "Point1=0 0 0\n"
        "Point2=5 0 0\n"
        "Point3=5 4 0\n"
        "Point4=0 4 0\n"
        "NrStruct=0\n"
        "NrLums=0\n"
        "NrFurns=0\n"
    )


@pytest.mark.skipif(not (ROOT / "build" / "test_room_1.stf").exists(),
                    reason="build/ 是运行产物（gitignore），S0 参考文件缺失时跳过")
def test_s0_reference_same_key_skeleton():
    """与 S0 参考文件做结构比对（键序列相同），参考文件被重新生成时能发现。"""
    ref = (ROOT / "build" / "test_room_1.stf").read_text(encoding="utf-8")
    ir = _one_room_ir([[0, 0], [5, 0], [5, 4], [0, 4]])
    mine = ir_to_stf(ir, close_polygon=False, date="2026-09-02")

    def keys(text):
        out = []
        for line in text.splitlines():
            out.append(line if not line or line.startswith("[") else line.split("=", 1)[0])
        return out

    assert keys(mine) == keys(ref), "键序列与 S0 实测通过的文件不一致，改格式必须重新导入验证"


# ---------- 输入校验 ----------

@pytest.mark.parametrize("mutate,msg", [
    (lambda ir: ir.__setitem__("storeys", []), "为空"),
    (lambda ir: ir.pop("storeys"), "缺失"),
    (lambda ir: ir.__setitem__("storeys", "oops"), "不是数组"),
    (lambda ir: ir["storeys"][0].__setitem__("spaces", []), "没有可导出的房间"),
    (lambda ir: ir["storeys"][0].__setitem__("spaces", "oops"), "不是数组"),
    (lambda ir: ir["storeys"][0]["spaces"].__setitem__(0, "oops"), "不是对象"),
    (lambda ir: ir.__setitem__("project", "oops"), "IR.project 必须是对象"),
    (lambda ir: ir["storeys"][0]["spaces"][0].__setitem__("polygon", [[0, 0], [1, 1]]),
     "无法生成房间"),
    (lambda ir: ir["storeys"][0]["spaces"][0].__setitem__("polygon", "oops"), "不是数组"),
    (lambda ir: ir["storeys"][0]["spaces"][0].__setitem__("polygon", [[0, 0], [1], [2, 2]]),
     "不是 \\[x, y\\] 坐标"),
    (lambda ir: ir["storeys"][0]["spaces"][0].__setitem__(
        "polygon", [[0, 0], ["a", 1], [2, 2]]), "坐标不是数字"),
    (lambda ir: ir["storeys"][0]["spaces"][0].__setitem__(
        "polygon", [[0, 0], [float("nan"), 1], [2, 2]]), "不是有限数"),
    (lambda ir: ir["storeys"][0]["spaces"][0].__setitem__(
        "polygon", [[0, 0], [float("inf"), 1], [2, 2]]), "不是有限数"),
    (lambda ir: ir["storeys"][0]["spaces"][0].__setitem__(
        "polygon", [[0, 0], [1, 0], [2, 0]]), "多边形退化"),
    (lambda ir: ir["storeys"][0]["spaces"][0].pop("ceil_h"), "缺少 ceil_h"),
    (lambda ir: ir["storeys"][0]["spaces"][0].__setitem__("ceil_h", 0), "必须 > 0"),
    (lambda ir: ir["storeys"][0]["spaces"][0].__setitem__("ceil_h", -2.8), "必须 > 0"),
    (lambda ir: ir["storeys"][0]["spaces"][0].__setitem__("ceil_h", True), "不能是布尔值"),
    (lambda ir: ir["storeys"][0]["spaces"][0].__setitem__("ceil_h", "高"), "不是数字"),
    (lambda ir: ir["storeys"][0]["spaces"][0].__setitem__("ceil_h", float("nan")), "不是有限数"),
    (lambda ir: ir["storeys"][0]["spaces"][0].__setitem__("ceil_h", float("inf")), "不是有限数"),
])
def test_invalid_ir_raises_value_error(mini_ir, mutate, msg):
    """TR-MVP2.5：违规输入抛 ValueError（不是 TypeError/KeyError/AttributeError），消息带中文定位。"""
    mutate(mini_ir)
    with pytest.raises(ValueError) as exc:
        ir_to_stf(mini_ir)
    if msg:
        assert re.search(msg, str(exc.value)), str(exc.value)


def test_unhashable_space_id_does_not_crash(mini_ir):
    """不可哈希 id 走到 furniture[] 兜底查找也不能炸成 TypeError。

    必须落在真实分支上：无 ceil_h + 无 furniture 键（否则 is_furniture_space 提前短路，
    `str(sid)` 的保护根本不被执行，测试就成了假绿）。
    """
    from optiflow.adapters.dialux.stf import is_furniture_space

    space = {"id": ["not", "hashable"], "name": "x",
             "polygon": [[0, 0], [1, 0], [1, 1]], "luminaires": []}
    assert is_furniture_space(space, {"F1"}) is False  # 不抛 TypeError

    # 端到端：把不可哈希 id 塞进一个没有 ceil_h 的 space（靠 --ceil-h 兜底导出）
    spaces = mini_ir["storeys"][0]["spaces"]
    spaces.append({"id": ["not", "hashable"], "name": "怪房间",
                   "polygon": [[20, 0], [23, 0], [23, 3], [20, 3]], "luminaires": []})
    text = ir_to_stf(mini_ir, default_ceil_h=2.5)
    assert _kv(text, "NrRooms") == "3"


def test_unhashable_ids_do_not_crash(mini_ir):
    """furniture[].id 是 list/dict 时不能炸成 TypeError（unhashable）。"""
    spaces = mini_ir["storeys"][0]["spaces"]
    spaces[0]["furniture"] = [{"id": ["a", "b"]}, {"id": {"k": 1}}]
    text = ir_to_stf(mini_ir)  # 不抛异常即通过
    assert _kv(text, "NrRooms") == "2"


def test_non_dict_ir_raises():
    with pytest.raises(ValueError, match="必须是 JSON 对象"):
        ir_to_stf(["not", "a", "dict"])  # type: ignore[arg-type]


def test_all_spaces_furniture_raises(mini_ir):
    """全是家具伪 space → ValueError 提示可用 --include-furniture --ceil-h。"""
    spaces = mini_ir["storeys"][0]["spaces"]
    mini_ir["storeys"][0]["spaces"] = [s for s in spaces if s["id"] == "F1"]
    with pytest.raises(ValueError, match="include-furniture"):
        ir_to_stf(mini_ir)


def test_small_area_warns(caplog):
    """面积 < 0.5 m²（validator 规则 2 下限）→ WARNING，但仍生成（可能是真小房间）。"""
    ir = _one_room_ir([[0, 0], [0.5, 0], [0.5, 0.5], [0, 0.5]])
    with caplog.at_level("WARNING"):
        text = ir_to_stf(ir)
    assert _kv(text, "NrRooms") == "1"
    msgs = _log_msgs(caplog)
    assert "面积" in msgs, msgs


def test_name_newline_cannot_forge_stf_lines():
    """房间名里的换行必须被折叠成单行，否则能伪造出多余 STF 行。"""
    ir = _one_room_ir([[0, 0], [5, 0], [5, 4], [0, 4]])
    ir["storeys"][0]["spaces"][0]["name"] = "A\nNrLums=9\nB"
    text = ir_to_stf(ir)
    # 关键：不能出现「行首就是 NrLums=9」的伪造行；名字里的字面内容被折进 Name= 一行是可以的
    assert not re.search(r"^NrLums=9$", text, flags=re.MULTILINE), text
    assert text.count("NrLums=0") == 1
    assert _kv(_room_block(text, "R1"), "Name") == "A NrLums=9 B"


# ---------- 写文件 / CLI ----------

def test_write_stf_lf_newlines(tmp_path: Path, mini_ir):
    """写出的文件用 LF 换行（与 S0 验证文件一致），且以换行结尾。"""
    out = write_stf(mini_ir, tmp_path / "out.stf")
    raw = out.read_bytes()
    assert b"\r\n" not in raw, "STF 不应含 CRLF"
    assert raw.endswith(b"\n")
    assert raw.decode("utf-8").startswith("[VERSION]\n")


def test_write_stf_project_name_fallback(tmp_path: Path, mini_ir):
    """project.name 为空 → 退回输出文件名（对齐 test_room_1.stf 的 Name=test_room_1）。"""
    mini_ir["project"]["name"] = ""
    out = write_stf(mini_ir, tmp_path / "mvp2_out.stf")
    assert _kv(out.read_text(encoding="utf-8"), "Name") == "mvp2_out"


def test_ir_to_stf_project_name_default(mini_ir):
    """直接调 ir_to_stf 且 project.name 为空 → DEFAULT_PROJECT_NAME（无文件名上下文）。"""
    mini_ir["project"].pop("name")
    assert _kv(ir_to_stf(mini_ir), "Name") == "dialux-project"


def _run_cli(*args, cwd=ROOT):
    return subprocess.run([sys.executable, "-m", "optiflow.adapters.dialux.stf", *args],
                          cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def test_cli_roundtrip(tmp_path: Path):
    """TR-MVP2.6：python -m optiflow.adapters.dialux.stf <ir.json> <out.stf> 退出码 0 且产出文件。"""
    out = tmp_path / "cli.stf"
    r = _run_cli(str(FIXTURE), str(out))
    assert r.returncode == 0, f"stderr:\n{r.stderr}"
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "[ROOM.R1]" in text and _kv(text, "NrRooms") == "2"


def test_cli_ceil_h_and_include_furniture(tmp_path: Path):
    """--include-furniture --ceil-h 2.5 → 家具也导出（逃生口在 CLI 上真能走通）。"""
    out = tmp_path / "furn.stf"
    r = _run_cli("--include-furniture", "--ceil-h", "2.5", str(FIXTURE), str(out))
    assert r.returncode == 0, r.stderr
    assert _kv(out.read_text(encoding="utf-8"), "NrRooms") == "3"


def test_cli_bad_ceil_h_exits_2(tmp_path: Path):
    r = _run_cli("--ceil-h", "0", str(FIXTURE), str(tmp_path / "x.stf"))
    assert r.returncode == 2, r.stdout + r.stderr


def test_cli_bad_ir_exits_2(tmp_path: Path):
    """IR 非法 → CLI 退出码 2，不产出半成品文件。"""
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": "0.1", "storeys": []}), encoding="utf-8")
    out = tmp_path / "bad.stf"
    r = _run_cli(str(bad), str(out))
    assert r.returncode == 2, r.stdout + r.stderr
    assert not out.exists()


def test_cli_missing_ir_exits_2(tmp_path: Path):
    r = _run_cli(str(tmp_path / "nope.json"), str(tmp_path / "x.stf"))
    assert r.returncode == 2


def test_cli_bom_json_ok(tmp_path: Path):
    """记事本/PowerShell 存出来的 UTF-8 BOM 也要能读（utf-8-sig）。"""
    src = tmp_path / "bom.json"
    src.write_bytes(b"\xef\xbb\xbf" + FIXTURE.read_bytes())
    out = tmp_path / "bom.stf"
    r = _run_cli(str(src), str(out))
    assert r.returncode == 0, r.stdout + r.stderr
    assert out.exists()


def test_cli_gbk_json_exits_2(tmp_path: Path):
    """GBK 编码的 IR → 退出码 2（而不是 UnicodeDecodeError 裸栈 exit 1）。"""
    src = tmp_path / "gbk.json"
    src.write_bytes(json.dumps({"project": {"name": "中文"}, "storeys": []},
                               ensure_ascii=False).encode("gbk"))
    r = _run_cli(str(src), str(tmp_path / "gbk.stf"))
    assert r.returncode == 2, r.stdout + r.stderr


def test_cli_output_path_is_dir_exits_2(tmp_path: Path):
    """输出路径是已存在目录 → 退出码 2（而不是 PermissionError/IsADirectoryError 裸栈）。"""
    d = tmp_path / "adir"
    d.mkdir()
    r = _run_cli(str(FIXTURE), str(d))
    assert r.returncode == 2, r.stdout + r.stderr


def test_cli_validate_real_ir_passes(tmp_path: Path):
    """--validate 对「过滤后的房间集」跑 src.validator：mini fixture 应零 ERROR/HALT。"""
    out = tmp_path / "val.stf"
    r = _run_cli("--validate", str(FIXTURE), str(out))
    assert r.returncode == 0, r.stdout + r.stderr
    assert out.exists()


def test_cli_validate_allows_generator_closing(tmp_path: Path):
    """--validate 校验的是「归一化后的几何」：IR 未闭合、由生成器补首点，不该被 POLY_NOT_CLOSED 误拦。"""
    ir = _one_room_ir([[0, 0], [5, 0], [5, 4], [0, 4]])  # 未闭合
    src = tmp_path / "unclosed.json"
    src.write_text(json.dumps(ir, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "unclosed.stf"
    r = _run_cli("--validate", str(src), str(out))
    assert r.returncode == 0, r.stdout + r.stderr
    assert out.exists()


def test_cli_validate_allows_luminaire_issues(tmp_path: Path):
    """--validate 只管几何：灯具越界 / catalog_match=False 不该挡住空房间导出（MVP2 NrLums=0）。"""
    ir = _one_room_ir([[0, 0], [5, 0], [5, 4], [0, 4]])
    ir["storeys"][0]["spaces"][0]["luminaires"] = [
        {"symbol": "L1", "x": 99.0, "y": 99.0, "z": 9.9, "catalog_match": False,
         "mount": "recessed"},
    ]
    src = tmp_path / "lum_issues.json"
    src.write_text(json.dumps(ir, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "lum_issues.stf"
    r = _run_cli("--validate", str(src), str(out))
    assert r.returncode == 0, r.stdout + r.stderr
    combined = r.stdout + r.stderr
    for code in ("LUM_OUTSIDE_SPACE", "CATALOG_MATCH_UNSET", "LUM_RECESSED_Z_OVER_CEIL"):
        assert code not in combined, f"MVP2 不导灯具，不该报 {code}：{combined}"
    # 灯具已导出（含越界/未匹配），STF 包含灯具数据
    stf_content = out.read_text(encoding="utf-8")
    assert out.exists()
    nr_lums = [m.group(1) for m in __import__('re').finditer(r'NrLums=(\d+)', stf_content)]
    assert int(nr_lums[-1]) >= 1, f"灯具段应有 NrLums>=1，实际 {nr_lums}" 


def test_cli_validate_blocks_schema_violation(tmp_path: Path):
    """--validate 会拦下 schema 违规（space 缺 id）→ exit 2；不加 --validate 仍能导出。"""
    ir = _one_room_ir([[0, 0], [5, 0], [5, 4], [0, 4]])
    ir["storeys"][0]["spaces"][0].pop("id")
    src = tmp_path / "no_id.json"
    src.write_text(json.dumps(ir, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "no_id.stf"
    r = _run_cli("--validate", str(src), str(out))
    assert r.returncode == 2, r.stdout + r.stderr
    assert "JSONSCHEMA" in (r.stdout + r.stderr)
    assert not out.exists()
    r2 = _run_cli(str(src), str(out))
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert out.exists()


# ---------- 真实 IR ----------

@pytest.mark.skipif(not REAL_IR.exists(),
                    reason="build/room_layout.json 是运行产物（gitignore），未生成时跳过")
def test_real_room_layout_generates(tmp_path: Path):
    """AC-MVP2：真实 IR（1 房间 + 22 家具伪 space）全量生成不报错，只出 1 个房间。"""
    ir = json.loads(REAL_IR.read_text(encoding="utf-8"))
    out = write_stf(ir, tmp_path / "mvp2_out.stf")
    text = out.read_text(encoding="utf-8")
    n_rooms = int(_kv(text, "NrRooms"))
    assert n_rooms == ir["_meta"]["rooms"] == 1, "家具伪 space 泄漏成房间了"
    assert "[ROOM.R1]" in text and "[ROOM.R2]" not in text
    r1 = _room_block(text, "R1")
    pts = _points(r1)
    assert len(pts) >= 4 and pts[0] == pts[-1]
    assert int(re.search(r"NrPoints=(\d+)", r1).group(1)) == len(pts)
    assert _kv(r1, "Height") == "2.8"
    # 灯具段已导出：检查 NrLums 匹配 _meta.luminaires
    assert "NrLums=28" in text, f"灯具段应有 NrLums=28：{text.split(chr(10))[-10:]}"
    # 真实数据里有 -1.42e-16 这种坐标：不能出现 "-0"
    assert not any(v == "-0" for p in pts for v in p), "负零泄漏到真实输出"
    # 默认保留 IR 原始绕向（不静默翻转）——期望符号从 IR 自身推，
    # 免得 parser 换了房间环（如 t_0deee369 修锯齿后主房间由 CW 变 CCW）就假红。
    room = max((s for s in ir["storeys"][0]["spaces"]
                if not s["name"].startswith("家具_")),
               key=lambda s: abs(_signed_area(s["polygon"])))
    ir_sign = _signed_area(room["polygon"])
    assert ir_sign != 0
    assert _signed_area(pts) * ir_sign > 0, "exporter 静默翻转了绕向"
