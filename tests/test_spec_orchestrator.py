from __future__ import annotations

import re
from pathlib import Path

from prescribe.adapters.toml import TomlAdapter
from prescribe.core import Planner
from prescribe.orchestrator import (
    Orchestrator,
    current_machine,
    machine_matches,
    platform_matches,
)
from prescribe.spec import SpecLoader


def test_platform_matches_empty_list_is_always_true() -> None:
    assert platform_matches([]) is True


def test_platform_matches_current_platform() -> None:
    import sys

    if sys.platform == "darwin":
        assert platform_matches(["macos"]) is True
        assert platform_matches(["linux"]) is False
    elif sys.platform.startswith("linux"):
        assert platform_matches(["linux"]) is True
        assert platform_matches(["macos"]) is False
    elif sys.platform == "win32":
        assert platform_matches(["windows"]) is True
        assert platform_matches(["macos"]) is False


def test_platform_matches_any_selector() -> None:
    assert platform_matches(["linux", "macos"]) is True


def test_machine_matches_current_machine() -> None:
    assert machine_matches([current_machine()]) is True
    assert machine_matches(["not-the-current-machine"]) is False


def test_orchestrator_skips_non_matching_platform(make_text_file, state_store) -> None:
    config_toml = make_text_file("config.toml", "title = 'hello'\ncount = 1\n")

    spec_path = make_text_file(
        "spec.toml",
        "[[files]]\npath = 'config.toml'\nformat = 'toml'\nplatforms = ['windows']\n[files.data]\ncount = 2\n",
    )

    store = state_store
    orchestrator = Orchestrator(store)

    results = orchestrator.run(spec_path)
    assert results[0].skipped is True
    assert results[0].applied is False
    assert results[0].changed is False
    assert config_toml.read_text() == "title = 'hello'\ncount = 1\n"


def test_orchestrator_applies_matching_platform(tmp_path: Path, state_store) -> None:
    import sys

    current = "macos" if sys.platform == "darwin" else "linux" if sys.platform.startswith("linux") else "windows"
    config_toml = tmp_path / "config.toml"
    config_toml.write_text("title = 'hello'\ncount = 1\n")

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        f"[[files]]\npath = 'config.toml'\nformat = 'toml'\nplatforms = ['{current}']\n[files.data]\ncount = 2\n"
    )

    store = state_store
    orchestrator = Orchestrator(store)

    results = orchestrator.run(spec_path)
    assert results[0].status == "applied"
    assert results[0].skipped is False
    assert results[0].applied is True


def test_orchestrator_skips_non_matching_machine(tmp_path: Path, monkeypatch, state_store) -> None:
    config_toml = tmp_path / "config.toml"
    config_toml.write_text("title = 'hello'\ncount = 1\n")

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[files]]\npath = 'config.toml'\nformat = 'toml'\nmachine = ['build-host']\n[files.data]\ncount = 2\n"
    )

    monkeypatch.setenv("PRESCRIBE_MACHINE", "other-host")

    store = state_store
    orchestrator = Orchestrator(store)

    results = orchestrator.run(spec_path)
    assert results[0].skipped is True
    assert results[0].applied is False
    assert results[0].changed is False
    assert config_toml.read_text() == "title = 'hello'\ncount = 1\n"


def test_spec_loader_and_orchestrator_apply_and_then_noop(tmp_path: Path, state_store) -> None:
    config_toml = tmp_path / "config.toml"
    config_toml.write_text("title = 'hello'\ncount = 1\n")

    env_file = tmp_path / ".env"
    env_file.write_text("# prescribe:begin managed\nalpha=1\n# prescribe:end managed\n")

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[files]]\n"
        "path = 'config.toml'\n"
        "format = 'toml'\n"
        "[files.data]\n"
        "count = 2\n"
        "extra = true\n"
        "\n"
        "[[files]]\n"
        "path = '.env'\n"
        "format = 'line'\n"
        "managed_block_id = 'managed'\n"
        "lines = ['alpha=1', 'beta=2']\n"
    )

    spec = SpecLoader().load(spec_path)
    assert len(spec.files) == 2
    assert spec.files[0].path == config_toml.resolve()
    assert spec.files[1].path == env_file.resolve()

    store = state_store
    orchestrator = Orchestrator(store)

    first = orchestrator.run(spec_path, tool_version="test")
    assert [result.status for result in first] == ["applied", "applied"]
    assert [result.applied for result in first] == [True, True]
    assert config_toml.read_text() == "title = 'hello'\ncount = 2\nextra = true\n"
    assert env_file.read_text() == ("# prescribe:begin managed\nalpha=1\nbeta=2\n# prescribe:end managed\n")

    second = orchestrator.run(spec_path, tool_version="test")
    assert [result.status for result in second] == ["noop", "noop"]
    assert [result.changed for result in second] == [False, False]
    assert [result.applied for result in second] == [False, False]


