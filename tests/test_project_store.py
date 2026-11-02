"""项目文件契约：一个项目一个文件、原子写、版本不静默升级。

三条是这个模块存在的理由，所以都要钉：

1. **分节**：原始输入（`inputs`）与派生（`derived`）在文件里物理分开。
   写派生不许碰原始输入，否则重算一次就把原始证据改没了。
2. **原子写**：替换失败时必须留着一份能打开的旧文件，不能留半截新文件。
3. **不静默升级**：版本对不上就报错并列三条出路。自动迁移会把「我还读得懂」
   这个假象留到更后面才爆。
"""
from __future__ import annotations

import json
import os

import pytest

from optiflow import project_store
from optiflow.ir import Geometry, Metric, Point, Space, TaskSpec
from optiflow.job import ResultSet
from optiflow.project import Project
from optiflow.project_store import ProjectFileError


def _task(kind: str = "layout") -> TaskSpec:
    outline = [Point(x=0, y=0), Point(x=10, y=0), Point(x=10, y=8), Point(x=0, y=8)]
    return TaskSpec(
        kind=kind,
        spaces=[Space(id="r1", name="测试房间",
                      geometry=Geometry(kind="room", outline=outline, height=3.0))],
        constraints=[Metric(name="illuminance_avg", target=500.0, unit="lx")],
        extra={"flux": 3000.0},
    )


def _project() -> Project:
    project = Project(id="p1", name="测试项目",
                      inputs=[_task(), _task("calculate")])
    project.results.append(ResultSet(
        job_id="j1",
        metrics=[Metric(name="illuminance_avg", value=511.4, unit="lx", target=500.0)],
    ))
    project.add_version(note="首版", artifacts={"plan": "/tmp/plan.json"},
                        metrics_summary={"illuminance_avg": 511.4})
    return project


def _doc(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 往返

def test_roundtrip_preserves_every_field(tmp_path) -> None:
    src = _project()
    path = project_store.project_path(tmp_path, "p1")
    project_store.save(src, path)
    back = project_store.load(path)

    assert back.id == src.id
    assert back.name == src.name
    assert back.created_at == src.created_at, "created_at 序列化往返丢了精度或时区"
    assert [t.kind for t in back.inputs] == ["layout", "calculate"]
    assert back.inputs[0].extra == src.inputs[0].extra
    assert back.inputs[0].spaces[0].geometry.height == 3.0
    assert back.artifacts == src.artifacts
    assert len(back.results) == 1
    assert back.results[0].metrics[0].value == 511.4
    assert len(back.versions) == 1
    assert back.versions[0].metrics_summary == {"illuminance_avg": 511.4}


def test_file_has_header_and_separate_sections(tmp_path) -> None:
    path = project_store.project_path(tmp_path, "p1")
    project_store.save(_project(), path)
    doc = _doc(path)

    assert set(doc) == {"schema_version", "saved_at", "project", "inputs", "derived"}
    assert doc["schema_version"] == project_store.SCHEMA_VERSION
    assert isinstance(doc["saved_at"], str) and doc["saved_at"]
    assert set(doc["project"]) == {"id", "name", "created_at"}
    assert isinstance(doc["inputs"], list) and len(doc["inputs"]) == 2
    assert set(doc["derived"]) == {"artifacts", "results", "versions"}
    # 派生不许混进 project 头
    assert "results" not in doc["project"]


def test_derived_write_does_not_touch_inputs(tmp_path) -> None:
    """重算只该改 derived 节。inputs 一个字节都不能变。"""
    project = _project()
    path = project_store.project_path(tmp_path, "p1")
    project_store.save(project, path)
    before = _doc(path)["inputs"]

    project.results.append(ResultSet(job_id="j2"))
    project.add_version(note="重算一版")
    project_store.save(project, path)

    after = _doc(path)
    assert after["inputs"] == before, "重算把原始输入改了"
    assert len(after["derived"]["versions"]) == 2


# ---------------------------------------------------------------- 原子写

def test_atomic_write_keeps_good_file_when_replace_fails(tmp_path, monkeypatch) -> None:
    path = project_store.project_path(tmp_path, "p1")
    project_store.save(_project(), path)
    good = path.read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("模拟替换失败")

    monkeypatch.setattr(os, "replace", boom)
    project = _project()
    project.name = "改过的名字"
    with pytest.raises(OSError):
        project_store.save(project, path)

    assert path.read_text(encoding="utf-8") == good, "好文件被半截写坏了"
    assert not list(path.parent.glob("*.tmp")), ".tmp 没清掉"


# ---------------------------------------------------------------- 版本

def _rewrite(path, **changes) -> None:
    doc = _doc(path)
    doc.update(changes)
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def test_old_version_rejected_and_lists_options(tmp_path) -> None:
    path = project_store.project_path(tmp_path, "p1")
    project_store.save(_project(), path)
    _rewrite(path, schema_version=0)

    with pytest.raises(ProjectFileError) as excinfo:
        project_store.load(path)
    err = excinfo.value
    assert err.code == "too_old"
    assert "没有自动升级" in err.hint
    assert "三条出路" in err.hint


def test_new_version_rejected(tmp_path) -> None:
    path = project_store.project_path(tmp_path, "p1")
    project_store.save(_project(), path)
    _rewrite(path, schema_version=99)

    with pytest.raises(ProjectFileError) as excinfo:
        project_store.load(path)
    assert excinfo.value.code == "too_new"
    assert "高于当前应用" in str(excinfo.value)


def test_missing_section_is_corrupt(tmp_path) -> None:
    path = project_store.project_path(tmp_path, "p1")
    project_store.save(_project(), path)
    doc = _doc(path)
    del doc["derived"]
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ProjectFileError) as excinfo:
        project_store.load(path)
    assert excinfo.value.code == "corrupt"
    assert "缺节" in str(excinfo.value)


def test_missing_file_is_reported_not_created(tmp_path) -> None:
    path = project_store.project_path(tmp_path, "不存在")
    with pytest.raises(ProjectFileError) as excinfo:
        project_store.load(path)
    assert excinfo.value.code == "missing"
    assert not path.exists(), "读操作不该顺手造一个空文件出来"


# ---------------------------------------------------------------- 路径

@pytest.mark.parametrize("bad", ["a/b", "a\\b", "c:", "a*b", "a?b", "  "])
def test_project_name_rejects_path_characters(tmp_path, bad) -> None:
    with pytest.raises(ProjectFileError):
        project_store.project_path(tmp_path, bad)


def test_project_path_lives_under_projects_dir(tmp_path) -> None:
    path = project_store.project_path(tmp_path, "208")
    assert path.parent.name == "projects"
    assert path.name == f"208{project_store.SUFFIX}"
    assert path.is_relative_to(tmp_path)
