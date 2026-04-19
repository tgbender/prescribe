from __future__ import annotations

from pathlib import Path

from prescribe.adapters.toml import TomlAdapter
from prescribe.adapters.yaml import YamlAdapter
from prescribe.core import DesiredState, Planner
from prescribe.core.apply import apply_operations


def test_planner_detects_nested_set_and_update(tmp_path: Path) -> None:
    source = tmp_path / "config.toml"
    source.write_text("title = 'hello'\n[tool]\n[tool.demo]\ncount = 1\n")

    document = TomlAdapter().load(source)
    plan = Planner().plan(
        document,
        DesiredState(
            path=source,
            format="toml",
            data={"tool": {"demo": {"count": 2, "extra": True}}},
        ),
    )

    assert plan.changed is True
    by_key = {op.key: op for op in plan.operations}
    assert by_key["tool.demo.count"].kind == "update"
    assert by_key["tool.demo.extra"].kind == "set"


def test_apply_nested_operations(tmp_path: Path) -> None:
    source = tmp_path / "config.toml"
    source.write_text("title = 'hello'\n[tool]\n[tool.demo]\ncount = 1\n")

    document = TomlAdapter().load(source)
    plan = Planner().plan(
        document,
        DesiredState(
            path=source,
            format="toml",
            data={"tool": {"demo": {"count": 2, "extra": True}}},
        ),
    )

    apply_operations(document, plan.operations)
    assert document.root["tool"]["demo"]["count"] == 2
    assert document.root["tool"]["demo"]["extra"] is True


def test_planner_no_changes_when_nested_matches(tmp_path: Path) -> None:
    source = tmp_path / "config.toml"
    source.write_text("title = 'hello'\n[tool]\n[tool.demo]\ncount = 1\n")

    document = TomlAdapter().load(source)
    plan = Planner().plan(
        document,
        DesiredState(path=source, format="toml", data={"tool": {"demo": {"count": 1}}}),
    )

    assert plan.changed is False
    assert plan.operations == []


def test_apply_creates_intermediate_dicts(tmp_path: Path) -> None:
    source = tmp_path / "config.toml"
    source.write_text("title = 'hello'\n")

    document = TomlAdapter().load(source)
    plan = Planner().plan(
        document,
        DesiredState(
            path=source, format="toml", data={"tool": {"new_section": {"key": "val"}}}
        ),
    )

    assert plan.changed is True
    apply_operations(document, plan.operations)
    assert document.root["tool"]["new_section"]["key"] == "val"


def test_nested_yaml_planning(tmp_path: Path) -> None:
    source = tmp_path / "config.yaml"
    source.write_text("title: hello\ntool:\n  demo:\n    count: 1\n")

    document = YamlAdapter().load(source)
    plan = Planner().plan(
        document,
        DesiredState(path=source, format="yaml", data={"tool": {"demo": {"count": 2}}}),
    )

    assert plan.changed is True
    assert len(plan.operations) == 1
    assert plan.operations[0].key == "tool.demo.count"

    apply_operations(document, plan.operations)
    assert document.root["tool"]["demo"]["count"] == 2


def test_planner_generates_delete_ops(tmp_path: Path) -> None:
    source = tmp_path / "config.toml"
    source.write_text("title = 'hello'\ncount = 1\nextra = true\n")

    document = TomlAdapter().load(source)
    plan = Planner().plan(
        document,
        DesiredState(path=source, format="toml", delete=["extra"]),
    )

    assert plan.changed is True
    assert len(plan.operations) == 1
    assert plan.operations[0].kind == "delete"
    assert plan.operations[0].key == "extra"


def test_apply_delete_operations(tmp_path: Path) -> None:
    source = tmp_path / "config.toml"
    source.write_text("title = 'hello'\ncount = 1\nextra = true\n")

    document = TomlAdapter().load(source)
    plan = Planner().plan(
        document,
        DesiredState(path=source, format="toml", delete=["extra"]),
    )

    apply_operations(document, plan.operations)
    assert "extra" not in document.root
    assert document.root["count"] == 1


def test_planner_delete_nested_key(tmp_path: Path) -> None:
    source = tmp_path / "config.toml"
    source.write_text("title = 'hello'\n[tool]\n[tool.demo]\ncount = 1\nextra = true\n")

    document = TomlAdapter().load(source)
    plan = Planner().plan(
        document,
        DesiredState(path=source, format="toml", delete=["tool.demo.extra"]),
    )

    assert plan.changed is True
    assert plan.operations[0].kind == "delete"
    assert plan.operations[0].key == "tool.demo.extra"

    apply_operations(document, plan.operations)
    assert "extra" not in document.root["tool"]["demo"]
    assert document.root["tool"]["demo"]["count"] == 1


def test_planner_delete_nonexistent_key_is_noop(tmp_path: Path) -> None:
    source = tmp_path / "config.toml"
    source.write_text("title = 'hello'\n")

    document = TomlAdapter().load(source)
    plan = Planner().plan(
        document,
        DesiredState(path=source, format="toml", delete=["nonexistent"]),
    )

    assert plan.changed is False
    assert plan.operations == []


def test_set_update_and_delete_combined(tmp_path: Path) -> None:
    source = tmp_path / "config.toml"
    source.write_text("title = 'hello'\ncount = 1\nextra = true\n")

    document = TomlAdapter().load(source)
    plan = Planner().plan(
        document,
        DesiredState(
            path=source,
            format="toml",
            data={"count": 2, "new_key": "val"},
            delete=["extra"],
        ),
    )

    apply_operations(document, plan.operations)
    assert document.root["count"] == 2
    assert document.root["new_key"] == "val"
    assert "extra" not in document.root