def test_orchestrator_conflict_when_file_changes_between_runs(tmp_path: Path, state_store) -> None:
    config_toml = tmp_path / "config.toml"
    config_toml.write_text("title = 'hello'\ncount = 1\n")

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    store = state_store
    orchestrator = Orchestrator(store)

    first = orchestrator.run(spec_path)
    assert first[0].status == "applied"
    assert first[0].applied is True

    config_toml.write_text("title = 'hello'\ncount = 3\n")

    second = orchestrator.run(spec_path)
    assert second[0].status == "conflict"
    assert second[0].conflict is not None
    assert second[0].conflict.reason == "managed key 'count' changed externally"
    assert second[0].conflict.baseline_fingerprint is not None
    assert second[0].conflict.current_fingerprint.path == config_toml
    assert second[0].conflict.baseline_hash == second[0].conflict.baseline_fingerprint.content_hash
    assert second[0].conflict.current_hash == second[0].conflict.current_fingerprint.content_hash
    assert second[0].conflict.baseline_hash != second[0].conflict.current_hash
    assert second[0].applied is False


def test_orchestrator_conflicts_when_managed_key_changes_between_runs(tmp_path: Path, state_store) -> None:
    config_toml = tmp_path / "config.toml"
    config_toml.write_text("title = 'hello'\ncount = 1\nexternal = true\n")

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    store = state_store
    orchestrator = Orchestrator(store)

    first = orchestrator.run(spec_path)
    assert first[0].status == "applied"
    assert first[0].applied is True

    config_toml.write_text("title = 'hello'\ncount = 3\nexternal = false\n")

    second = orchestrator.run(spec_path)
    assert second[0].status == "conflict"
    assert second[0].conflict is not None
    assert second[0].conflict.reason == "managed key 'count' changed externally"
    assert second[0].applied is False


def test_orchestrator_reports_managed_conflict_in_dry_run(tmp_path: Path, state_store) -> None:
    config_toml = tmp_path / "config.toml"
    config_toml.write_text("title = 'hello'\ncount = 1\nexternal = true\n")

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    orchestrator = Orchestrator(state_store)
    first = orchestrator.run(spec_path)
    assert first[0].status == "applied"

    config_toml.write_text("title = 'hello'\ncount = 3\nexternal = false\n")

    dry_run = orchestrator.run(spec_path, dry_run=True)
    assert dry_run[0].status == "conflict"
    assert dry_run[0].dry_run is True
    assert dry_run[0].applied is False
    assert dry_run[0].conflict is not None
    assert dry_run[0].conflict.reason == "managed key 'count' changed externally"


def test_orchestrator_creates_missing_toml_file(tmp_path: Path, state_store) -> None:
    config_toml = tmp_path / "new_config.toml"
    assert not config_toml.exists()

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[files]]\npath = 'new_config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\nextra = true\n"
    )

    store = state_store
    orchestrator = Orchestrator(store)

    results = orchestrator.run(spec_path)
    assert results[0].status == "applied"
    assert results[0].applied is True
    assert results[0].changed is True
    assert config_toml.exists()

    document = TomlAdapter().load(config_toml)
    assert document.root["count"] == 2
    assert document.root["extra"] is True


