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
