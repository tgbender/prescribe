"""Tests for FileTarget priority field and same-path resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from prescribe.spec import FileTarget, Spec, SpecError, SpecLoader

# ── Spec validation ──────────────────────────────────────


def test_file_target_has_default_priority():
    """FileTarget.priority defaults to 0."""
    ft = FileTarget(path=Path("config.toml"), format="toml")
    assert ft.priority == 0


def test_same_path_different_priority_allowed(tmp_path: Path):
    """Same path, same conditions, different priority → valid (no overlap error)."""
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[files]]\n"
        "path = 'config.toml'\n"
        "format = 'toml'\n"
        "tags = ['base']\n"
        "[files.data]\n"
        "count = 0\n"
        "\n"
        "[[files]]\n"
        "path = 'config.toml'\n"
        "format = 'toml'\n"
        "priority = 10\n"
        "tags = ['work']\n"
        "[files.data]\n"
        "count = 10\n"
    )
    spec = SpecLoader().load(spec_path)
    assert len(spec.files) == 2


def test_same_path_same_priority_rejected(tmp_path: Path):
    """Same path, same conditions, same priority → error."""
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[files]]\n"
        "path = 'config.toml'\n"
        "format = 'toml'\n"
        "[files.data]\n"
        "count = 0\n"
        "\n"
        "[[files]]\n"
        "path = 'config.toml'\n"
        "format = 'toml'\n"
        "[files.data]\n"
        "count = 10\n"
    )
    with pytest.raises(SpecError, match="overlaps"):
        SpecLoader().load(spec_path)


def test_same_path_same_priority_different_arch_allowed(tmp_path: Path):
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[files]]\n"
        "path = 'config.toml'\n"
        "format = 'toml'\n"
        "arch = ['x86_64']\n"
        "[files.data]\n"
        "count = 0\n"
        "\n"
        "[[files]]\n"
        "path = 'config.toml'\n"
        "format = 'toml'\n"
        "arch = ['arm64']\n"
        "[files.data]\n"
        "count = 10\n"
    )

    spec = SpecLoader().load(spec_path)

    assert [target.arch for target in spec.files] == [["x86_64"], ["arm64"]]


# ── Orchestrator resolution ──────────────────────────────


def test_lower_priority_wins_at_apply(fake_root, state_store):
    """When two entries target the same file, lower priority wins."""
    from prescribe.orchestrator import Orchestrator

    config = fake_root / "config.toml"
    config.write_text("count = 0\n")

    spec = Spec(
        id="priority-test",
        path=fake_root,
        files=[
            FileTarget(
                path=config,
                format="toml",
                priority=10,
                data={"count": 10},
            ),
            FileTarget(
                path=config,
                format="toml",
                priority=0,
                data={"count": 0, "name": "low"},
            ),
        ],
    )

    orch = Orchestrator(state_store)
    results = orch.run(spec=spec)
    applied = [r for r in results if r.status == "applied"]
    assert len(applied) == 1
    content = config.read_text()
    assert "count = 0" in content
    assert 'name = "low"' in content


def test_priority_with_tags_resolution(fake_root, state_store):
    """When tags filter out lower-priority entry, higher-priority entry applies."""
    from prescribe.orchestrator import Orchestrator

    config = fake_root / "config.toml"
    config.write_text("count = 0\n")

    spec = Spec(
        id="priority-tags-test",
        path=fake_root,
        files=[
            FileTarget(
                path=config,
                format="toml",
                priority=0,
                data={"count": 0, "name": "base"},
            ),
            FileTarget(
                path=config,
                format="toml",
                priority=10,
                tags=["work"],
                data={"count": 10, "name": "work"},
            ),
        ],
    )

    orch = Orchestrator(state_store)

    # With --tags work, only the work entry is active
    results = orch.run(spec=spec, tags={"work"})
    applied = [r for r in results if r.status == "applied"]
    assert len(applied) == 1
    content = config.read_text()
    assert "count = 10" in content
    assert 'name = "work"' in content