def test_orchestrator_expands_home_and_env_vars_in_target_path(tmp_path: Path, monkeypatch, state_store) -> None:
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("APP_NAME", "myapp")

    config_toml = home_dir / ".config" / "myapp" / "config.toml"
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[files]]\npath = '~/.config/${APP_NAME}/config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n"
    )

    store = state_store
    orchestrator = Orchestrator(store)

    results = orchestrator.run(spec_path)
    assert results[0].status == "applied"
    assert config_toml.exists()
    assert TomlAdapter().load(config_toml).root["count"] == 2


def test_orchestrator_creates_missing_line_file(tmp_path: Path, state_store) -> None:
    env_file = tmp_path / "new_env"
    assert not env_file.exists()

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[files]]\npath = 'new_env'\nformat = 'line'\nmanaged_block_id = 'managed'\nlines = ['alpha=1', 'beta=2']\n"
    )

    store = state_store
    orchestrator = Orchestrator(store)

    results = orchestrator.run(spec_path)
    assert results[0].status == "applied"
    assert results[0].applied is True
    assert env_file.exists()
    assert "alpha=1" in env_file.read_text()


def test_orchestrator_second_run_after_create_is_noop(tmp_path: Path, state_store) -> None:
    _config_toml = tmp_path / "new_config.toml"

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'new_config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    store = state_store
    orchestrator = Orchestrator(store)

    first = orchestrator.run(spec_path)
    assert first[0].status == "applied"
    assert first[0].applied is True

    second = orchestrator.run(spec_path)
    assert second[0].status == "noop"
    assert second[0].changed is False
    assert second[0].applied is False


def test_orchestrator_rollback_restores_managed_changes_and_preserves_unrelated_edits(
    tmp_path: Path, state_store
) -> None:
    config_toml = tmp_path / "config.toml"
    config_toml.write_text("title = 'hello'\ncount = 1\nexternal = true\n")

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    store = state_store
    orchestrator = Orchestrator(store)

    applied = orchestrator.run(spec_path)
    assert applied[0].status == "applied"

    config_toml.write_text("title = 'hello'\ncount = 2\nexternal = false\n")

    rolled_back = orchestrator.rollback(config_toml)
    assert rolled_back.status == "rolled-back"
    assert rolled_back.applied is True
    assert rolled_back.changed is True
    assert config_toml.read_text() == "title = 'hello'\ncount = 1\nexternal = false\n"


def test_orchestrator_rollback_recreates_deleted_file_from_checkpoint(fake_root: Path, state_store) -> None:
    config_toml = fake_root / "config.toml"
    config_toml.write_text("title = 'hello'\ncount = 1\nexternal = true\n")

    spec_path = fake_root / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    orchestrator = Orchestrator(state_store)
    applied = orchestrator.run(spec_path)
    assert applied[0].status == "applied"

    config_toml.unlink()

    rolled_back = orchestrator.rollback(config_toml)
    assert rolled_back.status == "rolled-back"
    assert rolled_back.applied is True
    assert rolled_back.changed is True
    assert config_toml.exists()

    parsed = TomlAdapter().load(config_toml).root
    assert parsed["title"] == "hello"
    assert parsed["count"] == 1
    assert parsed["external"] is True


def test_orchestrator_rollback_removes_managed_file_created_by_app(tmp_path: Path, state_store) -> None:
    config_toml = tmp_path / "new_config.toml"
    assert not config_toml.exists()

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'new_config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    store = state_store
    orchestrator = Orchestrator(store)

    applied = orchestrator.run(spec_path)
    assert applied[0].status == "applied"
    assert config_toml.exists()

    rolled_back = orchestrator.rollback(config_toml)
    assert rolled_back.status == "rolled-back"
    assert rolled_back.applied is True
    assert rolled_back.changed is True
    assert not config_toml.exists()


