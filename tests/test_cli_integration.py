"""CLI integration tests.

These tests invoke the `prescribe` entry-point via subprocess.  They run by
default when the binary is on PATH (e.g. inside the project virtualenv via
`uv run pytest`).  Pass --no-cli to skip them explicitly.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_CLI = shutil.which("prescribe")
pytestmark = pytest.mark.cli


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def workdir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def run(workdir: Path):
    """Return a helper that invokes `prescribe` with an isolated state DB."""
    state_db = workdir / ".prescribe" / "state.db"

    def _run(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [_CLI, *args],
            capture_output=True,
            text=True,
            cwd=workdir,
            input=stdin,
            env={**os.environ, "PRESCRIBE_STATE": str(state_db)},
        )

    return _run


# ---------------------------------------------------------------------------
# --version
# ---------------------------------------------------------------------------


def test_version_flag_exits_zero(run) -> None:
    result = run("--version")
    assert result.returncode == 0
    assert "prescribe" in result.stdout


# ---------------------------------------------------------------------------
# apply — basic
# ---------------------------------------------------------------------------


def test_apply_creates_missing_file(run, workdir: Path) -> None:
    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 1\n")

    result = run("apply", "spec.toml")

    assert result.returncode == 0
    assert "applied" in result.stdout
    assert (workdir / "config.toml").exists()


def test_apply_sets_key_in_existing_file(run, workdir: Path) -> None:
    config = workdir / "config.toml"
    config.write_text("title = 'hello'\ncount = 1\n")

    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    result = run("apply", "spec.toml")

    assert result.returncode == 0
    assert "applied" in result.stdout
    assert "count = 2" in config.read_text()
    assert "title" in config.read_text()


def test_apply_preserves_unmanaged_keys(run, workdir: Path) -> None:
    config = workdir / "config.toml"
    config.write_text("title = 'hello'\ncount = 1\nunrelated = true\n")

    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 99\n")

    run("apply", "spec.toml")

    text = config.read_text()
    assert "title = 'hello'" in text
    assert "unrelated = true" in text


# ---------------------------------------------------------------------------
# apply — idempotency
# ---------------------------------------------------------------------------


def test_apply_second_run_is_noop(run, workdir: Path) -> None:
    config = workdir / "config.toml"
    config.write_text("title = 'hello'\ncount = 1\n")

    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    first = run("apply", "spec.toml")
    assert first.returncode == 0
    assert "applied" in first.stdout

    second = run("apply", "spec.toml")
    assert second.returncode == 0
    assert "in sync" in second.stdout
    assert "applied" not in second.stdout


def test_apply_repeated_on_new_file_is_noop(run, workdir: Path) -> None:
    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 1\n")

    first = run("apply", "spec.toml")
    assert first.returncode == 0

    second = run("apply", "spec.toml")
    assert second.returncode == 0
    assert "in sync" in second.stdout


# ---------------------------------------------------------------------------
# apply — dry-run
# ---------------------------------------------------------------------------


def test_apply_dry_run_does_not_write(run, workdir: Path) -> None:
    config = workdir / "config.toml"
    config.write_text("count = 1\n")

    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    result = run("apply", "--dry-run", "spec.toml")

    assert result.returncode == 0
    assert "would change" in result.stdout
    assert config.read_text() == "count = 1\n"


def test_apply_dry_run_on_synced_file_shows_in_sync(run, workdir: Path) -> None:
    config = workdir / "config.toml"
    config.write_text("count = 1\n")

    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    run("apply", "spec.toml")

    result = run("apply", "--dry-run", "spec.toml")
    assert result.returncode == 0
    assert "in sync" in result.stdout


# ---------------------------------------------------------------------------
# apply — --json output
# ---------------------------------------------------------------------------


def test_apply_json_output_is_valid(run, workdir: Path) -> None:
    config = workdir / "config.toml"
    config.write_text("count = 1\n")

    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    result = run("apply", "--json", "spec.toml")
    assert result.returncode == 0

    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["status"] == "applied"
    assert data[0]["applied"] is True
    assert data[0]["changed"] is True


def test_apply_json_noop_output(run, workdir: Path) -> None:
    config = workdir / "config.toml"
    config.write_text("count = 1\n")

    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    run("apply", "spec.toml")

    result = run("apply", "--json", "spec.toml")
    assert result.returncode == 0

    data = json.loads(result.stdout)
    assert data[0]["status"] == "noop"
    assert data[0]["applied"] is False
    assert data[0]["changed"] is False


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_shows_would_change_before_apply(run, workdir: Path) -> None:
    config = workdir / "config.toml"
    config.write_text("count = 1\n")

    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    result = run("status", "spec.toml")
    assert result.returncode == 0
    assert "would change" in result.stdout


def test_status_shows_in_sync_after_apply(run, workdir: Path) -> None:
    config = workdir / "config.toml"
    config.write_text("count = 1\n")

    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    run("apply", "spec.toml")

    result = run("status", "spec.toml")
    assert result.returncode == 0
    assert "in sync" in result.stdout
    assert "would change" not in result.stdout


def test_status_json_output(run, workdir: Path) -> None:
    config = workdir / "config.toml"
    config.write_text("count = 1\n")

    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    run("apply", "spec.toml")

    result = run("status", "--json", "spec.toml")
    assert result.returncode == 0

    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert data[0]["status"] == "in sync"
    assert data[0]["changed"] is False


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_empty_before_any_apply(run) -> None:
    result = run("list")
    assert result.returncode == 0


def test_list_shows_managed_file_after_apply(run, workdir: Path) -> None:
    config = workdir / "config.toml"
    config.write_text("count = 1\n")

    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    run("apply", "spec.toml")

    result = run("list")
    assert result.returncode == 0
    assert "config.toml" in result.stdout


def test_list_json_output(run, workdir: Path) -> None:
    config = workdir / "config.toml"
    config.write_text("count = 1\n")

    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    run("apply", "spec.toml")

    result = run("list", "--json")
    assert result.returncode == 0

    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert len(data) == 1
    assert "config.toml" in data[0]["path"]
    assert data[0]["format"] == "toml"
    assert data[0]["exists"] is True


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------


def test_rollback_reverts_managed_key(run, workdir: Path) -> None:
    config = workdir / "config.toml"
    config.write_text("count = 1\nextra = true\n")

    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 99\n")

    run("apply", "spec.toml")
    assert "count = 99" in config.read_text()

    result = run("rollback", str(config))
    assert result.returncode == 0
    assert "rolled-back" in result.stdout
    assert "count = 1" in config.read_text()
    assert "extra = true" in config.read_text()


def test_rollback_dry_run_does_not_write(run, workdir: Path) -> None:
    config = workdir / "config.toml"
    config.write_text("count = 1\n")

    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 99\n")

    run("apply", "spec.toml")
    assert "count = 99" in config.read_text()

    result = run("rollback", "--dry-run", str(config))
    assert result.returncode == 0
    assert "would change" in result.stdout
    assert "count = 99" in config.read_text()


def test_rollback_original_deletes_prescribe_created_file(run, workdir: Path) -> None:
    config = workdir / "new_config.toml"
    assert not config.exists()

    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'new_config.toml'\nformat = 'toml'\n[files.data]\ncount = 1\n")

    run("apply", "spec.toml")
    assert config.exists()

    result = run("rollback", "--original", str(config))
    assert result.returncode == 0
    assert not config.exists()


def test_rollback_json_output(run, workdir: Path) -> None:
    config = workdir / "config.toml"
    config.write_text("count = 1\n")

    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    run("apply", "spec.toml")

    result = run("rollback", "--json", str(config))
    assert result.returncode == 0

    data = json.loads(result.stdout)
    assert data["status"] == "rolled-back"
    assert data["applied"] is True
    assert data["changed"] is True


# ---------------------------------------------------------------------------
# error handling
# ---------------------------------------------------------------------------


def test_apply_nonexistent_spec_exits_nonzero(run) -> None:
    result = run("apply", "nonexistent.toml")
    assert result.returncode != 0
    assert result.stderr


def test_apply_invalid_spec_exits_nonzero(run, workdir: Path) -> None:
    spec = workdir / "spec.toml"
    spec.write_text("this is not valid toml [\n")

    result = run("apply", "spec.toml")
    assert result.returncode != 0
    assert result.stderr


def test_apply_spec_empty_spec_is_valid(run, workdir: Path) -> None:
    """An empty spec (no files/env/shell targets) is valid and returns success."""
    spec = workdir / "spec.toml"
    spec.write_text("[metadata]\nname = 'test'\n")

    result = run("apply", "spec.toml")
    assert result.returncode == 0


def test_apply_spec_targets_key_raises_error(run, workdir: Path) -> None:
    spec = workdir / "spec.toml"
    spec.write_text("[[targets]]\npath = 'config.toml'\nformat = 'toml'\n")

    result = run("apply", "spec.toml")
    assert result.returncode != 0
    assert "[[targets]]" in result.stderr


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_validate_reports_targets_without_creating_state(run, workdir: Path) -> None:
    spec = workdir / "spec.toml"
    spec.write_text(
        "[[files]]\n"
        "path = 'config.toml'\n"
        "format = 'toml'\n"
        "tags = ['base']\n"
        "\n"
        "[[env]]\n"
        "name = 'EDITOR'\n"
        "value = 'nvim'\n"
        "\n"
        "[[shell]]\n"
        "path = 'profile.ps1'\n"
        "managed_block_id = 'prescribe-env'\n"
        "shells = ['pwsh']\n"
    )

    result = run("validate", "spec.toml")

    assert result.returncode == 0
    assert "valid" in result.stdout
    assert "file" in result.stdout
    assert "env" in result.stdout
    assert "shell" in result.stdout
    assert not (workdir / ".prescribe" / "state.db").exists()


def test_validate_json_reports_active_and_skipped_targets(run, workdir: Path) -> None:
    spec = workdir / "spec.toml"
    spec.write_text(
        "[[files]]\n"
        "path = 'agent.toml'\n"
        "format = 'toml'\n"
        "tags = ['agent']\n"
        "\n"
        "[[files]]\n"
        "path = 'secret.toml'\n"
        "format = 'toml'\n"
        "tags = ['secrets']\n"
    )

    result = run("validate", "--json", "--tags", "agent", "spec.toml")

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["valid"] is True
    assert [target["active"] for target in data["targets"]] == [True, False]
    assert data["targets"][0]["type"] == "file"
    assert data["targets"][0]["path"].endswith("agent.toml")


def test_validate_explain_skips_includes_reason(run, workdir: Path) -> None:
    non_current = "linux" if sys.platform == "win32" else "windows"
    spec = workdir / "spec.toml"
    spec.write_text(f"[[files]]\npath = 'linux.toml'\nformat = 'toml'\nplatforms = ['{non_current}']\n")

    result = run("validate", "--explain-skips", "--json", "spec.toml")

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["targets"][0]["active"] is False
    assert data["targets"][0]["skip_reason"] == "platform did not match"


def test_validate_invalid_spec_exits_nonzero(run, workdir: Path) -> None:
    spec = workdir / "spec.toml"
    spec.write_text("[[targets]]\npath = 'old.toml'\nformat = 'toml'\n")

    result = run("validate", "spec.toml")

    assert result.returncode != 0
    assert "[[targets]]" in result.stderr


def test_validate_plan_reports_would_change_without_state(run, workdir: Path) -> None:
    config = workdir / "config.toml"
    config.write_text("count = 1\n")
    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    result = run("validate", "--plan", "spec.toml")

    assert result.returncode == 0
    assert "would change" in result.stdout
    assert "config.toml" in result.stdout
    assert not (workdir / ".prescribe" / "state.db").exists()


def test_validate_plan_diff_outputs_unified_diff(run, workdir: Path) -> None:
    config = workdir / "config.toml"
    config.write_text("count = 1\n")
    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    result = run("validate", "--plan", "--diff", "spec.toml")

    assert result.returncode == 0
    assert "-count = 1" in result.stdout
    assert "+count = 2" in result.stdout


def test_validate_plan_json_includes_status(run, workdir: Path) -> None:
    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 1\n")

    result = run("validate", "--plan", "--json", "spec.toml")

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["valid"] is True
    assert data["targets"][0]["status"] == "would change"


def test_list_specs_reports_sorted_top_level_specs(run, workdir: Path) -> None:
    specs = workdir / "specs"
    nested = specs / "nested"
    nested.mkdir(parents=True)
    (specs / "20-work.toml").write_text("")
    (specs / "00-base.toml").write_text("")
    (nested / "10-nested.toml").write_text("")

    result = run("list-specs", "specs")

    assert result.returncode == 0
    lines = [Path(line).name for line in result.stdout.splitlines()]
    assert lines == ["00-base.toml", "20-work.toml"]


def test_status_dir_reports_each_spec_without_writing(run, workdir: Path) -> None:
    specs = workdir / "specs"
    specs.mkdir()
    (specs / "00-base.toml").write_text(
        "[[files]]\npath = '../config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n"
    )
    config = workdir / "config.toml"
    config.write_text("count = 1\n")

    result = run("status-dir", "specs", "--diff")

    assert result.returncode == 0
    assert "spec:" in result.stdout
    assert "would change" in result.stdout
    assert "+count = 2" in result.stdout
    assert config.read_text() == "count = 1\n"


def test_apply_dir_applies_specs_in_sorted_order(run, workdir: Path) -> None:
    specs = workdir / "specs"
    specs.mkdir()
    config = workdir / "config.toml"
    config.write_text("count = 0\n")
    (specs / "20-second.toml").write_text(
        "[[files]]\npath = '../config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n"
    )
    (specs / "10-first.toml").write_text(
        "[[files]]\npath = '../config.toml'\nformat = 'toml'\n[files.data]\ncount = 1\n"
    )

    result = run("apply-dir", "specs")

    assert result.returncode == 0
    assert "10-first.toml" in result.stdout
    assert "20-second.toml" in result.stdout
    assert "count = 2" in config.read_text()


def test_validate_dir_json_reports_asset_targets_without_state(run, workdir: Path) -> None:
    specs = workdir / "specs"
    specs.mkdir()
    source = specs / "mcp.json"
    source.write_text("{}\n")
    (specs / "agent.toml").write_text("[[assets]]\nsource = 'mcp.json'\ndest = '../out/mcp.json'\n")

    result = run("validate-dir", "--json", "--plan", "specs")

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["valid"] is True
    assert data["specs"][0]["targets"][0]["type"] == "asset"
    assert data["specs"][0]["targets"][0]["status"] == "would change"
    assert not (workdir / ".prescribe" / "state.db").exists()


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def test_doctor_reports_environment(run) -> None:
    result = run("doctor")

    assert result.returncode == 0
    assert "platform" in result.stdout
    assert "state path" in result.stdout
    assert "path separator" in result.stdout


def test_doctor_json_output(run) -> None:
    result = run("doctor", "--json")

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert "platform" in data
    assert "state_path" in data
    assert data["path_separator"] == os.pathsep


# ---------------------------------------------------------------------------
# formats
# ---------------------------------------------------------------------------


def test_apply_yaml_target(run, workdir: Path) -> None:
    config = workdir / "config.yaml"
    config.write_text("title: hello\ncount: 1\n")

    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'config.yaml'\nformat = 'yaml'\n[files.data]\ncount = 2\n")

    result = run("apply", "spec.toml")
    assert result.returncode == 0
    assert "count: 2" in config.read_text()
    assert "title: hello" in config.read_text()


def test_apply_jsonc_target(run, workdir: Path) -> None:
    config = workdir / "settings.json"
    config.write_text('{\n  // a comment\n  "count": 1\n}\n')

    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'settings.json'\nformat = 'jsonc'\n[files.data]\n\"count\" = 2\n")

    result = run("apply", "spec.toml")
    assert result.returncode == 0
    text = config.read_text()
    assert "// a comment" in text
    assert '"count": 2' in text


def test_apply_line_target(run, workdir: Path) -> None:
    config = workdir / ".env"
    config.write_text("UNMANAGED=yes\n")

    spec = workdir / "spec.toml"
    spec.write_text(
        "[[files]]\npath = '.env'\nformat = 'line'\nmanaged_block_id = 'myblock'\nlines = ['FOO=1', 'BAR=2']\n"
    )

    result = run("apply", "spec.toml")
    assert result.returncode == 0
    text = config.read_text()
    assert "UNMANAGED=yes" in text
    assert "FOO=1" in text
    assert "BAR=2" in text
    assert "prescribe:begin myblock" in text
    assert "prescribe:end myblock" in text


def test_rollback_original_restores_existing_file_to_baseline(run, workdir: Path) -> None:
    """--original rollback should restore an existing file to its pre-prescribe content."""
    config = workdir / "config.toml"
    config.write_text("count = 1\nname = 'original'\n")

    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 42\n")

    run("apply", "spec.toml")
    assert config.read_text() != "count = 1\nname = 'original'\n"

    result = run("rollback", "--original", str(config))
    assert result.returncode == 0
    assert config.read_text() == "count = 1\nname = 'original'\n"


# ── tags ────────────────────────────────────────────────────


def test_apply_with_tags_filter(run, workdir: Path) -> None:
    """--tags agent only applies targets tagged 'agent'."""
    spec = workdir / "spec.toml"
    config = workdir / "config.toml"
    config.write_text("count = 1\n")

    spec.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\ntags = ['agent']\n[files.data]\ncount = 2\n")

    result = run("apply", "--tags", "agent", "spec.toml")
    assert result.returncode == 0
    assert "applied" in result.stdout


def test_apply_with_skip_tags_filter(run, workdir: Path) -> None:
    """--skip-tags secrets skips targets tagged 'secrets'."""
    spec = workdir / "spec.toml"
    config = workdir / "config.toml"
    config.write_text("count = 1\n")

    spec.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\ntags = ['secrets']\n[files.data]\ncount = 2\n")

    result = run("apply", "--skip-tags", "secrets", "spec.toml")
    assert result.returncode == 0
    # Should NOT have been applied — file unchanged
    assert config.read_text().startswith("count = 1\n")


def test_status_explain_skips_shows_reason(run, workdir: Path) -> None:
    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'secret.toml'\nformat = 'toml'\ntags = ['secrets']\n")

    result = run("status", "--skip-tags", "secrets", "--explain-skips", "spec.toml")

    assert result.returncode == 0
    assert "skipped" in result.stdout
    assert "skip-tags matched" in result.stdout


def test_status_json_includes_skip_reason_when_explained(run, workdir: Path) -> None:
    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'agent.toml'\nformat = 'toml'\ntags = ['agent']\n")

    result = run("status", "--tags", "base", "--explain-skips", "--json", "spec.toml")

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data[0]["status"] == "skipped"
    assert data[0]["skip_reason"] == "tags did not match"


def test_status_explain_skips_names_priority_winner(run, workdir: Path) -> None:
    spec = workdir / "spec.toml"
    spec.write_text(
        "[[files]]\n"
        "path = 'config.toml'\n"
        "format = 'toml'\n"
        "priority = 10\n"
        "[files.data]\n"
        "count = 10\n"
        "\n"
        "[[files]]\n"
        "path = 'config.toml'\n"
        "format = 'toml'\n"
        "priority = 0\n"
        "[files.data]\n"
        "count = 0\n"
    )

    result = run("status", "--explain-skips", "spec.toml")

    assert result.returncode == 0
    assert "lower priority target selected" in result.stdout
    assert "winner priority 0" in result.stdout


# ── status --diff ──────────────────────────────────────────


def test_status_diff_shows_unified_diff(run, workdir: Path) -> None:
    """prescribe status --diff shows unified diffs in output."""
    config = workdir / "config.toml"
    config.write_text("count = 1\n")

    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    result = run("status", "--diff", "spec.toml")
    assert result.returncode == 0
    assert "-count = 1" in result.stdout
    assert "+count = 2" in result.stdout


def test_status_diff_json_includes_diff(run, workdir: Path) -> None:
    """prescribe status --diff --json includes diff in JSON output."""
    import json

    config = workdir / "config.toml"
    config.write_text("count = 1\n")

    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    result = run("status", "--diff", "--json", "spec.toml")
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert "diff" in data[0]
    assert "+count = 2" in data[0]["diff"]


def test_status_diff_no_changes_no_output(run, workdir: Path) -> None:
    """When everything is in sync, --diff shows nothing extra."""
    config = workdir / "config.toml"
    config.write_text("count = 2\n")

    spec = workdir / "spec.toml"
    spec.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n[files.data]\ncount = 2\n")

    result = run("status", "--diff", "spec.toml")
    assert result.returncode == 0
    # Should NOT contain diff markers — nothing changed
    assert "-count = 2" not in result.stdout


def test_status_diff_shows_shell_target_diff(run, workdir: Path) -> None:
    profile = workdir / ".bashrc"
    profile.write_text("# existing\n")
    spec = workdir / "spec.toml"
    spec.write_text(
        "[[env]]\n"
        "name = 'EDITOR'\n"
        "value = 'nvim'\n"
        "\n"
        "[[shell]]\n"
        "path = '.bashrc'\n"
        "managed_block_id = 'prescribe-env'\n"
        "shells = ['bash']\n"
    )

    result = run("status", "--diff", "spec.toml")

    assert result.returncode == 0
    assert "+# prescribe:begin prescribe-env" in result.stdout
    assert '+export EDITOR="nvim"' in result.stdout
