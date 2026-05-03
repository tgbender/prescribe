"""Tests for prescribe status --diff feature (library tests only)."""

from __future__ import annotations

from pathlib import Path

# ── library: diff generation ────────────────────────────


def test_diff_detects_changed_key(tmp_path: Path):
    """A changed key produces a unified diff."""
    from prescribe.adapters.toml import TomlAdapter
    from prescribe.core.planner import DesiredState, Planner
    from prescribe.diff import diff_file

    config = tmp_path / "config.toml"
    config.write_text("count = 1\n")

    adapter = TomlAdapter()
    document = adapter.load(config)
    desired = DesiredState(path=config, format="toml", data={"count": 2})
    plan = Planner().plan(document, desired)

    result = diff_file(
        document=document,
        plan=plan.operations,
        adapter=adapter,
        path=config,
    )
    assert result is not None
    assert "-count = 1" in result
    assert "+count = 2" in result


def test_diff_no_changes_returns_none(tmp_path: Path):
    """When the file is in sync, diff returns None."""
    from prescribe.adapters.toml import TomlAdapter
    from prescribe.core.planner import DesiredState, Planner
    from prescribe.diff import diff_file

    config = tmp_path / "config.toml"
    config.write_text("count = 2\n")

    adapter = TomlAdapter()
    document = adapter.load(config)
    desired = DesiredState(path=config, format="toml", data={"count": 2})
    plan = Planner().plan(document, desired)

    result = diff_file(
        document=document,
        plan=plan.operations,
        adapter=adapter,
        path=config,
    )
    assert result is None


def test_diff_new_file(tmp_path: Path):
    """A file that doesn't exist yet shows full creation diff."""
    from prescribe.adapters.toml import TomlAdapter
    from prescribe.core.planner import DesiredState, Planner
    from prescribe.diff import diff_new_file
    from prescribe.document import Document

    config = tmp_path / "new.toml"

    adapter = TomlAdapter()
    document = Document(path=config, format="toml", root={})
    desired = DesiredState(path=config, format="toml", data={"key": "value"})
    plan = Planner().plan(document, desired)

    result = diff_new_file(plan=plan.operations, adapter=adapter, path=config)
    assert result is not None
    assert "+key" in result


# ── orchestrator integration ────────────────────────────


def test_orchestrator_status_with_diff(fake_root, state_store):
    """Orchestrator.run(dry_run=True, diff=True) returns diffs on results."""
    from prescribe.orchestrator import Orchestrator

    config_toml = fake_root / "config.toml"
    config_toml.write_text("count = 1\n")

    spec_path = fake_root / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    orch = Orchestrator(state_store)
    results = orch.run(spec_path, dry_run=True, diff=True)
    assert len(results) == 1
    assert results[0].diff is not None
    assert "+count = 2" in results[0].diff