def test_orchestrator_rollback_preserves_regex_manual_edit_across_multiple_batches(tmp_path: Path, state_store) -> None:
    config_toml = tmp_path / "config.toml"
    config_toml.write_text("title = 'hello'\ncount = 1\ncolor = 'red'\nexternal = 'keep'\n")

    spec_path = tmp_path / "spec.toml"
    store = state_store
    orchestrator = Orchestrator(store)

    spec_path.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\ncolor = 'blue'\n")
    first = orchestrator.run(spec_path)
    assert first[0].status == "applied"

    config_toml.write_text(
        re.sub(
            r"^external = 'keep'$",
            "external = 'manual'",
            config_toml.read_text(),
            flags=re.MULTILINE,
        )
    )

    spec_path.write_text(
        "[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 3\ncolor = 'blue'\nnote = 'second'\n"
    )
    second = orchestrator.run(spec_path)
    assert second[0].status == "applied"

    spec_path.write_text(
        "[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 4\ncolor = 'green'\nnote = 'third'\n"
    )
    third = orchestrator.run(spec_path)
    assert third[0].status == "applied"

    rolled_back = orchestrator.rollback(config_toml)
    assert rolled_back.status == "rolled-back"
    assert rolled_back.applied is True
    assert rolled_back.changed is True

    parsed = TomlAdapter().load(config_toml).root
    assert parsed["title"] == "hello"
    assert parsed["count"] == 1
    assert parsed["color"] == "red"
    assert parsed["external"] == "manual"
    assert "note" not in parsed


def test_orchestrator_dry_run_reports_existing_file_changes_without_writing(tmp_path: Path, state_store) -> None:
    config_toml = tmp_path / "config.toml"
    config_toml.write_text("title = 'hello'\ncount = 1\n")

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    store = state_store
    orchestrator = Orchestrator(store)

    results = orchestrator.run(spec_path, dry_run=True)
    assert results[0].status == "dry-run"
    assert results[0].dry_run is True
    assert results[0].applied is False
    assert results[0].changed is True
    assert results[0].error is None
    assert config_toml.read_text() == "title = 'hello'\ncount = 1\n"
    assert str(config_toml) not in {str(m.path) for m in store.list_managed()}


def test_orchestrator_dry_run_reports_missing_file_creates_without_writing(tmp_path: Path, state_store) -> None:
    config_toml = tmp_path / "new_config.toml"
    assert not config_toml.exists()

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'new_config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    store = state_store
    orchestrator = Orchestrator(store)

    results = orchestrator.run(spec_path, dry_run=True)
    assert results[0].status == "dry-run"
    assert results[0].dry_run is True
    assert results[0].applied is False
    assert results[0].changed is True
    assert config_toml.exists() is False
    assert str(config_toml) not in {str(m.path) for m in store.list_managed()}


def test_orchestrator_reports_corrupt_existing_file(tmp_path: Path, state_store) -> None:
    config_toml = tmp_path / "config.toml"
    config_toml.write_text("title = 'hello'\ncount = [\n")

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    store = state_store
    orchestrator = Orchestrator(store)

    results = orchestrator.run(spec_path)
    assert results[0].status == "error"
    assert results[0].applied is False
    assert results[0].error is not None
    assert results[0].conflict is None
    assert config_toml.read_text() == "title = 'hello'\ncount = [\n"


def test_orchestrator_reports_corrupt_line_file(tmp_path: Path, state_store) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("# prescribe:begin managed\nalpha=1\n# prescribe:end other\n")

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[files]]\npath = '.env'\nformat = 'line'\nmanaged_block_id = 'managed'\nlines = ['alpha=1']\n"
    )

    store = state_store
    orchestrator = Orchestrator(store)

    results = orchestrator.run(spec_path)
    assert results[0].status == "error"
    assert results[0].applied is False
    assert results[0].error is not None
    assert "managed block mismatch" in results[0].error
    assert env_file.read_text() == ("# prescribe:begin managed\nalpha=1\n# prescribe:end other\n")


def test_orchestrator_detects_change_during_planning(tmp_path: Path, monkeypatch, state_store) -> None:
    config_toml = tmp_path / "config.toml"
    config_toml.write_text("title = 'hello'\ncount = 1\n")

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    store = state_store
    orchestrator = Orchestrator(store)
    original_plan = Planner().plan

    def fake_plan(document, desired):
        plan = original_plan(document, desired)
        config_toml.write_text("title = 'hello'\ncount = 3\n")
        return plan

    monkeypatch.setattr(orchestrator.planner, "plan", fake_plan)

    results = orchestrator.run(spec_path)
    assert results[0].status == "conflict"
    assert results[0].applied is False
    assert results[0].conflict is not None
    assert results[0].conflict.reason == "content changed"
    assert results[0].conflict.baseline_fingerprint is not None
    assert results[0].conflict.current_fingerprint.path == config_toml
    assert results[0].conflict.current_hash == results[0].conflict.current_fingerprint.content_hash
    assert config_toml.read_text() == "title = 'hello'\ncount = 3\n"


def test_orchestrator_detects_new_file_appearing_during_planning(tmp_path: Path, monkeypatch, state_store) -> None:
    config_toml = tmp_path / "new_config.toml"
    assert not config_toml.exists()

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'new_config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    store = state_store
    orchestrator = Orchestrator(store)
    original_plan = Planner().plan

    def fake_plan(document, desired):
        plan = original_plan(document, desired)
        config_toml.write_text("count = 3\n")
        return plan

    monkeypatch.setattr(orchestrator.planner, "plan", fake_plan)

    results = orchestrator.run(spec_path)
    assert results[0].status == "conflict"
    assert results[0].applied is False
    assert results[0].conflict is not None
    assert results[0].conflict.reason == "file appeared during orchestration"
    assert results[0].conflict.baseline_fingerprint is None
    assert results[0].conflict.current_fingerprint.path == config_toml
    assert results[0].conflict.current_hash == results[0].conflict.current_fingerprint.content_hash
    assert config_toml.read_text() == "count = 3\n"


def test_orchestrator_rollback_skips_managed_key_changed_externally(tmp_path: Path, state_store) -> None:
    config_toml = tmp_path / "config.toml"
    config_toml.write_text("title = 'hello'\ncount = 1\n")

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    orchestrator = Orchestrator(state_store)
    applied = orchestrator.run(spec_path)
    assert applied[0].status == "applied"

    config_toml.write_text("title = 'hello'\ncount = 99\n")

    rolled_back = orchestrator.rollback(config_toml)
    assert rolled_back.status == "noop"
    assert TomlAdapter().load(config_toml).root["count"] == 99


def test_orchestrator_rollback_force_reverts_externally_modified_key(tmp_path: Path, state_store) -> None:
    config_toml = tmp_path / "config.toml"
    config_toml.write_text("title = 'hello'\ncount = 1\n")

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    orchestrator = Orchestrator(state_store)
    applied = orchestrator.run(spec_path)
    assert applied[0].status == "applied"

    config_toml.write_text("title = 'hello'\ncount = 99\n")

    rolled_back = orchestrator.rollback(config_toml, conflict_resolver=lambda key: True)
    assert rolled_back.status == "rolled-back"
    assert rolled_back.applied is True
    assert rolled_back.changed is True
    assert TomlAdapter().load(config_toml).root["count"] == 1


def test_orchestrator_rollback_records_conflict_resolution_in_db(tmp_path: Path, state_store) -> None:
    import json

    config_toml = tmp_path / "config.toml"
    config_toml.write_text("title = 'hello'\ncount = 1\ncolor = 'red'\n")

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\ncolor = 'blue'\n")

    orchestrator = Orchestrator(state_store)
    run_result = orchestrator.run(spec_path)
    assert run_result[0].status == "applied"
    run_id = state_store.latest_run_id()
    assert run_id is not None

    # externally modify both managed keys so both trigger conflict resolution
    config_toml.write_text("title = 'hello'\ncount = 99\ncolor = 'green'\n")

    # revert count (force), leave color (ignore)
    def resolver(key: str) -> bool:
        return key == "count"

    rolled_back = orchestrator.rollback(config_toml, conflict_resolver=resolver)
    assert rolled_back.status == "rolled-back"

    rollback_run_id = state_store.latest_run_id()
    assert rollback_run_id is not None
    event = state_store.latest_event(rollback_run_id)
    assert event is not None
    assert event.event_type == "rollback"
    assert "force-reverted" in event.summary
    assert "skipped" in event.summary
    assert event.details is not None
    details = json.loads(event.details)
    assert "count" in details["force_reverted"]
    assert "color" in details["skipped"]
